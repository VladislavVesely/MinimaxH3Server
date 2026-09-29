# MiniMax H3 Server

A local browser controller and self-hosted Linux video-generation service. The
controller connects over verified SSH; inference runs on **your NVIDIA server**.
There is no hosted fallback, fake generation mode, or bundled model download.
Version **0.1.0 is experimental**, not a GPU-certified release.

## One-click controller

Download the native `h3-controller` (`h3-controller.exe` on Windows) from your
trusted release channel. Verify `SHA256SUMS` before running; a checksum downloaded
from the same compromised source is not independent authentication. Releases are
currently **unsigned/not notarized**: macOS Gatekeeper and Windows SmartScreen may
warn. Do not bypass OS protections for an unverified binary. Linux may require
`chmod +x h3-controller`. Double-click/run the executable; leave its console open.
It opens your browser at a one-use fragment URL, then removes the token from the
address bar. No local Python or Node is required. Close with Ctrl+C (Ctrl+Break on
Windows); closing the browser alone does not stop the controller or remote jobs.

```sh
./h3-controller --no-browser       # prints a private, single-use local launch URL
./h3-controller deploy            # same real deployment wizard
./h3-controller --help
```

Do not share the launch URL. Restart the controller to obtain a fresh session.
`--port PORT` reserves a specific loopback port; the default reserves a free one.
The distribution embeds a verified application wheel, web UI, pinned backend
manifest and full model license. Deploy does not depend on a checkout. Thin
wrappers `scripts/deploy.sh` and `scripts/deploy.ps1` use `H3_CONTROLLER` or the
executable on PATH. To deploy a separately authenticated wheel:
`h3-controller deploy --wheel PATH --sha256 TRUSTED_SHA256`. No password arguments.

### Setup and approvals

With memory-retention consent, the running controller automatically reconnects
for up to 120 seconds after an approved reboot or lost setup SSH connection,
verifies the known host key, restores the same target/plan digest, and replays
progress. The remote bootstrap continues independently; automatic recovery never
reissues installation, sudo or reboot commands. Disconnect, opt-out, session
expiry or controller shutdown clears retained credentials. Restarting requires
fresh authentication. Unknown/changed keys and authentication failures stop
retries; no automatic host-key approval occurs.

1. Enter SSH host/port/user. Verify and approve its SHA256 host fingerprint using
   a separate trusted channel; a changed key **blocks**, not silently re-enrolls.
2. Authenticate, then scan hardware. The clearly labelled authentication checkbox
   authorizes keeping the SSH password in **local controller memory only** for
   setup recovery (checked by default in the UI; the API requires explicit consent).
   Uncheck for manual reconnection. No password is written to disk/browser storage;
   sudo passwords remain request-scoped.
   SSH agents/private-key login are **not implemented**; no account SSH config or
   `server.txt` is imported. Never paste a private key into the password field.
3. Choose an experimental profile and **Stage release & review plan**. This
   approval stages application files, not OS changes. Review exact package,
   environment and service-registration changes; approve license eligibility,
   installation and sudo separately. Driver replacement is never unconditional.
4. Reboot, if needed, requires **separate** digest-bound approval. Model download
   requires its own license/eligibility and download approval after activation.
5. Inspect live setup/download progress and diagnostics, then submit a real job.
   Environment ready, daemon healthy, verified weights, model loaded and a real
   playable video are distinct milestones. A successful scan proves none of them.

### Hardware and support boundaries

The planner recognizes **Ubuntu 24.04/26.04 and Debian 12/13**, Linux x86-64 with
systemd, distro-provided package candidates and NVIDIA hardware. These are
**implemented eligibility rules, not tested server certifications**. Unknown OS
versions are rejected; driver repositories are not guessed. Missing tools yield
unknown observations. There are currently **no native Linux server/GPU versions
certified by this project**.

| Experimental profile | Upstream GPU recipe | Local planning budget (unmeasured) |
|---|---|---|
| bf16 CPU offload | one 80 GB GPU | 192 GiB RAM, 320 GiB free disk |
| int8 group offload | one 24–32 GB GPU | 96 GiB RAM, 320 GiB free disk |

The selected 62 files total **144,051,160,615 bytes (134.16 GiB)**, even for int8.
Budgets include staged copies and overhead, not guaranteed peak-memory fits;
retained videos/caches need more disk. Do not sum multiple GPUs into one fit.
The pinned runtime uses dense attention; sparse requests are rejected, including
an explicitly supplied false option. Optional LoRAs require confined safetensors,
explicit t2va compatibility metadata and backend capability acceptance; no arbitrary
paths/pickle or guaranteed quantization/offload compatibility. See
[the source-inspected runtime decision](docs/research/h3-runtime.md).

**Validation status:** macOS arm64 native build/loopback/security smoke and local
Python/browser suites are exercised in this integration. CI is configured for
native macOS/Linux/Windows builds and checksums; unrun CI is not OS certification.
Windows owner-only ACL code uses native pywin32 APIs, not chmod emulation; native
Windows validation is pending. Linux systemd, disposable-VM reboot, GPU/offload,
LoRA and real downloadable MP4 acceptance remain **unrun**.

## License prerequisites

Read the complete [MiniMax H3 Community License Agreement and Exhibit A](https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE)
([bundled copy](src/h3server/backends/LICENSE.h3.txt)). It is **not Apache-2.0 for
H3 as a whole**. Public access is not permission to use it. Key terms (not legal
advice; full text controls): territorial exclusions include EU, UK, Republic of
Korea and USA absent separate authorization; commercial products/services above
USD 20 million annual revenue require prior written authorization; commercial UI
must prominently display “MiniMax H3”. Redistribution needs the Agreement, required
NOTICE/modification marking and downstream obligations. AUP/safeguard/reporting
duties and restrictions on improving unrelated AI models also apply. A checkbox
cannot cure ineligibility. Review runtime/component licenses independently. The
controller distribution contains the license, **not weights**, and does not infer
user eligibility or grant rights to third-party software.

## Source development / native build

Python 3.11+; Node is needed **only** for browser tests. Use a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,packaging]'
.venv/bin/python -m pytest
.venv/bin/python -m pip check
(cd tests/browser && npm ci && npx playwright install chromium && npm test)
.venv/bin/python scripts/build_controller.py
.venv/bin/python scripts/smoke_controller.py dist/h3-controller
./dist/h3-controller
```

On Windows use `.venv\Scripts\python.exe` and `dist\h3-controller.exe`; controller
and native ACL tests run there, while POSIX remote-daemon tests belong on Linux/
macOS. Set `H3_TEST_PYTHON` to override the browser fixture interpreter. Native
builds are not cross-compiles. Build tooling installs controller dependencies only,
never Torch or weights. Output: `dist/h3-controller[.exe]`, `dist/SHA256SUMS`;
intermediate pure wheel: `build/controller-payload/`. Generated files are ignored.
For source launch, first build a wheel (`python -m build --wheel`), verify its hash,
then `python -m h3server --wheel dist/h3server-0.1.0-py3-none-any.whl --sha256 HASH`.
Source launch deliberately fails with an actionable message without a release
payload; it never guesses a wheel from the working directory.

[Operations](docs/operations.md) · [API/auth/events](docs/api.md) ·
[Deployment internals](docs/deployment.md) · [Approved design](docs/plans/2026-09-28-h3-server-design.md)
