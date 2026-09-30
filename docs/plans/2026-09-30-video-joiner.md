# Simple lossless video joiner

Approved scope: local files and generated History clips; ordered drag/drop timeline,
remove, simple sequential preview, export without additional encoding loss.

## Design

A separate local-controller workspace, independent of generation and SSH state.
FFmpeg/ffprobe are discovered locally (already installed on the acceptance host).
Missing tools are an explicit blocker, never an automatic install. No daemon
changes or GPU work. Compared with server-side remuxing this avoids server
maintenance and preserves generation resources; browser-only muxing would add a
large codec/container dependency and less predictable compatibility.

Upload MP4 originals through the existing authenticated loopback/CSRF boundary.
History import reads the original through the existing SSH proxy, then uploads it
to the same workspace. Private temporary copies are controller-session scoped;
page reload can discard the timeline but must offer clearing retained copies.
Removing an item deletes only its workspace copy, never a History job or original
local file. Limits: 64 MiB per clip, 32 clips, one hour total, 1 GiB workspace including exports.

Export uses FFmpeg concat demuxing and stream copy, never a video/audio encoder.
Require matching H.264/HEVC video and optional AAC audio stream layouts, codec
configuration, dimensions, frame rate, pixel/color format and audio parameters.
Reject unsupported/different streams, rotations or unsafe input rather than
silently dropping tracks, scaling or transcoding. Preserve packet payloads;
container timestamps/metadata may change. AAC encoder padding can affect seams;
this is not a sample-exact audio editor. Source media is never modified.

Use only application-generated private paths, no user filenames in subprocess
arguments or concat syntax; fixed subprocess argument vectors, no shell, bounded
probe/export time and size. Serialize writes/export/cleanup. Clean partial output
on failure. Opaque IDs serve preview and download with normal session checks and
byte ranges. Export download occurs only after successful completion.

UI: navigation entry and joiner panel, file/drop input, ordered clip list with
accessible move buttons as an alternative to dragging, selected/sequential preview,
remove, clear workspace, export status and download link. Layout remains usable
on mobile. UI operations cannot mutate generation settings or launch jobs.

## Implementation and evidence

1. Red/green focused backend tests: real compatible fixtures and packet hashes,
   incompatible codecs/geometry/audio, limits, missing IDs, cleanup and failures.
2. Authenticated controller routes, CSRF/body/path/range regressions.
3. Timeline UI, History import and focused JS ordering/removal/export tests.
4. Rebuild; native browser upload/drop/reorder/remove/preview/export verification,
   real original History import without generation or job mutation. Compare
   compressed packet payloads and decoded video frames across the joined output.
5. Document limits, reopen controller and commit on main. No full suite or push.

Implemented and verified: 47 focused Python tests, seven JS checks, rebuilt native
Chrome/WebKit interaction checks and exact original packet/decoded-frame
comparisons. Evidence and limitations are recorded in `docs/validation.md`.
