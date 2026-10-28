#!/usr/bin/env python3
"""Trusted-LAN remote, independent from the Telegram monitoring process."""
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import threading
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from tv_controller import ADB, Config, KEYS
from live_view import LiveManager, LiveSettings


class Remote:
    def __init__(self, tvs, factory=ADB):
        self.tvs = {tv.id: tv for tv in tvs}
        self.adbs = {tv.id: factory() for tv in tvs}
        self.locks = {tv.id: threading.Lock() for tv in tvs}

    def execute(self, tv_id, action):
        if tv_id not in self.tvs or action not in set(KEYS) | {'select', 'status'}:
            return 400, {'error': 'Unknown device or command.'}
        lock = self.locks[tv_id]
        if not lock.acquire(blocking=False):
            return 409, {'error': 'Device is busy. Try again after the current command.'}
        try:
            tv, adb = self.tvs[tv_id], self.adbs[tv_id]
            if action in ('select', 'status'):
                addr = adb.connect(tv)
                if not addr:
                    return 503, {'error': f'{tv.name} is not online (ADB unreachable).', 'offline': True}
                reading = adb.power(addr)
                if reading is None:
                    adb.cached.pop(tv_id, None)
                    return 503, {'error': f'{tv.name} stopped responding.', 'offline': True}
                return 200, {'name': tv.name, 'status': reading}
            addr = adb.cached.get(tv_id)
            if not addr:
                return 409, {'error': 'Select the device again to reconnect.', 'offline': True}
            # No discovery, preflight echo, Telegram roundtrip or automatic key retry.
            result = adb.run('-s', addr, 'shell', 'input', 'keyevent', KEYS[action])
            if result is None:
                adb.cached.pop(tv_id, None)
                return 503, {'error': 'Command was not confirmed. Select the device again; it may have executed.', 'offline': True}
            return 200, {'message': f'{tv.name}: command sent.'}
        finally:
            lock.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, remote, config=None, live_settings=None):
        self.remote = remote
        self.config = config
        self.live = LiveManager(remote.tvs.values(), live_settings or LiveSettings())
        self.csrf = secrets.token_urlsafe(32)
        self.web = Path(__file__).resolve().parent / 'web'
        hostname = socket.gethostname().lower()
        self.names = {'localhost', hostname, hostname.split('.')[0], hostname.split('.')[0] + '.local'}
        super().__init__(address, Handler)

    def server_close(self):
        self.live.close()
        super().server_close()

    def activity(self, tv_id):
        result = {'current': None, 'available': False, 'last_seen': None, 'history': []}
        if not self.config:
            return result
        try:
            state = json.loads(self.config.state_file.read_text())
            app = state.get('apps', {}).get(tv_id, {})
            package = app.get('confirmed')
            recent = False
            if app.get('last_seen'):
                recent = (datetime.now(timezone.utc) - datetime.fromisoformat(app['last_seen'])).total_seconds() < max(self.config.apps.interval, self.config.interval) * 3 + 30
            result.update(current={'package': package, 'name': self.config.apps.names.get(package, package)} if package else None,
                          available=bool(app.get('available')) and recent and self.config.apps.enabled and self.remote.tvs[tv_id].app_monitor, last_seen=app.get('last_seen'),
                          history=[event for event in state.get('app_history', []) if event.get('device_id') == tv_id][-50:][::-1])
        except (OSError, ValueError, AttributeError, TypeError):
            result['error'] = 'App observations are not available yet.'
        return result


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Avoid storing request contents or tokens.

    def valid_host(self):
        try:
            host = urlsplit('http://' + self.headers.get('Host', '')).hostname
            if host in self.server.names:
                return True
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback
        except (ValueError, TypeError):
            return False

    def respond(self, code, data, kind='application/json', headers=None):
        body = json.dumps(data).encode() if kind == 'application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def live_auth(self):
        # Live View is intentionally LAN-only without a user/password prompt.
        # Host validation plus the existing per-process CSRF token still applies
        # to mutating requests; do not expose this service beyond the LAN.
        return True

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {'error': 'Use the Pi LAN IP or hostname.'})
        url = urlsplit(self.path)
        parts = url.path.strip('/').split('/')
        if len(parts) == 3 and parts[:2] == ['api', 'activity']:
            if parts[2] not in self.server.remote.tvs:
                return self.respond(404, {'error': 'Unknown device.'})
            return self.respond(200, self.server.activity(parts[2]))
        if (len(parts) == 2 and parts[0] == 'live') or parts[:2] == ['api', 'live']:
            if not self.live_auth():
                return
            tv_id = parts[1] if parts[0] == 'live' else (parts[2] if len(parts) >= 3 else '')
            tv = self.server.remote.tvs.get(tv_id)
            if not tv:
                return self.respond(404, {'error': 'Unknown device.'})
            if parts[0] == 'live':
                return self.respond(200, (self.server.web / 'live.html').read_bytes(), 'text/html; charset=utf-8')
            if len(parts) == 4 and parts[3] == 'info':
                return self.respond(200, {'device': {'id': tv.id, 'name': tv.name}, 'csrf': self.server.csrf,
                                         'enabled': self.server.live.settings.enabled and tv.live_view,
                                         'message': self.server.live.settings.availability() if tv.live_view else
                                         'Live View is disabled or unsupported for this device. Monitoring and controls still work.',
                                         'activity': self.server.activity(tv_id)})
            if len(parts) == 4 and parts[3] == 'media':
                query = parse_qs(url.query)
                try:
                    after = int(query.get('after', ['0'])[0])
                    if after < 0:
                        raise ValueError
                except ValueError:
                    return self.respond(400, {'error': 'Invalid segment sequence.'})
                session = self.server.live.get(tv_id, query.get('viewer', [''])[0])
                if not session:
                    return self.respond(410, {'error': 'Viewer expired. Reconnect to start again.'})
                code, result = session.segment(after)
                if code == 200:
                    sequence, codec, data = result
                    return self.respond(200, data, 'video/mp4', {'X-Sequence': sequence,
                                                                 'X-Video-Codec': codec or '',
                                                                 'X-Stream-Bytes': len(data)})
                if code == 204:
                    return self.respond(204, b'', 'video/mp4')
                return self.respond(code, result)
            return self.respond(404, {'error': 'Not found.'})
        if url.path == '/api/devices':
            return self.respond(200, {'csrf': self.server.csrf, 'devices': [
                {'id': tv.id, 'name': tv.name, 'ip': tv.ip} for tv in self.server.remote.tvs.values()]})
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/live.js': ('live.js', 'text/javascript; charset=utf-8'),
                 '/live.css': ('live.css', 'text/css; charset=utf-8')}
        if url.path not in files:
            return self.respond(404, {'error': 'Not found.'})
        name, kind = files[url.path]
        return self.respond(200, (self.server.web / name).read_bytes(), kind)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or
                (origin is not None and origin != 'http://' + self.headers.get('Host', '')) or
                not secrets.compare_digest(self.headers.get('X-Remote-Token', ''), self.server.csrf)):
            return self.respond(403, {'error': 'Reload the remote page before sending commands.'})
        parts = urlsplit(self.path).path.strip('/').split('/')
        live = len(parts) == 4 and parts[:2] == ['api', 'live']
        if live and not self.live_auth():
            return
        if not live and self.path != '/api/command':
            return self.respond(404, {'error': 'Not found.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or (not live and not all(isinstance(body.get(k), str) for k in ('tv', 'action'))):
                raise ValueError
        except (ValueError, OSError):
            return self.respond(400, {'error': 'Invalid command.'})
        if live:
            tv_id, action = parts[2:]
            if action == 'start':
                code, result = self.server.live.start(tv_id)
            elif action == 'stop' and isinstance(body.get('viewer'), str):
                code, result = self.server.live.stop(tv_id, body['viewer'])
            else:
                return self.respond(400, {'error': 'Unknown live action or viewer.'})
        else:
            code, result = self.server.remote.execute(body['tv'], body['action'])
        return self.respond(code, result)


def main():
    try:
        config = Config.load()
        host = os.environ.get('WEB_HOST', '0.0.0.0')
        ipaddress.IPv4Address(host)
        port = int(os.environ.get('WEB_PORT', '8080'))
        if not 1 <= port <= 65535:
            raise ValueError
        server = Server((host, port), Remote(config.tvs), config, LiveSettings.load(os.environ))
    except (ValueError, OSError):
        logging.error('Cannot start LAN remote; check TV settings, WEB_HOST, WEB_PORT and port availability.')
        return 1
    logging.warning('LAN remote listening on %s:%d; trusted network only', host, port)
    def shutdown(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
