#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
[[ $# -eq 1 && ( $1 == --keep-data || $1 == --purge ) ]] || {
  echo 'Usage: sudo bash uninstall.sh --keep-data | --purge'
  echo '--keep-data retains config/secrets/state; --purge removes them.'
  exit 1
}
systemctl disable --now haier-tv-monitor || true
rm -f /etc/systemd/system/haier-tv-monitor.service /opt/haier-tv-monitor/haier_tv_bot.py
rmdir /opt/haier-tv-monitor 2>/dev/null || true
systemctl daemon-reload
if [[ $1 == --purge ]]; then
  rm -f /etc/haier-tv-monitor.env
  rm -rf /var/lib/haier-tv-monitor
fi
echo 'ADB keys and packages retained (they may be shared with other applications).'
