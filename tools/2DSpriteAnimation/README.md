# 2DSpriteAnimation for Pi

An **open-ended** style-driven animation workflow, not a catalogue of supported subjects. Pi interprets the requested composition, action, view, timing and loop behaviour; tools own the resumable GPT Image → H3 → transparent-frame pipeline.

**New default:20steps, Sage ON, Turbo OFF, native VAE → SatoDive → RGBA.** All recipe values, quality presets, background wording/check thresholds and retry allowance live in [`sprite-defaults.json`](sprite-defaults.json). See [the native workflow](NATIVE_VAE.md). Existing saved folders keep their pinned behavior; no migration of game assets.

## Quality and explicit controls

|Quality|Source video|Examples|
|---|---:|---|
|draft|512²|`nízká kvalita`, `low quality`, `náhled`|
|standard(default)|640²|`standardní kvalita`|
|fine|768²|`vyšší kvalita`, `better quality`|
|high|800²|`vysoká kvalita`, `high quality`|
|ultra|896²|`velmi vysoká kvalita`, `ultra quality`|
|extra|1024²|`extra kvalita`, `nejvyšší kvalita`|

`/sprite settings` / `/sprite presets` displays the actual shared configuration without generation. `/sprite video1024x1024, sprite128x128, ...` separates source and final canvas. `/sprite 1024 ...` and `rozlišení704` select a numeric source (512–1024,multiple32; unsupported numbers fail, never silently round). Explicit JSON options `resolution`, `quality`, `width/height`, `outputWidth/outputHeight`, `steps`, `seed`, `sageAttention`, `turboLora`, `transparency` and `backgroundRetries` override inferred text/style/defaults. Text recognizes e.g.`30kroků` with spaces, `bez Sage`, `s Turbo`, `lokální klíčování`, `bez opakování`. Explicit options > unambiguous text > style/reference defaults > central defaults. The live model guide still owns prompt structure; no subject/action catalogue.

Confirmed background drift is measured from every frame's VAE-float edge medians. Default repair allowance **one new-seed H3 job**, same artwork; maximum2jobs total, one image maximum. `backgroundRetries:0` disables,2 explicitly permits two repairs. Missing/occluded diagnostics require review, not regeneration; exporter/network/provenance failures never buy new inference. Every original/ZIP/attempt is retained, exact settings in `attempts/<job>.json`, searchable failed combinations in workspace `sprite-background-failures.jsonl`. An ambiguous submission resolves its SAME receipt. Exhaustion reports `background_rejected`, not a ready sprite.

## Explicit background and action prompting

New native jobs use central background wording version2, based on an operator-approved controlled test. It describes the selected colour in English as well as exact RGB/hex and explicitly holds its hue, saturation and brightness in **EVERY frame**, including all intermediate motion. Foreground lighting is separate from the flat unlit key plane. Exact primary blue/green/red names are configuration-owned; arbitrary keys retain exact numeric values without a guessed hue. This is not a blue-only tool.

The generic paragraph does not freeze the requested camera or foreground lighting/action. It follows the actual H3 section contract and is inserted once after `[Shot 1]`; the server's model-owned guide/digest is not rewritten. Existing prepared/submitted manifests retain their pinned policy and compiled prompts. Reload does not migrate them, reinterpret receipts or authorize another job.

Author the requested visible action positively. For a holding idle, describe support, subtle breathing and inactive controls/effect sources where applicable; avoid pasting a catalogue of forbidden action words. **Background pass is not action acceptance:** review all exported phases/motion on dark/light solids for interior haze, silhouette coverage, anatomy, unwanted effects and joins. An idle with unsolicited shots is not ready merely because its key passed. These are model-facing instructions and manual review obligations, not a new automatic action detector.

Evidence: same artwork/seed42/settings, baseline `2bc6a3b7d26a4209b30fcbbd208d68c7` versus control `eacd3d34827a4a47aa5468bed53b4ae1`; changing only the background paragraph reduced the native green-channel border range from1.84–71.97 to3.87–5.07/255 and removed the visible cyan haze in the reviewed native RGBA sequence. Both clips still contained unwanted shots. One controlled pair is not universal reliability, bit-exact colour or game readiness. See [native prompting/review](NATIVE_VAE.md#stable-background-prompting-and-review); local full evidence is `MyToolsTest/review/full-refresh-v2/blue-control/`.

## Start here

This checkout is wired for sessions started in any of these folders:

- `MinimaxH3Server/2D Animations` (recommended)
- `MinimaxH3Server` (uses `2D Animations/2DSpriteAnimation.style.md`)
- `MinimaxH3Server/tools/2DSpriteAnimation` (also uses the shared `MinimaxH3Server/2D Animations` style/output workspace)

Pi discovers project loaders only in the startup working directory, not its parents. A loader is included in all three locations. An explicit style in the current directory still takes priority.

Start Pi there, grant project trust when appropriate and run `/reload` in an existing session. The existing `generate_image` and `h3_*` tools must be loaded/callable. Requires Pi 1.3.0+ with nested tool execution; tested against the installed 1.3.0 SDK. Extension-only updates need `/reload`; native generation requires the matching server capability. The1024/per-frame-review backend is already deployed in this installation.

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
- **exact**: no GPT Image request; use the supplied artwork with a recorded contain/pad/resize transform. It must have alpha, or identify its existing uniform background through `referenceBackground` (e.g. `#FFFFFF`). Background colour removal is not general photographic segmentation. For exact opaque artwork, remove/unmix only the declared backdrop; never run the video's donor-based despill on opaque source paint. That previously inverted intentional purple-black ink and pale highlights before H3. Fully opaque foreground RGB is now preserved, while already-transparent sources retain their supplied RGBA. H3 can still alter artwork; “exact” never guarantees unchanged video pixels.

Use Pillow-readable still images (PNG/JPEG/WebP recommended). Unsupported formats, missing files and undecodable references fail before generation. For HEIC, convert to PNG/JPEG first with your existing image tools. References are copied privately into the result folder. Non-exact references are sent to GPT Image and may include their original background/metadata; crop or redact private content beforehand.

### New actions from one existing sprite folder

```text
/sprite Use "/path/Sprite_Existing" as reference. Make a new animation where the character walks upright while holding the same prop. Keep the appearance and grip, and make it loop.
```

Pi first calls `inspect_reference`, a local read-only inspection. It receives the original request/style/settings and a labeled overview sampled across the latest completed export. It can request additional `frameIndices` (up to 12 per call) to examine action phases, props, effects and neighboring poses. Export revisions can be specified explicitly. Overview samples are not a claim to have inspected every frame.

Pi interprets the NEW request, separates what should remain from what should change, and selects an appropriate frame with a reason and scope. **There is no default first-frame selection.** `extend` requires the inspected revision, frame index and image/metadata hashes; stale selections fail closed. A stable holding pose may suit a new locomotion action better than a transient recoil/effect frame, but this is contextual agent reasoning, not a hard-coded subject/action catalogue. Genuine ambiguity or absence of a suitable pose should prompt clarification.

The selected RGBA artwork is snapshotted, aligned and keyed without GPT repainting. Pi inspects it, authors the current model guide for the new action, runs one H3 generation and exports to a separate folder. Loop/one-shot comes from the new request—not the reference animation. A's output geometry/FPS/anchor and appearance flags are inherited unless explicitly overridden. New exports record source selection/scope/hashes/transform in `reference.json` and `animation.json`. Source media is unchanged; antialiasing is off by default, and only the manifest's bounded background-colour repair allowance may spend another job. H3 conditioning does not guarantee exact identity, prop geometry or motion preservation.

### Generated motion bridges between sprite folders

Use normal language—no JSON needed:

```text
/sprite Create a motion bridge from "/path/Sprite_Walking" to "/path/Sprite_Jumping"
```

Pi uses `bridge`, prepares and inspects **both** endpoints AND ordered outgoing/incoming contexts, then authors the live frame-mode guide against `bridge.sourcePlan`. New bridges default to **contextual**: ongoing A → short complete native-tempo event → ongoing B inside the supported long source, not one action slowed across5s. Explicit `bridgePattern:"repeated"` / `bridgeRepeats:1–3` adds complete causal B→A returns; the final bridge remains one-shot. `plan_bridge` can revise this plan before submission, without new images or inference. Default pins remain A's **last frame → B's first frame** from the latest completed exports; explicit `export_###` pins an older revision. Existing saved folders are not silently migrated (`legacy-full` is explicit compatibility only).

The bridge is a new `Sprite_*` folder, not a modification or concatenation of the input folders. Both RGBA endpoints are snapshotted and hashed. Their canvases are contain-scaled and anchors aligned on one key-colour background selected against both images and the workspace style/palette. Visible clipping or inconsistent metadata stops preparation rather than silently cropping. Default output canvas, anchor and pixel-art/soft flags come from A. New bridge review requires compatible A/B geometry and retains the discovered native FPS/all poses; incompatible geometry or an explicitly non-native sampling FPS stops for clarification. These geometric transforms do not infer semantic body size or repair mismatched identities/cameras—Pi must inspect both endpoints before submission.

No GPT Image request is made. `animate` requires `bridgeAuthoring:{planDigest,endpointsReviewed:true,contextsReviewed:true,sourceTempoReviewed:true}` bound to the new plan/sections. Phase-limited/one-shot parents additionally require concrete `sourceMechanics`: never hold a transient phase or repeat a one-shot without authorization to fill context; stop/clarify if fixed endpoints cannot fit. One initial H3 job plus only the recorded bounded native background-colour repair allowance is permitted.

`finish` first retains **every native phase** in separate `bridge-source_NNN` review output and returns `reviewRequired` (not a finish-poll loop). Ranked intervals are advisory, not semantic/contact acceptance. Inspect the entire source, use `preview_bridge` with source job/digest and `[startFrame,endFrame)`, inspect its exact A→window→B APNG/contact sheet on dark/light, then `select_bridge` with `previewDigest`, a reason and all six explicit review flags. Reject if no whole event is valid; no automatic inference follows rejection. A repeated source never authorizes looping the final bridge.

Only then `finish` exports all selected poses as one shot. Native interval duration is default; `cycleSeconds` is preferred event duration, NOT whole-source compression. Explicit `outputSeconds` plus `retimingReviewed:true` permits only0.5–2×timing change with every selected pose retained (maximum60fps). Frozen review processing prevents unreviewed alpha/RGB changes at export. `bridge-selection.json` / `animation.json` retain interval, source/endpoint hashes, exact preview proof, explicit review and `game_ready:false`; parents retain their canvas/anchor. Original film, full review, previews and failed attempts stay intact. Same-selection finish is idempotent; exporter defects never authorize new inference. Antialiasing remains off unless requested. Reload Pi to load new actions/schema.

Bridge prompt authoring now requires inspecting the outgoing last four and incoming first four frames (`motionContext`, playback-ordered with durations and hashes). These are local authoring context, not extra H3 conditioning. The gameplay base guidance asks for action already in progress at entry and exit, causal overlapping movement, maintained contact/structure, preserved in-place/root-motion behavior, and no manufactured stop, idle hold, settle-to-pose or morph. Genuine stops/rests remain allowed when required by the source actions or explicit request. Final gameplay timing is chosen separately from H3's minimum source duration; retiming cannot fix poor movement. The new guidance does not rewrite or regenerate existing exports.

Conditioning is **not** a pixel lock, velocity match or guaranteed seamless join. The initial automated tests use synthetic video and injected inference; real bridge motion quality needs visual review. This feature exports the transition itself; it does not silently assemble or rewrite A and B.

### Optional experimental server transparency

`finish.processing.transparency:"experimental-server"` selects the already-deployed authenticated H3 CPU keyer (`satodive-cpu-v1`). This is **postprocessing of an existing RGB video**, not native H3 alpha, another generation or a ComfyUI plugin. This is an explicit legacy alternative to new native-mode sprites. Choose `options.transparency:"local"` BEFORE generation for the old local keyer. A native job cannot silently fall back or be rekeyed by a finish flag. Run Pi `/reload` to load this added client support; no server restart is required.

```text
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"transparency":"experimental-server"}}
```

The pinned-TLS transport/checkpoint/archive validation was ported from `MinimaxH3Server`'s `feature/sprite-transparency-client` lineage; the local reviewed-border/native-despill/preserve-export fixes are retained, not overwritten. Stable request IDs are saved before POST. Pending or ambiguous submissions resume through GET, never another generation/task replay. Saved archives are SHA256-checked and bounded; exact source/FPS/key/dimensions, safe archive members and real RGBA PNGs are verified. Server RGBA bypasses local keying/despill **and legacy binary-alpha conversion**, then uses the canvas/loop/atlas/GIF packer. Supplied straight RGBA—including partial alpha—is preserved at unchanged size, or nearest-neighbour sampled for pixel art when resizing. `soft:false` must not turn already-keyed server alpha into0/255; pixel-art sampling is not a new matte. The quality report labels it `pixel_art_alpha:"server-provided"` (unless antialiasing is explicitly requested). Native server warnings are surfaced. Server-RGBA finish also writes `animation.apng` and records `transparent_animation` in metadata: this is lossless animated PNG with real8-bit RGBA, not a checkerboard image. Its decoded frames retain RGB/alpha; cumulative millisecond timing rounding avoids speed drift. PNG frames and atlas remain the engine assets. `preview.gif` still has a baked checkerboard and is only a preview. Default antialiasing remains off.

The fixed server preset cannot be combined with local tolerance/softness/background/palette overrides, disabling despill or `preserveExport` (which promises unchanged alpha). Changing options during pending processing is rejected. Tied-dominant chromatic keys such as magenta/cyan are unsupported and rejected before upload; originals are not recoloured automatically. The daemon authoritatively enforces64 MiB input,40 million sampled native pixels,128 MiB archive,180-second CPU-child timeout and idle-only admission. A124-frame640×512 original exceeds the pixel budget at24fps. Explicit lossless-RGB selected-pose derivatives are valid preparation: preserve originals, verify decoded RGB equality and record every parent-frame/time mapping. Do not silently reduce FPS or replace original media.

A changing key hue may still defeat this processor; the first two old-video tests were rejected for retained cyan. Subsequent green-background exports exposed a client integration bug: inherited pixel-art processing hardened supplied server alpha, amplifying pale edge colours. This is fixed: new `export_003` candidates import the same retained archives without alpha hardening; all48 PNGs/atlas cells match the native archive after nearest resizing, restoring25,205 partial-alpha pixel occurrences. No inference, CPU resubmission, extra antialiasing, local colour repair or character-specific patch was used. The prior30-frame parity with the server demo documented the old shared hardening behaviour, not proof that hardening RGBA was correct. User review still rejected these videos after alpha preservation. A later audit found the conditioning PNGs had already been damaged by exact-reference preparation before H3; that preparation is now fixed, but corrected animation has not been generated. Do not treat import parity as artwork acceptance. See `../../review/server-transparency/README.md` and its animated comparison. A subsequent one-job corrected-source control is now exported through the server-RGBA path, including a real transparent APNG and full original timing. See `../../review/server-transparency/clean-control/README.md`; source/PNG/atlas/APNG equality is verified, but exaggerated steps and loop/contour quality remain for visual review. Current game assets are unchanged.

### Optional edge antialiasing

Pass `processing.antialias:true` when finishing/reprocessing (default `false`). This averages cleaned, premultiplied RGBA coverage when downsampling, replacing only partially covered silhouette pixels; opaque interior detail keeps its existing sampling. It retains continuous edge alpha even for pixel art. It does not blur the whole image or add an outline. It works best when the source is larger than the export; equal-size binary edges gain no extra geometric detail. Reprocessing writes a separate export without generation.

## Changing backgrounds and colour-only repairs

This section concerns RGB input/local recovery, not already-keyed server RGBA (which must bypass these stages).

**Removing background alpha is not despill.** A recovered hard matte retaining native RGB still displays codec-contaminated green/magenta/cyan outlines. All recovery exports must use the shared native colour stage before resizing. The historical crouch regression came from an external alpha-only helper bypassing this stage; the helper now delegates to the same processor.

Default fixed-key processing now rejects detected native hue drift instead of producing an opaque rainbow export. Inspect the original. For a genuinely uniform border that changes colour, explicitly opt in:

```text
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"backgroundMode":"reviewed-border","backgroundThreshold":40}}
```

`backgroundThreshold` is a reviewed **matte** radius (default40, maximum80), not a colour-cleanup strength. This mode rejects nonuniform borders, preserves enclosed colour coverage and runs shared local/palette-guarded despill. `protectedPalette` can supply an explicit array of foreground `#RRGGBB` colours; folder palette defaults are forwarded. Colour collisions and actual enclosed background holes still require inspection. Do not increase tolerance until the fringe disappears: that can erase anatomy.

If an existing export already has reviewed pose selection, retiming, gap masks and calibration, preserve those exactly while repairing colour:

```text
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"preserveExport":"export_003","backgroundMode":"reviewed-border"}}
```

Preserve mode reads the original video at the export's recorded source times, verifies source hashes and compatible canvas geometry, reconstructs RGB at native resolution and retains **every alpha byte**, frame record, timing, loop flag, canvas and anchor. It regenerates PNGs, atlas, GIF and contact sheet in a separate revision. `color_repair` records the pinned base/frame hashes, processor hash, observed native keys and changed RGB counts. Inherited `recovery` describes the historical matte only; its old “RGB unchanged” wording does not describe the new final colour stage. Retiming, resize, key/matte and other processing overrides cannot be combined with `preserveExport`; make those a separately reviewed operation.

The estimator uses local clean interior/neutral seeds, fitted key mixtures and palette/collision guards; retained neon key pockets cannot become foreground seeds solely because they lie inside alpha. Collisions are evaluated against the pixel's nearby paint, not any unrelated palette colour elsewhere on the subject. Key-only chromatic sub-axes remain excluded when the background drifts (for example green residue from a cyan plate); legitimate declared key-like paint disables that exclusion. The matte-preserving path propagates a nearest trusted paint sample rather than averaging different materials into an invented green tint. Normal fixed-key reconstruction keeps its previous blending behavior. Real palette colours take precedence. It does not erode the sprite or impose a fixed outline colour. Estimates are not ground-truth paint, perfect segmentation or motion repair. Review every loop pose on dark/light solids and in playback before activating, including the middle of hue transitions. First/last or greatest-change samples are insufficient. For recorded failures, add whole-cycle residual-colour tests and inspect actual gameplay canvas pixels, not just action keys. Original videos and failed exports remain intact, and no inference is repeated. Reload Pi after changing the extension to load the new options/guidelines.

## What it actually does

1. Validate local dependencies and inputs, discover live H3 profiles/modes and read the actual selected frame-mode prompt contract. No invented sections or model downloads.
2. Pick a saturated contrasting key colour far from the supplied/reference/style palette. GPT Image is instructed to exclude it from the artwork and anticipated effects. One canonical source is generated, or exact artwork is prepared without generation. Inspect the source before H3; a missing/unusable flat background stops submission.
3. The agent writes the **actual model guide's sections** for this particular requested action. The tool checks section keys, refreshes the guide digest, uploads the real source, compiles aligned timing and preflights. Submission uses only the compiled `num_frames`: H3 rejects a payload containing both `duration` and `num_frames`, even when consistent. The authored draft is saved before preflight so a rejected validation can resume without new artwork. Frame conditioning is used; loops request a continuous path back to the same closing composition. This is conditioning, not a pixel lock.
4. Create/use a canonical H3 project and a durable run capped at **one initial generation plus the configured background-colour repair allowance** (default2jobs/1revision total); existing legacy runs retain their old cap. Native project responses identify records with `project_id`; run responses use `id`. The adapter records each response and validates its identifier before any downstream request. Save the stable request ID before queueing. An interrupted submission resumes through its receipt, never a new request ID.
5. Wait without cancelling H3. Inspect real output, download the original MP4 through the existing owner-only, certificate-pinned H3 TLS configuration. No browser automation, SSH, key-in-URL or new credentials implementation. The transport is vendored unchanged from `MinimaxH3Server/packages/pi-h3/src/client.mjs`.
6. **Native default:** key all decoded VAE float frames server-side BEFORE video encoding; validate/download the exact RGBA archive, check float-border diagnostics and pack without local rematting or binary-alpha hardening. Select/resample only AFTER native import. The following details describe the explicit legacy LOCAL route, not native SatoDive: decode the full clip to RGB at native resolution at the requested export sampling rate. Key **before resizing** so YUV chroma subsampling/downscaling cannot bake the background into the outline. Coverage uses RGB distance with a conservative uniform-border background estimate. Colour cleanup is separate: nearby uncontaminated foreground supplies a local mixture estimate, with a local-colour fallback for unstable estimates. Despill never changes alpha. Its local reconstruction radius scales with native resolution (6 px per 128 px on the longest side, minimum 6, maximum 64; 24 px for a 512px plate), rather than staying at six source pixels. The quality report records `despill_radius_pixels`. This prevents thicker native-resolution spill bands from surviving simply because their clean neighbours fall outside the search. The bounded estimator is still not a guarantee that all key-colour contamination is removable; inspect the result. Existing exports are immutable and require explicit reprocessing to receive this correction. No fixed outline colour, palette quantization, erosion or hole-filling is imposed. Recovery/preserve mode uses foreground palettes as protection and ambiguity guards, not replacement artwork. This is not universal segmentation: key-coloured subjects and ambiguous transparent materials remain underdetermined. There is no motion-compensated temporal stabilization yet; inspect moving edges for colour variation. Resize the resulting straight RGBA, not the coloured plate. Opaque pixel-art exports use binary alpha to eliminate translucent coloured speckles; soft mode retains continuous alpha. Fixed canvas/scale/anchor, no independent recentering or removal of intended movement. `processing.despill:false` disables edge colour estimation if a particular asset needs it.
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

Status separates `previous_error` (historical) from an explicit `resume` plan. `source_ready` with no request ID means no H3 generation was submitted: resume authors the guide sections if needed and starts the first animation using the existing source. Missing receipt/job/video at that stage is expected, not a blocker. If a request ID exists without a job ID, only receipt resolution is permitted.

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

For a separate offline check against the actual server validation classes, run `tests/native_contract_check.py <saved h3-prompt.json>` with the current H3 source on `PYTHONPATH` and its existing Python environment. It creates only a temporary local project/run SQLite store, validates the complete project → run → generation request contract and submits zero jobs.

Tests use real temporary files, real FFmpeg MP4 decoding/keying/GIF export and injected external-generation responses. They cover arbitrary periodic graphics, nonperiodic rejection, one-shots, estimated soft alpha, exact-source handling, live-guide-shaped sections, style defaults, quota boundaries, concurrent attempts, ambiguous receipts, pending jobs and immutable reprocessing. A read-only compile was also checked against the live H3 guide (no generation submitted). **No paid generation was performed for implementation testing; live visual quality is deliberately left for the operator's test set.**
