# Security

Never commit Telegram credentials, environment files, state, or ADB private keys. Revoke any previously exposed token using BotFather. The environment file is root-owned mode 600; the default state directory is mode 700 and atomic state files are mode 600. State contains message text and update offsets and should be treated as private.

Commands and callbacks require the configured positive private-chat ID and matching sender ID. Group and inline-only callback contexts are rejected. Telegram API errors are replaced with fixed messages before logging; token-bearing URLs and raw responses are never logged. Diagnostics do not call Telegram.

ADB provides substantial control over the TV. Use only a trusted LAN and never forward ADB ports to the internet. Legacy port 5555 has different security properties from paired TLS wireless debugging. Disable debugging when it is not needed.

## Service identity

This upgrade intentionally retains `User=root` and `HOME=/root`: the prior service used root, and switching users without inspecting the Pi could abandon its paired ADB identity. The installer verifies an existing unit's declared owner before changing it. Root increases the impact of compromise. Systemd enables NoNewPrivileges, a private temporary directory, a restrictive umask, and ProtectSystem=full; these are partial restrictions, not a full sandbox.

For a dedicated-user migration, stop the service and inspect the live ADB server owner first. Create a restricted service account with a persistent home; copy the relevant `.android` identity (including adbkey and adbkey.pub) privately to that home, set ownership and permissions, and transfer ownership of the state directory. Set the unit User and HOME accordingly. Stop the old ADB server only after checking other ADB users; start ADB as the new identity and verify both TVs are authorized before restarting monitoring. Preserve a backup and re-pair if necessary. Do not run this installer over a non-root unit: it deliberately rejects that situation. No automatic identity migration was attempted or tested.

Only one process/machine may poll each bot token. A local state-file lock cannot prevent consumers on another host. No system can guarantee exactly-once physical key events across crashes; see README delivery tradeoffs.

## LAN web remote

`tv-hub-web.service` is separate from the monitoring process and shares its root ADB identity. It listens on `0.0.0.0:8080` by default. The web app has no account/password: access to the trusted LAN port grants device control. Use a trusted private network and appropriate interface/firewall restrictions; never port-forward this service. Plain HTTP does not encrypt local traffic. A VPN or authenticated HTTPS gateway would require separate configuration before remote use.

The API accepts only predefined device IDs and key actions, without shell interpolation. A random per-process request token, same-origin checks, Host validation and no CORS access protect against ordinary cross-site browser commands and DNS rebinding through arbitrary domain names. These protections are not a substitute for LAN access control. Responses forbid framing, use a restrictive content policy, and contain no Telegram credentials. The web process does not modify monitoring state or issue Telegram requests. Restarting it requires open browser pages to reload to obtain a fresh request token.

## App activity and Live View

App history is private viewing metadata stored in the existing mode-600 state file. It is bounded by APP_HISTORY_LIMIT, displayed through a read-only LAN API, and can be disabled via APP_MONITOR_ENABLED or its device override. Existing LAN remote/history access continues to trust the LAN; this enhancement does not silently add a login to established remote flows.

Every Live View page and `/api/live/` endpoint requires a separate configured HTTP Basic login. Start/stop also require the existing CSRF token, and media reads require a random viewer lease bound to a configured, live-enabled device. Credentials are not included in API responses or logs. Plain HTTP Basic is not encrypted: use a unique credential on a trusted LAN, never public forwarding. Configure an authenticated TLS/VPN gateway separately if access requirements change.

Only known device IDs and fixed capture options are accepted; no client-supplied shell commands, IP addresses, codec options or executable paths are executed. The optional server JAR is version/hash pinned. FFmpeg only repackages video with stream copy. There is no attempt to bypass protected content. Capture sockets, process groups, temporary JARs and ADB forwards belong to an individual session and are cleaned when its last viewer leaves/expires. Background tabs stop their own lease. Viewer/device/time/memory limits bound accidental resource use; these do not establish Pi performance without measurement.
