# H3 Server Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Deliver a cross-platform local setup UI and a self-hosted Ubuntu/Debian H3 daemon with resumable installation, deployment, live generation progress, and downloads.

**Architecture:** A packaged local Python controller serves the browser UI and connects through verified SSH to a loopback-only remote API. The remote service persists setup/jobs/events in SQLite and runs inference in a separate process, with all application state rooted in one home-directory tree.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Paramiko, SQLite, pytest, vanilla browser JavaScript/CSS, PyInstaller, systemd; inference runtime selected only after Task 3 evidence.

---

## Execution rules

- Work directly until the operator authorizes delegation. Tasks below are sequential unless explicitly independent.
- For every task: write the named failing tests, run them and verify the expected failure, implement the smallest behavior, rerun tests, review the diff, and commit only explicit paths. Never stage `server.txt`.
- Use `python -m pytest ... -v` for focused tests and `python -m pytest -q` for regression checks.
- Do not install drivers, reboot, or download large checkpoints during reconnaissance. These require approval of a concrete plan in the wizard.
- Redact authentication data before logging. Use temporary directories for tests and test-only fake transports/backends, not a fake production generation mode.
- Use the approved design document for security, storage, and recovery invariants.

### Task 1: repository and typed domain foundations

**Create:** `pyproject.toml`, `src/h3server/__init__.py`, `src/h3server/domain.py`, `src/h3server/paths.py`, `tests/test_domain.py`, `tests/test_paths.py`.

1. Add tests for root expansion, directory enumeration, rejection of path escapes, event progress with unknown totals, invalid percentage bounds, and capability-gated job options.
2. Run `python -m pytest tests/test_domain.py tests/test_paths.py -v`; confirm missing implementation failure.
3. Implement typed hardware, capability, event, and job models. Separate measured progress from stage status. Model capabilities as supported/unsupported/unknown with evidence.
4. Implement explicit application-root paths and cache environment variables. No global directory creation on import.
5. Rerun focused tests, then commit.

Example required behavior:

```python
def test_unknown_total_has_no_percentage():
    event = Progress(stage="loading", completed=2, total=None)
    assert event.percent is None


def test_output_path_cannot_escape(tmp_path):
    paths = AppPaths(tmp_path)
    with pytest.raises(ValueError):
        paths.job_output("../../etc/passwd")
```

### Task 2: read-only hardware scan and conservative recommendations

**Create:** `src/h3server/probe.py`, `src/h3server/profiles.py`, `tests/test_probe.py`, `tests/test_profiles.py`, `tests/fixtures/hardware/`.

1. Write fixtures for NVIDIA multi-GPU, missing nvidia-smi, no GPU, missing driver, ARM64, unsupported Linux, insufficient disk, and the observed 4090 machine.
2. Write failing parser and recommendation tests. Missing nvidia-smi must not imply no NVIDIA PCI device; CUDA driver compatibility output must not imply an installed toolkit.
3. Implement a read-only probe using fixed commands and structured output; parse `/etc/os-release` without executing it. Keep raw command errors separate from results.
4. Implement recommendations with explicit uncertainty; do not sum separate GPUs as if they were one memory pool. Only claim tested support from the backend manifest created in Task 3.
5. Run focused tests and commit. Optionally rerun the read-only scan against the provided target without mutations.

### Task 3: inference feasibility and runtime selection — blocking gate

**Create:** `docs/research/h3-runtime.md`, `src/h3server/backends/manifest.json`, `tests/test_backend_manifest.py`.

1. Inspect current upstream H3 and candidate Diffusers/ComfyUI/SGLang implementations and licenses. Record source URLs, exact revisions, artifact sizes, hardware restrictions, invocation, and progress hooks.
2. Assess low-memory paths without promising support from architecture estimates alone. Determine whether LoRA and sparse attention are implemented in the selected pinned runtime.
3. Write failing manifest tests requiring revision pinning, explicit capability evidence, dependency constraints, and no cloud generation endpoint.
4. Add the manifest and feasibility report. Distinguish inspected, installed, and actually generation-tested profiles.
5. If no viable backend is identified, report the precise blocker before building a misleading installer. Do not fabricate adapter APIs. Run tests and commit.

### Task 4: durable event log, jobs, and setup checkpoints

**Create:** `src/h3server/store.py`, `src/h3server/events.py`, `tests/test_store.py`, `tests/test_events.py`.

1. Write failing tests for transactionally accepted jobs, monotonic event IDs, restart persistence, replay after ID, stage idempotency, corrupted checkpoint rejection, and concurrent writers.
2. Implement SQLite schema/versioning with transactions, bounded event retention, and serialized migrations.
3. Implement setup states: planned, awaiting_approval, running, waiting_for_reboot, reconnecting, failed, ready. Persist approvals against a plan digest so changed plans require new approval.
4. Test reopening the store with a second process and replaying progress. Run focused tests and commit.

### Task 5: secure SSH transport and local controller API

**Create:** `src/h3server/controller/{__init__,app,ssh,session}.py`, `tests/test_ssh.py`, `tests/test_controller_security.py`.

1. Write failing tests for unknown/changed fingerprints, password redaction, command quoting, SSH timeouts, unapproved commands, foreign Origin/Host, unauthenticated requests, and one-time bootstrap tokens.
2. Implement Paramiko transport with explicit host-key approval and bounded execution/SFTP APIs. Never AutoAddPolicy by default; keep credentials in memory and clear them on disconnect.
3. Implement a loopback-only local server with session authentication, CSRF protection, exact origin validation, body limits, and same-origin assets. Consume bootstrap tokens and remove them from the browser URL.
4. Add connect, disconnect, scan, and recommendation endpoints. Execute slow SSH work off the event loop.
5. Run focused tests; test against an isolated SSH fixture before using the target. Commit.

### Task 6: browser setup wizard

**Create:** `src/h3server/web/{index.html,app.js,styles.css}`, `tests/test_web_assets.py`, `tests/browser/setup.spec.js`.

1. Write failing asset/route tests and browser scenarios for connect → fingerprint → scan → recommendation → approval, plus connection failures and unavailable capabilities.
2. Build an accessible responsive interface with a connection form, hardware summary, profile comparison, proposed changes, separate privileged/reboot approvals, real progress, and expandable redacted logs.
3. Render server text with textContent, not HTML insertion. Do not store SSH passwords in browser storage. Disable submit while a relevant operation is pending.
4. Use browser tools to verify desktop/mobile layout and failure paths. Record screenshot paths in the work report.
5. Run focused tests and commit.

### Task 7: idempotent installation planner and runner

**Create:** `src/h3server/installer/{__init__,planner,runner,steps}.py`, `src/h3server/installer/templates/bootstrap.service`, `tests/test_installer.py`, `tests/test_install_plan.py`.

1. Write failing tests for preapproved plan digests, unsupported distro versions, existing working drivers, interrupted package configuration, repeated steps, noninteractive sudo failure, and secret-free logs.
2. Generate an ordered plan from scan + profile. Start with a documented version matrix; fail closed outside it. Describe package changes and estimated downloads before approval.
3. Implement detached bootstrap with atomic checkpoints and a single installer lock. Use distro package tooling safely; do not overwrite unrelated system CUDA/driver installs or run model code as root.
4. Create application directories and versioned Python environments. Configure every backend cache/temp location under the application root. Verify installed runtime compatibility rather than installing a toolkit unnecessarily.
5. Run failure-injection tests and disposable Ubuntu/Debian VM integration tests. Commit.

### Task 8: reboot recovery and systemd daemon registration

**Create:** `src/h3server/installer/recovery.py`, `src/h3server/installer/templates/h3server.service`, `tests/test_recovery.py`, `tests/integration/test_reboot.py`.

1. Write failing tests for disconnected sessions, reboot approval expiry, changed host keys on reconnect, local process restart requiring reauthentication, and repeated resume.
2. Register a persistent bootstrap continuation with narrowly scoped privileges. Checkpoint before triggering an approved reboot.
3. Add bounded reconnect/backoff and event replay. Never disable host-key checks to recover connectivity.
4. Register the user service and enable lingering with approval. Keep authoritative service definitions under the application root.
5. Verify on a disposable VM with a real reboot before touching a user's workstation/server reboot path. Commit.

### Task 9: remote API, worker lifecycle, and output safety

**Create:** `src/h3server/remote/{__init__,app,worker}.py`, `tests/test_remote_api.py`, `tests/test_worker.py`, `tests/test_downloads.py`.

1. Write failing tests for job creation/list/status/cancel, event replay, single active worker, restart reconciliation, duplicate cancellation, expired outputs, range downloads, and path traversal/symlink escape.
2. Implement `/api/v1/health`, `/capabilities`, `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/events`, `/jobs/{id}/video`. Require remote authentication in addition to loopback binding.
3. Supervise worker process groups; persist state before replies. Mark interrupted jobs explicitly after daemon restart.
4. Enforce prompt/settings limits, bounded queues, free-space thresholds, and retention excluding active jobs. Expose useful structured errors without environment secrets.
5. Run focused tests and commit.

### Task 10: real H3 adapter and model downloads

**Create:** `src/h3server/backends/{__init__,base,h3}.py`, `src/h3server/downloads.py`, `tests/test_h3_adapter.py`, `tests/test_downloads_progress.py`, `tests/integration/test_real_generation.py`.

1. Using Task 3's verified interfaces, write failing tests for invocation arguments, progress callbacks, cancellation, resumable downloads, corrupt artifacts, and rejection of unsupported LoRA/attention settings.
2. Implement the pinned local backend; serialize real byte counts for downloads and real denoising step counts. Keep loading/encoding indeterminate if not instrumentable.
3. Validate LoRA identifiers against managed artifacts, not arbitrary client paths. Honor backend limits and never silently ignore settings.
4. Add an opt-in real hardware test producing an MP4 checked with ffprobe. Keep this separate from fast test fixtures, with hardware/runtime revisions recorded.
5. After user-approved downloads, run a real short generation on a viable target. Report OOM/failures honestly. Commit tests and adapter, not model files.

### Task 11: generation dashboard and deployment rollback

**Create:** `src/h3server/controller/deploy.py`, `scripts/deploy.sh`, `scripts/deploy.ps1`, `tests/test_deploy.py`, `tests/browser/generation.spec.js`.
**Modify:** `src/h3server/web/{index.html,app.js,styles.css}`, `src/h3server/controller/app.py`.

1. Write failing deployment tests for staged upload, integrity checks, busy worker handling, atomic pointer switch, failed health checks, rollback, and model preservation.
2. Implement versioned release deployment via SFTP and restart over SSH. Keep rollback-compatible state migrations and backups.
3. Add prompt/settings, queue, cancel, live progress/reconnection, video preview/download, retention notices, and deployment controls to the UI.
4. Add thin POSIX/PowerShell wrappers calling the packaged controller's deploy command; no passwords in arguments.
5. Browser-test full flows and run a failed-update recovery drill. Run focused tests and commit.

### Task 12: packaging, native OS checks, documentation, acceptance

**Create:** `src/h3server/__main__.py`, `packaging/controller.spec`, `.github/workflows/test.yml`, `.github/workflows/release.yml`, `README.md`, `docs/api.md`, `docs/operations.md`, `tests/test_cli.py`.

1. Write failing CLI tests for launcher, no-browser mode, deployment, clean shutdown, packaged assets, and unavailable ports.
2. Implement platform-aware browser opening and native builds for macOS/Linux/Windows, with artifact checksums and explicit unsigned-app caveats until signing is configured.
3. CI: fast tests/security cases on all OS families; native package launch smoke tests. Do not call cross-compilation a substitute for native execution.
4. Document one-click setup, sudo/reboot approvals, SSH keys, supported server versions, cache locations, runtime limitations, API authentication, retention, backup, and rollback.
5. Run `python -m pytest -q`, browser tests, package smoke tests, and an approved remote daemon restart/generation/download check. Record unrun checks explicitly.
6. Final acceptance requires a real playable MP4, reboot recovery, successful rollback, and tested local OS artifacts. Report partial milestones as partial, not complete.

## Initial execution batch

Implement Tasks 1–3 first, then review the runtime feasibility evidence before privileged installer work. Tasks 4–6 form the first usable local UI milestone. Tasks 7–12 complete installation, inference, deployment, and release verification.
