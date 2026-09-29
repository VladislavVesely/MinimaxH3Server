# Deployment and launcher integration

## Launcher contract (Task12)

Packaged CLI: `h3-controller deploy --wheel PATH --sha256 TRUSTED_SHA256`
(source equivalent: `python -m h3server.controller.deploy deploy ...`). Optional
arguments: `--state-dir PATH`, `--port PORT` (default ephemeral loopback), and
`--no-browser` (print the one-time local launch URL). Password arguments are not
accepted. `scripts/deploy.sh` and `scripts/deploy.ps1` dispatch to this command;
`H3_CONTROLLER` may select the native executable.

A packaged launcher can dispatch to `h3server.controller.deploy.main(argv)` or
construct `ReleaseBundle(wheel_path, trusted_sha256)` and supply `release=` to
`controller.app.create_app`. Bundle a **separately built pure-Python release
wheel**, not a checkout/archive, alongside the native controller. Resolve it with
package resources (keep an `importlib.resources.as_file` context alive while
starting), not cwd/repository paths. Authenticate the expected SHA256 through the
release channel or explicit operator verification. Computing a digest from an
arbitrary wheel is not provenance authentication.

The remote wheel includes `h3server/controller/target.py`, installer/recovery,
remote daemon/worker, backend/downloader and these package resources:

- `h3server/web/{index.html,app.js,styles.css}`
- `h3server/backends/manifest.json`
- `h3server/backends/LICENSE.h3.txt` (complete pinned license and Exhibit A,
  SHA256 checked against the manifest)

Release validation permits only application package and wheel metadata entries,
rejecting symlinks, traversal, credentials/dev/test/cache/node/venv artifacts.
Native packaging must still supply the controller's dependencies and implement
Windows native private trust-store ACLs. Current POSIX trust tests are not Windows
certification; do not remove the fail-closed boundary as a packaging workaround.

## Browser/controller flow

Bootstrap the exact loopback origin with its single-use fragment token; all
controller API routes retain cookie, exact Host/Origin, CSRF and body limits.
SSH passwords and sudo credentials are request-scoped, never persisted. Closing
or losing SSH requires fresh authentication; there is no promise of retained
passwords or automatic unattended controller reconnection.

1. Connect, explicitly approve the fingerprint, authenticate, and scan.
2. Select an experimental profile and **Stage release & review plan**. This
   explicitly transfers a verified wheel and immutable versioned `daemon.json`
   over SFTP, using private directories/files and refusing path aliases. It
   inspects prerequisite changes without executing them.
3. Review the complete license/eligibility restrictions, installation plan and
   separate service-registration document. `setup/install` requires matching
   plan/service digests and explicit attestations/system consent. Sudo is stdin
   only. General installation approval does not authorize reboot or downloads.
4. Python/selector prerequisite rounds use the fixed SSH transaction guardian.
   Their durable status is readable over SFTP even before Python exists. Successful
   rounds automatically inspect again and request fresh approvals in the same
   wizard. Restore also replans a completed prerequisite round. Failed prerequisite
   rounds need renewed approval/credentials, not a Python resume command.
5. Environment work is detached through the existing recovery/user-systemd
   contract. Restore/replay after reconnect uses remote checkpoints; reboot is
   separately digest-approved and requires the durable waiting-for-reboot state.
6. Activate only after environment verification, then separately approve model
   acquisition. Model downloads emit real byte/file progress, resume pinned partial
   files, and support cooperative cancellation without claiming completion.
7. Refresh the dashboard, review actual diagnostics, submit source-valid settings,
   and inspect/cancel queued jobs or preview/download completed output.

`setup/events?after=N` returns durable setup snapshots plus exclusive-cursor event
pages and retention resets. `remote/events?after=N` proxies finite SSE replay pages;
the UI polls for additional real events, never advances synthetic progress.
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
