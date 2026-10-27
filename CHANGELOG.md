# Changelog

## 2026-10-27

- Reworked the mobile web remote into one viewport-sized screen with a top device dropdown, persistent controls, and compact landscape layout. Device changes still verify connectivity; offline controls stay disabled.

- Renamed the app to TV Hub, with `tv_controller.py`, `tv-hub-monitor`/`tv-hub-web` services and generic installation paths.
- Added direct migration from the original Haier package, preserving configuration, state and ADB keys while retiring legacy pollers.
- Added numbered device groups (`TV3_*`, `TV4_*`, …) that automatically populate both remotes and independent monitoring.

- Added a mobile-friendly LAN web remote on port 8080 with device selection, live availability checks, direct cached ADB commands, and response-time feedback.
- Separated web control into its own systemd service while retaining Telegram controls and notifications by default. Both control interfaces remain available.
- Added HTTP validation, cross-site request protection, per-device command locks, and installer/uninstaller support for both services.

- Added alerts for confirmed UNKNOWN power-state transitions and verified independent outage/recovery alerts for both devices without selecting a remote. Documented shared power/internet outage limits.

- Check live ADB connectivity before opening a selected device remote; unreachable devices show an offline message and return to the picker with selection cleared.

- Added a device-first Telegram flow: /home, /menu and /start show the TV picker, selection opens a dedicated remote, and Switch TV returns to the list. Selection persists across restarts.

- Identified the second device as Mi Box 3 and added Android D-pad, OK, Home, Back and play/pause controls for both devices, preserving legacy volume aliases.

- Extracted the supplied archive into the repository root and retained the implementation brief.
- Added a second configurable TV, default example endpoint 192.168.1.149:5555, with named Telegram buttons and explicit TV selectors.
- Replaced import-time globals with validated configuration and testable ADB, Telegram, persistence and state-machine components.
- Fixed connectivity initialization, UNKNOWN handling, callback authorization and token-bearing exception logging.
- Added atomic durable notification retries, update offsets, process locking, legacy-state migration and diagnostics.
- Added repeatable installation with config/state preservation, staged installer verification and explicit uninstall retention options.
- Documented upgrade commands, pairing identity, notification/crash tradeoffs and remaining on-device checks.
- Added mocked standard-library tests; no live hardware or Telegram success is claimed.
