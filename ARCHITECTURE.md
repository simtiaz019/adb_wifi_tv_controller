# Architecture

TV Hub retains its two Python standard-library services, existing routes and buildless frontend. No new Telegram bot/poller, database, framework, cloud service or continuously running capture is introduced.

## Foreground observation

`tv_controller.py` remains the sole writer of the atomic JSON state and durable outbox. Existing power/connectivity observation and command behavior remain unchanged. `app_activity.py` adds pure parsing, state transition and message-formatting functions.

After a successful awake (`ON`) observation, `activity_tick()` schedules a separate ADB foreground read at the configured interval. A bounded two-worker pool uses separate ADB client objects; at most two jobs are outstanding. The monitoring thread never waits for `dumpsys activity/window` to complete. Existing sequential power/Telegram calls can still delay scheduling/result collection, especially with many unreachable devices.

Detection prefers `topResumedActivity`, then `mResumedActivity`, and falls back to `mCurrentFocus`/`mFocusedApp` from `dumpsys window windows`. Only explicit package/activity components count. Ambiguous records do not select an arbitrary package. Background processes and media sessions are never used as foreground proof.

The main thread collects results, confirms two consecutive identical packages by default, and atomically commits a confirmed change, its history record and queued Telegram message. Baselines are silent. Activity-to-activity movement within one package is not a transition. Failures reset the candidate without erasing the confirmed package. OFF, UNKNOWN and transport failures pause app sampling and reset candidates. A generation counter discards jobs that finish after a standby/disconnect observation. Restart resets candidates and keeps confirmed packages; the same package produces no repeated alert.

## Persistence and delivery

The existing JSON file gains additive `apps` and `app_history` keys. Existing keys, legacy state migration and update offsets are retained. There is no destructive migration. History records contain an ID, device ID/name, previous/new package and friendly labels, timezone-aware timestamp, and notification status.

App outbox items link to history by `event_id`. Status progresses through `pending`, `retrying`, `sent`, `expired` or `dropped`; disabled notifications are recorded as `disabled`. History is bounded (default 500 total transitions). Queue limits and at-least-once delivery semantics are unchanged: a network ambiguity or crash after Telegram accepts a message can duplicate delivery even though one logical transition is recorded. Exactly-once Telegram delivery cannot be promised. Application observations missed during an outage cannot be reconstructed.

The web service reads atomic snapshots without modifying them. `/api/activity/<device-id>` exposes only the confirmed package/name, observation timestamp, availability and the last 50 transitions for that device. It never exposes the outbox or Telegram credentials. Availability becomes false when observations are paused/stale. A confirmed value remains visible as last known; a candidate app is not presented as confirmed.

## Live View pipeline

1. The existing remote's Open Live button navigates to `/live/<device-id>`. HTTP Basic protects this page and every `/api/live/…` endpoint. Existing LAN remote APIs keep their previous access model.
2. Start Stream authenticates the request and validates the existing CSRF header, configured device and per-device opt-in. The manager reserves one capture slot and returns a random viewer lease.
3. A background worker discovers the configured ADB endpoint, pushes the checksum-verified **scrcpy-server 5.0.1** JAR, allocates its own ADB forward and starts the server through `app_process` with screen capture only: no audio, no controls, no forced power-on.
4. The bridge uses scrcpy's documented `raw_stream=true` mode. H.264 is encoded **on the Android device**. FFmpeg receives those bytes and uses `-c:v copy` to mux fragmented MP4; it does not decode, filter or re-encode them.
5. Keyframe-aligned MP4 fragments are retained in a four-fragment memory ring, with a 4 MiB per-box bound. Authenticated HTTP requests fetch one fragment at a time; idle reads long-poll for at most three seconds. A new viewer receives the initialization segment and latest complete fragment, not the entire stream history.
6. Browser Media Source Extensions decode H.264 locally. The frontend selects the codec string from the stream's AVC configuration, trims old buffered data, catches up when lagging, and checks actual video advancement. It reports black frames as a possible capture/protection/dark-scene issue rather than claiming the picture works merely because ADB connected.

This is an actual encoded-video transport, not desktop GUI embedding or screenshot polling. HTTP fragment delivery is used instead of adding a WebSocket framework. Expect roughly seconds of buffering (typically one keyframe interval plus browser/mux buffering), not scrcpy desktop's tens-of-milliseconds target. This is separate from the low-latency remote buttons.

Raw mode does not carry scrcpy presentation timestamps: the muxer assigns the configured input frame rate, and the encoder is asked to repeat static frames and emit keyframes every second. Firmware may ignore those requests, causing pacing, delay or stale-frame errors. Reconnect starts a new codec session. Device/browser testing is required; no automatic transcoding fallback is enabled.

## Lifecycle and limits

There is one capture worker per device, shared by all viewer leases, with one device active by default. Limits are enforced under a lock. A second device is rejected until the first releases its slot; multiple viewers of one device share its encoder/muxer. Stopping one viewer leaves the others running.

Stop Stream, page navigation and document visibility changes release the viewer with a keepalive request; missed disconnects expire after the configured idle timeout. Media requests renew the lease. When no leases remain, the worker closes its video socket, terminates its own scrcpy/FFmpeg process groups, removes its own ADB forward and temporary device JAR. No global `adb kill-server`, unrelated process kill or device-wide reconnect is used. Starting/missing-packet deadlines and bounded subprocess timeouts prevent indefinite capture startup. The OS/systemd may need to finish cleanup after abrupt power loss.

The web unit bounds the entire web/capture process group to 50% of one CPU, 192 MiB and 64 tasks. Capture children run at lower scheduling priority. Monitoring and AdGuard remain separate services. These are protective limits, not measured proof of DNS performance on a Pi 3B. Increase them only after measuring. If the web cgroup exceeds memory limits, web/Live View can restart; the monitoring service remains separate.

## Routes

| Route | Purpose/access |
| --- | --- |
| `GET /`, `/api/devices`; `POST /api/command` | Existing LAN remote; unchanged host/origin/CSRF rules. |
| `GET /api/activity/<id>` | Read-only confirmed app/recent history under existing LAN access. |
| `GET /live/<id>` | Authenticated Live View page. |
| `GET /api/live/<id>/info` | Authenticated device/configuration availability, app data and CSRF token. |
| `POST /api/live/<id>/start` | Auth + CSRF; create/share capture and issue a viewer lease. |
| `GET /api/live/<id>/media?viewer=…&after=N` | Auth + matching live viewer lease; fMP4 bytes with sequence/codec headers, 204 while waiting. |
| `POST /api/live/<id>/stop` | Auth + CSRF + viewer lease; release this viewer only. |

Requests cannot supply shell commands, serials, encoder arguments, paths or arbitrary hostnames. Device IDs must exist in the current configuration. API output, request logging and failure messages exclude credentials.
