from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from app_activity import AppSettings, foreground, notification, observe_app
from tv_controller import ADB, App, Config, Store, TelegramError
from test_controller import ENV

YOUTUBE = 'com.google.android.youtube.tv'
NETFLIX = 'com.netflix.ninja'
HOME = 'com.google.android.tvlauncher'


class DetectionTests(unittest.TestCase):
    def test_both_android_formats_and_activity_names(self):
        for marker in ('mResumedActivity:', 'topResumedActivity='):
            for activity in ('.SplashActivity', '.HomeActivity'):
                self.assertEqual(foreground(f'{marker} ActivityRecord{{abc u0 com.sonyliv/{activity} t42}}'), 'com.sonyliv')
        self.assertEqual(foreground('topResumedActivity=ActivityRecord{abc u0 com.google.android.tv.inputplayer/.Main t2}'), 'com.google.android.tv.inputplayer')
        self.assertEqual(foreground('mResumedActivity: ActivityRecord{u0 org.unknown.player/.Main}'), 'org.unknown.player')

    def test_focus_fallback_and_false_positives(self):
        adb = ADB()
        adb.run = Mock(side_effect=['mResumedActivity: null', 'mCurrentFocus=Window{abc u0 com.netflix.ninja/.MainActivity}'])
        self.assertEqual(adb.foreground('endpoint'), NETFLIX)
        for output in ('ProcessRecord{com.netflix.ninja}', 'mResumedActivity: null', 'media session com.netflix.ninja/.Main'):
            self.assertIsNone(foreground(output))
        self.assertIsNone(foreground('mResumedActivity: a com.netflix.ninja/.A\nmResumedActivity: b com.sonyliv/.B'))

    def test_app_config(self):
        config = Config.load(dict(ENV, APP_NAME_MAP='{"org.custom.app":"Custom"}', TV2_APP_MONITOR_ENABLED='false', TV2_LIVE_VIEW_ENABLED='true'))
        self.assertFalse(config.tvs[1].app_monitor)
        self.assertTrue(config.tvs[1].live_view)
        self.assertEqual(config.apps.names['org.custom.app'], 'Custom')
        for key, value in [('TZ', 'No/SuchZone'), ('APP_MONITOR_INTERVAL', '0'), ('APP_MONITOR_CONFIRMATIONS', '1'), ('APP_NAME_MAP', '[]'), ('TV_APP_MONITOR_ENABLED', 'maybe')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                Config.load(dict(ENV, **{key: value}))


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.config = Config.load(dict(ENV, STATE_FILE=str(self.path)))
        self.adb, self.telegram = Mock(), Mock()
        self.adb.connect.return_value = 'device'
        self.adb.power.return_value = 'ON'
        self.app = App(self.config, Store(self.path), self.adb, self.telegram)
        self.addCleanup(self.app.close)
        self.tv = self.config.tvs[1]

    def sample(self, package, count=2, tv=None):
        for _ in range(count):
            self.app.record_activity(tv or self.tv, package)

    def test_changes_baseline_same_package_home_unknown_and_independence(self):
        self.sample(YOUTUBE)
        self.sample(YOUTUBE, 4)
        self.assertEqual(self.app.store.data['app_history'], [])
        self.sample(NETFLIX, 1)
        self.assertEqual(self.app.store.data['outbox'], [])
        self.sample(NETFLIX, 1)
        self.assertEqual(len(self.app.store.data['outbox']), 1)
        self.sample('com.sonyliv')
        for activity in ('.SplashActivity', '.HomeActivity'):
            self.sample(foreground('mResumedActivity: ActivityRecord{u0 com.sonyliv/' + activity + '}'))
        self.assertEqual(len(self.app.store.data['app_history']), 2)
        self.sample(HOME)
        self.assertIn('Returned to Home Screen', self.app.store.data['outbox'][-1]['text'])
        self.sample('org.unknown.player')
        self.assertIn('Application Opened: org.unknown.player', self.app.store.data['outbox'][-1]['text'])
        self.sample(NETFLIX, tv=self.config.tvs[0])
        self.assertEqual(len(self.app.store.data['app_history']), 4)
        self.assertEqual(self.app.store.data['apps']['main']['confirmed'], NETFLIX)
        self.assertEqual(self.app.store.data['apps']['second']['confirmed'], 'org.unknown.player')

    def test_disconnect_restart_and_standby_do_not_create_false_changes(self):
        self.sample(YOUTUBE)
        self.sample(NETFLIX, 1)
        self.app.monitor(self.tv)
        self.adb.power.return_value = 'OFF'
        self.app.monitor(self.tv)
        self.assertEqual(self.app.store.data['apps'][self.tv.id]['count'], 0)
        with patch('tv_controller.ADB') as factory:
            self.app.activity_tick()
            factory.assert_not_called()
        self.sample(None, 4)
        self.sample(YOUTUBE)
        self.assertEqual(len(self.app.store.data['outbox']), 0)
        restarted = App(self.config, Store(self.path), self.adb, self.telegram)
        self.addCleanup(restarted.close)
        restarted.record_activity(self.tv, YOUTUBE)
        restarted.record_activity(self.tv, YOUTUBE)
        self.assertEqual(restarted.store.data['app_history'], [])
        restarted.record_activity(self.tv, NETFLIX)
        restarted.record_activity(self.tv, NETFLIX)
        self.assertEqual(len(restarted.store.data['app_history']), 1)

    def test_stale_background_result_is_discarded(self):
        self.sample(YOUTUBE)
        self.app.readings[self.tv.id] = ('device', 'ON')
        future = Future()
        self.app.app_pending[self.tv.id] = (future, 0)
        self.adb.power.return_value = 'OFF'
        self.app.monitor(self.tv)
        future.set_result(NETFLIX)
        self.app.activity_tick()
        self.assertEqual(self.app.store.data['apps'][self.tv.id]['confirmed'], YOUTUBE)

    def test_history_delivery_lifecycle_and_bound(self):
        self.sample(YOUTUBE); self.sample(NETFLIX)
        event = self.app.store.data['app_history'][0]
        self.assertEqual(event['notification_status'], 'pending')
        self.telegram.request.side_effect = TelegramError('offline')
        self.app.flush()
        self.assertEqual(event['notification_status'], 'retrying')
        self.app.store.data['outbox'][0]['next'] = 0
        self.telegram.request.side_effect = None
        self.app.flush()
        self.assertEqual(Store(self.path).data['app_history'][0]['notification_status'], 'sent')
        self.sample(YOUTUBE)
        self.app.store.data['outbox'][0]['attempts'] = 12
        self.app.flush()
        self.assertEqual(self.app.store.data['app_history'][-1]['notification_status'], 'expired')
        self.app.config.apps.history_limit = 10
        self.app.config.apps.telegram = False
        for index in range(15):
            self.sample('org.test.app' + str(index))
        self.assertEqual(len(self.app.store.data['app_history']), 10)
        self.assertEqual(self.app.store.data['app_history'][-1]['notification_status'], 'disabled')

    def test_timezone_and_old_state_migration(self):
        self.path.write_text(json.dumps({'tv': 'ON', 'offset': 44, 'custom_field': 'keep'}))
        store = Store(self.path)
        self.assertEqual(store.data['custom_field'], 'keep')
        state = {'confirmed': HOME}
        stamp = datetime(2026, 10, 27, 13, 45, 36, tzinfo=ZoneInfo('Asia/Dhaka'))
        observe_app(state, YOUTUBE, self.tv, self.config.apps, stamp)
        event = observe_app(state, YOUTUBE, self.tv, self.config.apps, stamp)
        text = notification(event, self.config.apps)
        self.assertIn('13:45:36 (Asia/Dhaka)', text)
        self.assertTrue(event['timestamp'].endswith('+06:00'))

    def test_optional_monitoring_and_nonblocking_schedule(self):
        with patch('tv_controller.ADB') as factory:
            factory.return_value.foreground.return_value = YOUTUBE
            self.app.monitor(self.tv)
            self.app.activity_tick()
            future = self.app.app_pending[self.tv.id][0]
            future.result(timeout=1)
            self.app.activity_tick()
            self.assertEqual(self.app.store.data['apps'][self.tv.id]['count'], 1)
            self.app.activity_tick()
            factory.return_value.foreground.assert_called_once()
        self.config.apps.enabled = False
        self.app.app_due.clear()
        with patch('tv_controller.ADB') as factory:
            self.app.activity_tick()
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
