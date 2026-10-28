#!/usr/bin/env bash
# Optional: install architecture-independent scrcpy server; no desktop GUI/SDK build.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo bash install-live.sh'; exit 1; }
apt-get update
apt-get install -y ffmpeg ca-certificates python3
install -d -m 755 /opt/tv-hub/vendor
python3 - <<'PY'
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request
version = '5.0.1'
expected = '764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536'
target = Path('/opt/tv-hub/vendor/scrcpy-server-v' + version)
if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
    print('Pinned scrcpy-server already installed.')
else:
    url = f'https://github.com/Genymobile/scrcpy/releases/download/v{version}/scrcpy-server-v{version}'
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(4 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit('scrcpy-server checksum mismatch; installation aborted.')
    fd, name = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, 0o644)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print('Installed verified scrcpy-server v' + version)
PY
echo 'Configure LIVE_VIEW_ENABLED and device LIVE_VIEW_ENABLED flags in /etc/tv-hub.env.'
echo 'Then: sudo systemctl restart tv-hub-web'
echo 'Capture/browser compatibility and Pi performance still require on-device verification.'
