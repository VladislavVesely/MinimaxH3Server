# 2DSpriteAnimation for Pi

An **open-ended** style-driven animation workflow, not a catalogue of supported subjects. Pi interprets the requested composition, action, view, timing and loop behaviour; tools own the resumable GPT Image → H3 → transparent-frame pipeline.

## Start here

This checkout is already wired for sessions started in either:

- `MinimaxH3Server/2D Animations` (recommended)
- `MinimaxH3Server` (uses `2D Animations/2DSpriteAnimation.style.md`)

Start Pi there, grant project trust when appropriate and run `/reload` in an existing session. The existing `generate_image` and `h3_*` tools must be loaded/callable. Requires Pi 1.3.0+ with nested tool execution; tested against the installed 1.3.0 SDK. No server restart or generation-daemon change is involved.

```text
/sprite <describe any animation, view, timing and whether it should loop>
/sprite help
```

The command starts an agent turn. The agent reads the style, resolves intent, then uses `sprite_animation` actions in order: **prepare → source → inspect source → animate → finish**. A result with `pending:true` means continue `finish`, not generate another video. The final contact sheet is returned as an image for inspection. GIFs can be opened from their saved path.

Natural language and structured requests are supported:

```text
/sprite {"name":"MyAsset","request":"Describe exactly what should appear and how it changes","reference":"/absolute/path/reference.png","referenceRole":"identity"}
```

Optional fields: `camera`, `motion`, `palette` (`#RRGGBB`, including colours expected later in the animation), `loop`, `loopReason`, `profile` (already installed), `projectId` (existing canonical project), source `width`/`height`, `outputWidth`/`outputHeight`, `fps`, `anchor`, `pixelArt`, `soft`, `cycleSeconds` (final duration), `duration` (H3 source duration). In ordinary slash-command requests, Pi supplies omitted planning fields. The underlying `prepare` tool requires a resolved boolean `loop` and explanation `loopReason`, making the decision visible in the manifest.

### Style

Edit `2DSpriteAnimation.style.md` beside your outputs. Style is read and snapshotted for every new animation. It establishes visual language, not a restriction on what can be animated. An optional JSON `sprite-settings` code block supplies defaults for camera/palette/profile, dimensions, FPS, anchor, pixelArt/soft flags and durations. Explicit request values win. A style-wide `loop` setting is rejected: loop/one-shot is decided per request.

The supplied default is clean softly shaded illustration. Replace it for another visual language. Isometric/dimetric camera conventions, pixel art, graphical assets, effects, transformations and combinations are request/style data, not separate code paths. The agent must preserve explicit requested text, physical asymmetry and intentional movement.

### Reference roles

- **inspiration**: visual motivation, reinterpreted in the folder style.
- **identity**: preserve recognizable appearance/markings while applying the style and requested camera.
- **exact**: no GPT Image request; use the supplied artwork with a recorded contain/pad/resize transform. It must have alpha, or identify its existing uniform background through `referenceBackground` (e.g. `#FFFFFF`). Background colour removal is not general photographic segmentation. H3 can still alter artwork; “exact” never guarantees unchanged video pixels.

Use Pillow-readable still images (PNG/JPEG/WebP recommended). Unsupported formats, missing files and undecodable references fail before generation. For HEIC, convert to PNG/JPEG first with your existing image tools. References are copied privately into the result folder. Non-exact references are sent to GPT Image and may include their original background/metadata; crop or redact private content beforehand.

## What it actually does

1. Validate local dependencies and inputs, discover live H3 profiles/modes and read the actual selected frame-mode prompt contract. No invented sections or model downloads.
2. Pick a saturated contrasting key colour far from the supplied/reference/style palette. GPT Image is instructed to exclude it from the artwork and anticipated effects. One canonical source is generated, or exact artwork is prepared without generation. Inspect the source before H3; a missing/unusable flat background stops submission.
3. The agent writes the **actual model guide's sections** for this particular requested action. The tool checks section keys, refreshes the guide digest, uploads the real source, compiles aligned timing and preflights. Frame conditioning is used; loops request a continuous path back to the same closing composition. This is conditioning, not a pixel lock.
4. Create/use a canonical H3 project and a durable run capped at **one generation, zero automatic revisions**. Save the stable request ID before queueing. An interrupted submission resumes through its receipt, never a new request ID.
5. Wait without cancelling H3. Inspect real output, download the original MP4 through the existing owner-only, certificate-pinned H3 TLS configuration. No browser automation, SSH, key-in-URL or new credentials implementation. The transport is vendored unchanged from `MinimaxH3Server/packages/pi-h3/src/client.mjs`.
6. Decode the full clip at the requested export sampling rate. Remove the chosen background in every sampled frame, clean colour spill and retain straight RGBA. Fixed canvas/scale/anchor, no independent recentering or automatic removal of intended movement.
7. For cyclic content, compare recurring appearance, short continuation and motion direction. Select a suitable interval independent of H3's minimum duration. No subject-specific gait code, arbitrary five-second requirement, duplicate-last-frame pause or silent crossfade/ghosting. One-shots retain the full clip unless an explicit interval is supplied.
8. Export transparent PNG frames, padded sprite sheet, timings/anchor/provenance metadata, checkerboard contact sheet and GIF preview. An optional final duration retimes the selected interval; it does not blindly chop it to a guessed cycle length. GIF delays are quantized to centiseconds; PNG/JSON timing retains precision. GIF has no smooth alpha, so its preview uses a checkerboard.

Local prerequisites: `python3`, NumPy, Pillow, `ffmpeg`, `ffprobe`. They are available and tested on this Mac. For another machine, deliberately install `requirements.txt` into your own Python environment and set `SPRITE_PYTHON=/absolute/path/to/python` if needed. The extension never installs dependencies, downloads models or changes the server's global memory policy automatically.

## Outputs and feedback loop

```text
Sprite_Name/
  request.json
  2DSpriteAnimation.style.md
  guide.json
  manifest.json
  reference.*                 # optional original snapshot
  source.prompt.txt           # if GPT Image is used
  source.generated.png        # original model output; backend names can differ
  source.png                  # contain/pad derivative sent to H3
  h3-prompt.json
  original.mp4                # preserved original, never keyed/re-encoded in place
  export_001/
    frames/0000.png ...
    spritesheet.png
    animation.json
    quality-report.json
    contact-sheet.png
    preview.gif
  export_002/                 # later reprocessing; old export untouched
  feedback.json               # optional operator findings
```

Generated `Sprite_*` folders in the supplied workspace are Git-ignored by default so private references and large videos are not accidentally committed. They remain on disk for your tests.

The manifest records the exact style/source/video hashes, reference role, chosen key, raw backend warnings, selected guide, compiled prompt, canonical project/run/job/request IDs and export settings. No credentials are stored in result folders. Background/loop settings can be revised without another generation:

```text
/sprite resume /absolute/path/Sprite_Name
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"tolerance":0.08,"softness":0.25,"fps":12}}
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"soft":true,"key":"#FF00FF"}}
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"startFrame":12,"endFrame":36,"cycleSeconds":1,"anchor":[0.5,1]}}
/sprite {"action":"feedback","folder":"/absolute/path/Sprite_Name","feedback":"Expected ..., observed ...; frame/time ..."}
```

Each processing attempt gets a new `export_###`, including when an earlier export failed. If a source response was lost/failed, inspect the preserved generated image and explicitly adopt it through `source` with `adoptFile`; the tool will not automatically spend again. A failed or missing H3 receipt is not permission to submit another generation. Interrupted read-only downloads can resume; original inference is never repeated for download/processing problems.

For the improvement loop, give the **folder path, exact request, expected versus observed behaviour and an example frame/time**. Keep the manifest, original video and latest quality report/GIF. We can add a regression fixture, adjust the processor/prompting, and reprocess that same video. Only genuinely new source/motion generation should spend another request, explicitly approved.

## Honest limits

- No universal safe key colour exists. The palette search and prompts reduce collisions but cannot prove absence of that colour in future generated content. Source inspection and whole-clip matte diagnostics remain necessary.
- Arbitrary soft transparency cannot be uniquely recovered from one background. Soft mode estimates neutral translucent material and unmixes the key; coloured smoke, glass, reflections and fine edges can need further work.
- H3 may change shapes, markings, camera, illumination or background. Loop metrics are diagnostics, **not visual certification**. Poor seams are exported as review candidates with warnings, never labelled pixel-perfect. Do not “fix” intentional translation by centring every frame.
- Initial bounded processing: source downloads up to 128 MiB; local video up to 120 seconds, 900 sampled frames and 80 million total output pixels; output axes 16–1024px; single sprite sheet up to 8192px. Resource limits fail clearly instead of unbounded memory use. Current H3 supports 5–15 source seconds; its aligned minimum is 124 frames at 24 FPS (~5.167s). The final interval can be much shorter.
- Original audio remains in `original.mp4`; PNG/GIF exports are silent.

## Development / verification

```sh
cd tools/2DSpriteAnimation
npm ci --ignore-scripts
npm run typecheck
npm test
npm run check
npm pack --dry-run
```

Tests use real temporary files, real FFmpeg MP4 decoding/keying/GIF export and injected external-generation responses. They cover arbitrary periodic graphics, nonperiodic rejection, one-shots, estimated soft alpha, exact-source handling, live-guide-shaped sections, style defaults, quota boundaries, concurrent attempts, ambiguous receipts, pending jobs and immutable reprocessing. A read-only compile was also checked against the live H3 guide (no generation submitted). **No paid generation was performed for implementation testing; live visual quality is deliberately left for the operator's test set.**
