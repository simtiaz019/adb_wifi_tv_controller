#!/usr/bin/env python3
"""Multi-TV ADB monitor and private Telegram remote; Python standard library only."""
import argparse
import fcntl
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from app_activity import AppSettings, boolean, foreground, observe_app, notification

LOG = logging.getLogger('tv-monitor')
KEYS = {'on': 'KEYCODE_WAKEUP', 'off': 'KEYCODE_SLEEP',
        'volup': 'KEYCODE_VOLUME_UP', 'voldown': 'KEYCODE_VOLUME_DOWN',
        'mute': 'KEYCODE_VOLUME_MUTE',
        'navup': 'KEYCODE_DPAD_UP', 'navdown': 'KEYCODE_DPAD_DOWN',
        'left': 'KEYCODE_DPAD_LEFT', 'right': 'KEYCODE_DPAD_RIGHT',
        'ok': 'KEYCODE_DPAD_CENTER', 'home': 'KEYCODE_HOME',
        'back': 'KEYCODE_BACK', 'playpause': 'KEYCODE_MEDIA_PLAY_PAUSE'}
LABELS = {'on': 'Wake', 'off': 'Sleep', 'navup': '↑', 'navdown': '↓',
          'left': '←', 'right': '→', 'ok': 'OK', 'home': 'Home',
          'back': 'Back', 'playpause': 'Play / pause', 'volup': 'Volume +',
          'voldown': 'Volume −', 'mute': 'Mute', 'status': 'Status'}
REMOTE_ROWS = [('on', 'off'), ('home', 'back'), ('navup',),
               ('left', 'ok', 'right'), ('navdown',),
               ('volup', 'voldown', 'mute'), ('playpause', 'status')]
ACTIONS = set(KEYS) | {'menu', 'status'}


@dataclass(frozen=True)
class TV:
    id: str
    name: str
    ip: str
    ports: tuple
    app_monitor: bool = True
    live_view: bool = False

    def endpoint(self, port):
        return f'[{self.ip}]:{port}' if ':' in self.ip else f'{self.ip}:{port}'


@dataclass
class Config:
    token: str
    chat: str
    interval: float
    state_file: Path
    tvs: list
    telegram_remote: bool = True
    apps: AppSettings = field(default_factory=AppSettings)
    connection_failures: int = 5
    connection_recoveries: int = 3

    @classmethod
    def load(cls, env=None):
        env = os.environ if env is None else env
        def required(key):
            value = env.get(key, '').strip()
            if not value:
                raise ValueError(f'{key} is required')
            return value
        token = required('TELEGRAM_BOT_TOKEN')
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token):
            raise ValueError('TELEGRAM_BOT_TOKEN has invalid format (value redacted)')
        chat = required('TELEGRAM_CHAT_ID')
        if not re.fullmatch(r'[1-9]\d*', chat):
            raise ValueError('TELEGRAM_CHAT_ID must be a positive private-chat ID')
        try:
            interval = float(env.get('POLL_SECONDS', '15'))
            if not math.isfinite(interval) or interval < 5:
                raise ValueError
        except ValueError:
            raise ValueError('POLL_SECONDS must be a finite number >= 5') from None
        tvs = []
        numbers = set()
        for key in env:
            match = re.fullmatch(r'TV([0-9]+)_(NAME|IP|ADB_PORTS|APP_MONITOR_ENABLED|LIVE_VIEW_ENABLED)', key)
            if match:
                number = int(match.group(1))
                if number < 2 or str(number) != match.group(1):
                    raise ValueError('Use TV_ for the first device and TV2_, TV3_, ... for additional devices')
                numbers.add(number)
        devices = [('main', 'TV_')] + [
            ('second' if n == 2 else f'tv{n}', f'TV{n}_') for n in sorted(numbers)]
        for id_, prefix in devices:
            name = required(prefix + 'NAME')
            try:
                ip = str(ipaddress.ip_address(required(prefix + 'IP')))
            except ValueError:
                raise ValueError(prefix + 'IP must be a valid IP address') from None
            try:
                ports = tuple(dict.fromkeys(int(p.strip()) for p in required(prefix + 'ADB_PORTS').split(',')))
                if not ports or any(not 1 <= p <= 65535 for p in ports):
                    raise ValueError
            except ValueError:
                raise ValueError(prefix + 'ADB_PORTS must contain connection ports 1–65535') from None
            tvs.append(TV(id_, name, ip, ports, boolean(env, prefix + 'APP_MONITOR_ENABLED', True),
                          boolean(env, prefix + 'LIVE_VIEW_ENABLED', False)))
        state_file = Path(env.get('STATE_FILE', '/var/lib/tv-hub/state.json'))
        if not state_file.is_absolute():
            raise ValueError('STATE_FILE must be absolute')
        remote = env.get('TELEGRAM_REMOTE_ENABLED', '1')
        if remote not in ('0', '1'):
            raise ValueError('TELEGRAM_REMOTE_ENABLED must be 0 or 1')
        try:
            connection_failures = int(env.get('CONNECTION_FAILURE_CONFIRMATIONS', '5'))
            connection_recoveries = int(env.get('CONNECTION_RECOVERY_CONFIRMATIONS', '3'))
            if not 3 <= connection_failures <= 20 or not 2 <= connection_recoveries <= 10:
                raise ValueError
        except ValueError:
            raise ValueError('CONNECTION_FAILURE_CONFIRMATIONS must be 3–20 and CONNECTION_RECOVERY_CONFIRMATIONS 2–10') from None
        return cls(token, chat, interval, state_file, tvs, remote == '1', AppSettings.load(env),
                   connection_failures, connection_recoveries)


class Store:
    def __init__(self, path):
        self.path = path
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
                if not isinstance(self.data, dict):
                    raise ValueError
                if not isinstance(self.data.get('offset', 0), int) or self.data.get('offset', 0) < 0:
                    raise ValueError
                if not isinstance(self.data.get('tvs', {}), dict) or not isinstance(self.data.get('outbox', []), list):
                    raise ValueError
                for state in self.data.get('tvs', {}).values():
                    if not isinstance(state, dict) or state.get('confirmed') not in (None, 'ON', 'OFF', 'UNKNOWN'):
                        raise ValueError
                for item in self.data.get('outbox', []):
                    if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                        raise ValueError
                    for key in ('attempts', 'created', 'next'):
                        value = item.get(key)
                        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                            raise ValueError
            except (ValueError, OSError):
                raise ValueError('State file is unreadable or invalid; restore a backup before starting') from None
        else:
            self.data = {}
        # Preserve legacy fields and import their meaning into the default TV.
        if 'tvs' not in self.data:
            self.data['tvs'] = {'main': {
                'confirmed': self.data.get('tv'),
                'connected': self.data.get('connectivity') == 'UP' or self.data.get('tv') in ('ON', 'OFF'),
                'unreachable': self.data.get('connectivity') == 'DOWN'}}
        self.data.setdefault('outbox', [])
        self.data.setdefault('offset', 0)
        self.data.setdefault('apps', {})
        self.data.setdefault('app_history', [])
        if not isinstance(self.data['apps'], dict) or not all(isinstance(v, dict) for v in self.data['apps'].values()) or not isinstance(self.data['app_history'], list) or not all(isinstance(event, dict) for event in self.data['app_history']):
            raise ValueError('Invalid application state schema')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.state-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def history_status(self, item, status):
        if not item.get('event_id'):
            return
        for event in self.data['app_history']:
            if event.get('id') == item.get('event_id'):
                event['notification_status'] = status

    def enqueue(self, text, keyboard=None, event_id=None):
        # Bound outage backlog; latest events remain available without replay floods.
        self.data['outbox'].append({'text': text, 'keyboard': keyboard, 'attempts': 0,
                                    'next': 0, 'created': time.time(), 'event_id': event_id})
        if len(self.data['outbox']) > 100:
            self.history_status(self.data['outbox'][0], 'dropped')
            del self.data['outbox'][0]
            LOG.warning('Outbound queue full; oldest notification discarded')


def observe(state, reading, name, failure_threshold=3, recovery_threshold=1):
    """None means transport failure; UNKNOWN means a live but unrecognized power state."""
    alerts = []
    if reading is None:
        state['candidate'], state['count'] = None, 0
        state['failures'] = state.get('failures', 0) + 1
        state['recoveries'] = 0
        if state['failures'] >= failure_threshold and state.get('connected') and not state.get('unreachable'):
            state['unreachable'] = True
            alerts.append(f'{name} unreachable: possible power, Wi-Fi, or ADB interruption.')
        state['status'] = 'UNREACHABLE' if state.get('unreachable') else 'UNKNOWN'
        return alerts
    state['failures'] = 0
    if state.get('unreachable'):
        state['recoveries'] = state.get('recoveries', 0) + 1
        if state['recoveries'] < recovery_threshold:
            state['status'] = 'UNKNOWN'
            return alerts
        alerts.append(f'{name} connection restored.')
    state['connected'], state['unreachable'] = True, False
    state['recoveries'] = 0
    state['status'] = reading
    state['count'] = state.get('count', 0) + 1 if state.get('candidate') == reading else 1
    state['candidate'] = reading
    if state['count'] >= 2 and state.get('confirmed') != reading:
        old = state.get('confirmed')
        state['confirmed'] = reading
        if old in ('ON', 'OFF', 'UNKNOWN'):
            descriptions = {'ON': 'turned ON.', 'OFF': 'entered standby (OFF).',
                            'UNKNOWN': 'power state is UNKNOWN (ADB is online).'}
            alerts.append(f'{name} {descriptions[reading]}')
    return alerts


class ADB:
    def __init__(self):
        self.cached = {}

    def run(self, *args):
        try:
            result = subprocess.run(['adb', *args], capture_output=True, text=True, timeout=6)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def reachable(self, addr):
        return self.run('-s', addr, 'shell', 'echo', 'TV_ADB_OK') == 'TV_ADB_OK'

    def candidates(self, tv):
        addresses = []
        prefix = tv.endpoint(1).rsplit(':', 1)[0] + ':'
        for line in (self.run('devices') or '').splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == 'device' and parts[0].startswith(prefix):
                addresses.append(parts[0])
        addresses.extend(tv.endpoint(p) for p in tv.ports)
        for line in (self.run('mdns', 'services') or '').splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[1].rstrip('.') != '_adb-tls-connect._tcp':
                continue
            host, sep, port = parts[2].rpartition(':')
            if sep and host.strip('[]') == tv.ip and port.isdigit() and 1 <= int(port) <= 65535:
                addresses.append(tv.endpoint(int(port)))
        return list(dict.fromkeys(addresses))

    def connect(self, tv, cancelled=lambda: False):
        if cancelled():
            return None
        cached = self.cached.pop(tv.id, None)
        if cached and self.reachable(cached):
            self.cached[tv.id] = cached
            return cached
        for addr in self.candidates(tv):
            if cancelled():
                return None
            if not self.reachable(addr):
                if cancelled():
                    return None
                self.run('connect', addr)
                if cancelled():
                    return None
                if not self.reachable(addr):
                    continue
            self.cached[tv.id] = addr
            return addr
        return None

    def foreground(self, addr):
        package = foreground(self.run('-s', addr, 'shell', 'dumpsys', 'activity', 'activities'))
        return package or foreground(self.run('-s', addr, 'shell', 'dumpsys', 'window', 'windows'), window=True)

    def power(self, addr):
        output = self.run('-s', addr, 'shell', 'dumpsys', 'power')
        if output is None:
            return None
        match = re.search(r'\bmWakefulness=(\w+)', output)
        return {'Awake': 'ON', 'Asleep': 'OFF'}.get(match.group(1) if match else '', 'UNKNOWN')


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token):
        self.url = 'https://api.telegram.org/bot' + token + '/'

    def request(self, method, args=None):
        try:
            request = urllib.request.Request(self.url + method, data=urllib.parse.urlencode(args or {}).encode())
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.load(response)
            if not body.get('ok'):
                raise TelegramError('Telegram request rejected')
            return body.get('result')
        except Exception:
            # Exceptions may contain the full token-bearing URL; never propagate them.
            raise TelegramError('Telegram request unavailable; check network, token, webhook and other pollers') from None


class App:
    def __init__(self, config, store, adb=None, telegram=None):
        self.config, self.store = config, store
        self.adb = adb or ADB()
        self.telegram = telegram or Telegram(config.token)
        self.tvs = {tv.id: tv for tv in config.tvs}
        self.next_poll = 0
        self.poll_failures = 0
        self.app_due, self.app_pending, self.readings, self.generations = {}, {}, {}, {}
        self.app_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='app-observer')
        for state in store.data['apps'].values():
            state.update(candidate=None, count=0, available=False)
        # Require fresh consecutive observations after restart; retain confirmed facts.
        for state in store.data['tvs'].values():
            state.update(candidate=None, count=0, failures=0)

    def picker(self, notice=None):
        text = f'{notice}\n\nChoose a TV:' if notice else 'Choose a TV:'
        self.store.enqueue(text, {'inline_keyboard': [
            [{'text': tv.name, 'callback_data': f'select:{tv.id}'}]
            for tv in self.tvs.values()]})

    def keyboard(self, tv):
        rows = [[{'text': LABELS[action], 'callback_data': f'tv:{tv.id}:{action}'}
                 for action in row] for row in REMOTE_ROWS]
        rows.append([{'text': 'Switch TV', 'callback_data': 'choose_tv'}])
        return {'inline_keyboard': rows}

    def show_remote(self, tv):
        if not self.adb.connect(tv):
            self.store.data.pop('selected_tv', None)
            self.picker(f'{tv.name} is not online (ADB unreachable).')
            return
        self.store.data['selected_tv'] = tv.id
        self.store.enqueue(f'{tv.name} — controls', self.keyboard(tv))

    def command(self, tv, action):

        addr = self.adb.connect(tv)
        if not addr:
            return f'{tv.name}: ADB unreachable (power, Wi-Fi or debugging). Wake-up requires a working connection.', None
        if action == 'status':
            return f'{tv.name}: {self.adb.power(addr) or "UNREACHABLE"}\nADB: {addr}', None
        result = self.adb.run('-s', addr, 'shell', 'input', 'keyevent', KEYS[action])
        return f'{tv.name}: ' + (f'sent {action}.' if result is not None else 'command failed.'), None

    def handle(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        chat = message.get('chat', {})
        sender = (callback or message).get('from', {})
        if chat.get('type') != 'private' or str(chat.get('id')) != self.config.chat or str(sender.get('id')) != self.config.chat:
            return
        if callback:
            try:
                self.telegram.request('answerCallbackQuery', {'callback_query_id': callback.get('id')})
            except TelegramError:
                pass
            data = callback.get('data', '')
            if data == 'choose_tv':
                self.picker()
                return
            if data.startswith('select:'):
                tv_id = data.removeprefix('select:')
                if tv_id not in self.tvs:
                    self.picker()
                    return
                self.show_remote(self.tvs[tv_id])
                return
            if data.startswith('tv:') and data.count(':') == 1:
                tv_id, action = 'main', data[3:]  # Buttons sent by the legacy version.
            elif data.startswith('tv:') and data.count(':') == 2:
                _, tv_id, action = data.split(':')
            else:
                return
        else:
            parts = message.get('text', '').strip().lower().split()
            if not parts or len(parts) > 2:
                return
            action = parts[0].removeprefix('/').split('@', 1)[0]
            if len(parts) == 1 and action in ('home', 'menu', 'start'):
                self.picker()
                return
            tv_id = parts[1] if len(parts) == 2 else self.store.data.get('selected_tv')
            if tv_id is None:
                self.picker()
                return
        action = {'start': 'menu', 'up': 'volup', 'down': 'voldown',
                  'volumeup': 'volup', 'volumedown': 'voldown'}.get(action, action)
        if action not in ACTIONS or tv_id not in self.tvs:
            self.store.enqueue('Use /home to choose a device, or /status <device-id>.')
            return
        if action == 'menu':
            self.show_remote(self.tvs[tv_id])
            return
        text, keyboard = self.command(self.tvs[tv_id], action)
        self.store.enqueue(text, keyboard)

    def activity_tick(self):
        # Only the main thread mutates the shared JSON state/outbox.
        for tv_id, (future, generation) in list(self.app_pending.items()):
            if not future.done():
                continue
            del self.app_pending[tv_id]
            try:
                package = future.result()
            except Exception:
                LOG.warning('Foreground observation failed for %s', tv_id)
                package = None
            if self.generations.get(tv_id, 0) != generation or self.readings.get(tv_id, (None, None))[1] != 'ON':
                continue
            self.record_activity(self.tvs[tv_id], package)
        if not self.config.apps.enabled:
            return
        for tv in self.tvs.values():
            if len(self.app_pending) >= 2:
                break
            addr, reading = self.readings.get(tv.id, (None, None))
            if not tv.app_monitor or reading != 'ON' or tv.id in self.app_pending or time.monotonic() < self.app_due.get(tv.id, 0):
                continue
            self.app_due[tv.id] = time.monotonic() + self.config.apps.interval
            self.app_pending[tv.id] = (self.app_workers.submit(ADB().foreground, addr), self.generations.get(tv.id, 0))

    def record_activity(self, tv, package):
        state = self.store.data['apps'].setdefault(tv.id, {})
        event = observe_app(state, package, tv, self.config.apps)
        if event:
            self.store.data['app_history'].append(event)
            self.store.data['app_history'] = self.store.data['app_history'][-self.config.apps.history_limit:]
            if self.config.apps.telegram:
                self.store.enqueue(notification(event, self.config.apps), event_id=event['id'])
        self.store.save()

    def close(self):
        self.app_workers.shutdown(wait=True, cancel_futures=True)

    def monitor(self, tv):
        addr = self.adb.connect(tv)
        reading = self.adb.power(addr) if addr else None
        self.readings[tv.id] = (addr, reading)
        if reading != 'ON':
            self.generations[tv.id] = self.generations.get(tv.id, 0) + 1
            self.store.data['apps'].setdefault(tv.id, {}).update(candidate=None, count=0, available=False)
        LOG.info('%s observation: %s', tv.id, reading or 'NO_CONNECTION')
        state = self.store.data['tvs'].setdefault(tv.id, {})
        for alert in observe(state, reading, tv.name, self.config.connection_failures,
                             self.config.connection_recoveries):
            self.store.enqueue(alert)
        self.store.save()  # Alert and transition committed together.

    def poll(self):
        if not self.config.telegram_remote:
            return
        if time.time() < self.next_poll:
            return
        try:
            updates = self.telegram.request('getUpdates', {'offset': self.store.data['offset'], 'timeout': 1,
                                           'allowed_updates': json.dumps(['message', 'callback_query'])})
        except TelegramError as error:
            self.poll_failures += 1
            self.next_poll = time.time() + min(60, 2 ** min(self.poll_failures, 6))
            LOG.warning('%s', error)
            return
        self.poll_failures = 0
        for update in updates or []:
            if update['update_id'] < self.store.data['offset']:
                continue
            self.handle(update)
            self.store.data['offset'] = update['update_id'] + 1
            self.store.save()  # Commit response and offset before requesting more updates.

    def flush(self):
        queue = self.store.data['outbox']
        if not queue:
            return
        item = queue[0]
        now = time.time()
        if now - item['created'] > 86400 or item['attempts'] >= 12:
            self.store.history_status(item, 'expired')
            queue.pop(0)
            LOG.warning('Expired outbound message discarded')
            self.store.save()
            return
        if now < item['next']:
            return
        args = {'chat_id': self.config.chat, 'text': item['text']}
        if item.get('keyboard'):
            args['reply_markup'] = json.dumps(item['keyboard'])
        try:
            self.telegram.request('sendMessage', args)
        except TelegramError:
            item['attempts'] += 1
            item['next'] = now + min(3600, 2 ** item['attempts'])
            self.store.history_status(item, 'retrying')
            LOG.warning('Outbound message deferred')
        else:
            self.store.history_status(item, 'sent')
            queue.pop(0)
        self.store.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose', action='store_true', help='Check ADB only; no Telegram requests or state changes')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        config = Config.load()
        if args.diagnose:
            adb = ADB()
            for tv in config.tvs:
                addr = adb.connect(tv)
                print(f'{tv.name}: {addr or "unreachable"}; {adb.power(addr) if addr else "UNREACHABLE"}')
            return 0
        config.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(str(config.state_file) + '.lock', 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another process owns this state file') from None
            app = App(config, Store(config.state_file))
            due = {tv.id: 0 for tv in config.tvs}
            while True:
                for tv in config.tvs:
                    if time.monotonic() >= due[tv.id]:
                        app.monitor(tv)
                        due[tv.id] = time.monotonic() + config.interval
                    app.activity_tick()
                    app.poll()
                    app.flush()
                time.sleep(0.5)
    except (ValueError, OSError) as error:
        LOG.error('Startup/storage failure (%s); check configuration and state permissions', type(error).__name__)
        if isinstance(error, ValueError):
            LOG.error('%s', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
