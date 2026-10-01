# Multi-runtime installation Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task. Repository constraints override its delegation/worktree suggestions: main only, direct execution, focused tests, no production deletions.

**Goal:** Selecting models provisions their complete environments, dependencies and pinned weights alongside existing installations; generation automatically chooses the correct runtime. Missing runtimes are work to perform, not reasons to disable model selection.

**Architecture:** Keep one authenticated daemon/scheduler and controller. Maintain verified, application-owned per-model environment bindings. Provision independent immutable environment families using the existing pinned installer and staged release contracts; never switch the daemon to another model's environment. Route execution, readiness, media validation and accelerator verification through the selected model's environment. One desired-state operation owns maintenance through provisioning, downloads, verification and removals.

**Tech Stack:** Python/FastAPI, existing installer Steps and controller setup/SSH/Local transport, SQLite worker, vanilla Studio UI, pytest and native Chrome/WebKit fixtures.

## Scope and invariants

- All catalog models participate, including GGUF Q4/Q5, ConvRot, hardware-eligible NVFP4 and legacy profiles. Genuine hardware incompatibility remains a blocker; a missing Python environment does not.
- Selecting multiple models on fresh setup must retain the complete desired set, not silently reduce it to one base model.
- Existing ConvRot environments, optional dependencies, retained models, assets, outputs, history and active work are preserved. Never modify an existing environment to satisfy a different family.
- Runtime-family reuse requires matching pinned sources, dependency contract and native verification, not merely a similar model name.
- Do not run current-environment checks as evidence for a different interpreter. Bind receipts and diagnostics to environment identity and release/source versions.
- Controller stages the approved current wheel before provisioning. An old wheel left by an earlier deployment is not assumed to contain the new worker/receipt protocol.
- Reuse the existing privileged planner and in-memory authorization when OS prerequisites are actually missing. Never replay privileged work after ambiguous transport/reboot results. Never invoke sudo from the ordinary daemon.
- Durable explicit intent, digest binding, inherited operation locks and additions-before-removals remain mandatory. Runtime setup failure leaves existing models usable; no removals before additions succeed.
- No production model deletion/download as a test. Preserve all existing controller processes and joiner workspaces.

## Task 1: Verified runtime bindings

Create `src/h3server/remote/runtimes.py` and `tests/test_runtime_bindings.py`.

1. Write failing tests for owned environment identifiers, exact profile/artifact and release binding, private no-follow reads, unknown/malformed registry rejection, and same-family reuse versus separate GGUF/ConvRot environments.
2. Run `.venv/bin/python -m pytest -q tests/test_runtime_bindings.py` and observe missing behavior.
3. Implement explicit bindings with immutable receipts; no interpreter supplied by an HTTP caller, no PATH fallback and no environment mutation on inventory reads.
4. Re-run focused tests. Binding publication must occur only after a successful target-interpreter probe; unrelated profiles/bindings remain intact.

## Task 2: Per-profile execution and diagnostics

Modify `remote/worker.py`, `remote/model_library.py`, `backends/pruned.py`, `backends/h3.py`; add `tests/test_multi_runtime_worker.py`.

1. Test two different interpreter commands selected by request profile; resident recycling across environments; legacy non-resident execution; selected-profile media validation and capabilities; rejection of stale/missing bindings.
2. Introduce concrete per-profile backend selection, including readiness/diagnostics from that interpreter rather than the daemon interpreter. Keep one scheduler and idle maintenance gate.
3. Verify existing worker, library, resident, references, estimates and generation tests. Never publish a profile as runnable from weights alone.

## Task 3: Environment-local optional components

Modify `backends/sage_attention.py`, `remote/features.py`, sparse GPU receipts and runtime verification; add `tests/test_runtime_optionals.py`.

1. Test that a Sage receipt in environment A cannot certify B, installing in B cannot overwrite A's verification, and a frozen base dependency cannot be removed.
2. Namespace package/GPU verification by environment; preserve compatible historical receipts explicitly. Shared pinned Turbo/reference files remain shared while execution-specific checks remain bound.
3. Apply desired accelerator state to compatible selected runtimes. Report partial/unverified installations honestly.

## Task 4: Whole-operation provisioning

Modify controller setup/deployment/proxy orchestration, `remote/components.py` and `remote/runtimes.py`; add `tests/test_multi_runtime_setup.py`.

1. Test desired INT8 + Q5 with only INT8 installed: plan and stage current release, provision separate runtime, fetch pinned sources, resolve/freeze dependencies, native environment verification, weight download/verification, publish binding. No service switch and no mutation of the original environment.
2. Reuse existing pinned installer Steps and exact approvals. Source the current staged artifact from the controller, not an assumed old installed wheel. Use inherited maintenance ownership for all child work.
3. Test failure/cancel/crash at each boundary, controller restart and reboot/authorization recovery, active-job rejection, stale inventory, disk exhaustion, fresh multiple-model intent and additions-before-removals.
4. Store phase and real byte progress durably. Explicit resume may reuse verified artifacts but never automatically replay privileged work.

## Task 5: Checklist and generation UI

Modify `web/app.js`, `web/studio.js`, `tests/component-checklist.cjs` and selected browser fixtures.

1. Reproduce Q5 disabled solely for a different runtime; then test it selectable with a clear 'runtime + dependencies + weights' summary.
2. Keep all fresh-install selections; display provisioning stages and progress without another model selector or Review step.
3. Report model readiness and optional support for its chosen runtime. Preserve drafts and edited desired state across polling. Never install/generate on navigation.
4. Chrome/WebKit acceptance uses intercepted mutation requests and fixture runtimes.

## Task 6: Verification and guarded rollout

Run focused tests only. Build packaged controller. Verify exact remote prior sources, idle reject maintenance, backups and unchanged retained data before any daemon update. Patch compatible public controller assets without restarting the existing controller; if new controller backend code is needed, keep old processes/workspaces alive and explain the new endpoint rather than pretending asset replacement updates Python.

Do not mark this feature complete until selecting different families, provisioning their dependencies, and routing generation through the correct environments are tested together. Record explicitly which GPU paths were actually exercised versus fixture-tested.

## Initial foundation checkpoint

Local foundations initially implemented:

- `remote/runtimes.py`: exact runtime-family/release/freeze binding, target-interpreter verification before publication, guarded registry writes, and read-only lookup. Fifteen fixture tests cover coexistence, family mismatch, release/freeze changes, invalid paths, failed/wrong-environment probes, symlink/hardlink/permission rejection, malformed registry and changes during verification.
- `remote/worker.py`: optional concrete per-profile backend selection for admission, FIFO scheduling, execution protocol, command and media validation. Resident processes recycle when the command/environment changes, including for the same profile. Four fixture-subprocess tests cover mixed resident/nonresident routing, selected-runtime readiness, wrong-profile rejection and recycling. Tests first reproduced missing dispatch, then exposed the scheduler's residual daemon-environment readiness gate before its fix.
- **119 focused tests passed** across bindings, dispatch, worker/residency, library/API and existing component reconciliation; `git diff --check` passed. Native GPU execution was not tested for this change.

At this checkpoint the foundations were not yet connected or deployed; the running checklist could not provision another runtime family.

## Integrated and deployed implementation

All six tasks are now connected: model-library bindings/dispatch, environment-scoped accelerators, current-wheel staging, isolated complete runtime provisioning, full fresh desired-state reconciliation and the selectable multi-runtime checklist. Native environment bindings publish before weight download; models become selectable for generation only after verified weights are registered. Failed additions cannot remove retained models.

Focused integration includes an authenticated API-to-provisioning-to-selected-interpreter execution fixture. Native Chrome/WebKit acceptance passed on the preserved 8789 controller and the separate rebuilt 8790 controller. The daemon was deployed under exact-source, backup and idle maintenance guards; retained components/history remained unchanged. See `docs/validation.md` for evidence and paths.

This completes the connected implementation, not real multi-family GPU certification: no new production multi-GB install/removal/generation was run. Missing OS prerequisites on an already installed host require explicit guarded prerequisite repair; the ordinary daemon never grants itself sudo or replays privileged/reboot steps.
