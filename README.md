# TV Hub — LAN and Telegram remote

Two Python standard-library services for a Raspberry Pi: a fast local web remote for any configured Android TV or box and a background monitor that sends status notifications to Telegram. The repository was extracted from `haier_tv_codex_ready.zip` and implements the brief in `CODEX_PROMPT.md`.

The example configuration includes your two current devices:

| ID | Display name | Address | Connection ports |
| --- | --- | --- | --- |
| `main` | Haier MatrixTV CE | 192.168.1.243 | 36683, 5555 |
| `second` | Mi Box 3 | 192.168.1.149 | 5555 |

Addresses and names are configuration only. Port 36683 was observed previously and may have rotated. Neither TV has been tested from this development machine.

## Install or upgrade on the Raspberry Pi

Requires Debian/Raspberry Pi OS, Python 3.9+, systemd, ADB, and a trusted LAN. No Node.js, npm, or Python packages are needed on the Pi. Copy the **updated repository** to the Pi; the original ZIP is still the old package.

From the updated repository directory:

```bash
# Upgrading directly from the original installed Haier package:
sudo systemctl stop haier-tv-monitor
sudo install -d -m 700 /root/tv-hub-backup
sudo cp -a /etc/haier-tv-monitor.env /root/tv-hub-backup/
sudo cp -a /var/lib/haier-tv-monitor/state.json /root/tv-hub-backup/

sudo bash install.sh
sudo nano /etc/tv-hub.env
sudo chmod 600 /etc/tv-hub.env
sudo systemctl restart tv-hub-monitor tv-hub-web
sudo systemctl status tv-hub-monitor tv-hub-web --no-pager
hostname -I
```

You do not need to install any intermediate version. For a fresh installation skip the stop/backup commands. For later TV Hub upgrades, back up `/etc/tv-hub.env` and the active `STATE_FILE`, and stop `tv-hub-monitor` instead. The installer creates a config template and waits for credentials to be entered. On the first migration, the installer copies `/etc/haier-tv-monitor.env` byte-for-byte to `/etc/tv-hub.env` and preserves the original. Existing TV Hub configuration is never overwritten. It stops and disables the old Haier units so there is only one Telegram poller, and starts the generic `tv-hub-monitor` and `tv-hub-web` services. The installer installs/enables both services and restarts them if credentials are configured. Existing non-root service ownership is rejected to avoid silently changing ADB identities.

Migration retains an explicit `STATE_FILE` exactly as configured, even if it still points into `/var/lib/haier-tv-monitor`. The legacy default state is also copied to `/var/lib/tv-hub/state.json` when the destination does not exist, so configurations without an explicit path keep their history. Old source, configuration, pairing keys and state are retained. Do not restart the retired Haier service alongside TV Hub.

To move an explicitly configured legacy state path to the generic location, stop `tv-hub-monitor`, copy the **current active** state file to `/var/lib/tv-hub/state.json` with mode 600, update `STATE_FILE`, and restart. This is optional; keeping the legacy path is safe. Never replace live state with an older backup.

Edit existing configuration entries, avoiding duplicates:

```ini
# Keep your existing private Telegram credentials.
TV_NAME="Haier MatrixTV CE"
TV_IP=192.168.1.243
TV_ADB_PORTS=36683,5555
TV2_NAME="Mi Box 3"
TV2_IP=192.168.1.149
TV2_ADB_PORTS=5555
POLL_SECONDS=15
# Fresh-install default; on upgrade keep your existing active STATE_FILE.
STATE_FILE=/var/lib/tv-hub/state.json
WEB_HOST=0.0.0.0
WEB_PORT=8080
TELEGRAM_REMOTE_ENABLED=1
```

Keep the Haier's currently working connection ports; 36683 is only the previously observed port. Start a private conversation with your Telegram bot via `/start` once so it can send notifications. Telegram controls and notifications remain enabled alongside the LAN remote.

Open **`http://<Pi-LAN-IP>:8080`** on your phone while connected to your home Wi-Fi. Use the Pi address printed by `hostname -I`, not either TV's address. `http://adguard-home.local:8080` may also work if hostname discovery is available. Bookmark the page or add it to your phone's home screen.

The mobile web app keeps the full remote on one screen. Choose or switch devices using the dropdown at the top; each selection checks connectivity. Controls stay disabled until a device is online. An offline device shows a message and clears the dropdown so you can retry or select another device. The layout fits the dynamic mobile viewport, respects screen safe areas, and uses a side-by-side control layout on short screens/landscape to avoid page scrolling. The page shows measured request time after each command. The first connection can take longer while ADB discovers a usable endpoint; subsequent button presses go directly to the cached endpoint. An unreachable device may still take up to the ADB timeout to fail. No fixed latency is promised before hardware testing.

The web remote uses a separate process, so Telegram internet timeouts and monitoring checks do not hold up button presses. Per-device locks reject overlapping commands instead of building a delayed queue. Failed key events are never automatically replayed because they may already have taken effect. Reselect a device after a failed command. The two services share the system ADB server and root pairing keys; TV/network/ADB server performance can still affect latency.

The remote uses plain HTTP on port 8080 and has no login (Live View separately requires credentials): anyone on the trusted LAN who can reach that port can control the TVs. Do not port-forward it or expose it to guest/public networks. Bind `WEB_HOST` to the Pi's reserved LAN IPv4 address to restrict its listening interface, and use your firewall to limit access as appropriate. If port 8080 is occupied, change `WEB_PORT`. Host checking supports private IPs, localhost, and the Pi's system hostname/`.local` name. Reverse proxies and custom DNS names are not configured.

The defaults above also apply if the new web/Telegram-mode settings are absent on upgrade. Telegram controls and notifications stay enabled; the LAN web remote is an additional option. No intermediate version is required.

Configuration is loaded from the process environment; systemd reads `/etc/tv-hub.env`. Running Python directly does not read that file automatically. Names, valid literal IP addresses and comma-separated ports (1–65535) are required for each configured TV. `POLL_SECONDS` defaults to 15 and must be at least 5; `STATE_FILE` defaults to `/var/lib/tv-hub/state.json` and must be absolute. Remove all `TV2_` entries to disable the second device. The web service shares the configuration loader and therefore also requires valid Telegram credential syntax, but never makes Telegram requests or returns those credentials to the browser.

## New: foreground apps and optional Live View

The existing mobile remote and Telegram controls are preserved. The remote now shows the last confirmed app in its header; tap it to open recent activity and delivery history. **📺 Open Live** sits beside Refresh status and opens the selected device's separate Live View page. Back to Remote restores the selected device. The remote still fits one mobile screen; the history dialog and Live View page can scroll when needed.

App monitoring is enabled by default. Two successful observations confirm each package change. The first baseline is silent, movement between activities of one package does not alert, and standby/unreachable observations pause detection. Confirmed app state survives restarts. Notifications use the same Telegram bot/outbox; internet failures retry within existing delivery limits. HDMI Input on the Haier and YouTube on an HDMI-connected Mi Box are independent, expected observations.

### Upgrade this enhancement over an installed TV Hub

Copy the updated repository to the Pi. Back up `/etc/tv-hub.env` and the **active `STATE_FILE`** (it may still be `/var/lib/haier-tv-monitor/state.json` after migration), then run:

```bash
cd ~/tv-hub
sudo systemctl stop tv-hub-monitor tv-hub-web
sudo bash install.sh
sudo nano /etc/tv-hub.env
```

The installer preserves the existing environment file. Add/update these settings without duplicating keys:

```ini
APP_MONITOR_ENABLED=true
APP_MONITOR_INTERVAL=15
APP_MONITOR_CONFIRMATIONS=2
APP_MONITOR_TELEGRAM=true
TZ=Asia/Dhaka
CONNECTION_FAILURE_CONFIRMATIONS=5
CONNECTION_RECOVERY_CONFIRMATIONS=3
TV_APP_MONITOR_ENABLED=true
TV2_APP_MONITOR_ENABLED=true
TELEGRAM_REMOTE_ENABLED=1
```

```bash
sudo systemctl restart tv-hub-monitor tv-hub-web
```

No SQL migration or state reset is required: existing JSON gains app state/history on its next atomic save. State, update offsets, configuration and pairing keys remain intact. For an original Haier-package installation, use the direct migration instructions above instead.

### Install optional Live View dependencies

Normal monitoring and remotes do not require scrcpy or FFmpeg. To opt in:

```bash
cd ~/tv-hub
sudo bash install-live.sh
sudo nano /etc/tv-hub.env
```

Add/update:

```ini
LIVE_VIEW_ENABLED=true
LIVE_VIEW_MAX_FPS=15
LIVE_VIEW_MAX_WIDTH=1280
LIVE_VIEW_BITRATE=2000000
LIVE_VIEW_IDLE_TIMEOUT=60
LIVE_VIEW_MAX_CONCURRENT_DEVICES=1
TV_LIVE_VIEW_ENABLED=false
TV2_LIVE_VIEW_ENABLED=true
```

Protect the file and restart:

```bash
sudo chmod 600 /etc/tv-hub.env
sudo systemctl restart tv-hub-web
```

Select Mi Box 3 in the remote, tap Open Live, and press Start Stream. There is no username/password prompt. The page has a responsive 16:9 player, foreground-app label, loading/errors, Stop Stream, Reconnect, and Back to Remote. Capture is on demand; leaving or hiding the page releases the viewer, with a 60-second lease timeout as fallback. Multiple viewers share one capture. Haier's page explains its disabled/unsupported capture; its normal controls and monitoring still work.

Live View uses plain HTTP on the trusted LAN, not encrypted transport. Do not port-forward it. Existing LAN remote access behavior and Telegram authorization are unchanged. See [SECURITY.md](SECURITY.md).

### Dependencies and verification status

The optional bridge uses the actively maintained [Genymobile scrcpy-server **5.0.1**](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1), pinned to SHA-256 `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`. `install-live.sh` verifies the downloaded server before installation. Its architecture-independent Android JAR avoids building the desktop client on the Pi. See upstream [server options](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/Options.java) for raw H.264 mode.

FFmpeg is installed from the Pi OS security-maintained package repository, rather than pinning a cross-distribution Debian package version. The bridge requires the H.264 demuxer and fragmented MP4 muxer (`-c:v copy`); intended compatibility is FFmpeg 4.3–9.x, with the automated synthetic integration test run locally on **9.0.2**. Pi package versions remain unverified. No Python or browser packages, desktop GUI, video transcoding or audio stream are added. New runtime support for timezone data comes from `tzdata`.

**Verified here:** mocked app/Telegram transitions, history/restart behavior, HTTP access controls, viewer sharing/expiry and process cleanup; UI event behavior; and real FFmpeg stream-copy producing decodable fragmented MP4 from a synthetic H.264 source. **Not verified:** actual Mi Box capture through this bridge, rendered browser playback/layout, Haier capture, Pi 3B CPU/memory/DNS impact, or on-device systemd deployment. A successful desktop scrcpy test does not prove browser streaming compatibility.

On-device acceptance checklist before treating Live View as operational:

1. Start Mi Box Live View on its launcher; confirm recognizable, changing video frames in a mobile/desktop browser, not just a connection message.
2. Use Home/volume/navigation in the existing remote while viewing. Check power/app notifications still arrive once per confirmed change.
3. Open a second viewer of the same device and verify there is one FFmpeg capture pipeline. Stop one viewer, then the last; confirm owned capture/forward resources disappear.
4. Leave/background the page or disconnect its network; verify capture stops after the idle timeout when there are no other viewers.
5. Check an ADB disconnection produces an error. Haier should show the disabled/unsupported explanation, with normal remote controls intact.
6. Measure Pi CPU/memory and AdGuard DNS responsiveness before/during streaming. Keep one stream, 10–15 FPS and 1024–1280 maximum dimension; lower settings or disable Live View if the Pi cannot sustain it.

See [CONFIGURATION.md](CONFIGURATION.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for all settings, lifecycle details, black-screen/codec limitations and diagnostics.

## Add more TVs or Android boxes

The app, installation paths and services are named **TV Hub**. Haier and Mi Box are just device display names. Add a numbered group to `/etc/tv-hub.env`, then restart both services:

```ini
TV3_NAME="Bedroom TV"
TV3_IP=192.168.1.150
TV3_ADB_PORTS=5555
```

```bash
sudo systemctl restart tv-hub-monitor tv-hub-web
```

Use `TV4_*`, `TV5_*`, and so on for future devices; gaps are allowed. Each group needs NAME, IP and ADB_PORTS. New devices automatically appear in the web and Telegram pickers and receive independent status monitoring. The original IDs `main` (`TV_*`) and `second` (`TV2_*`) are preserved for state and existing buttons. Additional command IDs are `tv3`, `tv4`, etc.; for example `/status tv3`. Keep numbering stable when editing names/IPs, because monitoring state is keyed by that ID. Remove the entire group to stop monitoring a device. Monitoring is sequential, so more unreachable devices can increase notification latency; web command handling remains independent.

## ADB setup and verification

The service retains the legacy root ADB identity (`HOME=/root`) so existing pairing keys in `/root/.android` keep working. Use the same identity for pairing and diagnostics:

```bash
sudo -H adb connect 192.168.1.243:36683  # replace with current connection port
sudo -H adb connect 192.168.1.149:5555
sudo -H adb devices -l
sudo -H adb -s 192.168.1.149:5555 shell dumpsys power
sudo -H adb mdns services
```

If TLS pairing is required, show *Pair device with pairing code* on that TV, then run `sudo -H adb pair IP:PAIRING_PORT`. Enter the code interactively. Pairing ports are temporary and are different from connection ports. Never put a pairing port in `TV_ADB_PORTS` or `TV2_ADB_PORTS`. Port 5555 requires the TV to actually expose legacy TCP ADB; setting it in the file cannot enable that feature.

The controller validates a cached connection first, then tries existing device sessions, configured ports, and `_adb-tls-connect._tcp` endpoints reported by mDNS for the exact TV IP. It excludes pairing advertisements and other TVs. Discovery availability depends on the installed ADB build/network. Update configured ports if discovery fails after a reboot.

To run secret-free diagnostics with systemd loading the environment file:

```bash
sudo systemd-run --wait --pipe --collect \
  -p EnvironmentFile=/etc/tv-hub.env -p Environment=HOME=/root \
  /usr/bin/python3 /opt/tv-hub/tv_controller.py --diagnose
```

Diagnostics contact ADB, but do not contact Telegram or write monitoring state.

## Telegram controls

Telegram controls are enabled by default (`TELEGRAM_REMOTE_ENABLED=1`). The LAN web remote is an additional option for faster control at home.

Send `/home` (or `/menu` or `/start`) to choose **Haier MatrixTV CE** or **Mi Box 3**. Tap a device to check its live ADB connectivity. If it responds, its remote controls appear. If it is unreachable, the bot says the device is not online and returns to the device picker, clearing the previous selection. A reachable device in standby can still show its remote, allowing Wake. The panel includes **Switch TV** to return to the picker. Selecting a device sends no ADB key event.

The selected device is remembered across restarts. Commands such as `/status`, `/on`, `/off`, `/volup`, `/voldown`, and `/mute` use that selection; before a device is selected they show the picker without executing the command. Explicit selectors such as `/status second` still work. `/home` always opens the picker; the panel's **Home** button sends Android Home to that panel's device, and `/home second` remains an explicit shortcut. Buttons carry their device ID, so an older panel still controls the device named in its heading.

`/command@botname` syntax and plain command text are accepted. Both the private-chat ID and sender ID must match the configured owner, including selection callbacks. Previously sent single-TV buttons still target `main`.

Both devices use Android key events. The Mi Box 3 controls its own Android interface; its reported power state is the box's state, not an independent measurement of the attached display. Actual display power/volume behavior depends on the HDMI/CEC and audio setup and needs hardware verification.

| Commands | Android key events |
| --- | --- |
| `/on`, `/off` | `KEYCODE_WAKEUP`, `KEYCODE_SLEEP` |
| `/navup`, `/navdown`, `/left`, `/right` | `KEYCODE_DPAD_UP`, `KEYCODE_DPAD_DOWN`, `KEYCODE_DPAD_LEFT`, `KEYCODE_DPAD_RIGHT` |
| `/ok` | `KEYCODE_DPAD_CENTER` |
| `/home`, `/back` | `KEYCODE_HOME`, `KEYCODE_BACK` |
| `/playpause` | `KEYCODE_MEDIA_PLAY_PAUSE` |
| `/volup`, `/voldown`, `/mute` | `KEYCODE_VOLUME_UP`, `KEYCODE_VOLUME_DOWN`, `KEYCODE_VOLUME_MUTE` |

For example, `/home second` and `/ok second` operate the Mi Box 3. The inline menu includes navigation buttons for both devices. `/menu` opens the device picker. Legacy `/up` and `/down` aliases still adjust volume; use `/navup` and `/navdown` for navigation. Wake/sleep are explicit events, avoiding the ambiguous power toggle. These mappings follow the [Android KeyEvent reference](https://developer.android.com/reference/android/view/KeyEvent); firmware-specific behavior has not been verified on these devices.

Success means ADB accepted the key event, not that the physical screen or volume was independently verified. Wake-up cannot work through an unreachable ADB connection.

## Automatic status notifications

All configured devices are monitored all the time, regardless of which remote you select. You receive named alerts for ON, OFF/standby, confirmed UNKNOWN power state, loss of connectivity, and reconnection. The first observed baseline is silent; repeated identical readings do not generate repeated alerts.

Power states require two matching polls. By default, connectivity alerts require five failed polls after a previous successful connection, and a recovery alert requires three consecutive successful polls. This deliberately filters brief ADB/Wi-Fi sleep flaps common when a Mi Box screen is off. With the default 15-second interval, an outage alert takes roughly 75 seconds plus ADB/network processing time; recovery takes roughly 45 seconds. Change `CONNECTION_FAILURE_CONFIRMATIONS` and `CONNECTION_RECOVERY_CONFIRMATIONS` only if you accept more noise. A power cut or Wi-Fi failure is reported as **unreachable**, because ADB cannot distinguish those causes.

The Pi must remain powered to monitor, and must have internet access to send Telegram notifications. If the router/internet goes down too, messages are queued and retried when internet returns, subject to the queue limits below. If the Pi loses power, it cannot observe changes while off. Immediate reporting through a shared power or internet outage needs separately powered monitoring and a working backup internet connection.

## Architecture and delivery behavior

The monitoring process owns persistent state for all configured devices and is the only writer to that file. Each cycle checks due TVs and attempts one queued outgoing message. It also polls Telegram commands by default. The independent threaded web service holds per-device ADB connections and never reads or writes monitoring state. Individual ADB calls time out after six seconds and Telegram calls after ten seconds. Checks are sequential: an unreachable TV can delay monitoring of the other TV and Telegram commands by multiple ADB timeouts. It does not block the separate web service. `POLL_SECONDS` is a minimum spacing after each completed TV observation, not a strict real-time guarantee.

Each TV has independent confirmed power, candidate reading, consecutive count, connectivity and incident state:

- `Awake` maps to ON; `Asleep` maps to OFF/standby. A successful but unrecognized power response maps to UNKNOWN and proves connectivity.
- Two consecutive matching ON/OFF/UNKNOWN observations establish or change confirmed power. Each confirmed change sends an alert; a transport failure resets the candidate. The first baseline is silent.
- Five consecutive transport failures after a successful connection confirm UNREACHABLE by default and produce one alert. Further failures are silent. Three consecutive successful polls are required before one recovery alert. These values are configurable with `CONNECTION_FAILURE_CONFIRMATIONS` and `CONNECTION_RECOVERY_CONFIRMATIONS`; they apply independently to every device. A changed power state still needs two matching readings separately.
- Confirmed power and incident flags survive restarts; candidate/failure counters restart at zero. Legacy `tv`, `connectivity`, and `offset` fields are migrated without deleting original fields. Starting offline without previously confirmed connectivity is silent.

Atomic JSON writes use a private temporary file, fsync and replacement. Transitions and their queued notifications are saved together. The durable queue holds at most 100 messages, with exponential retry delay capped at one hour; messages expire after 12 failed attempts or 24 hours. Overflow drops the oldest message and logs the drop. Delivery is best effort with at-least-once retry semantics within these limits: an ambiguous network failure or crash after Telegram accepts a message can duplicate it. No delivery guarantee exists during extended outages.

The update offset is saved with the queued command response after executing the command. Transient Telegram failures do not advance it. A crash after a key event but before saving can repeat that event (including volume/mute); exactly-once effects cannot be guaranteed across ADB and Telegram. A filesystem lock prevents two local instances using the same state path. Do not run the same bot on another machine or under another state path.

## Troubleshooting

- **Web page unavailable:** check `sudo systemctl status tv-hub-web` and `sudo journalctl -u tv-hub-web -n 50 --no-pager`. Verify the phone is on the same LAN, the Pi address/port are correct, and Wi-Fi client isolation/firewall rules allow access.
- **Unauthorized/offline:** unlock the TV, approve the RSA prompt or re-pair with the root identity; confirm `sudo -H adb devices -l`. The controller verifies shell access before selecting an endpoint.
- **Rotating ports:** read the current wireless-debugging connection port or inspect mDNS, then edit the relevant port list and restart.
- **Telegram unavailable/conflicts:** verify token and owner chat ID privately; ensure internet/DNS works, disable any webhook via Telegram's `deleteWebhook` method and stop all other `getUpdates` clients. The controller does not delete webhooks or discard pending updates automatically. It backs off polling failures up to 60 seconds and never logs request URLs or API exception bodies.
- **Internet outage:** ADB monitoring continues between network timeouts; notifications queue within the limits above. Older events may arrive after connectivity returns.
- **Invalid state:** the service refuses unreadable/malformed state instead of silently resetting it. Restore the backup after stopping the service. Never delete state casually: it contains offsets and undelivered notifications.
- **Power detection:** unreachable can mean power, Wi-Fi or debugging failure. Distinguishing mains power needs an external meter/sensor. Telegram is not cellular SMS; both the Pi and phone need internet. Deep-off wake-up may require IR hardware.

## Development and removal

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q tv_controller.py tv_web.py app_activity.py live_view.py tests
bash -n install.sh install-live.sh uninstall.sh
# Optional development-only UI checks (requires Node.js on your dev machine):
node --test tests/*test.cjs
# Optional staging inspection; no apt/systemd operations:
bash install.sh --root /tmp/tv-monitor-stage
```

Tests exercise the real local HTTP server with mocked ADB, plus mocked Telegram, and verify command routing, connection reuse, cross-site request rejection, device isolation, notification-only mode, and state transitions, reconnects, restart migration, authorization, discovery, retries, redaction and installer preservation. They make no live Telegram calls. Hardware authorization, standby ADB availability and Raspberry Pi systemd deployment require on-device validation.

```bash
sudo bash uninstall.sh --keep-data  # explicitly retain state and secrets
# OR, deliberately delete service data and its credential file:
sudo bash uninstall.sh --purge
```

ADB packages and shared root pairing keys are retained in both cases. Legacy Haier files and a custom or migrated `STATE_FILE` outside the default directory are retained even with `--purge` and must be managed separately. See `SECURITY.md` for the root-service tradeoff.
