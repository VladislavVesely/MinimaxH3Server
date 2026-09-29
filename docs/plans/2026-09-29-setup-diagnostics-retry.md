# Actionable setup diagnostics and bounded retry

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Replace opaque stage states with actual operation/output/error information and recover safely from dependency failures without repeating completed privileged work.

**Architecture:** Persist bounded sanitized command diagnostics alongside durable installer stages; relay confined incremental log output and structured failure data to the authenticated controller and browser. Retry only classified safe dependency/source acquisition operations, with finite budgets and unchanged pins; offer explicit resume for an eligible failed unprivileged stage. Keep exact artifact/profile/target binding, cancellation and privileged transaction boundaries.

**Tech Stack:** Existing installer ProcessIO/Steps, SQLite stages/events, SSH/SFTP, FastAPI controller, vanilla UI and Playwright.

## Observed production failure and corrected evidence

User's ConvRot INT8 installation reached dependencies, fetched the pinned ComfyUI source, then pip reported `No matching distribution found for av==18.1.0`. The UI displayed only generic dependencies/recovery failure. Actual log: `~/MinimaxH3Server/logs/installer-c4f84cb5149a431b8458a835b302b08e.log`; saved sanitized tail `/tmp/h3-current-install-tail.log`.

Do NOT assume the pin is incompatible: fresh native metadata queries on the same target find `av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl`. Target Python3.14.4, pip25.1.1, normal GIL, compatible abi3 tags. Both cached and uncached index queries now resolve18.1.0; bounded pip dry-run reached the 35.8MB wheel download and was stopped at45s. Evidence `/tmp/h3-target-python-tags.json`, `/tmp/h3-target-pyav-index.log`, `/tmp/h3-target-pyav-index-cached.log`, `/tmp/h3-target-pyav-dry-run.log`. Exact original network/cache cause is not established. Preserve pin absent stronger evidence. No model weights were being downloaded at the failing stage.

The failed plan digest is `af679ee8092c4788866e1121abb7ac37a3af0bc3fed59a3326ff322c0e628f39`; current trusted local wheel and deployed release hash both `d7e9af0db46e5256428a0bc1ff76fc9a56ee3209b21c74758ee5b92022f7dbba` before this change. New builds change the wheel hash: do not accidentally make safe retry impossible solely by rebuilding, and do not execute an arbitrary old wheel named by remote data.

## Task 1: Installer/controller diagnostics and safe recovery

Exclusive owner: installer ProcessIO/Steps/runner/recovery, necessary SSH/deploy/setup/target routes and tests. No UI edits.

- Reproduce a dependency command with meaningful output followed by nonzero exit; assert error detail and bounded output survive through real setup/controller state, not generic wrapping.
- Emit sanitized command label, stage/substage, start/end timestamps, exit status and last output time. Clearly separate runtime source acquisition, Python package downloads/install, runtime checks, and MODEL WEIGHTS acquisition. Include actual package/source names and pinned versions, not only “dependencies”.
- Expose bounded confined log tail/incremental reads, safe diagnostic download/copy data, and structured failure summary. Bind to the selected authenticated setup; no arbitrary paths, symlinks, credentials, URL auth/query secrets, terminal escapes, HTML or unbounded logs. Display a heartbeat/last-output age without inventing percent/ETA.
- Ensure source fetching and pip show progress/output when stdout is piped, preserve line boundaries and avoid blocking status requests. Never log sudo password input or environment secrets. Record errors from subprocess launch failures too.
- Implement finite retry/backoff for recognized transient index/download/source failures. An initial no-distribution response may get a bounded fresh-index retry with exactly the same pin; do not loop permanent incompatibility or silently downgrade/change indexes/build from source. Preserve wheel cache where safe. Log each retry/reason/next delay. Never automatically retry apt/sudo/reboots or ambiguous setup mutations.
- Add explicit safe resume contract for failed unprivileged dependencies/runtime verification, reusing completed state/cache and guarding active workers. Handle existing saved failures across a controller rebuild only with a justified compatibility/trust proof; never erase failed checkpoints or weaken matching-release recovery globally. Escalate if no safe narrow transition exists. Surface blockers with precise reasons and a next action, not “ask administrator” for every case.
- Keep low-level APIs compatible, add regression tests for timeout/network/permanent resolution errors, credential redaction, log confinement/truncation, duplicate/concurrent retry and preservation of completed privileged stages.
- Commit and hand off exact APIs/status fields/log schema, readable diff and validation.

## Task 2: Useful UI and native launcher

Exclusive owner: web/browser fixtures/tests/docs/build; backend owner completes first, bounded integration fixes allowed.

- Selected model details remain in step04, one Install action. After authentication, automatically perform the read-only scan when possible, or show an explicit adjacent reason/action for disabled Install (never silently grey). No installation on selection/authentication.
- Show one current human-readable phase with active command/package/source, elapsed phase time and last-output age; measured byte/progress values only where actually available. Put raw internal lifecycle/checkpoint events in collapsed technical detail, not repetitive planned/approval/running rows as the main status.
- Show live sanitized output while installing, with sensible autoscroll and an expandable log. On failure retain the command, exit code, concrete error tail, log location/copy or download, and specific retryability/next action. Do not overwrite the error with a generic recovery banner.
- Provide Retry failed stage only for backend-declared safe failures; preserve completed work and original selection. Connection/session recovery prompts appear only when genuinely needed. Account for pre-update saved failures; no new release mismatch trap after restart.
- Real controller-route browser regressions must demonstrate a specific pip error visible in UI, successful bounded retry, permanent error stopping with detail, cancellation, reload preserving diagnostics, auto scan/disabled reason, and model-download stage distinguishable from dependency downloads.
- Full pytest -Werror, browser, pipcheck, build root dist/h3-controller and native smoke; commit and report exact results and untested hardware boundaries.

## Authority and review

Work directly on main, no branches/worktrees/push. One writer at a time. No agent may read server.txt or contact the real server, install remote dependencies, reboot or download model weights. Local isolated package/metadata diagnostic experiments are allowed; avoid multi-GB automatic downloads. Parent handles actual target checks. Independent review must inspect full committed diff and concrete recovery/observability safety. Do not claim the original transient cause proven or real end-to-end installation fixed based solely on mocks.
