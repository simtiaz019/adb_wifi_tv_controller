import http.client
import json
import threading
import time
import unittest
from unittest.mock import Mock

from tv_controller import Config, App, Store
from tv_web import Remote, Server
from test_controller import ENV
from pathlib import Path
import tempfile


class WebTests(unittest.TestCase):
    def setUp(self):
        self.config = Config.load(ENV)
        self.remote = Remote(self.config.tvs)
        for id_, adb in self.remote.adbs.items():
            adb.run = Mock(return_value='')
            def connect(tv, adb=adb):
                adb.cached[tv.id] = tv.endpoint(tv.ports[0])
                return adb.cached[tv.id]
            adb.connect = Mock(side_effect=connect)
            adb.power = Mock(return_value='ON')
        self.server = Server(('127.0.0.1', 0), self.remote)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        request_headers = {'Content-Type': 'application/json', 'X-Remote-Token': self.server.csrf}
        request_headers.update(headers or {})
        connection.request('POST' if body is not None else 'GET', path,
                           json.dumps(body) if body is not None else None, request_headers)
        response = connection.getresponse()
        data = response.read()
        code = response.status
        connection.close()
        return code, data

    def test_page_and_device_list_exclude_secrets(self):
        for path in ('/', '/style.css', '/app.js', '/api/devices'):
            code, body = self.request(path)
            self.assertEqual(code, 200)
            self.assertNotIn(self.config.token.encode(), body)
        data = json.loads(self.request('/api/devices')[1])
        self.assertEqual(len(data['devices']), 2)
        self.assertEqual(self.request('/../config.env.example')[0], 404)

    def test_direct_key_path_no_preflight_or_discovery(self):
        self.assertEqual(self.request('/api/command', {'tv': 'second', 'action': 'select'})[0], 200)
        adb = self.remote.adbs['second']
        adb.connect.reset_mock()
        adb.power.reset_mock()
        for action in ('home', 'ok', 'volup', 'off'):
            self.assertEqual(self.request('/api/command', {'tv': 'second', 'action': action})[0], 200)
        adb.connect.assert_not_called()
        adb.power.assert_not_called()
        self.assertEqual(adb.run.call_count, 4)
        adb.run.assert_called_with('-s', '192.168.1.149:5555', 'shell', 'input', 'keyevent', 'KEYCODE_SLEEP')

    def test_failed_command_is_not_replayed(self):
        self.remote.execute('main', 'select')
        adb = self.remote.adbs['main']
        adb.run.return_value = None
        code, data = self.remote.execute('main', 'mute')
        self.assertEqual(code, 503)
        self.assertTrue(data['offline'])
        self.assertNotIn('main', adb.cached)
        self.assertEqual(self.remote.execute('main', 'mute')[0], 409)
        adb.run.assert_called_once()

    def test_offline_selection(self):
        self.remote.adbs['second'].connect.side_effect = None
        self.remote.adbs['second'].connect.return_value = None
        code, body = self.request('/api/command', {'tv': 'second', 'action': 'select'})
        self.assertEqual(code, 503)
        self.assertTrue(json.loads(body)['offline'])

    def test_validation_and_cross_site_protection(self):
        command = {'tv': 'main', 'action': 'on'}
        for headers in ({'X-Remote-Token': ''}, {'Origin': 'https://attacker.example'}, {'Host': 'attacker.example'}):
            self.assertEqual(self.request('/api/command', command, headers)[0], 403)
        for data in ({'tv': 'nope', 'action': 'on'}, {'tv': 'main', 'action': 'shell'}, [], {'tv': [], 'action': 'on'}):
            self.assertEqual(self.request('/api/command', data)[0], 400)
        self.assertEqual(self.request('/api/devices', headers={'Host': 'attacker.example'})[0], 403)
        self.remote.adbs['main'].run.assert_not_called()

    def test_slow_device_does_not_block_other_remote(self):
        started, release = threading.Event(), threading.Event()
        def slow(tv):
            started.set()
            release.wait(3)
            return None
        self.remote.adbs['main'].connect.side_effect = slow
        worker = threading.Thread(target=lambda: self.remote.execute('main', 'select'))
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(self.remote.execute('main', 'select')[0], 409)
            # Other device completes while the first remains blocked.
            self.assertEqual(self.request('/api/command', {'tv': 'second', 'action': 'select'})[0], 200)
            self.assertEqual(self.request('/api/command', {'tv': 'second', 'action': 'ok'})[0], 200)
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join()

    def test_telegram_controls_remain_enabled_by_default(self):
        env = dict(ENV)
        del env['TELEGRAM_REMOTE_ENABLED']
        config = Config.load(env)
        self.assertTrue(config.telegram_remote)
        with tempfile.TemporaryDirectory() as directory:
            telegram = Mock()
            telegram.request.return_value = []
            app = App(config, Store(Path(directory) / 'state.json'), telegram=telegram)
            app.poll()
            self.assertEqual(telegram.request.call_args.args[0], 'getUpdates')

    def test_additional_devices_use_same_remote(self):
        config = Config.load(dict(ENV, TV3_NAME='Bedroom', TV3_IP='192.168.1.150', TV3_ADB_PORTS='5555'))
        remote = Remote(config.tvs)
        adb = remote.adbs['tv3']
        adb.cached['tv3'] = '192.168.1.150:5555'
        adb.run = Mock(return_value='')
        self.assertEqual(remote.execute('tv3', 'ok')[0], 200)
        adb.run.assert_called_once_with('-s', '192.168.1.150:5555', 'shell', 'input', 'keyevent', 'KEYCODE_DPAD_CENTER')

    def test_notification_only_explicit_opt_out(self):
        env = dict(ENV)
        env['TELEGRAM_REMOTE_ENABLED'] = '0'
        config = Config.load(env)
        with tempfile.TemporaryDirectory() as directory:
            telegram = Mock()
            app = App(config, Store(Path(directory) / 'state.json'), telegram=telegram)
            app.poll()
            telegram.request.assert_not_called()
            app.store.enqueue('Mi Box 3 unreachable')
            app.flush()
            self.assertEqual(telegram.request.call_args.args[0], 'sendMessage')


if __name__ == '__main__':
    unittest.main()
