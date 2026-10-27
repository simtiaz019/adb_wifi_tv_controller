import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from haier_tv_bot import ADB, App, Config, Store, Telegram, TelegramError, TV, observe

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(TELEGRAM_BOT_TOKEN='123:' + 'x' * 30, TELEGRAM_CHAT_ID='123',
           TV_NAME='Living Room TV', TV_IP='192.168.1.250', TV_ADB_PORTS='40123,5555',
           TV2_NAME='Mi Box 3', TV2_IP='192.168.1.149', TV2_ADB_PORTS='5555')


class MonitorTests(unittest.TestCase):
    def test_baseline_transition_and_no_repeat(self):
        s = {}
        self.assertEqual(observe(s, 'ON', 'Name'), [])
        self.assertNotIn('confirmed', s)
        self.assertEqual(observe(s, 'ON', 'Name'), [])
        self.assertEqual(s['confirmed'], 'ON')
        self.assertEqual(observe(s, 'OFF', 'Name'), [])
        self.assertEqual(observe(s, 'OFF', 'Name'), ['Name entered standby (OFF).'])
        self.assertEqual(observe(s, 'OFF', 'Name'), [])

    def test_failures_recovery_same_and_changed(self):
        for recovery in ('ON', 'OFF'):
            s = {}
            observe(s, 'ON', 'N'); observe(s, 'ON', 'N')
            self.assertEqual(observe(s, None, 'N'), [])
            self.assertEqual(observe(s, None, 'N'), [])
            self.assertIn('unreachable', observe(s, None, 'N')[0])
            self.assertEqual(observe(s, None, 'N'), [])
            self.assertEqual(s['status'], 'UNREACHABLE')
            self.assertEqual(observe(s, recovery, 'N'), ['N connection restored.'])
            self.assertEqual(len(observe(s, recovery, 'N')), int(recovery == 'OFF'))

    def test_unknown_and_intermittent(self):
        s = {}
        for _ in range(4):
            self.assertEqual(observe(s, None, 'N'), [])
        observe(s, 'ON', 'N'); observe(s, None, 'N'); observe(s, 'ON', 'N')
        self.assertNotIn('confirmed', s)
        for _ in range(4):
            self.assertEqual(observe(s, 'UNKNOWN', 'N'), [])
        self.assertFalse(s['unreachable'])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.config = Config.load(dict(ENV, STATE_FILE=str(self.path)))
        self.adb = Mock()
        self.adb.connect.return_value = '192.168.1.149:5555'
        self.adb.power.return_value = 'ON'
        self.adb.run.return_value = ''
        self.telegram = Mock()
        self.app = App(self.config, Store(self.path), self.adb, self.telegram)

    def msg(self, text):
        return {'update_id': 1, 'message': {'chat': {'id': 123, 'type': 'private'},
                                          'from': {'id': 123}, 'text': text}}

    def test_commands_and_second_tv(self):
        for action in ('menu', 'status', 'on', 'off', 'volup', 'voldown', 'mute'):
            self.app.handle(self.msg('/' + action + '@mybot second'))
            self.assertIn('Mi Box 3', self.app.store.data['outbox'][-1]['text'])
        self.assertEqual(self.adb.connect.call_args.args[0].ip, '192.168.1.149')
        self.app.handle(self.msg('status main'))
        self.assertIn('Living Room TV', self.app.store.data['outbox'][-1]['text'])

    def test_navigation_key_mapping_and_target(self):
        expected = {'navup': 'KEYCODE_DPAD_UP', 'navdown': 'KEYCODE_DPAD_DOWN',
                    'left': 'KEYCODE_DPAD_LEFT', 'right': 'KEYCODE_DPAD_RIGHT',
                    'ok': 'KEYCODE_DPAD_CENTER', 'home': 'KEYCODE_HOME',
                    'back': 'KEYCODE_BACK', 'playpause': 'KEYCODE_MEDIA_PLAY_PAUSE',
                    'on': 'KEYCODE_WAKEUP', 'off': 'KEYCODE_SLEEP',
                    'volup': 'KEYCODE_VOLUME_UP', 'voldown': 'KEYCODE_VOLUME_DOWN',
                    'mute': 'KEYCODE_VOLUME_MUTE'}
        for target in ('main', 'second'):
            for action, key in expected.items():
                self.app.handle(self.msg(f'/{action} {target}'))
                self.assertEqual(self.adb.connect.call_args.args[0].id, target)
                self.adb.run.assert_called_with('-s', '192.168.1.149:5555',
                                                'shell', 'input', 'keyevent', key)
        buttons = [button for row in self.app.keyboard(self.config.tvs[1])['inline_keyboard'] for button in row]
        for action in expected:
            button = next(b for b in buttons if b['callback_data'] == f'tv:second:{action}')
            self.app.handle({'callback_query': {'id': 'button', 'from': {'id': 123},
                             'message': self.msg('')['message'], 'data': button['callback_data']}})
            self.adb.run.assert_called_with('-s', '192.168.1.149:5555',
                                            'shell', 'input', 'keyevent', expected[action])
        self.app.handle(self.msg('/up second'))
        self.assertEqual(self.adb.run.call_args.args[-1], 'KEYCODE_VOLUME_UP')

    def test_picker_selection_controls_and_restart(self):
        for command in ('/home', '/menu', '/start', '/home@mybot', '/on'):
            self.app.handle(self.msg(command))
            item = self.app.store.data['outbox'][-1]
            self.assertEqual(item['text'], 'Choose a TV:')
            self.assertEqual([row[0]['text'] for row in item['keyboard']['inline_keyboard']],
                             ['Living Room TV', 'Mi Box 3'])
        self.adb.connect.assert_not_called()
        def click(data, owner=123):
            self.app.handle({'callback_query': {'id': 'pick', 'from': {'id': owner},
                             'message': self.msg('')['message'], 'data': data}})
        click('select:second', owner=999)
        self.assertNotIn('selected_tv', self.app.store.data)
        click('select:invalid')
        self.assertNotIn('selected_tv', self.app.store.data)
        click('select:second')
        panel = self.app.store.data['outbox'][-1]
        self.assertEqual(panel['text'], 'Mi Box 3 — controls')
        buttons = [b for row in panel['keyboard']['inline_keyboard'] for b in row]
        self.assertTrue(all(b['callback_data'].startswith('tv:second:') or
                            b['callback_data'] == 'choose_tv' for b in buttons))
        self.adb.connect.assert_not_called()
        self.app.store.save()
        self.app = App(self.config, Store(self.path), self.adb, self.telegram)
        self.app.handle(self.msg('/volup'))
        self.assertEqual(self.adb.connect.call_args.args[0].id, 'second')
        click('tv:second:home')
        self.assertEqual(self.adb.run.call_args.args[-1], 'KEYCODE_HOME')
        click('choose_tv')
        self.assertEqual(self.app.store.data['outbox'][-1]['text'], 'Choose a TV:')
        click('select:main')
        self.app.handle(self.msg('/off'))
        self.assertEqual(self.adb.connect.call_args.args[0].id, 'main')

    def test_authorization_callbacks(self):
        m = self.msg('/on')['message']
        callback = {'callback_query': {'id': 'a', 'from': {'id': 123}, 'message': m, 'data': 'tv:second:on'}}
        self.app.handle(callback)
        self.telegram.request.assert_called_with('answerCallbackQuery', {'callback_query_id': 'a'})
        self.adb.run.assert_called_once()
        self.adb.reset_mock()
        m['chat']['type'] = 'group'
        self.app.handle(callback)
        self.app.handle({'message': m})
        self.adb.connect.assert_not_called()
        m['chat']['type'] = 'private'
        m['chat']['id'] = 999
        self.app.handle(callback)
        self.adb.connect.assert_not_called()

    def test_legacy_restart_and_independent_baseline(self):
        self.path.write_text(json.dumps({'tv': 'ON', 'connectivity': 'DOWN', 'offset': 42}))
        store = Store(self.path)
        app = App(self.config, store, self.adb, self.telegram)
        app.monitor(self.config.tvs[0]); app.monitor(self.config.tvs[0])
        app.monitor(self.config.tvs[1]); app.monitor(self.config.tvs[1])
        self.assertEqual(len(store.data['outbox']), 1)
        self.assertEqual(store.data['offset'], 42)
        restarted = App(self.config, Store(self.path), self.adb, self.telegram)
        restarted.monitor(self.config.tvs[0])
        self.assertEqual(len(restarted.store.data['outbox']), 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_poll_retry_outbox_restart(self):
        self.telegram.request.side_effect = TelegramError('Unavailable')
        self.app.poll()
        self.assertEqual(self.app.store.data['offset'], 0)
        self.app.next_poll = 0
        self.telegram.request.side_effect = None
        self.telegram.request.return_value = [self.msg('/volup second')]
        self.app.poll()
        self.assertEqual(Store(self.path).data['offset'], 2)
        self.telegram.request.side_effect = TelegramError('Unavailable')
        self.app.flush()
        item = Store(self.path).data['outbox'][0]
        self.assertEqual(item['attempts'], 1)
        self.app.store.data['outbox'][0]['next'] = 0
        self.telegram.request.side_effect = None
        self.app.flush()
        self.assertEqual(Store(self.path).data['outbox'], [])

    def test_queue_bound_and_expiry(self):
        for n in range(120):
            self.app.store.enqueue(str(n))
        self.assertEqual(len(self.app.store.data['outbox']), 100)
        self.app.store.data['outbox'][0]['attempts'] = 12
        self.app.flush()
        self.telegram.request.assert_not_called()

    def test_invalid_state_schema(self):
        for data in ({'tvs': {'main': []}}, {'outbox': [{}]}, {'offset': -1}):
            self.path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                Store(self.path)

    def test_corrupt_state_preserved(self):
        self.path.write_text('broken')
        with self.assertRaises(ValueError):
            Store(self.path)
        self.assertEqual(self.path.read_text(), 'broken')


class ConfigADBTests(unittest.TestCase):
    def test_invalid_config(self):
        for key, value in [('TV_IP', 'bad'), ('TV_ADB_PORTS', '0'), ('TV_ADB_PORTS', '65536'),
                           ('TV_ADB_PORTS', '5555,'), ('POLL_SECONDS', 'nan'), ('POLL_SECONDS', '4'),
                           ('TELEGRAM_CHAT_ID', '-123'), ('TELEGRAM_BOT_TOKEN', 'secret')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as error:
                Config.load(dict(ENV, **{key: value}))
            self.assertNotIn('secret', str(error.exception))

    def test_redaction(self):
        with patch('urllib.request.urlopen', side_effect=OSError('secret URL')):
            with self.assertRaises(TelegramError) as error:
                Telegram(ENV['TELEGRAM_BOT_TOKEN']).request('getUpdates')
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn(ENV['TELEGRAM_BOT_TOKEN'], str(error.exception))

    def test_discovery_order_and_pairing_excluded(self):
        adb = ADB()
        tv = Config.load(ENV).tvs[0]
        adb.run = Mock(side_effect=['List of devices attached\n192.168.1.250:4444 device\n192.168.1.250:5556 unauthorized',
                                   'a _adb-tls-pairing._tcp 192.168.1.250:1234\n'
                                   'b _adb-tls-connect._tcp. 192.168.1.250:9999\n'
                                   'c _adb-tls-connect._tcp 192.168.1.250:99999\n'
                                   'd _adb-tls-connect._tcp 192.168.1.149:8888'])
        self.assertEqual(adb.candidates(tv), ['192.168.1.250:4444', '192.168.1.250:40123',
                                            '192.168.1.250:5555', '192.168.1.250:9999'])

    def test_cache_and_timeout(self):
        adb = ADB(); tv = Config.load(ENV).tvs[0]
        adb.cached[tv.id] = tv.endpoint(5555)
        adb.reachable = Mock(return_value=True)
        adb.candidates = Mock()
        self.assertEqual(adb.connect(tv), tv.endpoint(5555))
        adb.candidates.assert_not_called()
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('adb', 6)):
            self.assertIsNone(adb.run('devices'))

    def test_installer_preserves_config_and_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['bash', str(ROOT / 'install.sh'), '--root', temp], check=True, capture_output=True)
            config = root / 'etc/haier-tv-monitor.env'
            self.assertEqual(config.read_text().count('TELEGRAM_BOT_TOKEN='), 1)
            self.assertIn('TV2_IP=192.168.1.149', config.read_text())
            config.write_text('TV_ADB_PORTS=12345\nSECRET=keep\n')
            state = root / 'var/lib/haier-tv-monitor/state.json'
            state.write_text('{"tv":"OFF","offset":123}')
            subprocess.run(['bash', str(ROOT / 'install.sh'), '--root', temp], check=True, capture_output=True)
            self.assertEqual(config.read_text(), 'TV_ADB_PORTS=12345\nSECRET=keep\n')
            self.assertEqual(state.read_text(), '{"tv":"OFF","offset":123}')
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
