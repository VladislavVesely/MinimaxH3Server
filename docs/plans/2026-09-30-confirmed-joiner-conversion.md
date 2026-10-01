# Confirmed conversion fallback for incompatible clips

User intent: do not stop at codec/height/level incompatibility; ask first, then convert if approved.

## Design

1. Try the existing lossless stream-copy path. Its incompatible response creates no export.
2. On that specific error, show a confirmation naming differences, output canvas/frame rate, H.264/AAC re-encoding, possible quality loss, padding, frame-rate conversion and missing-audio silence.
3. Only confirmation submits the same ordered clip IDs with strict `convert: true`. Decline or unrelated/ambiguous errors never submit conversion.
4. Under the existing exclusive workspace operation, validate supported SDR geometry/color/timing, normalize each complete clip with aspect-preserving padding and audio resampling/silence, then concatenate through a single encoder. Bound duration, dimensions, workspace/output size and execution time. Validate the output before publishing; clean up failures; never modify originals.
5. Compatible clips remain stream-copy even with permission. Mark converted results so the UI cannot claim losslessness.

## Verification and rollout

Real FFmpeg tests cover incompatible clips, explicit approval, unchanged originals, decoded padding/order, missing-audio silence and compatible-copy preservation. API tests require an actual boolean approval; JS tests cover decline, approval, ordered IDs, older capability gating and no retry for unrelated errors.

Chrome and WebKit acceptance used isolated packaged-controller processes/workspaces: decline produced no conversion, approval downloaded real 320×240/24 fps H.264/AAC output from differently sized/rated clips. Existing user workspaces were not touched. A new controller on port 8792 supplies the updated backend; prior processes remain alive. No remote daemon or model change is required.
