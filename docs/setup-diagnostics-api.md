# Setup diagnostics and safe stage retry

Backend contract for the controller UI. No model/GPU success is implied by these APIs.
All routes use the existing authenticated browser session, exact SSH target/host trust,
and CSRF protection for POST. There is no caller-supplied command or log-path API.

## Observation

- `GET /api/v1/controller/setup/status`: nonblocking coordinator memory snapshot. During composite
  setup, `diagnostics` and `retry` mirror the latest selected target snapshot. They
  remain available after failure/cancellation. Polling never waits on the SSH worker.
- `GET /api/v1/controller/setup/events?after=<event cursor>`: authenticated target snapshot after
  restore, including `diagnostics` and `retry`. Unlike status, this performs bounded SSH
  observation. Event cursor and diagnostic byte offsets are separate concepts.
- `POST /api/v1/controller/setup/restore {}`: exact-release, observation-only discovery.
  Resolves the authenticated target root and reads saved records, without staging,
  prerequisite replan or remote service-plan execution (`service`/`service_digest`
  are null). A subsequent explicit Install observes the omitted service contract
  against the same root/plan digest before approving or recording a launch; inspection
  alone never does so. Missing deployment record returns `setup_not_found`; missing/malformed
  plan records remain errors, not consent to create a new plan. Follow with events
  after browser/controller reload. Do not turn observation into a retry automatically.
- `POST /api/v1/controller/setup/restore-compatible {}`: explicit narrow discovery of the audited
  predecessor described below. Returns the original plan document and `snapshot`.
  Normal restore still rejects mismatched releases. This endpoint stages trusted
  worker/payload bytes, but never starts an installer, rewrites a plan, or grants consent.

`diagnostics` is null when no selected checkpoint exists, otherwise:

```json
{
  "schema": 1,
  "setup_id": "32 hexadecimal characters",
  "plan_digest": "64 hexadecimal characters",
  "generation": "new identifier per installer run; null for legacy logs",
  "stage": "dependencies",
  "substage": "python-packages",
  "command": {
    "label": "Install Python packages: ...exact approved requirements...",
    "started_at": 1790000000.0,
    "ended_at": null,
    "exit_code": null,
    "last_output_at": 1790000001.0,
    "error": null,
    "elapsed_seconds": 1.0,
    "last_output_age_seconds": 0.0
  },
  "tail": "plain sanitized output with line boundaries\n",
  "offset": 12345,
  "start_offset": 0,
  "failure": null,
  "retry": null,
  "log_reference": "logs/installer-<setup_id>.diagnostics.json",
  "observed_at": 1790000001.0
}
```

Coordinator status also includes `phase_elapsed_seconds`: elapsed controller-observed
phase time from a monotonic clock, reset on an actual phase change/new operation,
not on polls or same-phase updates, and frozen on terminal state. It is null for
idle/unobserved historical phases after restart. This is separate from target
command duration; neither value implies progress, percentage or ETA.

Times are target Unix seconds; ages are calculated on target to avoid controller
clock skew. Null exit status is not success. Launch errors have an error and no
exit code. Automatic retry metadata, when present, is
`{attempt, reason, delay_seconds, at}`; it is distinct from eligibility below.
Persisted output is a rolling **32 KiB UTF-8 tail**, not an audit archive.
Legacy raw-log reads discard the first line fragment when the bounded read starts
inside the file, before redaction: a truncated credential prefix must never expose
its value. An oversized line without a newline is omitted entirely. `offset`
counts sanitized bytes; replace the browser tail when generation changes or bytes
before `start_offset` expire. The API returns the entire bounded tail, allowing
clients to replace it on every poll instead of merging. The object is safe copy/
download data; `log_reference` is informational, never a path accepted for reading.
Render with textContent, not innerHTML. Legacy failures expose the confined old log
tail with `legacy: true`, null command metadata, and no invented start/exit times.

Phases: `runtime-source` names pinned repository/revision (including ComfyUI);
`python-packages` names exact Python package requirements (torch/CUDA/PyAV etc.);
`runtime-checks` covers source verification, pip check, imports/CUDA/codec checks.
Coordinator `phase: downloading` means **MODEL WEIGHTS** and takes presentation
precedence over old installer command metadata. Download events retain measured
byte progress. No phase has an estimated percentage or ETA. Do not render the
internal checkpoint/event lifecycle as the primary user activity.

On failure, retain `diagnostics.failure`, `command.label/error/exit_code`, and tail.
`error: setup_install_failed` is a summary code, not a replacement for that detail.
Status/cancellation does not kill detached package managers: their installer lock
and durable state remain authoritative until the children finish.

## Explicit retry

`retry` at snapshot/status top level is:

```json
{
  "eligible": true,
  "reason": "failed_unprivileged_stage",
  "stage": "dependencies",
  "next_action": "Retry failed stage with unchanged pins; completed privileged work and caches are preserved."
}
```

False reasons include `setup_not_failed`, `stage_not_safe`,
`checkpoint_inconsistent`, `recovery_receipt_invalid`, `installer_busy`,
`deployment_busy`, and `no_failed_setup`; each includes a specific next action.
Only show Retry when eligible. Checkpoint, approved service receipt/units, completed
preceding stages, installer ownership, and deployment ownership are rechecked on
mutation, not trusted from the browser's last observation.

`POST /api/v1/controller/setup/retry` returns 202 and starts the composite continuation:

```json
{
  "profile": "original selected profile name",
  "artifact_set_digest": "original catalog digest, or null for legacy profiles",
  "plan_digest": "original exact plan digest",
  "request_id": "new-unique-retry-request-id"
}
```

Repeated identical request IDs return the same operation, not a second launch.
Conflicting binding/active worker requests fail. This unprivileged path needs an
already authenticated SSH connection, **not a fresh sudo password or retained
credential**. A lost connection requires reauthentication; mutation replies are
never blindly replayed. A retry is refused for planned, active, ready, privileged,
registration, or inconsistent checkpoints. Completed APT/linger/service setup and
environment creation are not repeated.

The coordinator binds a launched retry to its accepted operation ID, original
plan digest and setup ID. READY alone is not completion: it waits for that exact
operation's `completed` record and confirmed lock release before re-observing
readiness and attempting activation. Target snapshots add `operation.lock_released`:
true only when a nonblocking deployment-lock probe succeeds and the same operation
identity is reread under that lock. A stale running record is marked interrupted
only after this reread. Missing/malformed records, changed ownership or unavailable
locks do not prove release. This observation is not a lease: activation still
acquires its own authoritative lock. Missing lock metadata is handled fail-closed
for retry handoff, while existing non-retry observation remains compatible.

Running retry observation remains within the overall workflow deadline and is
cancellable. A completed retry gets at most 30 seconds to confirm lock release;
missing/uncertain acceptance or release fails with `setup_operation_outcome_unknown`,
never a replay of retry or activation. Failed/interrupted retry completion stops
with `setup_operation_failed`; changed operation ownership stops with `setup_remote_busy`. Caches and original environment digest
remain in place. Activation/model acquisition only follow successful verification.

Automatic retries occur only inside fixed pip-install/git-fetch acquisition:
recognized network/download errors allow at most three total attempts (1s, 2s
backoff); no-matching-distribution allows one fresh-index attempt (`--no-cache-dir`)
then stops if unresolved. Pins, index, binary-only requirement and source revisions
never change. No cache files are deleted. Permanent conflicts, unknown errors,
APT/sudo/reboots, checkout and runtime checks are not automatically retried. Pip
uses native raw byte output when supported, otherwise verbose non-terminal output.

## Audited predecessor compatibility

Packaged compatibility payload:
`h3server/installer/compat/h3server-0.1.0-py3-none-any.whl`, 187946 bytes,
SHA256 `d7e9af0db46e5256428a0bc1ff76fc9a56ee3209b21c74758ee5b92022f7dbba`.
These are locally verified pre-diagnostics build bytes, **not** a remotely asserted
trust anchor. They preserve installation of the exact originally approved wheel.
Current trusted code executes diagnostics/retry; it does not execute that old wheel
as the observer/installer worker. Current `validate_plan` must accept the same
schema, requirements, profile/runtime sources, artifact contract and target root.
The original failed dependencies/verify checkpoint must be eligible, with intact
completed-stage/service receipts and no active/conflicting deployment. No hostname,
user-specific plan digest or setup ID is hardcoded. Unknown releases, contract
changes, active releases and invalid receipts fail closed. Error codes include
`compatible_retry_contract_changed` and `compatible_retry_not_safe`.

An audited-predecessor plan proved never launched is not a failed-stage recovery:
explicit Install uses the existing stale-discovery path, repeating the complete
unlaunched proof under ownership locks before replacing its discovery pointer.
Failure to prove unlaunched does not permit replacement; only the independent
approved failed-stage compatibility proof can allow diagnostic/retry recovery.

This is not a general update migration, active-service conversion, or automatic
resume of arbitrary historical failures. It does not establish why the original
PyAV lookup failed; the pin remains unchanged. Real Linux/GPU/model validation is
separate from local subprocess, filesystem, route and packaging tests.
