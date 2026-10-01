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

### One-click setup

1. Enter SSH host/port/user. Verify and approve its SHA256 host fingerprint using
   a separate trusted channel; a changed key **blocks**, not silently re-enrolls.
2. Authenticate; hardware is scanned automatically. **Remember this connection**
   saves host/port/user/password in owner-only local `preferences.json`. The password
   is **plaintext**, not encrypted: account access or backups can expose it. It is
   never returned to browser storage or logs. Next launch verifies the saved host
   key, authenticates, and reloads setup/dashboard automatically. **Forget saved
   connection** removes it. Temporary setup/sudo credentials are still cleared from
   memory on completion, failure, disconnect or expiry; remembering is separate.
   No account SSH configuration or credential file is imported by the application.
3. In **Install**, use the model/optional-component checkboxes. Installed items
   start checked. Open each ⓘ dialog for size, compatibility and pinned license
   details. There is no Review button or duplicate model selector.
4. Check additions/uncheck removals and click **Apply changes** once. Missing
   model runtimes install their pinned sources, isolated Python environment and
   dependencies before verified weights; selecting GGUF does not replace ConvRot
   or switch the daemon environment. Generation selects the model's interpreter.
   Additions finish before managed removals; history, outputs and shared dependencies
   are preserved. Hardware-ineligible choices remain disabled. Fresh setup retains
   the full selection, bootstraps one model with guarded prerequisites/reboot, then
   reconciles the remaining models and optional components.
5. Watch actual action/download progress. Reload observes the running operation
   without starting another. Stop preserves completed changes, not rollback;
   interrupted work needs explicit review and retry. Busy generation jobs are
   rejected, never cancelled automatically. Exact consent/digests remain enforced.
   Sudo rejection shows a specific administrator/password-policy explanation.
   Use **Retry failed stage** for an eligible failed environment; do not reinstall
   a completed setup. Restarts attach to saved work, and remembered connections
   authenticate automatically. Unknown/changed host keys always stop recovery;
   no automatic trust approval occurs.
6. Refresh the dashboard, inspect diagnostics, then submit a real job when ready.
   Environment ready, daemon healthy, verified weights, model loaded and a real
   playable video are distinct milestones. A successful scan proves none of them.

### Optional acceleration and duration

- Separate remembered switches control **install SageAttention** and **use it for
  generation**. Existing pruned environments can add it without reinstalling models.
  Version **1.0.6 (pure Triton)** is wheel-hash pinned and GPU-probed; it does not
  upgrade Torch or require a CUDA SDK download. Installation requires an idle queue.
- Sage is distinct from sparse attention. It applies to supported attention in the
  pruned ComfyUI backend; masked/full-precision layers retain PyTorch. Upstream kernel
  fallbacks are logged. Legacy Diffusers profiles do not advertise Sage support.
- **Turbo LoRA v4-600 EMA** has separate remembered install/use switches. The
  optional pinned download is about 785 MB; it does not replace base models or
  upgrade Torch/ComfyUI. Currently enabled for native ConvRot INT8/mixed only.
  Start with **6 steps, strength 1.0** (supported range: 4–8 steps). The optional
  low-VRAM merge mode can soften quantized output. The publisher warns about
  four-step fast-motion smear and preview audio quality. This is a dedicated
  LoRA + pruned-conditioning + custom-sampler integration, not arbitrary LoRA loading.
  Exact checkpoint/code identities and generation choices are saved in job inputs.
- **Experimental sparse attention** is optional for ConvRot INT8. Its one-click
  setup downloads about 22 MB of pinned code/kernels, checks them on the GPU,
  and needs **no new model weights or dependency upgrades**. Start with
  **Quality-first (50%), 20 steps and a 5-second clip**. Balanced (30%) and
  Fast draft (15%) trade more fidelity for less video-attention work; leave Off
  for the original path. SageAttention stays independent for compatible dense
  work. **Turbo + sparse is blocked** because the upstream sparse path bypasses
  some Turbo adapters. Presets are remembered; actual sparse execution/fallback
  is recorded in job output. See [validation and limitations](docs/plans/2026-09-29-sparse-attention.md).
- **Retained models**: the Comfy/pruned runtime keeps its base model, text encoder
  and VAEs between jobs where memory permits. GPU/CPU offloading remains automatic;
  under host-memory pressure, decoder caches—and, if necessary, diffusion weights—
  may reload while retaining the text encoder rather than exhausting RAM. **Unload models** explicitly releases the idle worker, never model files;
  queued/running work blocks unload. Native faults, forced cancellation and server
  shutdown also release the worker. The experimental legacy Diffusers path remains
  isolated rather than claiming unverified residency.
- **Timing estimates** use successful, hardware/software/configuration-matched runs,
  separating cold starts, full reuse and partial reuse. Phase and sampling-step
  averages expose sample counts and observed spread. No matching samples means no
  estimate—not a guessed countdown. Prompt-free timing data can be downloaded for
  future community analysis; nothing is automatically uploaded.
- Seconds and frames update each other at 24fps. Seconds snap to the nearest valid
  H3 length (`17*n+5`, 124–345 frames); the effective duration is shown explicitly.
- macOS saved settings: `~/Library/Application Support/H3Server/preferences.json`.
  Linux/Windows use the launcher's normal state directory (or `--state-dir`).
  Sage choices persist there too; no installation or generation starts on launch.

A rebuilt controller needs a new process for Python backend changes. You can launch
it on a separate port while preserving an older controller's private joiner workspace;
finish/export that work before closing the old process. Avoid concurrent changes
from multiple controllers.
On the next explicit Install, an older release's staging-only discovery can be
rediscovered with the current release and selected model, without deleting old
artifacts. The current discovery must have a verified plan/root and no launch or
approval evidence, and no checkpoints, active release or operations may exist.
Older prerequisite history is preserved only with validated digest-bound approval,
terminal `waiting_for_replan` status and no required reboot; any composite launch
marker must match that historical digest exactly. Launched environment stages and
ambiguous/malformed history stay blocked; actual interrupted work still needs its
matching release. Scan, selection and reconnect never perform this replacement.
Current controllers serialize staging;
older versions do not share that lock, so mixed-version concurrent staging is
not supported. Remote Python is not needed for this proof (a bounded, sidecar-free
SQLite copy, if present, is checked read-only by the local controller).

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

The managed headless ComfyUI/GGUF backend supports local text-to-video and
first/last-frame images with audio, 24 fps, 20 default steps, seed and
multiples-of-32 canvas. ConvRot INT8 additionally supports reference images,
videos and audio using a separately consented ~21 GB Ref2VA checkpoint. Uploaded
assets are retained privately with job inputs. No ComfyUI server, hosted API,
arbitrary workflow/LoRA or negative-prompt/CFG controls are exposed. Generation options come from the **active
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
duties and restrictions on improving unrelated AI models also apply. An Install action
cannot cure ineligibility. Both Abiray cards declare MiniMax H3 Community License
(`other`); Winnougan encoder publisher metadata says Apache-2.0 but does **not**
remove upstream MiniMax/Qwen derivative obligations. The UI presents these
provenance disclosures with the full upstream license before the single Install action.
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
