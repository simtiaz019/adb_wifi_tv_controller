#!/usr/bin/env python3
"""Trusted-LAN remote, independent from the Telegram monitoring process."""
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tv_controller import ADB, Config, KEYS


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

    def __init__(self, address, remote):
        self.remote = remote
        self.csrf = secrets.token_urlsafe(32)
        self.web = Path(__file__).resolve().parent / 'web'
        hostname = socket.gethostname().lower()
        self.names = {'localhost', hostname, hostname.split('.')[0], hostname.split('.')[0] + '.local'}
        super().__init__(address, Handler)


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

    def respond(self, code, data, kind='application/json'):
        body = json.dumps(data).encode() if kind == 'application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {'error': 'Use the Pi LAN IP or hostname.'})
        if self.path == '/api/devices':
            return self.respond(200, {'csrf': self.server.csrf, 'devices': [
                {'id': tv.id, 'name': tv.name, 'ip': tv.ip} for tv in self.server.remote.tvs.values()]})
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path not in files:
            return self.respond(404, {'error': 'Not found.'})
        name, kind = files[self.path]
        return self.respond(200, (self.server.web / name).read_bytes(), kind)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or
                (origin is not None and origin != 'http://' + self.headers.get('Host', '')) or
                not secrets.compare_digest(self.headers.get('X-Remote-Token', ''), self.server.csrf)):
            return self.respond(403, {'error': 'Reload the remote page before sending commands.'})
        if self.path != '/api/command':
            return self.respond(404, {'error': 'Not found.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or not all(isinstance(body.get(k), str) for k in ('tv', 'action')):
                raise ValueError
        except (ValueError, OSError):
            return self.respond(400, {'error': 'Invalid command.'})
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
        server = Server((host, port), Remote(config.tvs))
    except (ValueError, OSError):
        logging.error('Cannot start LAN remote; check TV settings, WEB_HOST, WEB_PORT and port availability.')
        return 1
    logging.warning('LAN remote listening on %s:%d; trusted network only', host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
