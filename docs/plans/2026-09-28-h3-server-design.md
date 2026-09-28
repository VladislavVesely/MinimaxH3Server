# H3 Server: approved design

## Scope

A self-hosted MiniMax H3 video-generation service on generic Ubuntu/Debian Linux servers. A packaged local controller supports macOS, Linux, and Windows and opens a local browser UI. No hosted generation fallback. Initially text-to-video only; LoRA and sparse attention are capability-gated, not assumed supported.

The supplied server is a test target, not a product assumption. Its credentials are in ignored `server.txt`; never put them in source, release artifacts, command arguments, telemetry, or logs.

## Architecture

Use Python for a local FastAPI controller with Paramiko SSH/SFTP, bundled with PyInstaller per platform. Serve bundled HTML/CSS/JavaScript from the controller, requiring no local Python or Node in release distributions. A remote FastAPI daemon supervises a separate Python inference worker. Use SQLite for durable setup checkpoints, jobs, and replayable events. Bind both APIs to loopback; reach the remote API through an authenticated SSH channel. Do not open public inbound API ports.

SSH identity must be verified. Unknown host keys require fingerprint approval; changed keys block connection. Passwords are memory-only by default. Do not silently import repository credentials into the browser or persist them. Use session-scoped authentication, exact Host/Origin validation, and CSRF protection on the local API; loopback alone is not authentication.

## First-run flow

1. Local executable starts a loopback listener on an available port and opens the browser with a one-time session bootstrap token. Consume the token, replace browser URL, and use an HttpOnly SameSite session cookie.
2. User supplies host, port, username, and SSH authentication, then approves the host fingerprint.
3. Read-only scan collects distribution/version, architecture, CPU, RAM, GPU identity/VRAM, driver, CUDA runtime availability, disk space, and service-manager support. Missing tools are reported as unknown, not as proof of missing hardware.
4. Present detected hardware, recommended profiles, confidence level, disk estimates, and proposed changes. No unverified H3 memory thresholds are advertised as guaranteed fits.
5. Obtain explicit approval for the installation plan, sudo use, model download/license prerequisites, and any reboot. Separate reboot approval from general sudo approval.
6. Execute resumable, idempotent stages. Stream logs and byte/item/step progress where measurable; use indeterminate stages otherwise. Preserve a functioning driver. Install only the CUDA components required by the selected pinned backend.
7. Checkpoint before reboot. A systemd-managed bootstrap resumes remote work after reboot; the local controller reconnects with backoff and replays events. If the controller closes, reopen and reauthenticate to resume; do not promise passwordless reconnection after losing in-memory credentials.
8. Report environment readiness independently from a real video-generation smoke test.

## Remote layout

All application-owned data is under the SSH user's `~/MinimaxH3Server` (normally zuby):

```
MinimaxH3Server/
  releases/<release-id>/
  current -> releases/<release-id>/
  environments/<environment-id>/
  bootstrap/
  bin/
  config/
  state/
  models/
  loras/
  cache/{huggingface,torch,pip,cuda,triton}/
  logs/
  tmp/{downloads,jobs}/
```

Set HOME-dependent cache overrides and TMPDIR for all application processes, without globally changing the account's home. OS packages, NVIDIA drivers, systemd registration, and OS-managed journals necessarily exist outside this tree. Use explicit approvals for system mutations. Never execute downloaded model Python code with sudo.

## Installation and updates

Initial supported distribution/version combinations must be explicitly tested and documented. Detect other Debian/Ubuntu versions without automatically assuming compatible driver repositories. Reject unsupported platforms with actionable reasons.

Stage verified release artifacts via SFTP. Install dependencies into versioned environments, run preflight checks, stop/quiesce generation, switch `current` atomically, restart, and health-check. Roll back the release pointer when checks fail. Avoid irreversible database migrations; take backups before compatible migrations. Never replace a release in place or delete models during updates. Provide POSIX `deploy.sh` and Windows `deploy.ps1` thin wrappers over the controller's deployment CLI.

Use a systemd user service with lingering enabled through an approved privileged action so generation runs after logout and reboot. Keep the authoritative service file in the application tree with only registration outside it. Bootstrap must survive disconnects rather than depend on an open SSH exec session.

## Generation and API

Version prefix `/api/v1`. Health/capabilities endpoints; submit/list/get/cancel jobs; replayable server-sent events; download and preview endpoints. Typed errors and generated OpenAPI documentation support a later Pi extension. Requests accept only capabilities advertised by the selected backend. Unknown or unsupported LoRA/sparse options produce validation errors rather than silent fallback.

Initially run one inference job at a time per configured worker. Persist jobs and events before acknowledging requests. On daemon restart, reconcile interrupted jobs explicitly; do not falsely mark them completed. Cancellation terminates the appropriate worker process group and cleans partial output. Decode/export/mux stages are separate from denoising progress.

Store generated MP4 files in `tmp/jobs/<opaque-job-id>/`. Resolve downloads only through a validated job record, never user-provided paths. Support range requests for browser playback. Configurable retention and disk limits must protect active jobs, reject work before disk exhaustion, and report expired outputs clearly.

## H3 feasibility gate

Before committing to an inference engine, inspect current official and maintained runtime implementations, pin revisions, verify license and model layout, and demonstrate a real text-to-video invocation. Evaluate quantization, CPU/disk offload, supported GPU architectures, progress callbacks, LoRA, sparse attention, and cancellation. If a configuration cannot run, say so; do not substitute cloud inference or produce mock video.

The upstream README reviewed during design describes a 33B transformer, a 32B encoder, and initially unreleased sparse attention. These are research observations, not a permanent feature matrix. Capability reporting must reflect the pinned implementation actually shipped.

## Verification

Unit tests for profiles, validation, checkpoint transitions, event replay, output confinement, and retention. Integration tests for SSH authentication and host keys, staged install, disconnect/reboot recovery, update rollback, daemon restart, cancellation, and API access controls. Browser verification of setup and generation flows. Build/test native controller artifacts on all three OS families. A real model-generated downloadable MP4 is required for end-to-end generation acceptance; fixture backends are test-only and visibly labelled.
