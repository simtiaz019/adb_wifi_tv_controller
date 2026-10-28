# Configuration

All settings use the existing `/etc/tv-hub.env` systemd EnvironmentFile. The installer preserves that file byte-for-byte. Add missing keys once, edit existing values in place, and restart `tv-hub-monitor` and `tv-hub-web`. Keep the file root-owned, mode 600. Do not change your active `STATE_FILE` during this upgrade.

Existing `TV_*`, `TV2_*`, `TV3_*` groups, Telegram credentials, remote controls, poll intervals, state paths and `WEB_HOST`/`WEB_PORT` remain supported. Device IDs stay `main`, `second`, `tv3`, etc. There is no new configuration file/database.

## Foreground apps

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_MONITOR_ENABLED` | `true` | Global app observation switch; power/connectivity monitoring is independent. |
| `APP_MONITOR_INTERVAL` | `15` | Minimum seconds between scheduled app reads, 5–3600. Slow ADB/power checks can increase effective interval. |
| `APP_MONITOR_CONFIRMATIONS` | `2` | Matching successful observations before a package change, 2–10. |
| `APP_MONITOR_TELEGRAM` | `true` | Enqueue app alerts through the existing bot; history still records changes if false. |
| `APP_HISTORY_LIMIT` | `500` | Maximum retained transitions across all devices, 10–10000. Oldest entries are discarded. |
| `TZ` | `Asia/Dhaka` | IANA timezone for event timestamps/messages. Requires system timezone data (`tzdata`). |
| `TV_APP_MONITOR_ENABLED` | `true` | Per-device switch; use `TV2_`, `TV3_`, etc. for other devices. |
| `APP_NAME_MAP` | `{}` | JSON object overriding/extending built-in package labels. |
| `APP_HOME_PACKAGES` | See below | Comma-separated launcher packages that produce Home Screen messages. |

Boolean settings accept `true`/`false` or `1`/`0`. The existing `TELEGRAM_REMOTE_ENABLED` continues to accept `1`/`0` and defaults to `1`.

Example mapping (outer single quotes preserve the JSON in a systemd environment file):

```ini
APP_NAME_MAP='{"com.example.player":"My Player","com.google.android.tvlauncher":"Home Screen"}'
APP_HOME_PACKAGES=com.google.android.tvlauncher,com.google.android.apps.tv.launcherx,com.spocky.projengmenu
```

Built-in labels: YouTube (`com.google.android.youtube.tv`), Netflix (`com.netflix.ninja`), SonyLIV (`com.sonyliv`), Projectivy Launcher (`com.spocky.projengmenu`), Android TV Home (`com.google.android.tvlauncher`), HDMI Input (`com.google.android.tv.inputplayer`), Media Hub (`com.oumi.utility.media.hub`), and VLC (`org.videolan.vlc`). Unknown packages are displayed verbatim. Mapping changes do not rewrite historical names or cause package-change alerts.

## Optional Live View

| Setting | Default | Meaning |
| --- | --- | --- |
| `LIVE_VIEW_ENABLED` | `false` | Global on-demand streaming switch. Normal monitoring/remotes work without streaming dependencies. |
| `TV_LIVE_VIEW_ENABLED` | `false` | Per-device opt-in; use the corresponding numbered prefix. |
| `LIVE_VIEW_MAX_FPS` | `15` | Maximum requested encoder frame rate, 5–30. |
| `LIVE_VIEW_MAX_WIDTH` | `1280` | Scrcpy maximum **dimension**, 320–1920; landscape is approximately 720p at 1280. |
| `LIVE_VIEW_BITRATE` | `2000000` | Encoder bits/second, 100000–8000000. |
| `LIVE_VIEW_IDLE_TIMEOUT` | `60` | Viewer lease expiry in seconds, 10–300. |
| `LIVE_VIEW_MAX_CONCURRENT_DEVICES` | `1` | Simultaneous capture pipelines, 1–2. Keep 1 on a Pi 3B. |
| `LIVE_VIEW_MAX_VIEWERS` | `4` | Total active viewer leases, 1–8. Each viewer adds LAN bandwidth. |
| `LIVE_VIEW_STARTUP_TIMEOUT` | `30` | Capture startup/no-packet deadline, 10–120 seconds; bounded ADB calls/cleanup may add a few seconds. |
| `LIVE_VIEW_SERVER_PATH` | `/opt/tv-hub/vendor/scrcpy-server-v5.0.1` | Must match the pinned server checksum. |
| `LIVE_VIEW_FFMPEG` | `ffmpeg` | Executable name or absolute path, configured only by the administrator. |

Recommended opt-ins for your current devices:

```ini
TV_APP_MONITOR_ENABLED=true
TV_LIVE_VIEW_ENABLED=false
TV2_APP_MONITOR_ENABLED=true
TV2_LIVE_VIEW_ENABLED=true
```

Haier remains opted out because its capture has previously produced black/empty results. The example config uses port 5555 for both devices as confirmed in the latest task. Existing installed port lists are never rewritten. A new device defaults to app observation enabled and Live View disabled.

Live View intentionally has no username/password prompt. Host validation, the per-process CSRF token for mutating requests, device IDs and viewer leases still apply. Anyone who can reach port 8080 can use enabled Live View and the existing LAN remote. Plain HTTP is not encrypted; keep it on a trusted LAN and do not port-forward it.
