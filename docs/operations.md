# Operations and acceptance

## Local controller

Run the native executable; default is the deployment wizard. It reserves one IPv4
loopback socket and hands **that socket** to the real ASGI server (no close/rebind
race). Browser opening occurs after startup. `--no-browser` prints the one-use
fragment URL to the local console. Bootstrap tokens are one-use and expire after
120 seconds, but authenticated sessions have no application timeout. The access
key and trusted-browser credentials persist across restarts. Ctrl+C shuts down the
local server and closes SSH without revoking browser access. Remote detached work
is independent. `--reset-access-key` deliberately rotates the key and revokes old
cookies/remembered grants. Clearing browser data also requires unlocking again.
If a browser cannot be opened, use the printed URL; never publish the token.

State defaults (`--state-dir` overrides):

- macOS: `~/Library/Application Support/H3Server`
- Linux: `$XDG_STATE_HOME/h3server` or `~/.local/state/h3server`
- Windows: `%LOCALAPPDATA%\H3Server`

The dedicated `known_hosts` trust store and owner-only `local-access.json` browser
credentials are persisted locally. LAN mode exports the stable key to
`lan-access.txt`. SSH passwords are saved only with the existing explicit opt-in
preferences; browser storage retains only an opaque remembered-access grant.
POSIX checks ownership, regular-file type/link count and 0700/0600 permissions.
Windows uses pywin32 security descriptors: current owner only, protected DACLs,
no inherited group grants, no reparse/junction traversal or hardlinked trust files.
Filesystems without working security descriptors fail closed. Do not place trust
state on network shares or symlink/junction paths. Administrator/root compromise
is outside this boundary. Changed keys need deliberate out-of-band investigation;
there is no automatic trust reset. Back up trust state privately, never credentials.
SSH password auth is implemented; agent/private-key login is not. SSH/SFTP and
`direct-tcpip` forwarding to remote loopback must be permitted by server policy.

## Remote ownership and files

All application-owned files live under the SSH user's `~/MinimaxH3Server`:

```
releases/<sha256-id>/       immutable application wheel/config/runtime record
current                    atomically switched release symlink
environments/<plan-digest>/ versioned Python environment/readiness/resolution
bootstrap/                 digest-bound plans, approvals, recovery checkpoints
bin/ config/ state/        service units, owner-only token, SQLite/jobs/events
models/ loras/             verified pinned model snapshot and admitted adapters
cache/{huggingface,torch,pip,cuda,triton}/
logs/ tmp/{downloads,jobs}/
```

Do not edit immutable plans/releases or copy credentials into this tree. Cache and
TMP overrides apply to application processes, not the account globally. OS packages,
NVIDIA drivers, systemd registration/lingering and OS journal entries necessarily
live outside the tree and require explicit approval. Downloaded model code never
runs with sudo. No public inbound API ports are required: daemon `127.0.0.1:8765`
is reached through the authenticated SSH channel. Do not expose it using a reverse
proxy or public bind; bearer auth alone is not an approved deployment topology.

## Progress, storage and retention

Setup checkpoints and replayable events live in SQLite. Live downloads report
actual bytes/files, verify full size/digest before promotion, resume pinned partials
and support cooperative cancellation. Interrupted bytes are not completed files.
Generation separates load/encode, measured denoising, decode and MP4 mux/export;
indeterminate stages do not fabricate percentages. A disconnect does not cancel a
job. Cancellation terminates the isolated worker process group and removes partial
output; queued work and daemon-restart interruptions are explicitly reconciled.

Default worker policy: one running job, eight pending jobs, 4 GiB maximum output,
1 GiB free-space reserve (admission also reserves the maximum output), six-hour
runtime limit, seven-day retention, 20 GiB retained output budget. Outputs under
`tmp/jobs/<opaque-id>/` may expire; active jobs are protected. Download/preview uses
validated records, single byte ranges and a 410 response for expired outputs. Save
important MP4s elsewhere. The remote `python -m h3server.remote --config PATH`
accepts an owner-only JSON with `root`, `port`, `queue_limit`, `max_output_bytes`,
`min_free_bytes`, `retention_seconds`, `retained_bytes`, `max_runtime_seconds`.
Packaged deployment currently approves fixed root/8765 configuration: do not edit
its immutable `daemon.json` in place. Alternate limits require an administrator's
separately managed service/config, not an unadvertised browser setting.

## GPU memory settings

Open **Settings → GPU memory settings**. Policy belongs to the generation server,
not the browser/project, and persists in owner-only `config/runtime-memory.json`.
Edit a draft, then explicitly **Apply memory settings** while the queue and
maintenance are idle. Changed policies release resident models; the next GPU worker
starts fresh. Other clients must reload when a revision changes; event-triggered refreshes preserve
unsaved drafts and revision checks block stale Apply. There is no Settings refresh
timer: reads happen on page entry, target changes, browser foregrounding, queue/
maintenance becoming idle, and explicit reload. Background event refreshes do not
disable fields or interrupt typing. **Restore defaults to draft** never saves
without Apply. Normal deployment preserves this configuration.

Controls for pruned H3/ComfyUI runtimes:

- **Automatic GPU headroom** preserves the upstream reserve. Manual 0–64 GiB maps
  to ComfyUI `reserve_vram`; the runtime adds its minimum inference allowance.
  Increasing it can offload more weights and use more CPU RAM/time. It is not a
  hard memory cap or an OOM guarantee; excessive reserves can hurt performance.
- **Low VRAM** selects upstream `lowvram` offloading. It does not enable Turbo’s
  separate low-VRAM merging option or change job dimensions/frames/quantization.
- **Expandable CUDA allocations** sets
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` in the GPU child environment
  before PyTorch imports. Disabled means allocator defaults in a fresh worker.
  This may reduce fragmentation on supported CUDA systems, not increase VRAM.

The policy isolates performance-estimate samples and discards cached hardware
estimates after changed settings. Historical jobs and timing records remain intact.
Corrupt/unsafe policy files fail closed; do not hand-edit them while jobs run.
Back up this file with the server root. Changing it via the API is the supported
path. No actual GPU generation benchmark is implied by CPU/browser tests.

## Updates, rollback, backups

Use a new trusted controller or `deploy --wheel ... --sha256 TRUSTED_PIN`, connect
and stage/review the new plan. Candidate dependencies are resolved on target and
recorded with pip resolution/freeze; this is **not a portable transitive hash lock**.
Existing drivers/services are preserved while installing candidate environments.
OS/driver changes during update are refused: use a separately authorized maintenance
window and rescan. Models are not deleted or redownloaded just for a wheel update.

Activation serializes with the installer and an authenticated durable maintenance
gate. Policy can reject busy work, drain accepted jobs, or cancel them only with
separate explicit confirmation. It backs up compatible SQLite state, stops the
service, switches `current`, registers/restarts the environment-specific unit and
checks authenticated health. Failure rolls back pointer/unit/database; no previous
release means candidate autostart is disabled. **Approve interrupted activation
recovery** repairs only its own journal/gate, never silently treats disconnect as
success. Do not manually replace `current` while a deployment journal is unresolved.
Only known compatible schemas are accepted, not irreversible guessed migrations.

For independent backups, stop/quiesce the user service and installer, save the
application tree (including `state`, config, active releases/environments and LoRAs)
with permissions preserved. SQLite backups must be consistent: use its backup API
or a stopped database, not a live DB-only copy omitting WAL. Model files can be
reacquired only with license/download approval; preserving verified snapshots saves
134 GiB of transfer. Verify a restore on a disposable target. Keep daemon tokens
private; never attach configs, `known_hosts`, logs containing operator data, or
`server.txt` to public issue reports.

## Reboot and reconnect limits

Installation/reboot/service approval are separate. The approved bootstrap unit
survives SSH loss, checkpoints before reboot and resumes remote work through the
systemd user service/approved lingering registration. At authentication, a clearly
labelled checkbox offers local-controller-memory-only SSH password retention for
setup recovery. With consent, the live controller automatically reconnects after
approved reboot or setup transport loss for up to 120 seconds (0.5–8 second
backoff), restores only the same target/root/release/digest, and the web UI polls
connecting/backoff/recovered/failed states and replays durable events. This is
observation, not permission to repeat installation, resume, sudo or reboot.

Unknown/changed keys, authentication failure and changed setup binding stop
retries. Verify identity out of band; reconnect/authenticate and restore manually.
Timeout is also terminal: inspect the server/network and reconnect manually.
**Forget retained SSH password / stop automatic recovery**, disconnect, target
switch, session expiry and controller shutdown clear retained credentials. Only
SSH credentials can be retained, never sudo credentials; neither is written to
disk/browser storage or exposed in status/logs. Python strings cannot be reliably
zeroized; cancellation releases in-flight references after bounded IO finishes.
A closed/restarted controller or declined retention requires fresh authentication:
reopen, reconnect and **Restore saved setup**. Firmware,
Secure Boot/MOK interaction, disk encryption, network/login failure and host-key
changes may require console access. Prerequisite rounds before target Python exists
re-inspect/replan with fresh approvals rather than invoking Python resume blindly.

## Native and target acceptance checklist

Local integration evidence is **macOS arm64** native build, real loopback security/
assets/CLI smoke and Python/browser fixtures. CI defines native Linux and Windows
controller checks, but a workflow definition or source inspection is not a run.
The following are **UNRUN** until an operator records real evidence:

- [ ] Native Linux controller executable launch/security/browser checks.
- [ ] Native Windows executable, owner-only ACL/foreign-key/junction/hardlink tests.
- [ ] Linux systemd/privilege/disconnect/restart/update rollback on each claimed distro.
- [ ] Real NVIDIA GPU/environment resolution, offload peaks and optional LoRA compatibility.
- [ ] Real H3 MP4 generated, downloaded, decoded and manually played (video + audio).
- [ ] Separately approved disposable-VM reboot and installer checkpoint resume.

### Opt-in target acceptance command (never invoked by CI)

Copy `scripts/target_acceptance.py` to an approved **disposable Linux target** and
run it with the already installed application's environment Python, as the SSH
owner, not root. No SSH automation, package installation or model downloads occur.
Each mutating mode demands a typed **action + hostname** confirmation. Arrange
console access, consistent backups, licensed eligibility and idle jobs first. Use
an approved installed candidate for rollback. This script deliberately changes
real services; do not run it on production or mistake guard tests for target tests.

```sh
PY=~/MinimaxH3Server/environments/ACTIVE_PLAN_DIGEST/bin/python
"$PY" target_acceptance.py restart
"$PY" target_acceptance.py generation --profile h3-int8-group-offload-experimental
"$PY" target_acceptance.py rollback --candidate-digest APPROVED_INSTALLED_CANDIDATE_DIGEST
# Separately confirmed; invokes interactive sudo only on this target:
"$PY" target_acceptance.py reboot --disposable-vm
# After reconnecting (read-only verification plus local evidence record):
"$PY" target_acceptance.py verify-reboot
```

Restart checks authenticated health and durable maintenance ownership. Generation
requires real pinned-backend readiness, submits a 124-frame 960x544/50-step job,
waits, downloads the MP4 and decodes video/audio with the installed PyAV. It does
not replace **manual playback in the controller**; record that separately. Timeout
does not silently cancel remote work: inspect/cancel explicitly. Evidence and the
acceptance MP4 are under `state/acceptance-*`; the extra MP4 is not job-retention
managed, so remove it yourself after archiving evidence.

Rollback injects exactly one candidate-health failure **after a real candidate
switch/restart**, then requires the actual activation rollback to restore the old
release and clear maintenance. It does not fake a healthy server. A failed drill
can intentionally leave a fail-closed gate/journal: reconnect and approve recovery.
Restart failure likewise leaves its recorded owner gate closed; inspect the private
maintenance record before releasing that specific operation.

Reboot records boot identity and refuses without `--disposable-vm` plus the distinct
`REBOOT DISPOSABLE VM <hostname>` phrase. `verify-reboot` checks a new boot and healthy
authenticated daemon, **not installer resume**. Separately exercise the wizard's
waiting-for-reboot checkpoint, approve that plan's reboot, observe automatic
reconnection with memory consent (or reauthenticate manually after opt-out),
and verify the persisted setup events complete/resume correctly. Record OS/kernel,
GPU/driver/CUDA, exact resolved environment and revisions, plan digests, commands,
MP4 hash, playback and recovery evidence without passwords/tokens. No GPU or native
server certification is earned by fixture tests or this script's existence.
