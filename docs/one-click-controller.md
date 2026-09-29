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
{"password":"<SSH password>","reuse_for_setup":true,"remember_connection":true}
```

The UI distinguishes operation-scoped memory reuse from explicit persistent
remembering. `remember_connection` defaults false at the API. When selected, the
successfully authenticated host/port/user/password are atomically saved in the
controller state directory's owner-only `preferences.json`. This is plaintext,
not Keychain storage or encryption, as requested by the operator. The browser
receives only public connection fields and a `password_saved` boolean.
`POST /connection/forget` removes the saved connection. Session expiry and setup
completion still clear temporary in-memory reuse, but not the opted-in disk copy.

`GET/POST /preferences` reads public preferences/updates `install_sage` and
`use_sage`. `POST /connection/restore` reconnects and authenticates the remembered
target only after stored host-key verification. A new fingerprint pauses for
approval; a changed fingerprint blocks. `POST /connection/authenticate-saved`
requires an exact current host/port/user match. Startup never installs or generates.

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

## Saved generation inputs

Queue/history cards show the seed directly alongside geometry and steps. Expand
**Inputs** to inspect the full untruncated prompt, model, dimensions, frames,
steps, seed, SageAttention, LoRAs and sparse-attention selection. These come from
that job's persisted request, not the current form; existing history works too.
Expansion and video elements survive dashboard polling. Prompt text is rendered
as text, never HTML. Missing legacy settings are explicitly marked unavailable.
Images are not yet a supported input; their durable references must join the job
request when image support is implemented.

## Optional sparse attention

Select **Install optional sparse attention**, then Install. Existing environments
only acquire the optional component; fresh setup installs it after base models
and optional SageAttention. About 22 MB is downloaded (archive plus extracted
files use about 33 MB); no model weights, pip packages or system packages are added.
The install is idle-only, checksum-bound and GPU-probed. Source/native files are
verified again before generation. Changed file signatures invalidate fast readiness.

Choose a remembered preset: Off, Quality-first (50%), Balanced (30%) or Fast draft
(15%). Start with Quality-first, 20 steps and a short clip; compare results before
lowering the budget. The early ramp starts with at least 50% attention. These are
video-attention budgets, not claimed speedup percentages. Sparse changes model
computation and can affect motion, detail, prompt adherence and the joint output.
Text/audio connections remain dense, but this does not promise identical audio.
Off bypasses the patch entirely. No preset changes steps, Sage choices or geometry.

SageAttention remains selectable for compatible dense work. Sparse calls use their
own kernels; this is not two kernels accelerating the same call. Turbo + sparse is
rejected in both UI and API after a GPU test detected bypassed Turbo QKV adapters.
Only ConvRot INT8 is enabled; other profiles/hardware are not assumed compatible.

Preferences add `install_sparse` and `sparse_preset`; older documents migrate to
false/off. `/setup/start` accepts `sparse_attention:true` for installation only.
Local GET/POST `/remote/features/sparse` proxies the authenticated daemon feature
endpoint with the controller's exact artifact digest. Generation uses
`sparse_preset` and `sparse_artifact_digest` (not the legacy `sparse_attention`
boolean). Published preset meanings are immutable; changed policies require a
new identity. Stored requests/history contain the selected preset and component
identity; execution events distinguish actual native launches from an unconfirmed
sparse/fallback path. Reconnect never starts installation or generation.

See [implementation evidence](plans/2026-09-29-sparse-attention.md).

## Optional Turbo LoRA

The UI offers independent remembered install/use flags (`install_turbo`,
`use_turbo` in preferences). `POST /setup/start` accepts `turbo_lora:true` to acquire
it after the base environment, model and optional SageAttention setup. Existing
ready installs use `GET/POST /remote/features/turbo` on the local controller.
The controller supplies its fixed artifact digest; the authenticated daemon's
`POST /api/v1/features/turbo` requires `{artifact_digest: <exact pinned digest>}`.
A mismatched preset is rejected before any mutation. This is not an arbitrary URL,
plugin or LoRA installation endpoint. Running/queued jobs block installation.

Generation accepts `turbo_lora`, `turbo_strength` (0.8–1.2), `turbo_low_vram`, and
`turbo_artifact_digest`. The UI defaults to six steps when enabling Turbo and
bounds it to 4–8. Settings/preset identity persist in each job's inputs; loading
checks the requested set and all hashes again. Standard generation does not load
Turbo code. Native ConvRot INT8/mixed are the current supported profile scope;
only INT8 received the real GPU acceptance check. See
[Turbo implementation and evidence](plans/2026-09-29-turbo-lora.md).

## Optional SageAttention

`POST /setup/start` accepts `sage_attention: true` to add the pinned optional
component after the ordinary environment/model setup, before reporting completion.
The choice participates in request-id binding. Existing ready environments use
`GET/POST /remote/features/sageattention`; POST is explicit consent to install and
GPU-probe the pinned pure-Triton 1.0.6 wheel. It uses daemon-owned maintenance with
reject-if-busy policy, never cancelling jobs or changing model downloads/units.
Live bounded output and installation state survive controller restarts. Generation
uses an independent strict boolean and fails validation if selected but unavailable.
Both UI choices persist. Sage is not the unsupported sparse-attention feature.

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
resets visible status to idle. Controller restart loses the browser session and
in-memory composite progress. A remembered connection authenticates after host-key
verification and automatically observes saved work; otherwise authenticate manually.
**Inspect saved setup** also recovers the original selection and durable diagnostics. A failed setup
is never implicitly resumed by Install: only backend-eligible unprivileged failures
expose **Retry failed stage**, preserving completed privileged work and caches.
See [diagnostics/retry contract](setup-diagnostics-api.md) for exact bindings and the
narrow audited predecessor recovery path across a launcher rebuild.

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
  After repairing a blocked target, disconnect/reconnect/authenticate and submit
  a new explicit Install. A same-selection blocked plan is freshly inspected only
  when it has no real checkpoint, matching operation/active environment, or
  bootstrap launch/approval/status record. Even partial launch records prevent
  rediscovery, so a new digest cannot bypass an ambiguous privileged operation.
  A hardware scan alone does not refresh installation prerequisites.
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

Authentication sends `reuse_for_setup:true` and the explicit `remember_connection` choice next to the plaintext-storage disclosure.
Only Install sends `/setup/start`; an ambiguous request keeps its public ID in
page memory until reconciliation or a same-ID retry. No browser storage is used.
Selection and duplicate Install are disabled while setup owns the target. The page
polls `/status` for the setup document even during SSH recovery. Reload restores
that progress and exact selection without a plan/install/restore mutation.
Authentication automatically performs the read-only hardware scan (never Install).
Every disabled Install has an adjacent reason/action, including scan errors and
missing memory-only setup authorization. Sudo rejection has a specific policy/password
message, not a normal second field. Safe unprivileged retry does not require a new
sudo password; reconnect/authenticate is requested only when SSH is actually lost.

The primary progress view shows one human phase, active command/package/source,
controller-observed phase elapsed time and target-computed command elapsed/last-output
age. MODEL WEIGHTS takes precedence over the last dependency command. Measured model
file events remain separate; no percentage/ETA is inferred. Internal lifecycle and
checkpoint events are collapsed under Technical checkpoint details.

Live output is a replacement-friendly bounded sanitized tail with follow-at-bottom
scrolling, an expanded log, and JSON copy/download. Failures retain command label,
exit/error and output rather than replacing them with a generic recovery message.
Historical logs explicitly report unavailable command/timing metadata. The export
is not a full audit archive. **Inspect saved setup** is optional recovery, not a
new install approval wall: ordinary restore reads only saved state, and the narrow
compatible fallback may stage the trusted observer/payload but never starts work.
Unknown releases, changed contracts/units and active locks fail closed with reasons.

`tests/browser/generation_fixture.py` and `target_fixture.py` compose real controller,
installer, target authorization/activation and durable store logic while controlling
SSH, native process/service and model-download boundaries. Browser tests preserve
host verification/CSRF, low-level digest rejection and generation/video regressions.
Native smoke additionally checks that obsolete approval fields are absent and the
embedded application source/resources equal the current checkout. Browser diagnostics
regressions use real authenticated controller routes, installer/Recovery/store and
real local child output/nonzero exits at controlled process IO boundaries (no HTTP
fakes). They cover av==18.1.0 failure, finite fresh-index recovery, permanent stop,
explicit retry, reload/export, cancellation, unsafe receipt rejection and original
trusted predecessor recovery with a legacy log. They do not run pip, CUDA, codecs,
model downloads or real server installation, and do not establish the original
transient PyAV lookup cause.
