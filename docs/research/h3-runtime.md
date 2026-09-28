# MiniMax H3 runtime decision: pinned, source-inspected, experimental

## Decision and confidence

Use the official Hugging Face **Diffusers modular MiniMax H3 `t2va`** workflow for
local text-to-video-and-audio execution. There is no hosted generation endpoint,
cloud fallback, or ordinary `DiffusionPipeline` H3 implementation in the selected
integration. Other workflows, original-format weights and `transformer_ref` are
outside this component's scope.

This is a **source-inspection decision, not execution verification**. No inference
packages or model weights were installed, no remote host was contacted, and no
video was generated for this research component. The design's real-generation
feasibility gate remains open for the later inference/deployment task. In
particular, these profiles are experimental, never verified or guaranteed fits.
The manifest loader reads local packaged JSON only; it is not an inference adapter.

### Immutable provenance

| Source | Revision |
| --- | --- |
| [Diffusers][diffusers-repo] | `5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2` |
| [MiniMaxAI/MiniMax-H3 model][model-index] | `42ed227ee7df40d41602854ae760620d6eb651fe` |
| [Original MiniMax project][minimax-repo] | `d21241f0a4b3acbb34c97dae47fa417b7065e438` |

The parent supplied fetched source and metadata from these revisions. The durable
record is [`src/h3server/backends/manifest.json`](../../src/h3server/backends/manifest.json):
it contains source URLs, saved-content SHA256 fingerprints, observations, candidate
dependencies, capability evidence, license prerequisites, experimental budgets,
and the complete selected artifact inventory. No whole upstream implementation
is vendored. Original-project provenance is recorded, but its runtime is not
selected and is not represented as tested. API metadata hashes fingerprint the
saved response bytes; re-fetching a pinned API resource can change unrelated
metadata/serialization. Raw source and model file URLs are revision-pinned.

## Model selection and verified-download inputs

[The modular model index][model-index] names `MiniMaxH3ModularPipeline` and
`MiniMaxH3Blocks`. Select `workflow="t2va"` when constructing the pipeline. This
prunes component requirements rather than loading both transformer partitions.
The selected subfolders are:

- `transformer`: `MiniMaxH3Transformer3DModel`, 14 weight shards and index/config;
- `text_encoder`: full `Qwen3VLForConditionalGeneration`, 14 weight shards plus
  index/config and auxiliary tokenizer/processor files;
- `vae`: `AutoencoderKLMiniMaxH3`, three weight shards and index/config;
- `audio_vae`: `AutoencoderKLMiniMaxH3Audio`, one weight file and config;
- `tokenizer`, `processor`, `scheduler`, `audio_scheduler`;
- root `LICENSE`, `model_index.json`, `modular_model_index.json`.

The conditioner reads the unnormalized hidden state after decoder layer 50;
the released full checkpoint is used even though its language-model head is unused.
Video and audio have distinct schedulers (released shifts 12 and 3) in one joint
transformer denoising pass. There is no separate post-hoc audio generation service.

**62 files, exactly 144,051,160,615 bytes** (about 134.16 GiB). This is the complete
selected on-disk download, not GPU or resident RAM use. It includes auxiliary
files duplicated across selected component folders. It excludes original `FL2VA`
and `Ref2VA` trees and all `transformer_ref` files. Do not download the whole
repository: unpruned `load_components()` can bring both transformer partitions.

Each `model.files` entry preserves upstream `rfilename`, `size`, `blobId`, and
`lfs` (SHA256, payload size, pointer size) where available. The pinned
`download_url_template` resolves each relative name without consulting `main`.
For later resumed downloads:

1. Bind checkpoints to repository, revision, relative path and expected size/hash;
   confine files using the existing application path contracts under `models/`
   and `tmp/downloads/`. Never trust an arbitrary request path.
2. Count actual received bytes; a resumed partial file is not verified completion.
   Recheck the final full file's size and digest before atomic promotion.
3. For LFS entries, check payload SHA256, **not** the Git blob ID of the pointer.
   For small non-LFS files, `blobId` is Git SHA1 of
   `b"blob " + str(len(content)).encode("ascii") + b"\0" + content`, not raw SHA1.
   There is no supplied raw SHA256 for those files; do not invent one.
4. Download approval and license acknowledgement precede acquisition. The manifest
   provides inventory only; no downloader or resumability implementation is
   claimed here.

## Candidate environment, not a resolved lock

Python **>=3.11** is the application baseline and also meets the fetched PyAV
requirement. The candidate constraints below satisfy the *inspected direct*
version constraints, but no resolver, wheel installation, imports or combination
has been tested. They are not a complete transitive/hash lock or an installation
command. Do not install giant inference dependencies on the controller/developer
machine as part of this research.

| Package | Candidate constraint | Evidence / limitation |
| --- | --- | --- |
| diffusers | Git revision above | H3 modular source pin, not an assumed PyPI release |
| torch | `==2.14.0` | Fetched PyPI metadata; Linux selects CUDA 13.0.3 libraries and Triton `~=3.8.0` |
| torchvision | `==0.29.0` | Metadata requires exactly torch 2.14.0 |
| torchao | `==0.18.0` | Meets Diffusers' `>=0.15.0`; wheel/ABI and int8 version-2 support still need testing |
| transformers | `==5.17.0` | Qwen3-VL API required; metadata has Hub `<2.0,>=1.5.0` |
| accelerate | `==1.15.0` | Meets inspected Diffusers/PEFT minima |
| peft | `==0.21.0` | Meets inspected Diffusers `>=0.17.0` |
| huggingface-hub | `>=1.32.0,<2.0` | Diffusers' exact declared range; **observed latest 2.0.0 is incompatible** |
| av | `==18.1.0` | `encode_video` PyAV muxing; codec availability untested |

The manifest preserves relevant requirement strings and versioned PyPI JSON
source URLs. A compatible exact Hub 1.x release was **not** fetched; selecting one
and locking all transitive artifacts remains unresolved, not silently replaced
by latest. TorchAO metadata supplies no useful torch ABI guarantee. CUDA library
metadata does not certify any installed driver, GPU compute capability, OS or
Python minor version. Target-side validation must resolve this environment,
verify driver/runtime compatibility and codecs, then record a tested lock before
claiming readiness. Do not replace a functioning driver merely because a candidate
wheel lists CUDA libraries. Native OS certification is a separate task.

## Resource profiles and offloading

These are upstream recipes from [the H3 documentation][h3-docs], not measurements
made by this project. The docs describe roughly 61.7 GB of bf16 transformer and
62.1 GB of conditioner; those descriptions are not a substitute for the exact
artifact inventory or peak-memory measurement.

| Experimental profile | Upstream recipe | Local planning budget (not measured) |
| --- | --- | --- |
| `h3-bf16-cpu-offload-experimental` | One 80 GB card, `ComponentsManager.enable_auto_cpu_offload(device="cuda", memory_reserve_margin="12GB")` | 192 GiB host RAM, 320 GiB free disk |
| `h3-int8-group-offload-experimental` | One 24–32 GB card, TorchAO int8 weight-only and streamed group offload; approximately 75 GB host RAM | 96 GiB host RAM, 320 GiB free disk |

The RAM and disk numbers are **local conservative estimates**, not measured
requirements or guarantees. Disk budgets two full artifact copies (staging/cache
and installed files) plus about 51.7 GiB for environment, temporary output and
overhead. More cache copies, videos or retained jobs need more space. GPU
thresholds use decimal GB from upstream prose; these are not calibrated usable
VRAM thresholds. Do not sum separate GPUs into a single-device fit.

`load_profiles()` returns existing `Profile`/`DiskEstimate` contracts with
`EXPERIMENTAL` confidence. Distribution versions, GPU models, architectures,
drivers and service managers remain unspecified, because none were certified.
The existing evaluator consequently returns **unknown** for otherwise ample but
uncertified hardware rather than advertising a verified installation path.

The int8 recipe quantizes both the transformer and conditioner using
`Int8WeightOnlyConfig(version=2)` with the upstream exclusion lists. For the
transformer, exclusions are `proj_in`, `audio_proj_in`, `context_embedder`,
`time_embedder`, `time_proj`, `token_refiner`, `norm_out`, `proj_out`,
`audio_proj_out`; its loader uses `low_cpu_mem_usage=False`. For Qwen3-VL they are
`model.visual`, `model.language_model.embed_tokens`,
`model.language_model.norm`, `lm_head`. Use Diffusers `TorchAoConfig` for the
transformer and Transformers `TorchAoConfig` for the conditioner, not interchangeably.
After `pipe.update_components(...)`, load the remaining t2va components. Freeze
both big models with `requires_grad_(False)`. Enable transformer
`enable_group_offload(offload_type="block_level", num_blocks_per_group=1, ...)`
and `apply_group_offloading(pipe.text_encoder.model, offload_type="leaf_level", ...)`,
with CUDA onload, CPU offload and `use_stream=True`. The documented video/audio
VAEs stay on the GPU. Quantizing on load does **not** reduce the original bf16
artifact download, and loading peaks remain unmeasured.

The docs additionally mention 12–16 GB with video-VAE leaf-level offload (no
stream) and a 960x544 canvas, and split-pipeline multi-GPU recipes. These are not
additional shipped profiles in this task. No disk-offload execution has been
established by the inspected H3 recipes. Dense default attention is the candidate
baseline. The optional `_flash_3_hub` example is Hopper-specific and fetches
kernels; do not activate that unpinned external download implicitly.

## Later adapter: inspected interfaces and failure boundaries

### Loading and invocation

[ModularPipeline source][modular] exposes:

- `ModularPipeline.from_pretrained(pretrained_model_name_or_path,
  trust_remote_code=None, components_manager=None, collection=None,
  workflow=None, **kwargs)`;
- `pipe.load_components(names=None, workflow=None, **kwargs)`; `names` and
  `workflow` are mutually exclusive. Loading fields such as
  `pretrained_model_name_or_path`, `revision`, `local_files_only` and `dtype` can
  be overridden globally or per component;
- `pipe(state=None, output=None, **kwargs)`. `output=[...]` returns a dictionary;
  omitted `output` returns pipeline state. Inspect `pipe.doc` for pruned inputs.

**Pin every component**, not just the outer config: the saved model index contains
Hub repository references with `revision=null`. The later adapter should load a
fully verified local snapshot, use `local_files_only=True`, and override each
component's repository path to that snapshot; otherwise an outer local config
alone does not ensure local component loading. Set `trust_remote_code=False`;
this integration uses classes in the pinned installed packages. Enforce offline
Hub operation as a second boundary, not a substitute for verified files.

The following is a source-derived **unexecuted interface sketch**, not a runnable
server feature or a claim that the environment is ready. `snapshot` denotes a
confined, verified local directory containing exactly the manifest selection;
`output_path` must come from the validated job's confined output directory.

```python
manager = ComponentsManager()
pipe = ModularPipeline.from_pretrained(
    snapshot, workflow="t2va", components_manager=manager,
    local_files_only=True, trust_remote_code=False,
)
pipe.load_components(
    pretrained_model_name_or_path=snapshot,
    revision="42ed227ee7df40d41602854ae760620d6eb651fe",
    local_files_only=True, trust_remote_code=False, dtype=torch.bfloat16,
)
required = ("transformer", "text_encoder", "tokenizer", "processor",
            "vae", "audio_vae", "scheduler", "audio_scheduler")
missing = [name for name in required if getattr(pipe, name, None) is None]
if missing:
    raise RuntimeError(f"Required H3 components failed to load: {missing}")
manager.enable_auto_cpu_offload(device="cuda", memory_reserve_margin="12GB")
results = pipe(
    prompt=prompt, num_frames=124, height=768, width=1344,
    num_inference_steps=50, generator=torch.Generator().manual_seed(42),
    output=["videos", "audio", "sampling_rate"],
)
encode_video(
    results["videos"][0], fps=24, output_path=output_path,
    audio=results["audio"][0], audio_sample_rate=results["sampling_rate"],
)
```

`load_components` catches exceptions and logs warnings instead of necessarily
raising. Assert every required component after loading; logging is not success.
`__call__` warns and **ignores** unknown inputs: validate a strict request schema
before calling, rather than forwarding arbitrary kwargs. In particular reject
`negative_prompt`, `guidance_scale`, sparse-attention requests, reference inputs
and any other options not actually implemented by the t2va adapter.

The [H3 geometry helpers][h3-pipeline] and [input preparation][before-denoise]
require 24 fps, dimensions divisible by 32, and an aligned duration of 5–15 seconds.
Frames snap **up** to `17*n+5`; valid aligned outputs in that duration range are
124, 141, …, 345 frames. Requesting 360 (nominally 15 seconds) snaps to 362 and is
rejected. Report the effective aligned duration; do not promise an exact integer
number of seconds. Default 16:9 canvas is 1344x768; smaller multiples of 32 are
allowed. `num_inference_steps` counts sigma-grid points including terminal zero,
so the documented model-evaluation count is one less. Use actual timesteps as the
progress denominator. The same generator drives conditioning, video and audio
noise draws; do not promise cross-hardware deterministic output without testing.

### Export, progress and cancellation

[The export source][export] implements
`encode_video(video, fps, output_path, audio=None, audio_sample_rate=None,
video_chunks_number=1) -> None` with PyAV, `libx264`, `yuv420p`, and audio muxing.
It accepts PIL frames, normalized NumPy frames, uint8 `[frames,height,width,channels]`
tensors or chunk iterators; audio is `[channels,samples]`. H3 outputs include
stereo audio and its sample rate (released default 32 kHz). Codec/import/mux
failures must surface as job failures. Validate the completed MP4 before exposing
it; existence alone is not a generation smoke test.

[MiniMaxH3DenoiseLoopWrapper.__call__][denoise] wraps the actual loop with
`self.progress_bar(total=len(block_state.timesteps))`, calls
`self.loop_step(components, block_state, i=i, t=t)`, then `progress_bar.update()`.
The block base offers `progress_bar(iterable=None, total=None)` returning tqdm
and `set_progress_bar_config(**kwargs)`; the pipeline forwards bar configuration
to subblocks. There is **no evidenced `callback_on_step_end` H3 input**. Do not
pass such a kwarg and mistake the ignored option for a working hook.

A later adapter must explicitly instrument the denoise block's progress-bar
factory (context manager/update contract) or wrap/subclass that loop at the
pinned block boundary, then test measured events; this component implements
neither. Console tqdm text is not a reliable machine callback. Model loading,
text encoding, video decoding, audio decoding and MP4 export/mux are separate
stages. Use byte/item/step counts where actually measured and indeterminate
progress elsewhere. The exporter reports chunks, not denoising steps.

No cooperative cancellation interface was established in this H3 call path.
The approved process-isolated worker design must terminate the worker process
group and clean partial output; an HTTP disconnect is not cancellation. Test
this later rather than claiming cancellation from the existence of a progress bar.

## LoRA and sparse-attention capability evidence

[MiniMaxH3ModularPipeline][h3-pipeline] explicitly inherits
`MiniMaxH3LoraLoaderMixin`. [The exact mixin source][lora] implements:

- `lora_state_dict(pretrained_model_name_or_path_or_dict, **kwargs)`;
- `load_lora_weights(pretrained_model_name_or_path_or_dict, adapter_name=None,
  hotswap=False, load_into_transformer_ref=False, **kwargs)`;
- `load_lora_into_transformer(...)`, `load_lora_into_transformer_ref(...)`;
- `save_lora_weights(...)`, `fuse_lora(components=["transformer",
  "transformer_ref"], lora_scale=1.0, safe_fusing=False, adapter_names=None, **kwargs)`
  and `unfuse_lora(components=["transformer", "transformer_ref"], **kwargs)`.

Thus LoRA is **supported in pinned upstream source**, not unknown or assumed
absent. This is not a declaration of live service availability: the adapter is
not implemented here, and LoRA with quantization/offload has not been tested.
For t2va target only `transformer`; do not pass the two-partition fusion default
uncritically. Prefer confined local safetensors with `use_safetensors=True` and
`local_files_only=True`: omitting the safetensors preference can enable pickle
fallback. A LoRA's compatible training partition cannot always be inferred from
module shapes: the two partitions have identical module names and a wrong LoRA
can load while degrading output. Require explicit compatibility metadata.

The inspected loader handles converted Diffusers and some non-Diffusers layouts,
including QKV de-interleaving. Pruned-checkpoint LoRAs can size-mismatch. Alpha-less
files use alpha equal to rank unless file metadata supplies alpha; malformed alpha
can be ignored with a warning. DiffSynth fp32 factors may change the memory
budget (upstream suggests restoring bf16). DoRA keys are filtered with a warning;
unrecognized layouts or missing denoisers can warn and return without loading.
The later adapter must reject unsupported formats/options up front and verify
actual adapter installation; do not treat those paths as successful LoRA requests.

Sparse attention is **unsupported by this selected backend contract**. The pinned
[MiniMaxH3AttnProcessor][transformer] documents and dispatches **full self-attention**
over one packed document. No H3 sparse mask or sparse request option is evidenced
in this integration. Choosing Sage Attention or FlashAttention is not evidence
of sparsity. Reject sparse options rather than silently using dense attention.
This conclusion is limited to this pin, not a claim about every upstream project.

## License acknowledgement before acquisition/use

The [pinned license][license] is **MiniMax H3 Community License Agreement**,
dated August 2, 2026, not Apache-2.0 for the whole model. The Hub metadata says
`gated=false`; public downloadability does not waive the license. The manifest
records acknowledgement as required and **not granted**. It is prerequisite
metadata, not a license-acceptance implementation or legal approval.

Present the complete license and Exhibit A, and obtain explicit user
acknowledgement/eligibility confirmation before download or execution. Important
terms to surface (summary only, full text controls):

- Applicable territory excludes the European Union, United Kingdom, Republic of
  Korea and United States of America; excluded-territory deployment requires
  separate authorization. A checkbox alone cannot cure ineligibility.
- Commercial products/services generating more than USD 20 million yearly revenue
  require prior written authorization. Commercial UI must prominently display
  “MiniMax H3”.
- Provide the Agreement to recipients; mark modified files. Non-hosted
  redistribution requires the specified NOTICE. Downstream binding use terms,
  service safeguards, violation reporting and AUP duties also apply.
- Restrictions include using H3/outputs to improve unrelated AI models. The
  encoder's Apache-2.0 license is noted separately; review component/runtime
  licensing before any redistribution.

No acknowledgement or territorial certification is inferred from installation
approval. The later setup flow owns persisted consent and enforcement. A real
local smoke test, target environment validation, strict option handling and native
packaging certification remain separate work; none is replaced by these tests.

[diffusers-repo]: https://github.com/huggingface/diffusers/tree/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2
[minimax-repo]: https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438
[model-index]: https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/42ed227ee7df40d41602854ae760620d6eb651fe/modular_model_index.json
[h3-docs]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/docs/source/en/api/pipelines/minimax_h3.md
[h3-pipeline]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/modular_pipelines/minimax_h3/modular_pipeline.py
[modular]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/modular_pipelines/modular_pipeline.py
[before-denoise]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/modular_pipelines/minimax_h3/before_denoise.py
[denoise]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/modular_pipelines/minimax_h3/denoise.py
[transformer]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/models/transformers/transformer_minimax_h3.py
[lora]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/loaders/lora_pipeline.py
[export]: https://raw.githubusercontent.com/huggingface/diffusers/5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2/src/diffusers/utils/export_utils.py
[license]: https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE
