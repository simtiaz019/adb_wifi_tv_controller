"""Foreground detection, configuration and bounded history; no Telegram transport."""
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PACKAGE = r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+'
DEFAULT_NAMES = {
    'com.google.android.youtube.tv': 'YouTube', 'com.netflix.ninja': 'Netflix',
    'com.sonyliv': 'SonyLIV', 'com.spocky.projengmenu': 'Projectivy Launcher',
    'com.google.android.tvlauncher': 'Android TV Home',
    'com.google.android.tv.inputplayer': 'HDMI Input',
    'com.oumi.utility.media.hub': 'Media Hub', 'org.videolan.vlc': 'VLC',
}
DEFAULT_HOME = ('com.google.android.tvlauncher', 'com.google.android.apps.tv.launcherx', 'com.spocky.projengmenu')


def boolean(env, key, default):
    value = str(env.get(key, str(default))).lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError(f'{key} must be true/false or 1/0')
    return value in ('true', '1')


def integer(env, key, default, low, high):
    try:
        value = int(env.get(key, default))
        if not low <= value <= high:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValueError(f'{key} must be an integer between {low} and {high}') from None


@dataclass
class AppSettings:
    enabled: bool = True
    interval: int = 15
    confirmations: int = 2
    telegram: bool = True
    history_limit: int = 500
    timezone: str = 'Asia/Dhaka'
    names: dict = field(default_factory=lambda: dict(DEFAULT_NAMES))
    home_packages: tuple = DEFAULT_HOME

    @classmethod
    def load(cls, env):
        timezone = env.get('TZ', 'Asia/Dhaka')
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError('TZ must name an installed IANA timezone (default Asia/Dhaka)') from None
        names = dict(DEFAULT_NAMES)
        try:
            extra = json.loads(env.get('APP_NAME_MAP', '{}'))
            if not isinstance(extra, dict) or any(not re.fullmatch(PACKAGE, k) or not isinstance(v, str) or
                                                  not v or len(v) > 100 for k, v in extra.items()):
                raise ValueError
            names.update(extra)
        except (ValueError, TypeError):
            raise ValueError('APP_NAME_MAP must be a JSON object mapping package IDs to short names') from None
        homes = tuple(p.strip() for p in env.get('APP_HOME_PACKAGES', ','.join(DEFAULT_HOME)).split(',') if p.strip())
        if any(not re.fullmatch(PACKAGE, p) for p in homes):
            raise ValueError('APP_HOME_PACKAGES must contain comma-separated package IDs')
        return cls(boolean(env, 'APP_MONITOR_ENABLED', True),
                   integer(env, 'APP_MONITOR_INTERVAL', 15, 5, 3600),
                   integer(env, 'APP_MONITOR_CONFIRMATIONS', 2, 2, 10),
                   boolean(env, 'APP_MONITOR_TELEGRAM', True),
                   integer(env, 'APP_HISTORY_LIMIT', 500, 10, 10000), timezone, names, homes)


def foreground(output, window=False):
    """Only explicit resumed/focused component records count; not process lists."""
    if not isinstance(output, str):
        return None
    markers = ('mCurrentFocus', 'mFocusedApp') if window else ('topResumedActivity', 'mResumedActivity')
    for marker in markers:
        packages = set()
        for line in output.splitlines():
            if re.search(r'\b' + marker + r'\s*[:=]', line):
                match = re.search(r'(?<![\w.])(' + PACKAGE + r')/[A-Za-z0-9_.$]+', line)
                if match:
                    packages.add(match.group(1))
        # Ambiguous multi-display output is not a foreground change.
        if len(packages) > 1:
            return None
        if packages:
            return packages.pop()
    return None


def observe_app(state, package, tv, settings, now=None):
    if not isinstance(package, str) or not re.fullmatch(PACKAGE, package):
        state.update(candidate=None, count=0, available=False)
        return None
    stamp = (now or datetime.now(ZoneInfo(settings.timezone))).isoformat(timespec='seconds')
    state.update(available=True, last_seen=stamp)
    state['count'] = state.get('count', 0) + 1 if state.get('candidate') == package else 1
    state['candidate'] = package
    if state['count'] < settings.confirmations or state.get('confirmed') == package:
        return None
    old = state.get('confirmed')
    state['confirmed'] = package
    state['name'] = settings.names.get(package, package)
    if old is None:
        return None
    return {'id': uuid.uuid4().hex, 'device_id': tv.id, 'device_name': tv.name,
            'previous_package': old, 'package': package, 'previous_name': settings.names.get(old, old),
            'name': settings.names.get(package, package), 'timestamp': stamp,
            'notification_status': 'pending' if settings.telegram else 'disabled'}


def notification(event, settings):
    old, new = event['previous_package'], event['package']
    if new in settings.home_packages:
        body = f"🏠 Returned to Home Screen\nPrevious: {event['previous_name']}\nHome: {event['name']}"
    elif old in settings.home_packages:
        body = f"▶️ Application Opened: {event['name']}\nPrevious: {event['previous_name']}"
    else:
        body = f"🔄 Application Changed\nFrom: {event['previous_name']}\nTo: {event['name']}"
    clock = datetime.fromisoformat(event['timestamp']).strftime('%H:%M:%S')
    return f"📺 {event['device_name']}\n\n{body}\nTime: {clock} ({settings.timezone})"
