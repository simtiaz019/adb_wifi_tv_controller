# Security

Never commit Telegram credentials, environment files, state, or ADB private keys. Revoke any previously exposed token using BotFather. The environment file is root-owned mode 600; the default state directory is mode 700 and atomic state files are mode 600. State contains message text and update offsets and should be treated as private.

Commands and callbacks require the configured positive private-chat ID and matching sender ID. Group and inline-only callback contexts are rejected. Telegram API errors are replaced with fixed messages before logging; token-bearing URLs and raw responses are never logged. Diagnostics do not call Telegram.

ADB provides substantial control over the TV. Use only a trusted LAN and never forward ADB ports to the internet. Legacy port 5555 has different security properties from paired TLS wireless debugging. Disable debugging when it is not needed.

## Service identity

This upgrade intentionally retains `User=root` and `HOME=/root`: the prior service used root, and switching users without inspecting the Pi could abandon its paired ADB identity. The installer verifies an existing unit's declared owner before changing it. Root increases the impact of compromise. Systemd enables NoNewPrivileges, a private temporary directory, a restrictive umask, and ProtectSystem=full; these are partial restrictions, not a full sandbox.

For a dedicated-user migration, stop the service and inspect the live ADB server owner first. Create a restricted service account with a persistent home; copy the relevant `.android` identity (including adbkey and adbkey.pub) privately to that home, set ownership and permissions, and transfer ownership of the state directory. Set the unit User and HOME accordingly. Stop the old ADB server only after checking other ADB users; start ADB as the new identity and verify both TVs are authorized before restarting monitoring. Preserve a backup and re-pair if necessary. Do not run this installer over a non-root unit: it deliberately rejects that situation. No automatic identity migration was attempted or tested.

Only one process/machine may poll each bot token. A local state-file lock cannot prevent consumers on another host. No system can guarantee exactly-once physical key events across crashes; see README delivery tradeoffs.
