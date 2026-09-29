# Task 3: headless pruned runtime operation

Status: implemented fixed adapter and managed provisioning; **not H3 inference
certified**. Native CPU imports and synthetic H264/AAC export were checked; no
model weights, model load, GPU inference or actual target/service installation.
Task 4 still owns browser selection/approval integration and executable rebuild.
Existing main-worktree executable was not changed.

## Runtime/public contracts

- `backends.select_backend(paths, profile=...)` selects exactly one installed
  profile. `None` retains legacy behavior. Unknown/unavailable names fail; no
  Diffusers/original-weight fallback for a pruned name.
- `Worker(..., profile=...)` selects that backend. Remote CLI adds `--profile`;
  recovery generates it in the approved user unit for pruned plans **before**
  reboot continuation/activation. No mutable global active-profile file or
  environment override controls selection. Existing legacy defaults are intact.
- `Backend.runtime_id` and `.profiles` expose the actual installed selection.
  `/api/v1/capabilities` reports these, actual adapter LoRA/sparse support and
  `service_limits.steps_default`. Pruned LoRA and sparse attention are unsupported.
- `GenerationInput` accepts the five catalog IDs. API omitted steps default to
  20 for pruned and 50 for legacy. The normalized `GenerationSettings` constructor
  retains its legacy defaults; adapters execute the supplied normalized steps.
  Explicit pruned duration uses `max(5, round(seconds*24))`, then upward 17k+5
  alignment. Service policy remains 124..345 frames, axes 32..1344/multiples of32,
  area<=1344*768; it is not a new reference/image/2K API. Canvas is preserved.
  Seed/steps map to the real sampler. Unknown inputs (including fps, CFG,
  negative prompt, images, workflows) are rejected, not ignored.
- Jobs expose `effective_geometry={width,height,num_frames,fps:24,duration}` in
  addition to existing effective_duration and normalized settings. This describes
  the requested effective geometry, not proof of successful generation.
- Task 2 catalog remains unchanged. Small required integration: domain settings
  now validate exact catalog lookup instead of the legacy two-name tuple; legacy
  adapter explicitly rejects pruned settings. Controller target passes approved
  `plan.profile` and download payload `artifact_set_digest` to consent, and that
  profile to the downloader. A plan cannot authorize another artifact set.

## Automatic provisioning and paths

`build_plan(..., profile=<pruned ID>)` includes exact `runtime` and
`artifact_set_digest`. `validate_plan` reconstructs them, revisions and candidate
requirements before execution. Approval digests cover them. Model/download
approval remains separate: runtime.weight_download_approved is always false.
The full upstream license and runtime.license_disclosures must be presented by
Task 4 before independent license/eligibility/download approval.

Environment remains `AppPaths.environments/<canonical-plan-digest>/`:

- `bin/python`: isolated venv, also runs the loopback application daemon.
- `ComfyUI/`: exact a7169322485d0049380fb207fa17e9fb3ec40486 checkout.
- `ComfyUI-GGUF/`: exact 6ea2651e7df66d7585f6ffee804b20e92fb38b8a checkout,
  selected only for GGUF profiles. Not a global Comfy install or discovered plugin.
- `resolution.json`: pip's target resolution including selected wheel metadata.
- `resolved.txt`: complete `pip freeze --all`, reused as constraints on a retry.
- `runtime.json`: exact profile, artifact digest, provisioning contract and freeze
  SHA256. Runtime compares the actual installed freeze and runs pip check.
- `readiness.json`: written only after native import/CUDA BF16 kernel/codec smoke;
  model_ready and generation_tested remain false. Activation checks runtime.json
  against the approved selection before service IO.

Installer fetches only the exact public Git commits (shallow fetch), verifies all
source bytes, then installs the application wheel and pinned-source requirements
with binary-only PyPI packages. Candidate direct pins: Torch2.14.0,
Torchvision0.29.0, Transformers5.17.0, PyAV18.1.0, HF Hub>=1.32,<2.0. Upstream
pins include kitchen0.2.36 and aimdo0.5.5. This is a target-resolved version lock
plus pip report, **not a portable transitive hash lock or tested CUDA matrix**.
Existing recorded Linux Torch metadata selects CUDA13.0.3; target import/kernel
smoke must pass with its driver or setup fails, without driver substitution.

Pinned source inventory fingerprints are SHA256 of sorted compact JSON mapping
all relative tracked file paths to SHA256. They include requirements, licenses
and tokenizer, reject modified/extra files and links; Git internals alone are
excluded. Bytecode writing is disabled before native imports. Fingerprints were
computed from the pinned evidence checkouts and independently checked against
`git archive` copies during native smoke:

| Source | Files | Unpacked bytes | Inventory SHA256 |
|---|---:|---:|---|
| ComfyUI |1233|48322430|4595a17750229415aa8f067bf8036eecb8e82fcddfd253adfcebc2d543b60cae|
| ComfyUI-GGUF |16|120270|0224788b3db97592c9151c1a35b6aaf76fee70947a23cc478d558fa843bf7428|

All three tokenizer files are additionally verified against catalog sizes/hashes.
Known source bytes already include 4456704 tokenizer bytes once. Package wheels,
Git objects, unpacked dependencies and prior environments are additional,
unresolved disk costs. Installer checks known source bytes plus 5GiB operational
reserve (existing default worker's 4GiB output cap +1GiB free reserve), **not an
invented all-install multiplier**. Pip/disk failure prevents readiness. Selected
weights retain Task 2's per-file partial->final rename, with no second cache copy.

No setup step downloads weights. Weight paths stay
`models/h3-<artifact-set-digest>/{diffusion_models,text_encoders,vae}/...`.
Before execution the worker checks profile consent and the entire exact weight
inventory offline, then provisioning/source/tokenizer/freeze integrity; only then
imports native code and loads models. Readiness runs bounded background child
checks; each execution rechecks. Readiness does not claim a loaded model or a
successful generation.

## Actual execution and lifecycle

`backends.pruned` imports explicit pinned modules/classes. It does not run
ComfyUI main.py, instantiate PromptServer, initialize extra/API nodes, discover
custom-node directories, or accept graphs/paths. GGUF package relative imports
are preserved. Only selected snapshot folders and an empty app-owned embeddings
directory are registered. `tmp/comfy/input` is descriptor-validated before
folder_paths' import-time directory creation. HF/Transformers are offline; no
Hub tokenizer/config fetch is needed or permitted.

Composition follows `h3-pruned-runtime.md`: GGUF or native diffusion loader;
**native metadata-aware CLIP for every profile**, both VAEs, no-image
MiniMaxH3ImageToVideo, sigma shift12/3, cfg1 res_multistep/simple joint-AV sampler,
AV split, native video/audio decode, real CreateVideo/save_to MP4 mux. Code uses
legacy tuples versus V3 `.result`/indexing correctly and inference_mode around
execution. No turbo LoRA or independent audio sampling.

ComfyUI main.py normally initializes Aimdo and replaces the core patcher for
DynamicVRAM (`main.py:264–304`). This headless composition explicitly disables
that optional mode, using the ordinary upstream ModelPatcher/offload path (also
used by GGUF); it does not pretend server startup ran. Memory fit remains unknown.
Latent previews are disabled to avoid auxiliary preview weight discovery.

Known non-Blackwell or unknown-family devices fail NVFP4 before any model load;
all visible devices must pass conservative positive identification. This does
not impose a Blackwell restriction on the shared INT4 encoder or mixed/INT8/GGUF.
No old96/192GiB RAM rejection or measured fit claim is introduced.

Stages: loading, text_encoding, denoising, video_decoding, audio_decoding, mux,
validation. Global Comfy progress hook emits **stage-local** counters and is
restored on every exit. A cancellation watcher sets the upstream interrupt flag;
checks occur between stages. BaseException-derived native interrupts clean up.
SIGTERM requests interruption; the parent still kills the group after its
existing deadline, because native decode/mux/kernel calls may not interrupt.

Each unique job directory reserves `partial.mp4`, passes a Linux anchored
`/proc/self/fd/<job-dir-fd>/partial.mp4` to PyAV, validates actual AV media, rechecks
consent and atomically renames to `video.mp4`. Parent separately validates media
before marking completed; only completed outputs are served. Live partial-byte
limits and failure/cancel/restart cleanup cover partial.mp4 too. macOS cannot
reopen a directory as `/dev/fd/N/name`; controlled IO tests emulate only this
native export boundary through the real dirfd. Production server is Linux.

## Validation boundaries

New tests directly exercise production composition with faithful controlled
legacy/V3 node IO, not an unrelated graph builder or numerical model inference.
Other fixtures cover settings, selected dispatch, offline missing files, source
and dependency integrity, consent recheck/revocation, atomic output/confinement,
interrupt cleanup/progress, installer source/requirements/plan isolation,
replan, service selection and activation/rollback. Existing process deadline,
maintenance, recovery/security tests are rerun unchanged.

With supervisor approval, a fresh isolated `/tmp/h3-pruned-native.VeqU5b` venv
installed published macOS arm64/CPython3.14 wheels with exact direct pins. No
worktree/global environment was changed. PyPI metadata was bounded to3MiB per
package and confirmed platform wheels before installation. `resolution.json`,
`resolved.txt` (97 distributions), `install.log`, `app-resolution.json`,
`app-dependencies.log` and `smoke.log` remain there. All application dependency
constraints from pyproject.toml were also installed; native imports/codecs and
h3server.remote.app imports passed together (anyio resolved to4.11.0). The
application itself was imported from source, not rebuilt/installed as a wheel.
The reproducible opt-in harness is `tests/smoke_pruned_native.py <sandbox>` with
PYTHONPATH=src and that sandbox's interpreter/source folders. It blocks socket
connect/bind, forces CPU plus HF offline, verifies full pinned source fingerprints,
native loaders/sampler registry and old-quant metadata conversion, then uses the
real V3 CreateVideo/PyAV path to encode/decode 124 synthetic black32x32 frames and
silent stereo32kHz AAC. Production media validation also passes. This is actual
codec/native-import evidence, **not H3 conditioning/sampling/quantized inference**.

Native CPU pip check passed. Final full `pytest -q -W error`: **798 passed,
10 skipped** (+41 tests over Task 2); worker/new-IO focused suite:34 passed.
One intermediate full run exposed an unclosed-reader ResourceWarning. A focused
red test proved the worker's termination-error branch could skip closing stdout;
cleanup now closes it in a nested finally while retaining the process/ownership
state for recovery. The spontaneous warning's precise origin was not captured;
tracemalloc and subsequent ordinary full runs passed. Exact command history and
scoped commit/diff are in the Task 3 handoff. No server credentials, actual target, sudo/reboot,
model-weight downloads, GPU inference, push/merge or delegation occurred.

Residual validation: real Linux CUDA dependency/driver compatibility, quantized
model numerical loading/sampling, peak VRAM/RAM, speed/quality, optimized versus
eager ConvRot behavior and native cancellation latency remain unmeasured.
Installer/recovery/service operations were controlled fixtures, not target IO.
Task 4 must bind visible selected artifact approvals and send the active profile
in generation requests, and rebuild the executable. Independent review plus
separately approved hardware inference acceptance remain required.
