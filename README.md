# Telegram TV monitor and remote

A Python standard-library service for a Raspberry Pi that monitors and controls two Android TVs over ADB using one Telegram bot. The repository was extracted from `haier_tv_codex_ready.zip` and implements the brief in `CODEX_PROMPT.md`.

The example configuration includes:

| ID | Display name | Address | Connection ports |
| --- | --- | --- | --- |
| `main` | Haier MatrixTV CE | 192.168.1.243 | 36683, 5555 |
| `second` | Mi Box 3 | 192.168.1.149 | 5555 |

Addresses and names are configuration only. Port 36683 was observed previously and may have rotated. Neither TV has been tested from this development machine.

## Install or upgrade on the Raspberry Pi

Requires Debian/Raspberry Pi OS, Python 3.9+, systemd, internet access and a trusted LAN. Copy this repository to the Pi, then run from its directory:

```bash
# On upgrades, make private backups first:
sudo install -d -m 700 /root/haier-tv-backup
sudo cp -a /etc/haier-tv-monitor.env /root/haier-tv-backup/  # if installed
sudo systemctl stop haier-tv-monitor                      # if installed
sudo cp -a /var/lib/haier-tv-monitor/state.json /root/haier-tv-backup/  # if present

sudo bash install.sh
sudo nano /etc/haier-tv-monitor.env
sudo chmod 600 /etc/haier-tv-monitor.env
sudo systemctl restart haier-tv-monitor
sudo systemctl status haier-tv-monitor --no-pager
sudo journalctl -u haier-tv-monitor -n 50 --no-pager
```

Fresh installs create a configuration template and wait for credentials to be entered. Upgrades preserve the environment file byte-for-byte and preserve state; they restart the service. Existing non-root service ownership is rejected before installation to avoid silently switching ADB identities. The installer never appends settings or replaces configured ports.

For an existing installation, add the following keys **once**, editing existing entries if already present:

```ini
TV2_NAME="Mi Box 3"
TV2_IP=192.168.1.149
TV2_ADB_PORTS=5555
```

Check that `TV_NAME`, `TV_IP`, and `TV_ADB_PORTS` also exist: old installations may have relied on hard-coded defaults. Use `config.env.example` as the reference, keeping your existing ports and credentials. Restart after changes. Remove all `TV2_` keys to disable the second TV.

Configuration is loaded from the process environment; systemd reads `/etc/haier-tv-monitor.env`. Running Python directly does not read that file automatically. Names, valid literal IP addresses and comma-separated ports (1–65535) are required for each configured TV. `POLL_SECONDS` defaults to 15 and must be at least 5; `STATE_FILE` defaults to `/var/lib/haier-tv-monitor/state.json` and must be absolute. The bot token must have Telegram token syntax and the chat ID must be a positive private-chat ID. Validation cannot verify credentials remotely.

Set the credentials locally, never in Git. Start a private conversation with the bot via `/start` before using it.

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
  -p EnvironmentFile=/etc/haier-tv-monitor.env -p Environment=HOME=/root \
  /usr/bin/python3 /opt/haier-tv-monitor/haier_tv_bot.py --diagnose
```

Diagnostics contact ADB, but do not contact Telegram or write monitoring state.

## Telegram commands

Send `/home` (or `/menu` or `/start`) to choose **Haier MatrixTV CE** or **Mi Box 3**. Tap a device to see only its remote controls. The panel includes **Switch TV** to return to the picker. Selecting a device sends no ADB key event.

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

## Architecture and delivery behavior

One process owns both TVs and is the only Telegram `getUpdates` consumer. Each cycle checks due TVs, polls commands and attempts one queued outgoing message. Individual ADB calls time out after six seconds and Telegram calls after ten seconds. Checks are sequential: an unreachable TV can delay the other TV and remote commands by multiple ADB timeouts. `POLL_SECONDS` is a minimum spacing after each completed TV observation, not a strict real-time guarantee.

Each TV has independent confirmed power, candidate reading, consecutive count, connectivity and incident state:

- `Awake` maps to ON; `Asleep` maps to OFF/standby. A successful but unrecognized power response maps to UNKNOWN and proves connectivity.
- Two consecutive matching ON/OFF observations establish or change confirmed power. A failure or UNKNOWN resets the candidate. The first baseline is silent.
- Three consecutive transport failures after a successful connection confirm UNREACHABLE and produce one alert. Further failures are silent. First successful communication produces one recovery alert; a changed power state needs two matching readings separately.
- Confirmed power and incident flags survive restarts; candidate/failure counters restart at zero. Legacy `tv`, `connectivity`, and `offset` fields are migrated without deleting original fields. Starting offline without previously confirmed connectivity is silent.

Atomic JSON writes use a private temporary file, fsync and replacement. Transitions and their queued notifications are saved together. The durable queue holds at most 100 messages, with exponential retry delay capped at one hour; messages expire after 12 failed attempts or 24 hours. Overflow drops the oldest message and logs the drop. Delivery is best effort with at-least-once retry semantics within these limits: an ambiguous network failure or crash after Telegram accepts a message can duplicate it. No delivery guarantee exists during extended outages.

The update offset is saved with the queued command response after executing the command. Transient Telegram failures do not advance it. A crash after a key event but before saving can repeat that event (including volume/mute); exactly-once effects cannot be guaranteed across ADB and Telegram. A filesystem lock prevents two local instances using the same state path. Do not run the same bot on another machine or under another state path.

## Troubleshooting

- **Unauthorized/offline:** unlock the TV, approve the RSA prompt or re-pair with the root identity; confirm `sudo -H adb devices -l`. The controller verifies shell access before selecting an endpoint.
- **Rotating ports:** read the current wireless-debugging connection port or inspect mDNS, then edit the relevant port list and restart.
- **Telegram unavailable/conflicts:** verify token and owner chat ID privately; ensure internet/DNS works, disable any webhook via Telegram's `deleteWebhook` method and stop all other `getUpdates` clients. The controller does not delete webhooks or discard pending updates automatically. It backs off polling failures up to 60 seconds and never logs request URLs or API exception bodies.
- **Internet outage:** ADB monitoring continues between network timeouts; notifications queue within the limits above. Older events may arrive after connectivity returns.
- **Invalid state:** the service refuses unreadable/malformed state instead of silently resetting it. Restore the backup after stopping the service. Never delete state casually: it contains offsets and undelivered notifications.
- **Power detection:** unreachable can mean power, Wi-Fi or debugging failure. Distinguishing mains power needs an external meter/sensor. Telegram is not cellular SMS; both the Pi and phone need internet. Deep-off wake-up may require IR hardware.

## Development and removal

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q haier_tv_bot.py tests
bash -n install.sh uninstall.sh
# Optional staging inspection; no apt/systemd operations:
bash install.sh --root /tmp/tv-monitor-stage
```

Tests mock ADB and Telegram and verify state transitions, reconnects, restart migration, authorization, discovery, retries, redaction and installer preservation. They make no live Telegram calls. Hardware authorization, standby ADB availability and Raspberry Pi systemd deployment require on-device validation.

```bash
sudo bash uninstall.sh --keep-data  # explicitly retain state and secrets
# OR, deliberately delete service data and its credential file:
sudo bash uninstall.sh --purge
```

ADB packages and shared root pairing keys are retained in both cases. A custom `STATE_FILE` outside the default directory must be managed separately. See `SECURITY.md` for the root-service tradeoff.
