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
3. Choose an experimental profile (smaller pruned GGUF Q4_K_M is the default)
   and **Stage release & review plan**. Both requested Abiray collections are
   selectable. Review the diffusion model, shared INT4 encoder, both VAEs, exact
   total bytes and source/license disclosures; changing selection clears approvals. This
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

### Smaller pruned choices (no original snapshot)

| Profile ID | Selected weight download bytes | Publisher guidance (not measured peaks) |
|---|---:|---|
| `h3-gguf-q4-k-m` (default) |31,550,953,118|16 GB VRAM|
| `h3-gguf-q5-k-m` |34,055,739,038|24 GB VRAM|
| `h3-convrot-mixed` |35,889,785,333|15+ GB VRAM (~15.5 GB)|
| `h3-convrot-int8` |40,957,152,230|24+ GB VRAM|
| `h3-nvfp4` |32,515,409,407|**Blackwell only**, not RTX 4090/Ada|

GGUF models are from `Abiray/MiniMax-H3-Pruned-GGUF`; ConvRot/NVFP4 models and
both VAEs are from `Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot`. Each choice selects
**one** diffusion file, the explicitly disclosed Winnougan
`MiniMax-H3-INT4_Convrot_ComfyUI` encoder (not an NVFP4 encoder), and video/audio
VAEs. Pure INT4 diffusion is **unavailable** in the pinned Abiray inventory; no
advertised-but-absent URL is synthesized. All revisions, file hashes and selected
sets are pinned. The UI exposes exact sources and aggregate totals before consent.

Host RAM and peak VRAM are **unknown/unmeasured**, including on RTX 4090/29 GiB;
pruned profiles do not inherit the legacy 96/192 GiB RAM rules. Q4 is the smaller
experimental default, not a memory-fit guarantee. NVFP4 selection requires positive
Blackwell identification; the current conservative name mapping recognizes RTX
5090, rejects RTX 4090, and leaves other names unknown (not eligible).

The 4,456,704-byte tokenizer is included once in pinned runtime source, not fetched
again with weights. Runtime/dependency bytes are extra and not fully known.
Selected-weight partials are renamed per file, not duplicated into a second cache;
other snapshots, stale partials, runtime/upgrade environments and outputs add disk
use. No whole repository or original 134 GiB snapshot is downloaded by these choices.

The managed headless ComfyUI/GGUF backend supports fixed local base text-to-video
with audio, 24 fps, 20 default steps, seed and multiples-of-32 canvas. No ComfyUI
server, hosted API, arbitrary workflow, LoRA, negative-prompt/CFG controls or
image/reference inputs are exposed. Generation options come from the **active
daemon**, not the installation dropdown; switching requires approved installation
and activation. No automatic fallback. See [source evidence and exact artifacts](docs/research/h3-pruned-runtime.md),
[runtime operations](docs/research/h3-pruned-operation.md) and
[UI/build integration](docs/research/h3-pruned-integration.md).

### Explicit legacy alternatives

| Experimental profile | Upstream GPU recipe | Local planning budget (unmeasured) |
|---|---|---|
| bf16 CPU offload | one 80 GB GPU | 192 GiB RAM, 320 GiB free disk |
| int8 group offload | one 24–32 GB GPU | 96 GiB RAM, 320 GiB free disk |

Only the **legacy** profiles select 62 files totaling **144,051,160,615 bytes (134.16 GiB)**, even for legacy int8.
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
cannot cure ineligibility. Both Abiray cards declare MiniMax H3 Community License
(`other`); Winnougan encoder publisher metadata says Apache-2.0 but does **not**
remove upstream MiniMax/Qwen derivative obligations. The UI presents these
provenance disclosures with the full upstream license before separate approvals.
Review runtime/component licenses independently. The
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
never Torch or weights. Wheel build uses the installed build tools (`--no-isolation`);
keep setuptools >=68 installed. Verification compares every application source/resource
in the remote wheel to this checkout, checks embedded resources and exercises the
native authenticated profile catalog against source. Output: `dist/h3-controller[.exe]`, `dist/SHA256SUMS`;
intermediate pure wheel: `build/controller-payload/`. Generated files are ignored.
For source launch, first build a wheel (`python -m build --wheel`), verify its hash,
then `python -m h3server --wheel dist/h3server-0.1.0-py3-none-any.whl --sha256 HASH`.
Source launch deliberately fails with an actionable message without a release
payload; it never guesses a wheel from the working directory.

[Operations](docs/operations.md) · [API/auth/events](docs/api.md) ·
[Deployment internals](docs/deployment.md) · [Approved design](docs/plans/2026-09-28-h3-server-design.md)
