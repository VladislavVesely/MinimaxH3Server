# Pruned H3: source contract and catalog handoff

Status: **source-inspected, ready for implementation, not inference-certified**.
This document preserves Task 1 investigation and Task 2 catalog decisions. No
full model weights, Torch/GPU runtime, server access, or inference were involved.
Published LFS hashes below are not locally computed whole-weight hashes.

## Immutable evidence

Source checkouts retained in `/tmp/h3-gguf-evidence/`:

- Comfy-Org/ComfyUI `a7169322485d0049380fb207fa17e9fb3ec40486`.
- city96/ComfyUI-GGUF `6ea2651e7df66d7585f6ffee804b20e92fb38b8a`.
- G: `Abiray/MiniMax-H3-Pruned-GGUF` revision
  `76061e7fe4866dbc68903bfdf852430f0f1b2487`.
- C: `Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot` revision
  `908eccad7e68751190d04c171956f163bfeed741`.
- E: `Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI` revision
  `6387f8cd370fd8b4deaa9aa7e9e1be4d7298e7df`.

Saved HF API JSON inventories named `Abiray--MiniMax-H3-Pruned-GGUF.json`,
`Abiray--Minimax-H3-nvfp4-INT4-INT8-Convrot.json`, and
`Winnougan--MiniMax-H3-INT4_Convrot_ComfyUI.json` provide actual filenames, bytes,
LFS hashes and revisions. Saved cards disclose publisher guidance, not measured
memory. Also investigated but NOT selected: Comfy-Org/MiniMax-H3, nif0 encoder/
mmproj, and Kijai LoRAs. Inventories, not advertised card filenames, govern files.

### Exact weights

Each profile uses exactly one diffusion row and the same three auxiliary rows.
URLs are `https://huggingface.co/{repository}/resolve/{revision}/{path}`.

| Role/repo | Path | Bytes | SHA256 |
|---|---|---:|---|
| diffusion G Q4 | MiniMax-H3-FL2VA-Pruned-Q4_K_M.gguf | 11564180576 | d74e644906fe95b8c7bf4837ba9d3f9392925f7301675a0993d419b62a4824a3 |
| diffusion G Q5 | MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf | 14068966496 | abe739ac98bc14d6e4e56987198765b02899da632ad254c820f559b6dc4d1171 |
| diffusion C mixed | MiniMax_H3_FL2VA_pruned_mixed_int4_int8_convrot.safetensors | 15903012791 | 541c7797d815a165a3b71fa6cfe03cafa1ba658cb03af1dc9f17cfe3985e7f99 |
| diffusion C INT8 | MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors | 20970379688 | f07a54277ca85b59e5243a924fb5c54d79768f820fc4155c1b614b6f613e9dbf |
| diffusion C NVFP4 | MiniMax_H3_FL2VA_pruned_nvfp4.safetensors | 12528636865 | 6ab7f0c48141e7919b32f925ca3def22e06a6aebeb9e0b6f5a0be0fe8409976f |
| text encoder E | qwen3vl_32b_minimax_h3-int4_convrot.safetensors | 14173709116 | a97a557136057a8bcf6f2459b0875aeee3e8274408bd6616b669b41caefdb48d |
| video VAE C | vae/minimax_h3_video_vae_fp16.safetensors | 5207808557 | 88539d729fd7e9f9de765498428cd8e9152b8420317ec0e7542a362fffb7e0e1 |
| audio VAE C | vae/minimax_h3_audio_vae_fp32.safetensors | 605254869 | 83043bf3afccf4cab4d7e114f6e04607e9687bef16930a2f6088d721a893d3c4 |

Pure INT4 diffusion and the advertised text_encoders directory are **absent** in
C at this pin. `h3-convrot-int4` is explicitly unavailable; no invented URL.
E is deliberately selected, not a silent original/NVFP4 encoder substitution.

### Code-bundled tokenizer

Pinned ComfyUI already includes these in `comfy/text_encoders/qwen25_tokenizer/`:

| File | Bytes | SHA256 |
|---|---:|---|
| merges.txt | 1671853 | 8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5 |
| tokenizer_config.json | 8018 | 49292bd4a58a43382bf01311bc3ed7a151a7f16fe07d155e900ea1af281555db |
| vocab.json | 2776833 | ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910 |

Preserve and verify these during runtime provisioning. No Qwen HF snapshot,
config, chat-template download or network discovery is needed. They are counted
once in source provisioning, not fetched again by the weight downloader.

| Profile ID | Weight download bytes | Weights + tokenizer bytes |
|---|---:|---:|
| h3-gguf-q4-k-m | 31550953118 | 31555409822 |
| h3-gguf-q5-k-m | 34055739038 | 34060195742 |
| h3-convrot-mixed | 35889785333 | 35894242037 |
| h3-convrot-int8 | 40957152230 | 40961608934 |
| h3-nvfp4 | 32515409407 | 32519866111 |

Auxiliary weights alone total 19986772542 bytes. Tokenizers total 4456704.
Dependency/source installation bytes are separate and unresolved.

## Runtime evidence (Task 3 contract, NOT implemented in Task 2)

At the pinned ComfyUI revision:

- `comfy_extras/nodes_minimax_h3.py:29–166`: canvas helper, temporal grid,
  NestedTensor AV latent creation and `MiniMaxH3ImageToVideo.execute`.
  Lines 381–426: `MiniMaxH3SigmaShift`.
- `nodes.py:775–820,843ff,991–1055,1580–1633`: loaders and sampler;
  320–346: VAE decode. Legacy nodes return tuples.
- `comfy/text_encoders/minimax.py:1–235`: raw prompt, 50-layer hidden encoder,
  `minimax_token_tags`; `qwen3vl.py:1–65,145–205`: tokenizer location.
- `comfy/sd.py:1030–1088,1600–1625,1720–1736,1970–2005`: VAEs,
  metadata-aware native encoder load/detection; `convert_old_quants` is required.
- `comfy/quant_ops.py:190–270`, `comfy/ops.py:1237–1285`,
  `comfy/utils.py:1297–1335,1434–1499`: quant registry, layouts and progress.
- `comfy/model_detection.py:390–419` detects H3 via video/audio patch keys and
  compressed AdaLN; `model_base.py:2152–2197` handles joint AV/text scaling.
- `comfy_extras/nodes_lt.py:972–1003`, `nodes_audio.py:98–136`,
  `nodes_video.py:206–263`: AV split, audio decode and CreateVideo.
- `comfy_api/latest/_input_impl/video_types.py:1116–1175`: actual PyAV
  MP4/H264/AAC export; `comfy/model_management.py:2173–2200`: cancellation.
- `comfy_api/latest/_io.py:2344–2374`: V3 NodeOutput has `.result` and indexing.
- `blueprints/Image to Video (MiniMax H3).json`: base 20 steps,
  res_multistep/simple, positive-only BasicGuider. No turbo LoRA selected.
  Blueprint NVFP4 encoder is deliberately NOT copied to these profiles.

At pinned GGUF source: `nodes.py:1–340` implements `UnetLoaderGGUF`, GGMLOps
and GGUFModelPatcher; `loader.py:1–70` accepts architecture `wan`. Bounded Q4
header (65536 bytes) reports `general.architecture=wan`, 532 tensors, 60 KV
entries; subsequent native tensor-key detection recognizes H3. A missing literal
MiniMax whitelist name is not incompatibility evidence.

### Fixed local composition

Use one supervised inference process, pinned runtime import path and explicit
modules only. Do NOT run ComfyUI main.py, PromptServer, hosted
`comfy_api_nodes/nodes_minimax.py`, arbitrary workflows, or plugin discovery.
Comfy options default `args_parsing=False`; do not enable upstream CLI parsing.
Load GGUF as an explicit package from its pinned directory (relative imports).

Before importing/loading: require runtime provisioning and exact approved
artifacts; verify hashes offline, set HF/transformers offline environment;
register ONLY approved snapshot folders for diffusion_models, text_encoders,
vae in folder_paths. `unet` aliases diffusion_models. Isolate embeddings to an
empty application-owned directory. No fallback downloads.

```python
# GGUF profile only:
model = gguf_nodes.UnetLoaderGGUF().load_unet(model_basename)[0]
# Native quantized safetensors profile only:
model = nodes.UNETLoader().load_unet(model_basename, "default")[0]
# Shared, native metadata-aware CLIP loader, even for GGUF diffusion:
clip = nodes.CLIPLoader().load_clip(encoder_basename, "minimax", "default")[0]
video_vae = nodes.VAELoader().load_vae(video_vae_basename)[0]
audio_vae = nodes.VAELoader().load_vae(audio_vae_basename)[0]
positive, latent = MiniMaxH3ImageToVideo.execute(
    clip=clip, vae=video_vae, prompt=prompt, width=width, height=height,
    length=frames, first_frame=None, last_frame=None).result
model = MiniMaxH3SigmaShift.execute(model, 12.0, 3.0)[0]
sampled = nodes.KSampler().sample(
    model=model, seed=seed, steps=steps, cfg=1.0,
    sampler_name="res_multistep", scheduler="simple", positive=positive,
    negative=[], latent_image=latent, denoise=1.0)[0]
video_latent, audio_latent = LTXVSeparateAVLatent.execute(sampled).result
images = nodes.VAEDecode().decode(video_vae, video_latent)[0]
audio = VAEDecodeAudio.execute(vae=audio_vae, samples=audio_latent)[0]
video = CreateVideo.execute(images=images, fps=24.0, audio=audio,
    bit_depth=8, color_space="sRGB", codec="none")[0]
video.save_to(confined_partial_mp4_path)
```

Wrap actual execution in torch.inference_mode(). V3 imports are from
`comfy_extras.nodes_minimax_h3`, `nodes_lt`, `nodes_audio`, `nodes_video`.
Test this production composition, including tuple versus NodeOutput distinction.
Reject nonempty negative prompts and non-1 CFG unless an intentional conditional
path is separately implemented/tested; never silently discard settings.

No-image `MiniMaxH3ImageToVideo` is T2V: raw prompt tokenization with images=[],
`encode_from_tokens_scheduled`, unnormalized hidden layer 50 width 5120 and
minimax_token_tags. Generic chat templates/Qwen conditioning are wrong.

T2V execute does NOT invoke adapt_canvas. Validate positive multiples of 32 and
preserve the requested canvas, or explicitly apply/report adapt_canvas (768
short edge, area cap 768*1344, axes rounded to 32). Frames snap upward from
max(5, requested) until frames % 17 == 5; video latent T = 2 + 5*((frames-5)//17),
audio T = round(frames/24*40). Latents are video [1,24,T,H//16,W//16] and audio
[1,32,2,T40]. Duration mapping: max(5, round(seconds*24)), then snap. Fixed 24fps:
reject unsupported fps. Return effective dimensions/frames/fps/duration. Training
range approximately 124–362 frames; beyond it unverified. No image/reference/2K
API is included. Joint AV schedules must not be sampled independently.

Video VAE tiles internally (spatial256, temporal17). Audio decoder produces
normalized stereo 32000Hz waveform; mux trims to video duration. save_to(.mp4)
uses PyAV with H264/AAC, not a dummy frame dump. Use confined unique partial MP4,
atomic publication on success, no SaveVideo shared-folder/UI side effects.

Progress hook: `comfy.utils.set_progress_bar_global_hook(hook)` with
hook(value,total,preview,node_id=None); KSampler ProgressBar uses latent_preview.
Disable preview method to avoid preview-weight discovery. Explicit loading,
conditioning, sampling, decode and mux stages; counters are stage-local, not a
fabricated globally monotonic denominator. Restore previous hook in cleanup.
Cancellation watcher: interrupt_current_processing(True), and
throw_exception_if_processing_interrupted() between stages. The interrupt
exception derives from BaseException, not Exception. Reset False on start/exit.
Native kernels/decode/mux may not interrupt promptly: retain worker kill deadline
and clean partial files. Do not promise instantaneous cancellation.

### Quantization proof and limitations

Task 1 bounded range inspections:

- E encoder JSON header 181584 bytes; header-only SHA256
  `093082b70d481fe2b7acf2fca3324da9e03fc19baacfeaaaa36927457b8fe213`.
  Visual norm BF16 [4608] and layer49 q_proj I8 [8192,2560] match native
  MiniMax QWEN3VL_32B detector. Old `_quantization_metadata`: 350 convrot_w4a4
  layers (group256/quant-group64), one int8_tensorwise embedding. Native
  sd.load_clip preserves metadata and convert_old_quants converts it into
  .comfy_quant tensors. **Do not use GGUF CLIP loader** for this encoder.
- Mixed header 102632 bytes, SHA256
  `86f75a8e7014d824188eddd39ef91df8498a13bf7d3307f31397df0a06c40bda`;
  14868-byte quant tail: 117 convrot_w4a4 linear_dtype:int8/group256 and 83
  int8_tensorwise convrot:true/group256. Native ops propagate linear_dtype.
- INT8 header 95416 bytes, SHA256
  `c1a65ff59085cbed8a6ea7c935ae7e58b1d2f870239df54c58a68fb7989e1cd8`;
  14400-byte tail: 200 int8_tensorwise convrot:true/group256 entries.
- NVFP4 header 116000 bytes, SHA256
  `af8c4e1fd4745336b6c13d9c82e4716bc834f5ecaa7b7f5d377ae649b6bcd820`;
  200 quant entries, three individually fetched records declare format:nvfp4.
  No __metadata__ header object (detection uses tensors). Large noncontiguous
  sweep was refused; final diagnostic KeyError is not a successful full sweep.
- comfy-kitchen 0.2.36 wheel inspected in memory, not installed: 231027 bytes,
  SHA256 `1fe58d9035b2b3d5524f8e344a5c2abcf7b6cc50c1e469238286e20cac45236b`.
  tensor/convrot_w4a4.py:80–112,116–178,215–261 and
  backends/eager/convrot_w4a4.py:118–122,140–199 implement int4/int8 layouts,
  unpack/rotation/matmul fallback. Eager temporaries may be large/slow. This is
  source support, not optimized-kernel, throughput or 29GiB host-RAM proof.

Publisher guidance: Q4 16GB VRAM, Q5 24GB, mixed 15+GB (~15.5), INT8 24+GB;
NVFP4 Blackwell-only. These are not measured peaks or sums of file sizes.
The chosen INT4 encoder is not Blackwell-only. Do not substitute Comfy-Org's
NVFP4 encoder on a 4090. Host RAM stays unknown, not inherited legacy96/192GiB.

Task 2 uses conservative exact GPU names: NVIDIA GeForce RTX4090 (spelled
`NVIDIA GeForce RTX 4090`) is Ada; NVIDIA GeForce RTX5090 is Blackwell, per NVIDIA
product specification pages:
https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/
and https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/ .
Other names remain unknown, even if the device could be Blackwell. No inference
of family from VRAM, no generic 'RTX' heuristic, no CUDA usability assertion.
`gpu_family_eligible(hardware, "blackwell")` returns True/False/None; runtime must
require **True** for NVFP4. Mixed known-incompatible devices return False because
no device-selection contract exists yet. Unknown family is not permission.

### Provisioning and licensing gates

Installer must pin both source commits under application-owned root/venv. Pinned
Comfy requirements include comfy-kitchen==0.2.36, comfy-aimdo==0.5.5, frontend
1.53.6, templates0.11.70, embedded-docs0.5.12; Torch/torchvision/torchsde,
numpy>=1.25, transformers>=4.50.3, tokenizers>=0.13.3, safetensors>=0.4.2,
PyAV>=17, einops, sentencepiece, scipy, Pillow, psutil, aiohttp/yarl, pyyaml,
SQLAlchemy/alembic, filelock, requests, simpleeval, blake3 and others.
GGUF requirements: gguf>=0.13, sentencepiece/protobuf. These are not a resolved
lock: installer must resolve/record CUDA-compatible torch/torchvision/dependencies
and run import smoke. No tested GPU wheel matrix was established. Importing
model management can initialize GPU selection; no full runtime import was run.

Abiray cards declare MiniMax H3 Community License (`other`); GGUF has LICENSE/
NOTICE. Winnougan metadata says apache-2.0 but does not eliminate upstream
MiniMax/Qwen derivative obligations. Preserve upstream ComfyUI licensing and
GGUF Apache-2.0 notices. Present provenance/terms before independent explicit
license, eligibility and selected-model download approval. Source research and
general installation consent are not model/license approval.

## Task 2 public API

`h3server.backends`:

- `load_manifest()` unchanged legacy manifest only. No implicit pruned dispatch.
- `load_profiles()` unchanged two legacy profiles/constraints/order.
- `load_profiles(include_pruned=True)` five smaller pruned choices first, then
  unchanged legacy profiles. Pruned profiles are experimental source evidence;
  evaluating incomplete hardware/requirements can still return UNKNOWN.
- `get_profile(name)` exact lookup across both families; no fallback.
- `get_artifact_set(name)` frozen ArtifactSet, pruned only; rejects unknown or
  explicitly unavailable pure INT4.

`h3server.backends.catalog`: Artifact has role, repository, revision, path,
size_bytes, sha256, local_relative_path, derived immutable URL. ArtifactSet has
profile_name, label, runtime_id (`comfyui-h3-gguf` or `comfyui-h3-native`), immutable
artifact/tokenizer/runtime-source tuples, publisher_vram_guidance,
required_gpu_family, license_disclosures, memory_status=unmeasured,
validation_status=source-inspected-not-inference-certified, digest, weight_bytes,
tokenizer_bytes, selected_bytes, runtime_bytes=None and peak_weight_disk_bytes.
Digest is SHA256 of canonical sorted compact JSON of dataclass fields, including
runtime pins, all roles/paths/hashes and licensing disclosures. Auxiliary changes
therefore invalidate approval. `UNAVAILABLE_VARIANTS` gives displayable reasons.

Role local folders are catalog-controlled diffusion_models, text_encoders and
vae; tokenizers remain within runtime source at the full code-relative path.
Native CLIP loading is required for every profile. No artifact-set readiness
claim is made by catalog lookup.

Downloads: existing record_consent, require_consent, snapshot_path,
verify_snapshot and download_model now accept optional keyword `profile_name`.
None (or explicit legacy name) preserves the old behavior. For pruned consent,
record_consent additionally **requires artifact_set_digest** plus existing pinned
upstream license_sha256 and three explicit boolean attestations. The caller must
present full upstream terms AND catalog auxiliary provenance/license disclosures.
Separate per-profile consent JSON stores schema2 binding including set digest.
Legacy consent cannot authorize pruned downloads or vice versa. CLI adds
`--profile`; it never records consent itself.

Weight snapshots are `models/h3-<artifact-set-digest>/`; partials are
`tmp/downloads/<artifact-set-digest>/<local-relative-path>.part`.
`verify_snapshot(..., profile_name=...)` verifies exactly four weights, inventory,
size and full SHA256 offline, rejecting extras, symlinks or mismatches. It does
NOT establish runtime provisioning or verify runtime tokenizers; Task 3 must
verify those separately and combine the gates. Calling it without profile is
legacy behavior, not auto-discovery. No original snapshot fallback exists for an
explicit pruned name.

Transport receives a pinned local name, profile and artifact-set digest, never a
caller URL. The child independently reconstructs selected inventory, checks
consent, and resolves each artifact's own repository/revision/source path. Existing
bounded process deadline, cancellation polling, redirect policy, no credentials,
partial resume, descriptor confinement and atomic per-file promotion are reused.

Disk: fresh weight acquisition keeps completed files plus remaining partials,
then renames each verified partial without cache/final duplication. Thus peak
**selected-weight bytes** equal weight_bytes, not 2x or 3x. Cross-filesystem rename
fails rather than silently copying. Existing other-profile snapshots and stale
partials still occupy disk; no automatic cleanup/dedup. Tokenizers occupy runtime
source bytes once. Profile DiskEstimate is a known selected-files lower bound;
installer must add unresolved runtime/package, output and any planned upgrade
coexistence budget. It is not an all-installation free-disk guarantee or an
estimate of incremental network transfer after resume.

## Task 2 validation and omissions

Initial focused TDD: `python -m pytest -q tests/test_pruned_catalog.py` via .venv:
8 expected failures + 2 setup errors because public catalog/profile APIs did not
exist. First implementation rerun: 125 passed, 1 failed because missing selected
file correctly raised inventory ValueError rather than test's FileNotFoundError;
test expectation corrected. Next focused run: 126 passed. Extended controlled
transport/resume/confinement suite: 137 passed (6.36s), before final additions.
Final validation commands:

- `.venv/bin/python -m pytest -q tests/test_pruned_catalog.py tests/test_pruned_downloads.py tests/test_profiles.py tests/test_downloads_progress.py`: 137 passed.
- `.venv/bin/python -m pytest -q -W error`: 757 passed, 10 skipped (baseline supplied: 736 passed, 10 skipped).
- Read-only Python evidence check compared every catalog reference to saved HF
  inventory revision/path/size/LFS SHA256 and hashed local pinned tokenizer code:
  all 20 weight references and 15 tokenizer references matched.
- `git diff --check`: clean. Exact commit/diff and any subsequent rerun results
  are in the task handoff artifact.

All added downloads use tiny synthetic payloads and fake HTTP boundaries. They
prove selection, approval binding, byte/hash integrity, isolation, progress,
resume, URL pin resolution and offline rejection, **not numerical inference**.
Existing child-process regression tests continue to exercise actual deadline/
cancellation supervision. No runtime adapter, installer, remote dispatch, UI,
packaging rebuild or GPU validation is part of Task 2. Existing executable and
main worktree remain unaffected. Task 3 must explicitly opt in to the catalog
instead of changing default legacy callers implicitly; Task 4 owns user-visible
selection/licensing and packaging.

Residual risks: runtime/dependency import and CUDA-wheel compatibility, actual
quant kernels/VRAM/host-RAM/speed/quality, eager ConvRot temporary allocations,
PyAV codec availability and bounded native cancellation remain unmeasured.
Source/header compatibility does not establish tensor values or output quality.
Other Blackwell product names are conservatively unknown until evidence-backed
identification is extended. Full native GPU acceptance requires separate approval.
