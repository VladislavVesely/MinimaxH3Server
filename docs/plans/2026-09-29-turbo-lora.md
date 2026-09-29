# Optional Turbo LoRA — implemented

Direct work on main; no delegation, branch/worktree, or full test-suite run.

## Pins and scope

- Weights: `larryvrh/MiniMax-H3-Turbo-Lora`, revision
  `43a74557ac3f6539db8e0f2a959d03feb7a81480`,
  `minimax_h3_turbo_v4_step600_ema.safetensors`, 779,849,816 bytes,
  SHA256 `5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3`.
- Custom node: `Larryvrh/ComfyUI-MiniMax-H3-Turbo`, commit
  `4274783a23afcfdbea3b4876cb79effd6c510785`. Exact source, conditioning grid and
  Apache-2.0 license hashes are in `backends/turbo.py`.
- Combined set: `ebf8ef915d00e23902e0c014e5941a0aa7d90cfeff5bd2b87c854f2cd0afb99e`;
  total 785,398,137 bytes. Publisher metadata declares Apache-2.0; base-model
  restrictions/consent still apply.
- Native ConvRot INT8/mixed profiles only. INT8 is GPU-tested here; mixed remains
  experimental. GGUF, NVFP4 and legacy Diffusers are deliberately disabled.

## Behavior

- Separate persistent install/use choices. Fresh setup adds Turbo after base-model
  download; existing ready installations add it without reinstalling anything.
- Explicit, authenticated install carries the controller's exact artifact digest;
  mismatched daemon pins fail before mutation. Installation is idle-only under the
  daemon maintenance gate. Partial transfers resume; hashes are checked before
  atomic promotion. All data remains beneath the application root.
- Reuses supervised download IO with a closed, source-owned component inventory:
  no caller URLs, arbitrary scripts, package upgrades or runtime network fetches.
- Generation records enabled flag, strength, low-VRAM choice and artifact digest.
  It validates files before executing pinned source bytes (not an unchecked pyc).
  The node applies bypass/merge LoRA and pruned time-conditioning; sampling uses
  its AV-schedule-aware sampler with ComfyUI's custom-sampler nodes.
- Fail closed if the node attaches no backbone or pruned conditioning updates.
- Default when enabling: six steps, strength 1.0, bypass mode. Enforce 4–8 steps
  and strength 0.8–1.2. Low-VRAM merging is optional and can soften quantized output.
- Settings appear in job history; the pinned set prevents queued requests silently
  changing to a different future LoRA revision. Arbitrary LoRAs remain unsupported.

## Evidence

- 27 focused Python checks passed, plus 6 selected existing transport checks.
  Existing timing/input JS checks passed. No whole-suite acceptance claimed.
- Real native download completed; both switches persisted across reload; final
  native smoke verified restored connection, installed state, step constraints,
  saved Turbo history/inputs, no page errors and 375px layout without overflow.
- Real install RPC rejected an incorrect digest and accepted the verified preset
  idempotently, without redownloading.
- Real RTX 4090 job `9aa37ffb869341eeb37ffe30598e55dd` completed: 512×288,
  124 frames, 6 steps, seed 42, strength 1.0, bypass mode, SageAttention on.
  Logs show 158 bypass adapters, 50 INT8 fc2 merges, 51 pruned adaln injections,
  active BypassForwardHook on each forward, native ModelSamplingAV and all 6 steps.
- Independent PyAV check decoded all 124 video frames and an audio frame from the
  368,075-byte MP4; audio is 32 kHz stereo. Persisted run time: 122.818 seconds
  including preparation/loading/decoding. This is not a comparative speed test.
- Full-resolution Turbo, mixed-profile inference, merge-mode quality/VRAM, and
  broad quality/audio fidelity are not validated here. The upstream preview's
  four-step motion-smear/audio caveats are surfaced in the UI.

Target code updates used known-byte guards and idle maintenance with backups.
Base model files, downloads, prior outputs and Sage preferences were preserved.
The Turbo switches were restored to off after verification; the files are installed.
