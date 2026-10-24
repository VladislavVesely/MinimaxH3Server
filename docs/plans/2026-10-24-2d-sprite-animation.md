# 2D Sprite Animation Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Implement an open-ended, style-driven Pi workflow producing transparent frame animation and GIF previews from GPT Image source artwork and real H3 video.

**Architecture:** A project-loaded extension exposes resumable prepare/source/animate/finish/status operations. The agent interprets arbitrary user intent and authors prompt sections after live discovery/guide; deterministic tools own validation, request IDs, quota boundaries, download and image processing. A local Python/Pillow/NumPy/FFmpeg worker keys complete clips, scores general cyclic intervals, and exports fixed-canvas RGBA PNGs, sprite sheets, GIF previews and quality/provenance reports. Inference stays on H3; no daemon changes/deployment.

**Tech Stack:** Pi TypeScript extension with nested tools; existing pinned-TLS H3 client; Python 3, NumPy, Pillow, installed FFmpeg/ffprobe.

## Task 1 — contracts and resumable orchestration
Create `tools/2DSpriteAnimation/{index.ts,src/workflow.ts,src/worker.ts,src/h3-client.mjs,package.json,tsconfig.json}`; test in `tests/workflow.test.ts`.
1. Write failing tests for style-required preparation, arbitrary intent, reference roles, explicit loop choice, runtime discovery/guide, one image request, unknown contracts, durable request ID/ambiguous receipts and resume without re-generation.
2. Run via Node/tsx; confirm failure, implement workflow and rerun.
3. Use current installed profile/mode only. Read guide before sections, compile/preflight before submit. Persist request ID before inference; do not replay an ambiguous submission, increase budget or retry image generation automatically.
4. Download original video through the existing private pinned-TLS creative endpoint, rejecting redirects/oversize/invalid media. Never print configuration or keys.

## Task 2 — processing
Create `tools/2DSpriteAnimation/processor.py`, `tests/test_processor.py`, `requirements.txt`.
1. Write failing synthetic tests for adaptive key colour, RGB/alpha removal and despill, whole-clip exports, non-looping GIF, periodic content of different kinds, static/poor-loop reporting, fixed canvas/anchor, timing, safe paths and immutable reprocessing.
2. Run `python3 -m unittest discover -s tests -p 'test_*.py'`; implement and rerun.
3. Extract timestamp-resampled frames with bounded dimensions/counts. Choose loops by recurring appearance, continuation and velocity, not subject categories. Never force a last-frame duplicate or claim generated motion is pixel-perfect. Preserve originals and report uncertain mattes/loop seams.
4. Write independent export revisions so experiments never cost another generation or overwrite prior results.

## Task 3 — activation and acceptance
Create `2D Animations/{2DSpriteAnimation.style.md,.pi/extensions/2DSpriteAnimation.ts}` and root `.pi/extensions/2DSpriteAnimation.ts`; no existing files overwritten. Add package README with commands, dependency checks, source/animation reference roles, exact-source limitations, how to resume/reprocess and collect findings.
1. Test real FFmpeg synthetic MP4 → PNG sequence + sheet + GIF, check metadata/timing/alpha. Fake only external inference in workflow tests; no account spend.
2. Typecheck and test pure registration with injected nested tools; verify package includes processor and vendored client.
3. Give user exact working directory and `/reload`, `/sprite` usage. Live generation quality remains for operator's test set; no paid generation performed as implementation smoke test.

## Acceptance

Implemented the package, project/root loaders, editable style and feedback/resume/reprocess commands. Verified with the installed Pi 1.3.0 SDK: both loaders imported through Jiti and registered exactly one tool/command. Typecheck passed. 17 TypeScript tests and 10 Python tests passed, including a full orchestrator → real FFmpeg → transparent PNG/sheet/GIF integration with only external inference stubbed. Local dependencies passed checks; npm package dry run includes all runtime files. Live H3 discovery/guide/read-only compile matched actual frame contract and aligned 5 seconds to 124 frames (~5.167s); no inference was submitted. No GPT Image quota used and no daemon/application restart. The extension/workspace now live in MinimaxH3Server; the example project's implementation files were moved out without changing unrelated work.

Operator trial: start Pi in `2D Animations`, `/reload`, then `/sprite <request>`. Review `Sprite_*/export_###/preview.gif`, report expected/actual result plus folder and frame/time. Source/video/guide/style hashes and immutable export revisions support a regression-driven improvement loop without regeneration for processing-only changes. Generated private folders are Git-ignored by default.

## Limits to expose honestly
Chroma colour cannot be guaranteed absent by prompting; inspect source and report video contamination. Soft alpha is underdetermined from one background. H3 is not a deterministic animation renderer; loop scores are diagnostics, not visual certification. Explicit instructions override inferred loop decisions; non-looping actions remain non-looping. Subject vocabulary is unrestricted. The existing 3D example extension and unrelated files remain untouched. Preserve MinimaxH3Server's unrelated AGENTS.md and video_prompt.txt.
