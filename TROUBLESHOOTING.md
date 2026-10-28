# Troubleshooting

Start with:

```bash
sudo systemctl status tv-hub-monitor tv-hub-web --no-pager
sudo journalctl -u tv-hub-monitor -u tv-hub-web -n 100 --no-pager
sudo -H adb devices -l
```

## App detection

Use the actual configured serial (examples below reflect the current devices):

```bash
sudo -H adb -s 192.168.1.149:5555 shell dumpsys activity activities
sudo -H adb -s 192.168.1.243:5555 shell dumpsys activity activities
sudo -H adb -s 192.168.1.149:5555 shell dumpsys window windows
```

Look for `mResumedActivity:` or `topResumedActivity=`, or focused Window/Activity records. A running process or media session does not establish foreground activity. If output contains no unambiguous supported record, the previous app remains last known and no change is invented. Share a redacted dump if another firmware format needs support.

No alert on first observation is expected. A change needs two consecutive matching reads; standby, disconnections and unknown observations reset candidates. Defaults require about two polling observations, plus ADB and scheduler time. App checks run only following an awake power reading. Check `APP_MONITOR_ENABLED`, the matching `TVn_APP_MONITOR_ENABLED`, and `APP_MONITOR_TELEGRAM` if history appears without messages. Review the activity dialog for `pending`, `retrying`, `expired` or `dropped` delivery status.

Connectivity alerts intentionally use hysteresis: five failed polls confirm unreachable and three successful polls confirm recovery. This prevents repeated unreachable/restored pairs when a sleeping Mi Box intermittently answers ADB. If alerts still flap, inspect `adb devices` and Wi-Fi power-saving, then raise the two `CONNECTION_*_CONFIRMATIONS` values rather than lowering them.

The Haier HDMI input app describes the TV's input player; it cannot identify which application is running on an attached Mi Box. HDMI Input on Haier and YouTube on Mi Box at the same time is expected.

If the service reports an invalid timezone, install `tzdata` and use `TZ=Asia/Dhaka` or another IANA timezone. Do not delete state to resolve a notification issue; that file contains power/app baselines, update offsets and undelivered messages. Stop the service and back it up before repairs.

## Live authentication and availability

Live View no longer prompts for a username/password. It is intentionally LAN-only and uses host validation, CSRF for mutations, device IDs and viewer leases. Anyone who can reach port 8080 can use enabled Live View and the existing remote; keep it off guest/public networks and never port-forward it.

If the page reports disabled or unsupported, check both `LIVE_VIEW_ENABLED` and the selected device's `TVn_LIVE_VIEW_ENABLED`. The Haier opt-out is intentional. If it reports missing dependencies or a checksum mismatch, run `sudo bash install-live.sh` from the updated checkout; do not substitute an arbitrary scrcpy server version. FFmpeg must be visible to the service or configured using an absolute `LIVE_VIEW_FFMPEG` path.

A startup deadline, empty packet stream or ADB failure displays an error and cleans up the owned capture. Inspect connectivity and device authorization. Pair as the same root ADB identity used by the services. The bridge does not change TCP ports or kill the shared ADB server. A disconnected device can require reselection/reconnection after its debugging port changes.

## Black screens and codecs

The Haier MatrixTV CE has already produced empty screenshots and black scrcpy output in the user's tests. This implementation does not fix firmware capture restrictions. Keep `TV_LIVE_VIEW_ENABLED=false` unless deliberately retesting. Its Live View page explains the opt-out; monitoring and remote controls still work.

The Mi Box's successful desktop scrcpy test does **not** establish that this browser bridge works. Test non-protected UI such as its launcher first. Protected video may be black even when the launcher works. The browser reports sustained near-black sampled frames; an intentionally dark scene can trigger the same warning. This is a diagnostic heuristic, not a DRM detector. No protected-content bypass is attempted.

The player requires H.264 in MediaSource (or ManagedMediaSource). Unsupported profiles or older mobile browsers display a codec/player error. Try an updated browser. If Safari rejects playback, try current Chrome on Android or desktop for comparison. No software transcoding fallback runs on the Pi. Raw H.264 timestamps are generated at the configured FPS; firmware that does not honor frame-repeat/keyframe settings may have timing limitations.

## Lifecycle and Pi 3B performance

Stop Stream releases your viewer. Other viewers keep the shared capture running. Close all viewers before switching to another streaming device; the default limit is one device. If a tab crashes or loses network, allow `LIVE_VIEW_IDLE_TIMEOUT` (default 60 seconds) plus process cleanup. A previous capture that is still stopping rejects immediate restart; wait briefly and press Reconnect again. Returning from a backgrounded mobile tab requires Start Stream again.

On the Pi, while testing an actual stream:

```bash
sudo systemd-cgtop
sudo systemctl show tv-hub-web -p MemoryCurrent -p CPUUsageNSec -p TasksCurrent
ps -eo pid,ppid,comm,%cpu,%mem | grep -E 'ffmpeg|adb|python'
```

Compare DNS lookup behavior/latency on your LAN before and during streaming, and verify Telegram responsiveness at the same time. These measurements have not been performed on your Pi. Keep one device/one viewer initially, lower `LIVE_VIEW_MAX_WIDTH=1024`, `LIVE_VIEW_MAX_FPS=10`, and `LIVE_VIEW_BITRATE=1000000` if needed. Multiple viewers multiply LAN output bandwidth, not capture processes. Do not raise resource caps merely to conceal a leak or runaway process.

If this bridge is unreliable on the Pi/device/browser combination, disable `LIVE_VIEW_ENABLED`; all remotes and notifications remain available. The user's already-tested standalone scrcpy setup on the Mac is the fallback for viewing the Mi Box. Do not describe the optional browser bridge as hardware-verified until it passes the checklist in README.
