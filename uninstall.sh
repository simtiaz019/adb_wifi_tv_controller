#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
[[ $# -eq 1 && ( $1 == --keep-data || $1 == --purge ) ]] || {
  echo 'Usage: sudo bash uninstall.sh --keep-data | --purge'
  echo '--keep-data retains config/secrets/state; --purge removes TV Hub default data.'
  exit 1
}
systemctl disable --now tv-hub-monitor tv-hub-web || true
rm -f /etc/systemd/system/tv-hub-monitor.service /etc/systemd/system/tv-hub-web.service
rm -f /opt/tv-hub/tv_controller.py /opt/tv-hub/tv_web.py
rm -f /opt/tv-hub/web/index.html /opt/tv-hub/web/app.js /opt/tv-hub/web/style.css
rmdir /opt/tv-hub/web 2>/dev/null || true
rmdir /opt/tv-hub 2>/dev/null || true
systemctl daemon-reload
if [[ $1 == --purge ]]; then
  rm -f /etc/tv-hub.env
  rm -rf /var/lib/tv-hub
fi
echo 'ADB keys/packages, legacy Haier files and custom STATE_FILE paths retained; remove separately if needed.'
