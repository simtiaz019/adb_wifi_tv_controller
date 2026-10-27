import base64
from dataclasses import replace
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from live_view import Capture, LiveManager, LiveSettings, boxes, ffmpeg_command
from tv_controller import Config
from tv_web import Remote, Server
from test_controller import ENV


class FakeCapture(Capture):
    def start(self):
        self.phase = 'receiving'

    def stop(self):
        super().stop()
        self.done.set()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tvs = [replace(tv, live_view=True) for tv in Config.load(ENV).tvs]
        self.settings = LiveSettings(enabled=True, password='private-test-password')
        self.settings.availability = Mock(return_value=None)
        self.factory = Mock(side_effect=FakeCapture)
        self.manager = LiveManager(self.tvs, self.settings, self.factory)
        self.addCleanup(self.manager.close)

    def test_two_viewers_share_capture_and_last_stop_releases(self):
        _, first = self.manager.start('second')
        _, second = self.manager.start('second')
        self.assertEqual(self.factory.call_count, 1)
        session = self.manager.sessions['second']
        self.manager.stop('second', first['viewer'])
        self.assertFalse(session.stopped.is_set())
        self.manager.stop('second', second['viewer'])
        self.assertTrue(session.done.is_set())
        self.assertNotIn('second', self.manager.sessions)

    def test_limits_invalid_ids_disabled_device_and_idle_cleanup(self):
        self.assertEqual(self.manager.start('arbitrary-host')[0], 404)
        _, first = self.manager.start('main')
        self.assertEqual(self.manager.start('second')[0], 409)
        self.manager.viewers[first['viewer']] = ('main', time.monotonic() - 61)
        with self.manager.lock:
            self.manager.reap()
        self.assertNotIn('main', self.manager.sessions)
        self.assertEqual(self.manager.start('second')[0], 200)
        self.settings.max_viewers = 1
        self.assertEqual(self.manager.start('second')[0], 429)
        self.manager.tvs['main'] = replace(self.tvs[0], live_view=False)
        self.assertEqual(self.manager.start('main')[0], 409)

    def test_viewer_cannot_read_other_device_and_failure_is_explicit(self):
        _, data = self.manager.start('second')
        self.assertIsNone(self.manager.get('main', data['viewer']))
        session = self.manager.get('second', data['viewer'])
        session.error = 'Capture unavailable'
        self.assertEqual(session.segment(0), (503, {'error': 'Capture unavailable'}))

    def test_fragment_history_bounded_and_first_response_contains_init(self):
        capture = FakeCapture(self.tvs[0], self.settings)
        capture.publish(b'ftyp', b'ftyp')
        capture.publish(b'moov', b'moov-avcC\x01\x42\xe0\x1f-tail')
        for _ in range(10):
            capture.publish(b'mdat', b'fragment')
        self.assertEqual(len(capture.fragments), 4)
        code, (seq, codec, data) = capture.segment(0)
        self.assertEqual((code, seq, codec), (200, 10, 'avc1.42e01f'))
        self.assertTrue(data.startswith(b'ftyp'))

    def test_capture_worker_cleans_processes_forward_and_temporary_file(self):
        capture = Capture(self.tvs[1], self.settings)
        server = Mock(pid=12001, stdin=None, stdout=None)
        server.poll.return_value = None
        mux = Mock(pid=12002, stdin=io.BytesIO(), stdout=io.BytesIO())
        mux.poll.return_value = None
        sock = Mock()
        sock.recv.side_effect = [b'x', b'']
        result = Mock(returncode=0, stdout=b'12345')
        fragments = [(b'ftyp', b'ftyp'), (b'moov', b'avcC\x01\x42\xe0\x1f'),
                     (b'moof', b'moof'), (b'mdat', b'mdat')]
        with patch('tv_controller.ADB') as adb, patch('live_view.subprocess.run', return_value=result) as run, \
                patch('live_view.subprocess.Popen', side_effect=[server, mux]) as popen, \
                patch('live_view.socket.create_connection', return_value=sock), \
                patch('live_view.os.killpg') as kill, patch('live_view.boxes', return_value=iter(fragments)):
            adb.return_value.connect.return_value = '192.168.1.149:5555'
            capture.run()
        self.assertTrue(capture.done.is_set())
        self.assertEqual(capture.sequence, 1)
        self.assertEqual(kill.call_count, 2)
        sock.close.assert_called_once()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any('forward' in cmd and '--remove' in cmd for cmd in commands))
        self.assertTrue(any('rm' in cmd and capture.device_path in cmd for cmd in commands))
        scrcpy_command = popen.call_args_list[0].args[0]
        self.assertIn('192.168.1.149:5555', scrcpy_command)
        self.assertIn('raw_stream=true', scrcpy_command)
        self.assertIn('power_on=false', scrcpy_command)
        self.assertIn('copy', popen.call_args_list[1].args[0])

    def test_missing_optional_dependencies_do_not_enable_capture(self):
        self.assertIn('disabled', LiveSettings().availability())
        self.assertIn('Configure', LiveSettings(enabled=True).availability())
        self.assertIn('missing', LiveSettings(enabled=True, password='private-test-password', ffmpeg='/missing/ffmpeg').availability())
        with self.assertRaises(ValueError):
            LiveSettings.load({'LIVE_VIEW_MAX_FPS': '1000'})


class LiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config.load(dict(ENV, STATE_FILE=str(Path(self.temp.name) / 'state.json'), TV2_LIVE_VIEW_ENABLED='true'))
        self.settings = LiveSettings(enabled=True, password='private-test-password')
        self.settings.availability = Mock(return_value=None)
        self.server = Server(('127.0.0.1', 0), Remote(self.config.tvs), self.config, self.settings)
        self.server.live.factory = FakeCapture
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, body=None, auth=True, csrf=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'viewer:private-test-password').decode()
        if csrf:
            headers['X-Remote-Token'] = self.server.csrf
        connection.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def test_live_auth_page_device_and_csrf(self):
        self.assertEqual(self.request('/live/second', auth=False)[0], 401)
        self.assertEqual(self.request('/api/live/second/info', auth=False)[0], 401)
        self.assertEqual(self.request('/api/live/second/start', {}, auth=False)[0], 401)
        self.assertEqual(self.request('/api/live/second/start', {}, csrf=False)[0], 403)
        self.assertEqual(self.request('/live/second')[0], 200)
        self.assertEqual(self.request('/live/not-configured')[0], 404)
        data = json.loads(self.request('/api/live/main/info')[1])
        self.assertFalse(data['enabled'])
        self.assertIn('unsupported', data['message'])
        self.assertEqual(self.request('/api/live/main/start', {})[0], 409)
        # Existing LAN routes remain available under their existing trust model.
        self.assertEqual(self.request('/api/devices', auth=False)[0], 200)

    def test_media_requires_auth_and_lease_and_stop_releases(self):
        info = json.loads(self.request('/api/live/second/start', {})[1])
        viewer = info['viewer']
        self.assertEqual(self.request('/api/live/second/media?viewer=' + viewer, auth=False)[0], 401)
        self.assertEqual(self.request('/api/live/second/media?viewer=invalid')[0], 410)
        session = self.server.live.sessions['second']
        session.init, session.codec = b'init', 'avc1.42e01f'
        session.publish(b'mdat', b'video-fragment')
        code, body, headers = self.request('/api/live/second/media?viewer=' + viewer)
        self.assertEqual((code, body), (200, b'initvideo-fragment'))
        self.assertEqual(headers['X-Video-Codec'], 'avc1.42e01f')
        self.assertEqual(self.request('/api/live/second/stop', {'viewer': viewer})[0], 200)
        self.assertTrue(session.done.is_set())

    def test_activity_api_is_read_only_and_excludes_outbox_and_credentials(self):
        self.config.state_file.write_text(json.dumps({'apps': {'second': {'confirmed': 'com.netflix.ninja', 'available': False}},
                                                     'app_history': [{'device_id': 'second', 'name': 'Netflix'}],
                                                     'outbox': [{'text': 'PRIVATE'}], 'token': self.config.token}))
        code, body, _ = self.request('/api/activity/second')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['current']['name'], 'Netflix')
        self.assertNotIn(b'PRIVATE', body)
        self.assertNotIn(self.config.token.encode(), body)
        self.assertEqual(self.request('/api/activity/unknown')[0], 404)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'optional FFmpeg integration tools not installed')
class MuxIntegrationTests(unittest.TestCase):
    def test_real_stream_copy_produces_decodable_fragmented_mp4(self):
        # Synthetic test pattern only, never a claim about an actual TV/browser.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'test.h264'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=320x180:rate=15', '-t', '3', '-c:v', 'libx264', '-preset', 'ultrafast',
                            '-tune', 'zerolatency', '-g', '15', '-f', 'h264', str(source)], check=True, timeout=20)
            result = subprocess.run(ffmpeg_command(LiveSettings()), input=source.read_bytes(), capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            output = root / 'test.mp4'; output.write_bytes(result.stdout)
            found = []
            with output.open('rb', buffering=0) as stream:
                try:
                    for kind, data in boxes(stream, threading.Event()):
                        found.append(kind)
                except EOFError:
                    pass
            self.assertIn(b'moov', found)
            self.assertGreaterEqual(found.count(b'moof'), 2)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'stream=codec_name,nb_read_frames',
                                    '-of', 'json', str(output)], capture_output=True, text=True, timeout=10, check=True)
            details = json.loads(probe.stdout)['streams'][0]
            self.assertEqual(details['codec_name'], 'h264')
            self.assertGreater(int(details['nb_read_frames']), 20)


if __name__ == '__main__':
    unittest.main()
