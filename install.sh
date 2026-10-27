#!/usr/bin/env bash
set -euo pipefail
# --root DIR stages installation/migration without apt or systemd operations.
root=''
if [[ $# -gt 0 ]]; then
  [[ $# -eq 2 && $1 == --root && $2 == /* && $2 != / ]] || { echo 'Usage: install.sh [--root ABSOLUTE_STAGING_DIR]'; exit 1; }
  root=${2%/}
fi
if [[ -z $root && $(id -u) -ne 0 ]]; then echo 'Run with sudo'; exit 1; fi
cd "$(dirname "$0")"
if [[ -z $root ]]; then
  for unit in haier-tv-monitor haier-tv-web tv-hub-monitor tv-hub-web; do
    owner=$(systemctl show "$unit" -p User --value 2>/dev/null || true)
    [[ -z $owner || $owner == root ]] || { echo "Existing $unit has a different owner; migrate its ADB identity first."; exit 1; }
  done
  apt-get update
  apt-get install -y adb python3 tzdata
  for unit in haier-tv-monitor haier-tv-web tv-hub-monitor tv-hub-web; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
      systemctl stop "$unit"
    fi
  done
  # Retire legacy pollers so only TV Hub consumes this bot's updates.
  for unit in haier-tv-monitor haier-tv-web; do
    if systemctl cat "$unit" >/dev/null 2>&1; then systemctl disable "$unit"; fi
  done
fi
install -d -m 755 "$root/opt/tv-hub/web" "$root/etc/systemd/system"
install -d -m 700 "$root/var/lib/tv-hub"
install -m 755 tv_controller.py tv_web.py app_activity.py live_view.py "$root/opt/tv-hub/"
install -m 644 web/index.html web/app.js web/style.css web/live.html web/live.js web/live.css "$root/opt/tv-hub/web/"
install -m 644 tv-hub-monitor.service tv-hub-web.service "$root/etc/systemd/system/"
config="$root/etc/tv-hub.env"
legacy_config="$root/etc/haier-tv-monitor.env"
if [[ ! -e $config ]]; then
  if [[ -e $legacy_config ]]; then
    install -m 600 "$legacy_config" "$config"
    echo 'Copied legacy configuration byte-for-byte to /etc/tv-hub.env; original retained.'
  else
    install -m 600 config.env.example "$config"
    echo 'Created config template. Set Telegram credentials before starting services.'
  fi
else
  echo 'Existing /etc/tv-hub.env preserved byte-for-byte.'
fi
# If legacy config has explicit STATE_FILE, it stays active unchanged. Otherwise
# the copied state becomes the generic default. Never overwrite existing state.
if [[ ! -e "$root/var/lib/tv-hub/state.json" && -f "$root/var/lib/haier-tv-monitor/state.json" ]]; then
  install -m 600 "$root/var/lib/haier-tv-monitor/state.json" "$root/var/lib/tv-hub/state.json"
fi
chmod 600 "$config"
if [[ -z $root ]]; then
  chown root:root "$config" /var/lib/tv-hub
  systemctl daemon-reload
  systemctl enable tv-hub-monitor tv-hub-web
  if /usr/bin/python3 - "$config" <<'PY'
import sys
from pathlib import Path
s = Path(sys.argv[1]).read_text()
raise SystemExit(1 if 'REPLACE_WITH_' in s else 0)
PY
  then
    systemctl restart tv-hub-monitor tv-hub-web
    systemctl --no-pager --full status tv-hub-monitor tv-hub-web
  else
    echo 'Edit /etc/tv-hub.env, then: sudo systemctl restart tv-hub-monitor tv-hub-web'
  fi
fi
