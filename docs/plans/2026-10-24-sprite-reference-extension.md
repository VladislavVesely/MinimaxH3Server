# Context-aware single-folder sprite extension

Approved scope: given an existing sprite-generation folder and a new action, Pi inspects request/settings/style and sampled animation frames, determines what should remain versus change, and chooses a suitable source pose. No default first frame; ask only for genuine ambiguity. New output folder, no source mutation, no GPT repaint, bounded single H3 generation, existing receipt recovery and quota-free export revisions. Antialiasing off by default.

Implementation:
- `inspect_reference`: validates latest/explicit export, returns source context, bounded labeled PNG overview and pinned sample hashes. Additional frame-index requests enable detailed inspection. Local/read-only; no H3 calls.
- `extend`: requires explicit export/frame selection, image and metadata hashes, reason and scope. Revalidates the selection, snapshots the exact selected artwork, inherits geometry/FPS/anchor and flags, and preserves new-request loop choice.
- `source`: prepares selected RGBA with recorded anchor transform; rejects clipping, metadata mismatch or modified snapshot. No image generation.
- `animate`/`finish`: reuse existing bounded server workflow and exporter. Record reference provenance in manifest, reference.json and animation.json. Prepared source hash is checked before submission.
- Natural `/sprite` instructions distinguish new one-folder actions from two-folder bridges and ordinary reprocessing. Semantic choice is performed by Pi after visual inspection, not a content classifier or fixed catalogue.

Verification: 35 TypeScript + 21 Python tests passed with typecheck. Integration uses real Pillow/FFmpeg plus injected inference, checks nonzero frame selection, preserved source manifest, no GPT, distinct loop intent, exported reference metadata, absent-choice rejection and stale metadata rejection. Existing bridge/path/receipt tests remain green. Read-only local acceptance inspected the existing 62-frame shotgun animation at indices 0,9,17,26,35,44,52,61, producing `/tmp/sprite-reference-inspection.png`; no new animation folder or remote calls. Real generated new-action quality remains untested. Extension-only change; no controller or daemon restart.
