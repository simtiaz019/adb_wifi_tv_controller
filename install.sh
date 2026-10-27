#!/usr/bin/env bash
set -euo pipefail
# --root DIR stages files for inspection/tests without touching the host service.
root=''
if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && $1 == --root && $2 == /* && $2 != / ]] || { echo 'Usage: install.sh [--root ABSOLUTE_STAGING_DIR]'; exit 1; }
  root=${2%/}
fi
if [[ -z $root && $(id -u) -ne 0 ]]; then echo 'Run with sudo'; exit 1; fi
cd "$(dirname "$0")"
if [[ -z $root ]]; then
  owner=$(systemctl show haier-tv-monitor -p User --value 2>/dev/null || true)
  [[ -z $owner || $owner == root ]] || { echo 'Existing service has a different owner; migrate its ADB keys before installing.'; exit 1; }
  apt-get update
  apt-get install -y adb python3
  systemctl stop haier-tv-monitor 2>/dev/null || true
fi
install -d -m 755 "$root/opt/haier-tv-monitor" "$root/etc/systemd/system"
install -d -m 700 "$root/var/lib/haier-tv-monitor"
install -m 755 haier_tv_bot.py "$root/opt/haier-tv-monitor/haier_tv_bot.py"
install -m 644 haier-tv-monitor.service "$root/etc/systemd/system/haier-tv-monitor.service"
config="$root/etc/haier-tv-monitor.env"
if [[ ! -e $config ]]; then
  install -m 600 config.env.example "$config"
  echo 'Created config template. Set Telegram credentials before starting the service.'
else
  echo 'Existing configuration preserved byte-for-byte; review config.env.example for optional TV2 settings.'
fi
chmod 600 "$config"
if [[ -z $root ]]; then
  chown root:root "$config" /var/lib/haier-tv-monitor
  systemctl daemon-reload
  systemctl enable haier-tv-monitor
  if /usr/bin/python3 - "$config" <<'PY'
import sys
from pathlib import Path
s = Path(sys.argv[1]).read_text()
raise SystemExit(1 if 'REPLACE_WITH_' in s else 0)
PY
  then
    systemctl restart haier-tv-monitor
    systemctl --no-pager --full status haier-tv-monitor
  else
    echo 'Edit /etc/haier-tv-monitor.env, then: sudo systemctl restart haier-tv-monitor'
  fi
fi
