# Sprite chroma-edge correction

Reported fixture: `2D Animations/Sprite_Walking_Dachshund/export_001/frames/0000.png` retained a blue outline. Native video inspection showed dimmed/mixed blue edge pixels; old pure-RGB distance keying promoted these to opaque foreground. Scaling YUV before keying also contaminated edge colours.

Changes: native RGB decode → brightness-normalized saturated-key matte (neutral keys keep RGB distance) → bounded six-pixel foreground-guided mixed-edge alpha estimation/unmix → RGBA resize. No mask erosion or subject-colour-specific rules. Opaque pixel art gets binary alpha; soft-mode alpha remains continuous. Explicit reused intervals now get their own seam measurements rather than stale search metrics or an unconditional failure warning.

TDD: reproduced dark-blue and mixed-edge failures; added protections for dark neutral foreground, unrelated blue/green against magenta, native-before-resize ordering, pixel-art alpha and preserved-cycle evaluation. 23 TypeScript + 16 Python tests and typecheck passed.

Local acceptance: reused original video with all H3/network/download callbacks forbidden. Preserved export_001, original source/video hashes, anchor, frames 23–53, 30 exported frames and 2500ms duration. Final output `export_003` (export_002 was a retained processing iteration). First-frame blue-dominant visible pixels: 1426 → 0; all thirty frames: 42901 → 3 (diagnostic threshold, not proof of perfect matting). Inspected final first frame and 12-frame contact sheet; paws, tail and dark intended outline remain visible. `edge-cleanup-comparison.png` records side-by-side proof. No generation or remote mutation. Existing exports and original assets retained.
