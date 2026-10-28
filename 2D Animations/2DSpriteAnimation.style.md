# 2DSpriteAnimation — shared visual style

Human-editable visual source of truth. Applies to **any requested 2D subject, composition, action or effect**. It is not a list of supported categories. Explicit request overrides these defaults; otherwise retain this design language across assets.

## Default appearance

Clean, readable illustrated assets with deliberate silhouettes, gently rounded forms and restrained, softly shaded colour regions. Clear at small display sizes; avoid incidental noisy texture and unrequested decorative detail. Keep the appearance consistent across subjects without forcing every object to have a face, eyes or a body. This default can be replaced with pixel art, painted, graphic, realistic, abstract or other visual rules for a different project.

Suggested colour family: warm cream #FFF2D8, sunny yellow #F4C542, coral #EE7662, sky blue #68B9DF, leaf green #86BD79, lavender #AC96CF, tan #C99C72, cocoa #805B46, charcoal #34343C. These are foreground colours, **not background-key choices**. Preserve explicitly requested colours and identity-reference colours instead of mechanically recolouring them. Include colours that will appear later in the animation when planning the excluded key colour.

Do not add lettering, labels, faces, scenery, watermarks or extra objects unless requested. Preserve requested spelling, capitalization and diacritics. Preserve reference asymmetry on its actual physical side; do not mirror to fake another orientation. Do not impose any 3D printing, underside, pedestal, size-in-millimetres or toy-safety requirements.

## Camera and framing

The request chooses side/front/back/top-down/isometric/dimetric/perspective or another view. Do not force all artwork into a single camera view. Default to a fixed camera and stable scale unless the requested action needs something else. Leave enough margin for the complete anticipated motion/effect and keep requested content visible.

For isometric/old-strategy-game assets, define one explicit orthographic/dimetric camera convention and repeat it across related assets: elevation, azimuth/facing, zero unintended roll, consistent foreshortening and lighting. An optional project convention is 2:1 ground diamonds (orthographic camera elevation 30 degrees, azimuth 45 degrees; projected ground-axis lines have a screen angle about 26.565 degrees). True isometric elevation is about 35.264 degrees; these are different looks. A named game's aesthetic is inspiration, not proof of an exact renderer match. Record the chosen convention in the request/camera description.

## Motion, timing and transparency

Choose a loop when the request explicitly asks for it or repetition makes sense for that action; do not force one-shot events to repeat. Infer this from the complete request, not a fixed subject/action keyword list. H3 source videos are at least five seconds and may be aligned slightly longer by the service. A final animation may be much shorter. Cyclic requests should generate stable repeated action across the available clip, then extract the best cycle. Never pad the final animation to five seconds merely because H3 generated five seconds.

Reserve a contrasting flat background colour **per asset**, outside the known subject, text, shadow and anticipated effect palette. No universal green/magenta rule. No checkerboard in generated artwork. H3 has no alpha: its background must remain flat and unchanging. By default the server estimates RGBA directly from decoded VAE float frames before video encoding; avoid colouring the subject with the key. The shared tool controls native background checks and bounded repair attempts. Soft translucent materials need an estimated soft matte and explicit visual review; one background cannot uniquely recover arbitrary transparency.

Frames share one canvas, scale and anchor. Do not independently crop/recentre frames or eliminate intentionally moving content. Do not promise pixel-perfect identity, camera or infinite-loop motion from generative video. Check actual output and the looping GIF.

## Machine-readable export defaults

Generation recipe, quality presets, FPS and source duration live in `tools/2DSpriteAnimation/sprite-defaults.json`, not duplicated here. Only project visual/output settings belong in this block. Per-request values take priority. Loop choice deliberately is **not** a style-wide switch.

```sprite-settings
{
  "outputWidth": 256,
  "outputHeight": 256,
  "pixelArt": false,
  "anchor": [0.5, 0.5]
}
```

Use an appropriate shared anchor for the requested asset (e.g. a ground contact point or a central pivot); these defaults do not imply a ground plane for abstract effects or interface graphics. Set `soft` per request when the content needs partial transparency, not globally for every asset.
