# Optional sparse attention implementation plan

**Goal:** Beginner-friendly optional H3 sparse attention using existing weights, preserving SageAttention and Turbo.

**Architecture:** Pin Zironic/H3-Optimizations at `862774944a331bc1a66cee1cf805b94bd138ebbe`. Download an exact archive only with consent, verify/extract a closed source/native-library inventory beneath the managed root, and load it only in isolated generation workers. Reuse idle maintenance and supervised transfer, without upgrading dependencies. Start with ConvRot INT8; broaden only with evidence.

**Tech stack:** Existing Python/FastAPI/SQLite controller and daemon, ComfyUI/Triton/native CUDA package, vanilla JS.

User approved existing-weights approach. Direct work on main; no subagents/worktrees/push/full suites.

## Tasks
1. Pin archive and source inventory; inspect compatibility and license notices. Add focused failing artifact/protocol/validation tests, then implement safe download/extraction and verified loading. Verify imports/kernel on target before exposing usable state.
   Files: `backends/sparse.py`, optional manifest JSON, `_download_transport.py`, `tests/test_sparse.py`.
2. Add saved generation preset (off/quality/balanced/draft), pinned identity and constraints. Keep 20/6 steps and Sage flag independent. Apply sparse patch and capture actual execution/fallback in job events; do not claim sparse from a selection alone. Gate Turbo combination if hooks cannot be proven preserved.
   Files: `domain.py`, `store.py`, `remote/app.py`, `backends/pruned.py`.
3. Add explicit idle-only installation API with digest binding, durable progress, recovery and consent, and optional fresh-setup integration. Do not touch Torch, ComfyUI or Sage installations. Support reconnect without auto-install/generation.
   Files: `remote/sparse.py`, `remote/app.py`, `controller/{app,proxy,setup,preferences}.py`.
4. Add remembered installation/preset controls and plain-language recommendations. Quality-first 50%, balanced 30%, draft 15%; denser early steps. Off is exact old path, not a 100% sparse-budget substitute. Keep expert settings hidden/not exposed. Show download cost/no new weights, compatibility, quality warnings, and saved settings.
   Files: `web/{app.js,index.html}`, focused JS tests.
5. Focused tests only, real native UI checks, real GPU sparse/Sage and sparse/Turbo compatibility checks while idle, valid MP4 decode, rebuild/reopen controller. Preserve user preferences and all old jobs/models. Document remaining unverified cases.

## Implementation / acceptance evidence

- Implemented the tasks above. Archive: 21,870,839 bytes,
  SHA256 `83655b4ddc10ff832ab9eeedd27ac585c2ec8dede04332ae31cb050d878e6bb3`.
  Closed inventory: 113 files / 10,994,191 extracted bytes.
  Component/preset identity: `5d435a56cac66784050bba96b43c8549b0e39e13d93356089e636038a47e778e`.
- Source is downloaded on demand, not bundled into this repository. Native
  license/upstream notices are included; the upstream project does not declare
  a blanket top-level Python-source license, so no blanket license claim is made.
- Explicit digest-bound installation passed on the RTX 4090. Native kernel
  numerical self-tests passed; the INT8-vs-SDPA relative-L2 result was 0.014564.
  All four tested full-route sparse geometries matched the native reference.
  This is a kernel check, not a lossless sparse-generation claim.
- Ordinary sparse/Sage generation passed with **all three presets**: 512×288,
  124 frames, 20 steps, seed 42. Each logged SageAttention execution and 2,000
  successful native sparse kernel calls; each MP4 independently decoded all 124
  video frames and an audio frame. Quality-first audio was verified as 32 kHz
  stereo. Runs include loading/verification/decoding and are not speed comparisons:
  - Quality-first: `59324bba70c641ab9aa383e87c5c644d`, 368,284 bytes, 121.886 s.
  - Balanced: `fb989d3ecd9f4bc4b59913fe3adea0a4`, 369,463 bytes, 123.216 s.
  - Draft: `272c60366642432bb90631b0918523be`, 385,292 bytes, 130.188 s.
- **Turbo composition failed the compatibility gate.** A separate six-step
  prototype produced a decodable clip and 600 sparse kernel calls, but only two
  QKV bypass-LoRA hook calls, not the required transformer coverage. Merely seeing
  the upstream `BypassForwardHook` activity banner was insufficient. Both GUI and
  API therefore reject Turbo + sparse; this combination is not offered as working.
- Verified source-byte loader ignores pyc and restricts native library candidates
  to the pinned shipped ELF, disabling upstream developer library-path overrides.
  GPU receipts bind environment/Torch and extracted-file stat signatures; same-size
  changes invalidate fast status. Hashes are checked before use. Per-generation
  native self-tests finish before execution counters start.
- Native UI checks passed for remembered flags/preset, unchanged Sage/steps,
  disabled Turbo combination, saved per-job preset, 375px layout, no page errors,
  and no install/generation POSTs during startup or preference restoration.
- Focused checks: 39 component/preferences/timing Python tests, 6 selected transport
  tests, 3 selected setup lifecycle tests, plus input/timing/toggle JS checks.
  No full suite or subagents. Existing models/history/preferences were preserved.

## Acceptance boundaries
- No package changes or inference during discovery.
- Invalid artifacts, unsupported combinations and incompatible source versions fail clearly.
- Dense fallback is reported explicitly; no fabricated acceleration or fit.
- A completed video alone does not prove Turbo hooks or sparse execution: inspect runtime evidence.
- Full-resolution speed/quality and wider hardware remain unclaimed without measurements.
