# Deployment and launcher integration

## Launcher contract

Packaged CLI: `h3-controller deploy` uses the embedded release wheel and integrity
pin. `h3-controller deploy --wheel PATH --sha256 TRUSTED_SHA256` selects an explicitly
verified override (source equivalent: `python -m h3server deploy ...`). Optional
arguments: `--state-dir PATH`, `--port PORT` (default ephemeral loopback), and
`--no-browser` (print the one-time local launch URL). Password arguments are not
accepted. `scripts/deploy.sh` and `scripts/deploy.ps1` dispatch to this command;
`H3_CONTROLLER` may select the native executable.

The launcher constructs `ReleaseBundle(wheel_path, trusted_sha256)` and supplies
`release=` to `controller.app.create_app`. Native builds embed a **separately built
pure-Python release wheel**, not a checkout/archive, using package resources with
an `importlib.resources.as_file` context alive through shutdown. No cwd/repository
path lookup occurs. `scripts/build_controller.py` validates its contents and binds
the embedded digest; `scripts/smoke_controller.py` independently verifies executable
payload bytes and real loopback behavior. Authenticate the executable/checksum
through a trusted release channel or explicit operator verification. Computing a
digest from an arbitrary wheel is not provenance authentication.

The remote wheel includes `h3server/controller/target.py`, installer/recovery,
remote daemon/worker, backend/downloader and these package resources:

- `h3server/web/{index.html,app.js,styles.css}`
- `h3server/backends/manifest.json`
- `h3server/backends/LICENSE.h3.txt` (complete pinned license and Exhibit A,
  SHA256 checked against the manifest)

Release validation permits only application package and wheel metadata entries,
rejecting symlinks, traversal, credentials/dev/test/cache/node/venv artifacts.
Native packaging supplies controller dependencies. Windows private trust storage
uses native protected owner-only ACLs through pywin32; unsupported security
filesystems fail closed. Native Windows ACL/reparse tests are included in Windows
CI. Their existence and POSIX test results are not Windows certification.

## Browser/controller flow

Bootstrap the exact loopback origin with its single-use fragment token; all
controller API routes retain cookie, exact Host/Origin, CSRF and body limits.
SSH passwords are memory-only, never persisted. The UI's inline authentication
notice discloses target/session-bound reuse for setup, sudo and reconnection and
submits `reuse_for_setup:true`. API consent defaults remain false. Completion,
failure, cancellation, disconnect/target change, session expiry and shutdown clear
retained credentials. Python cannot reliably zeroize strings; bounded in-flight
IO may hold references until cancellation completes. A restarted controller
requires authentication again. See [the composite contract](one-click-controller.md)
for exact lifecycle, request IDs, cancellation and conservative recovery limits.

1. Connect, explicitly verify the host fingerprint, authenticate, and scan.
2. Select an experimental model in step04. Details change with the selection:
   exact selected bytes, GPU guidance, unknown memory fit, auxiliary components,
   full pinned license and provenance. Selection alone starts nothing; step03's
   comparison and the separate approval controls are removed, not CSS-hidden.
3. **Install** authorizes the disclosed source-owned composite workflow. It stages
   the verified wheel/private immutable configuration, inspects prerequisites,
   internally binds exact plan/service/artifact/license digests, installs required
   OS/runtime/user-service prerequisites, performs bounded replans, reboots if
   required, reconnects with normal host verification, activates, and downloads
   only the selected artifacts. The SSH password is also used for fixed sudo
   operations via stdin, never argv. No arbitrary command or package input exists.
4. Observe real phases/checkpoints/events; no timer-derived percentage. Reload
   restores controller-memory progress; restart requires authentication again.
   The target's durable bootstrap/launch markers prevent ambiguous privileged
   replay. Unknown outcomes stop for inspection, not repeated Install clicks.
5. **Cancel setup** stops subsequent actions and requests cancellation only of
   this setup's owned download. It is not rollback or a guarantee detached remote
   work stopped offline. Wait for `busy:false` before target changes or retry.
   Sudo/password-policy rejection is explicit, not a second normal password form.
6. Refresh the dashboard and inspect actual diagnostics, then submit source-valid
   generation settings and inspect/cancel jobs or preview/download completed output.

Advanced low-level plan/install/reboot/activation/download routes remain available
with their original independent explicit authorization. Their approvals are not
removed globally. Composite activation always rejects existing busy jobs.

`setup/events?after=N` returns durable setup snapshots plus exclusive-cursor event
pages and retention resets. `remote/events?after=N` proxies finite SSE replay pages;
the generation UI polls for additional real events. Setup uses `/setup/status`
(or the `setup` document in `/status`) with real checkpoint events, never synthetic progress.
Generation/media requests use SSH `direct-tcpip` to remote `127.0.0.1:8765`, without
a public listener or even a local forwarding port. The controller reads the
private daemon token via SFTP and injects it server-side only. Browser URLs/logs
never receive it. Exact routes and Range headers are allowlisted. Paramiko stream
ownership is retained through the complete HTTP response and bounded by deadlines.

## Updates and recovery

Wheel paths are content-addressed and never overwritten. Environments are
plan-digest-versioned. `daemon.json` is immutable and bound into the approved plan;
`runtime-<plan-digest>.json` records the selected environment and resolved-dependency
fingerprint. The existing installer resolves the manifest's candidate dependencies
on target and records pip resolution/freeze. **This is not a portable transitive
artifact/hash lock or a certified runtime.**

Updates preserve existing services during candidate installation. OS/driver changes
in an update are rejected with an explicit requirement for a separate administrator
maintenance window; rescan afterward. The controller checks the existing release
marker over SFTP before either prerequisite or environment installation, including
when target Python is unavailable. Existing compatible database schemas only
are supported, not guessed/irreversible migrations.

Activation owns an exclusive deployment/installer lock and persists its journal.
The authenticated daemon maintenance gate serializes with submission: reject if
busy, reject new submissions and drain (bounded wait), or cancel accepted jobs only
with separate explicit confirmation. Gate ownership persists across daemon restarts,
without a lease that might reopen submissions during a switch.

After quiescence: stop the user service, back up SQLite, atomically switch `current`,
register/restart the candidate unit, and check authenticated daemon health. Failure
restores the previous release, environment-specific unit and compatible database
backup; models are untouched. When there is no previous release, rollback disables
the candidate's user-service autostart before declaring rollback complete. A newly
approved activation restores registration; removing `current` alone is insufficient
because the daemon unit references its environment directly. Disabling an externally
linked unit also removes its registration: no-previous-release retry/repair treats
failed stop/disable as a no-op only after a successful user-manager query confirms
`LoadState=not-found` and `ActiveState=inactive`. Bus, authorization, loaded-unit
and unknown-status failures remain errors. A lost activation process does not
mean completion.
Use **Approve interrupted activation recovery**: before switching it releases only
its own gate (never kills draining jobs); during switching it rolls back; after a
durable healthy commit it finalizes the gate without restoring backups over newly
accepted jobs. The same commit ordering protects rollback recovery. A new activation
cannot overwrite an unresolved journal.

## Validation boundaries

Environment installation ready, authenticated daemon healthy, verified model files,
model loaded, and a real completed video are separate claims. Automated tests use
real applications/stores/filesystems with mocked SSH/runtime IO. Browser fixtures
are visibly TEST ONLY, including their non-video output bytes. They do not certify
native Linux systemd/reboot/privilege handling, Windows controller ACLs, GPU/offload/
LoRA compatibility, the resolved inference environment, or real generated MP4s.
Those require separately approved native/target acceptance; no weights or server
credentials are accessed during these tests.
