# One-click controller contract

The primary browser flow uses a single composite Install intent. It does not call
`setup/plan`, `setup/install`, `setup/reboot`, `setup/activate` or `setup/download`
separately. Those low-level APIs still require their existing explicit approvals.
No arbitrary command, package list, busy-job policy or replacement artifact comes
from the composite request.

All paths below are relative to `/api/v1/controller`. Existing bootstrap cookie,
exact local Origin/Host, CSRF header, body limits and SSH host-key verification
apply unchanged. Never put credentials in browser storage, URLs or logs.

## Authentication

`POST /authenticate` accepts:

```json
{"password":"<SSH password>","reuse_for_setup":true}
```

The UI must disclose memory-only reuse for setup/sudo and reconnect next to the
normal SSH authentication action; no second normal password prompt or checkbox
wizard. Example: “Your SSH password is kept only in this controller's memory for
setup, sudo and reconnection. It is cleared when setup ends, you cancel/disconnect,
your session expires, or the controller closes.”

Both `reuse_for_setup` and the legacy `retain_for_setup_recovery` default to false.
The legacy flag alone never authorizes sudo reuse. Authentication still requires
an established, verified SSH connection awaiting authentication. To authenticate
again after a terminal setup, disconnect/connect first (saved unchanged host keys
remain trusted; new or changed keys still require the existing security handling).

Authentication and general status include `recovery.credential_retained` and
`recovery.setup_credential_retained` booleans, never a credential. Credentials are
bound to the authenticated target and browser session. Setup keeps no independent
password copy between calls. Python strings cannot be reliably zeroized; clearing
drops owned references and cancellation interrupts in-flight SSH IO where safe.

## Start

`POST /setup/start` → HTTP 202 with a status document:

```json
{
  "profile":"h3-gguf-q4-k-m",
  "artifact_set_digest":"<exact digest from /profiles or /scan>",
  "request_id":"<crypto.randomUUID()>"
}
```

For a legacy profile, omit `artifact_set_digest` or supply null. Pruned profiles
require the exact catalog digest. `request_id` is 8–64 ASCII letters, digits,
underscores or hyphens. Keep the same ID when retrying an ambiguous start response;
a genuinely new attempt uses a new ID. Same ID/target/selection returns the same
operation; conflicting reuse returns `setup_request_conflict`. A second distinct
request while work is active returns `setup_in_progress`. At most 32 distinct
operations are accepted per controller process. Selecting a profile, scanning,
loading or reloading a page must never submit this request.

Before the Install action, show the selected profile/artifact/license disclosures
and bytes, plus one concise scope statement: setup may install required packages,
drivers and user services, reboot/reconnect, activate the selected environment and
download the selected artifacts. Associate the action with license eligibility and
terms without claiming to grant eligibility. Do not add replacement approval
checkboxes. The coordinator generates internal exact-digest approvals only for
this source-owned workflow; activation uses `policy: reject`, never job cancellation.

## Observe

`GET /setup/status` returns immediately from controller memory; it never polls SSH.
`GET /status` also includes this document under `setup`. While setup owns SSH,
general status uses the last observed SSH state rather than waiting on its IO lock.

Status fields:

- `state`: `idle`, `running`, `cancelling`, `completed`, `failed`, or `cancelled`.
- `busy`: worker still owns the target, including final cleanup. Wait for false
  before reconnecting/re-authenticating or starting a new attempt.
- `phase`: `planning`, `prerequisites`, `replanning`, `installing`, `rebooting`,
  `reconnecting`, `activating`, `downloading`, `completed` (or `idle`). A terminal
  failure retains the last actual phase.
- `operation_id`: opaque controller operation ID; null when idle.
- `request_id`, `profile`, `artifact_set_digest`, `plan_digest`: original selection
  and latest internally approved plan binding, present once started.
- `snapshot`: latest durable target state, or null before discovery. It contains
  `setups`, `operation`, `active_release`, `cursor`, `has_more`, and a rolling last
  100 real events. No timer-derived percentage. Show phases/indeterminate progress
  and available real event/download byte information; do not dump the raw plan.
- `installation_ready`, `model_ready`, `generation_tested`: independent facts.
  Completion requires a ready environment, matching active environment and durable
  selected-download completion. `generation_tested` remains false.
- `error`: safe fixed code, null otherwise. `blockers` contains source-owned
  prerequisite explanations when `error` is `setup_plan_blocked`.

Page reload can recover this in-memory status. Target disconnect/replacement
resets visible status to idle. Controller restart loses browser session,
credentials and composite progress; after fresh authentication, a new explicit
Install can inspect durable target checkpoints without replaying privileged work.

## Cancel and connection changes

`POST /setup/cancel {}` → HTTP 202. It immediately signals cancellation and drops
retained credentials. Already-started OS/package work is not killed unsafely, and
no unrelated job is cancelled. Further automatic actions stop. If this Install
owns a known remote download operation ID, it requests cancellation of exactly
that ID; transport loss can prevent delivery, so verified/partial files and an
already-detached remote operation may remain. Do not claim cancellation rolled
back installation or that offline remote work was killed.

While busy, low-level mutation/proxy routes and authentication reject with
`setup_in_progress` instead of waiting on a global operation lock. Explicit
`connect`, `disconnect` and `recovery/forget` also signal setup cancellation and
clear credentials, but return that conflict until the worker has released the
old target. Poll `busy`, then retry the requested connection action. This avoids
an old worker operating on a replacement connection.

## Errors and conservative recovery

- `setup_sudo_password_rejected`: “The SSH password was not accepted for sudo, or
  this account's sudo policy requires administrator attention.” This is a specific
  recovery error, not a reason to show a second normal password field. No automatic
  repeated password attempt occurs. Advanced low-level repair retains its explicit
  password/approval API; the composite endpoint has no alternate-password field.
- `setup_credential_required`: reconnect/authenticate with disclosed setup reuse.
- `selection_changed`, `recovery_binding_changed`, `matching_release_required`:
  stop; do not switch profile, artifacts, target or release silently.
- `host_key_changed`, `trust_required`, `authentication_failed`: preserve connection
  security handling; do not automatically approve a host key.
- `setup_remote_busy`: existing/interrupted target operation or changed operation
  ownership; no unrelated operation was cancelled.
- `setup_operation_outcome_unknown`: a mutation's result could not be proved.
  Observe/perform explicit advanced repair; do not repeatedly click Install.
- `setup_plan_blocked`, `update_prerequisite_maintenance_required`: administrator
  intervention is required; do not cancel jobs or bypass privilege policy.
- `setup_install_failed`, `setup_activation_failed`, `setup_operation_failed`,
  `setup_replan_stalled`, `setup_replan_limit`, `setup_reboot_not_observed`,
  `setup_timeout`, `setup_credential_check_failed`, `setup_failed`: stop with safe
  error text and available checkpoint progress, never claim readiness.

There are at most four prerequisite replans, one unprivileged resume per digest,
one reboot request per digest/operation (also guarded by the target's durable
boot record), bounded reconnect recovery, a 30-second launch-observation grace,
a 180-second unchanged-reboot limit and a 24-hour overall workflow budget.
`bootstrap/<digest>/composite-install.json` is an exclusive, private, credential-free
launch marker written before composite installation. Even a partial marker blocks
automatic replay across controller restarts; ambiguous privileged launches require
observation or explicit low-level repair. Existing low-level APIs do not silently
inherit the composite approvals and are not globally weakened.

## Local test boundary

`tests/test_setup_coordinator.py` exercises real authenticated/CSRF routes,
Deployment, staging and host-key/reconnect handling with controlled SSH command and
SFTP fixtures. Other focused tests cover the actual fixed sudo transport and
operation-ID-bound target cancellation. No real server, package install, reboot,
model download or GPU operation is used by these tests.


## Browser integration

The primary page removes step03 comparison and the old approval controls entirely.
Step04 renders the selected profile's bytes, GPU guidance, unknown memory fit and
auxiliary components; pinned license/provenance/technical details are collapsed.
`GET /profiles` and `/scan` now include verified `license_text` and `license_sha256`
for disclosure before Install, without staging or any target mutation.

Authentication sends `reuse_for_setup:true` next to the memory-only inline notice.
Only Install sends `/setup/start`; an ambiguous request keeps its public ID in
page memory until reconciliation or a same-ID retry. No browser storage is used.
Selection and duplicate Install are disabled while setup owns the target. The page
polls `/status` for the setup document even during SSH recovery. Reload restores
that progress and exact selection without a plan/install/restore mutation.
Completed/failed/cancelled workflows explain credential clearing and reauthentication;
sudo rejection has a specific policy/password message, not a normal second field.

`tests/browser/generation_fixture.py` and `target_fixture.py` compose real controller,
installer, target authorization/activation and durable store logic while controlling
SSH, native process/service and model-download boundaries. Browser tests preserve
host verification/CSRF, low-level digest rejection and generation/video regressions.
Native smoke additionally checks that obsolete approval fields are absent and the
embedded application source/resources equal the current checkout.
