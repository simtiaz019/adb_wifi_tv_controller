# Codex prompt — Haier TV Telegram controller

You are a senior Python/Linux/Android ADB engineer. Inspect this repository before editing. Refactor and harden an existing Raspberry Pi Telegram TV monitor/controller **without losing established behavior or stored state**. Produce working, tested code, docs, upgrade path, and complete installation instructions. Do not invent successful on-device tests.

## System and current behavior

- Existing Python script: `haier_tv_bot.py`, with stdlib HTTP Telegram Bot API calls and subprocess ADB commands.
- Installed on Raspberry Pi `adguard-home` via `haier-tv-monitor.service`; env file `/etc/haier-tv-monitor.env`, persisted state `/var/lib/haier-tv-monitor/state.json`.
- Actual TV screenshot: `Haier MatrixTV CE`, IP `192.168.1.243`, wireless debugging connection port `36683` (this can rotate); previously successful `192.168.1.243:5555` ADB port. Temporary pairing-code port is different.
- Verified TV outputs: `mWakefulness=Awake` when ON and `mWakefulness=Asleep` when standby. Do not interpret no ADB response as OFF.
- Telegram commands: `/menu`, `/status`, `/on`, `/off`, `/volup`, `/voldown`, `/mute`; in-chat inline callback menu; command authorization tied to configured private chat ID; key events `KEYCODE_WAKEUP`, `KEYCODE_SLEEP`, `KEYCODE_VOLUME_UP`, `KEYCODE_VOLUME_DOWN`, `KEYCODE_VOLUME_MUTE`.
- Alerts: ON/OFF transitions only after 2 matching polls, with no baseline/startup power alert; unreachable after 3 failed polls following confirmed connectivity; one reconnection alert; avoid repeats. ADB cannot identify whether disconnect is mains power, Wi-Fi or debugging failure.

## Required implementation

1. **External configuration**: `TV_NAME`, `TV_IP`, `TV_ADB_PORTS` (comma-separated), `POLL_SECONDS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `STATE_FILE` loaded from systemd EnvironmentFile or environment. Use `TV_NAME` everywhere a user-visible TV name occurs (including menus, status, transition/unreachable/recovery messages). Never hard-code addresses/names in core logic. Validate IP, port range, interval, token/chat settings at startup and fail with a useful **redacted** error. Provide `config.env.example`, and document port rotation and mDNS discovery. No real token in repo.
2. **Monitoring**: explicit state machine (ON/OFF/UNKNOWN/UNREACHABLE); two matching successful readings to confirm state changes, 3 consecutive connection failures to confirm unreachable; prevent duplicate alerts and startup alerts. Persist confirmed state and Telegram offset atomically; recover across restarts without spurious notifications. Log observations without leaking secrets. Test intermittent failures and reconnection with same vs changed state.
3. **ADB**: prefer a working existing connection, try configured ports, optionally use correctly parsed `adb mdns services`. Never confuse pairing with connection. Respect subprocess timeouts, handle unauthorized/offline devices, avoid stale endpoints. Add an optional diagnostics CLI (`--diagnose`) that prints no secrets. Do not assume ADB wake-up works when unreachable.
4. **Telegram**: private-chat authorization on both normal messages and callback queries, safe callback acknowledgements, parse `/command@botname` and optional plain text safely, usable inline keyboard, useful success/failure responses. Harden polling and request timeouts. Avoid Telegram update conflicts, make reconnect behavior predictable, and avoid losing commands across transient Telegram errors. Implement bounded durable outbound notification retries (avoid duplicate floods, document at-least-once tradeoffs); no notification spam.
5. **Installer**: idempotent `install.sh`; preserve existing `/etc/haier-tv-monitor.env` and state JSON, never append duplicate config keys or overwrite configured ports. Confirm service ownership and secure token file permissions. Support clean upgrade and systemd restart; add `uninstall.sh` with opt-in retention of state and secrets. Prefer dedicated restricted service user with persistent ADB keys; carefully migrate pairing context, else document why root remains.
6. **Tests**: use stdlib unittest or pytest, mock `adb` and Telegram; cover state transitions, baseline, 2 confirmations, 3 failures, reconnect, service restart, port discovery, callbacks, invalid config, credential redaction, and installer config preservation. Do not call real Telegram during automated tests.
7. **Docs**: update README, `SECURITY.md`, `CHANGELOG.md`, architecture/control flow, troubleshooting (ADB auth, changing ports, Telegram getUpdates/webhooks, internet outage). Explain clearly that Telegram ≠ cellular SMS and power-vs-Wi-Fi needs external hardware. Provide sample commands to verify from Raspberry Pi.

## Acceptance criteria

- `TV_NAME="Living Room TV"`, `TV_IP=192.168.1.250`, and `TV_ADB_PORTS=40123,5555` in env change all relevant behavior after systemd restart, without source edits.
- `/menu`, `/status`, `/on`, `/off`, `/volup`, `/voldown`, `/mute` respond only to configured owner; button and text actions both work with mocked Telegram and ADB.
- Alerts send only on meaningful transitions, and unreachable/reconnected once per incident; no 15-second spam.
- Mains cutoff or Wi-Fi outage produces an *unreachable* alert, never an unsupported assertion of power failure.
- Existing state and env are retained on upgrade; automated tests pass, `python3 -m compileall` passes, installation instructions are reproducible.

First provide a concise summary of issues in the existing implementation, then implement changes, run tests, and report modified files, tests executed, remaining hardware-dependent limitations and exact upgrade commands.
