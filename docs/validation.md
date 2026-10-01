# Validation and acceptance status

## Measurement lookup recovery

Reproduced a successful estimate HTTP response being discarded after replaying
persisted pagehide/pageshow: pageActive stayed false and Checking measurements
never resolved. Also added regressions for completed-job invalidation, prompt-only
matching, request deadlines and preservation of hardware/configuration/cache
boundaries. Six focused JS and 59 Python checks passed. Chrome/WebKit fixture
acceptance verified an untouched form acquiring a newly completed measurement,
wording changes retaining it, different-length approximations being disclosed,
and restored lifecycle requests resolving. Zero generation POSTs were attempted.

The live server reported zero retained jobs and timing samples during inspection;
no deleted or unrecorded durations were reconstructed. The one-file performance
change was deployed with exact prior hash and idle maintenance guards. Its deployed
SHA-256 was verified and cross-length matching exercised in a disposable separate
statistics store without touching production measurements. The idle service restart
released resident memory, not model files; maintenance was confirmed released.
Helpers: `/tmp/h3-estimate-browser.cjs`, `/tmp/h3-estimate-fixed-native.cjs`,
`/tmp/h3-estimate-deployed-check.py`.

## Reuse generation inputs

Eight focused JavaScript checks and 62 reference/controller-security Python tests
passed. Packaged Chrome and WebKit verified Reuse inputs on Current and History
cards: prompt/model/seed/dimensions/frame count, exact Turbo step counts and other
options, image/video/audio references, first/last frames, clearing obsolete draft
assets/options, persistence across polling, missing-asset rollback and mobile
layout. Unit checks cover unsafe integers, unknown fields, changed pins,
unavailable models, unrepresentable LoRAs and target changes during asset reads.

All generation and feature-install POSTs were intercepted; zero were attempted.
A separate acceptance reused a real saved reference job with exact prompt,
settings and retained assets, again with zero generation/install attempts. No
original job or file was modified; no daemon deployment was needed. Native
helpers: `/tmp/h3-reuse-native.cjs`, `/tmp/h3-reuse-real.cjs`; status screenshots:
`/tmp/h3-reuse-chrome.png`, `/tmp/h3-reuse-webkit.png`.

## Timeline keyboard and scrollbar repair

Reproduced Delete and Space failures: after clicking a clip, replacement of its
DOM node moved focus to BODY. Also reproduced automatic hardware discovery
stealing focus to `hardware-heading` during playback. Stable timeline focus,
scoped shortcuts and a discovery focus guard resolve both. Space preserves the
selected playback time; no document-global deletion handler is installed.

Six focused JS checks and 47 Python joiner/security checks passed. Packaged Chrome
and WebKit acceptance verified click→Delete without right-click, held-key repeat
suppression, subsequent Backspace, Space play/pause from a nonzero selected time,
playback across clip boundaries, ruler→Space, typing isolation, native drag→Delete
of the moved clip, and a real visible 18px scrollbar with mouse/keyboard/native
scroll synchronization. Overflow controls hide when clips fit or are removed;
375px layout passes after resize observation settles. A source-change layout shift
in WebKit was measured at 448→374→448px and fixed by reserving the preview aspect
ratio; existing live-ruler/filmstrip/export acceptance passes again.

No real History jobs or original user files were removed. Only isolated synthetic
joiner copies were used. Screenshots: `/tmp/h3-timeline-scroll-chrome.png`,
`/tmp/h3-timeline-scroll-webkit.png`. No daemon deployment, installation or
inference was needed. Existing controller workspaces remain open.

## Visual filmstrip joiner

Whole clips now appear as adjacent thumbnail blocks proportional to duration,
with a linked preview/playhead, draggable ruler seeking, keyboard seeking, zoom,
horizontal scrolling and selected-clip controls. No backend/export change.
Four focused JS checks and 47 Python joiner/security tests passed. Rebuilt native
Chrome and WebKit acceptance verified actual thumbnail pixels, 2s:4s block-width
ratio, native drag rearrangement, live ruler dragging, seeking across clip
boundaries, keyboard Home, zoom, sequence playback, export, removal and 375px
layout. WebKit omitted pointerdown after a native drag; the failing live-scrub
check passed after adding ordinary mouse handling alongside pointer capture.

Synthetic outputs in both browsers retained exact packets and all decoded frames;
a real existing H3 clip pair also produced filmstrips and a whole-clip export
matching original compressed video/audio payloads and all 248 decoded frames.
Only local copies of existing test media were used; no remote write or inference.
Screenshots: `/tmp/h3-filmstrip-chrome.png`, `/tmp/h3-filmstrip-webkit.png`,
`/tmp/h3-filmstrip-mobile-webkit.png`, `/tmp/h3-filmstrip-real.png`.
Media comparisons: `/tmp/h3-filmstrip-acceptance/`. Unsupported browser codecs may
lack thumbnail/preview decoding; stream-copy export remains independently usable.

## Bulk job deletion

41 focused Python deletion/security tests and seven JS checks passed, including
selection scoping, stopped-only eligibility, confirmation dismissal, partial
failure/retry and stopping on connection/session failures. Existing single-job
cleanup, tab semantics, queue-click identity, video preview, last-frame and joiner
checks remain passing. Packaged Chrome and WebKit acceptance checked Current and
History batch deletion, individual checkboxes, Select all, polling persistence,
confirmation dismissal, failed-item retry, tab resets and 375px layout. Every job
mutation was intercepted against fixtures: no real job was deleted, cancelled or
submitted. Existing guarded per-job APIs are reused; no daemon update was needed.
Screenshots: `/tmp/h3-bulk-delete-chrome.png`, `/tmp/h3-bulk-delete-webkit.png`.

## Local video joiner

47 focused Python tests passed across joiner, failure/cleanup, authenticated API
and existing controller security tests. Coverage includes real H.264/AAC and HEVC
packet preservation, every decoded video frame in order, rotation preservation,
export decoder-metadata verification, mismatched geometry/audio, unknown IDs,
symlinks, quotas/duration, missing tools, partial export cleanup, session/CSRF and
HTTP byte ranges. Seven focused JS checks passed (joiner, video previews, last
frames, job deletion, queue click, tabs and asset dimensions); syntax/diff checks
passed. No full suite, dependency installation or GPU inference was run.

The rebuilt packaged controller passed Chrome and WebKit native acceptance for
local file selection/drop, actual drag reorder, sequential preview, removal,
incompatible export refusal, confirmed clearing, real History imports and
exports, and 375px layout. The two existing owned H3 clips were
`ed74f1da26704297832748497c1a2e66` and `0f12f2caf8d64df99a4ceccfd2f6e5bb`.
Independent comparison of exported MP4s against originals proved exact compressed
video AND audio packet payloads and all 248 decoded video frames in timeline order
in both engines (24 frames for synthetic red/blue fixtures). No remote job
mutations or server deployment were needed. Browser HEVC playback, Windows/Linux
packaged joining and maximum-size/hour-long timelines remain unverified.

The diagnostic initially lost a DevTools response-body capture; it was replaced
with an explicit authenticated read of the originals, the temporary test
controller was stopped, and both complete native runs were repeated successfully.
Screenshots: `/tmp/h3-joiner-chrome.png`, `/tmp/h3-joiner-webkit.png`,
`/tmp/h3-joiner-mobile-chrome.png`, `/tmp/h3-joiner-mobile-webkit.png`.
Media evidence: `/tmp/h3-joiner-acceptance/`. AAC padding/container timing means
sample-exact seamless audio is not claimed; no re-encoding is performed.

## History video preview loading

Reproduced a gray WebKit preview: after metadata loading, readyState remained 1
with no decoded frame, though native playback and HTTP 206 byte-range streaming
worked. The page also preloaded every output, including hidden history cards.
Changing only preload to auto on an isolated video produced readyState 4 before
Play. The fix applies that behavior only to players near the visible viewport,
using IntersectionObserver; hidden history no longer eagerly fetches every MP4.
Intrinsic dimensions reserve layout space, with inline playback and visible
loading/decoding-error feedback. Polling never resets an initialized player.
Removed, expired and disconnected cards release observation and media resources.

Five focused JS scripts passed. Rebuilt-native WebKit and Chrome checks used two
existing owned generated clips: zero media requests while History was hidden,
actual first-frame pixels before Play, native playback controls (WebKit mouse,
Chrome keyboard), continuing playback across a dashboard poll and seeking.
Screenshots: `/tmp/h3-playback-webkit.png`, `/tmp/h3-playback-chrome.png`.
No files were rewritten/transcoded and no job mutations or inference were run.
The browser test harness was corrected to drain pending routes before teardown.

## Uncompressed last-frame PNG export

Local focused tests: 91 passed, one codec test skipped because the controller
Python environment lacks PyAV. No dependency installation was performed. Tests
cover authentication, unavailable outputs, bounded concurrency, timeout/error
handling and refusal to reduce higher-bit-depth inputs. Five JS scripts passed,
including exact-byte reuse, profile/target guards and error handling.

Real deployed codec verification passed on a three-frame H.264 fixture (including
delayed decoder flush) and an existing owned 512×288 generated video. PNG pixels
exactly equalled an independent final-frame RGB decode. Every PNG DEFLATE block
was a stored/uncompressed block. Original video size/mtime were unchanged, and
no persistent export file or inference job was created.

The rebuilt native controller downloaded a real PNG through its authenticated SSH
proxy and reused byte-identical data in the first-frame slot. Only the upload was
intercepted, avoiding test assets on the server. Prompt/dimensions stayed unchanged,
no generation was submitted, and 375px layout passed. Screenshot:
`/tmp/h3-last-frame-reuse.png`. Current H3 8-bit output was verified; no claim of
recovering pre-video-compression pixels or supporting high-bit-depth conversion.

## Queue first-click preservation

Reproduced missing first-click submissions in the installed WebKit engine after
editing seed, prompt or duration. Event traces showed an enabled button at unchanged
coordinates, mouse-down → field change/blur → mouse-up, but no click/submit. Chrome
did not reproduce it. The change handler called render, which unconditionally
replaced the Queue button's text node. Changing only that assignment to preserve
unchanged text made the same WebKit cases submit on their first click.

A red/green JS regression checks text identity across repeated renders, real label
changes, busy state and blockers. Five focused JS scripts passed. The rebuilt
controller passed first-click tests after edits to seed, prompt, steps, duration
and Turbo strength, plus keyboard submission and rapid-double-click deduplication,
in Chrome and the installed WebKit engine. All generation POSTs were intercepted;
no GPU jobs were created. An initial verification run timed out waiting for the
control to re-enable; the instrumented rerun and subsequent Chrome run passed.
No pointer-down submission workaround, validation bypass or automatic retry added.

## Clone visual-asset dimensions

Six focused JS scripts passed: asset dimensions, resolution presets, reference
inputs, estimate refresh, generation blockers and job deletion. Regression cases
include exact sizes, nearest-32 rounding/ties/minimum, portrait, video, invalid or
nonvisual metadata, axis/area limits, busy-state protection and synchronized
presets/estimates. No Python runtime behavior changed.

Rebuilt-native browser checks used intercepted upload-metadata fixtures for first
and last frames, reference images/video/audio, portrait 1080×1920 → 1088×1920,
video 1920×1080 → 1920×1088, exact sizes and oversized-source rejection. Verified
mismatch notices, audio exclusion, unchanged preview identity through a background
poll and 375px layout. No server files or GPU jobs were created. Screenshot:
`/tmp/h3-asset-dimensions.png`. This is UI acceptance, not new resolution inference
evidence. Source media is never modified.

## Explicit job deletion

185 focused Python tests passed across deletion, worker, API, store/replay,
performance, controller security and assets. Eight deletion tests cover cancel-first
and active-process barriers, shared references, symlink safety, retry/idempotence,
reopened replay integrity, timing-average recomputation, CSRF/authentication,
submission/deletion serialization and actionable missing-file errors. Five JS
scripts passed, including confirmation, failed-delete preservation and draft cleanup.

Rebuilt-native controlled fixtures verified Delete in Current and History,
disabled queued deletion, explicit cancellation before deletion, confirmation
accept/dismiss, card/count refresh and 375px layout. Screenshot:
`/tmp/h3-delete-history.png`. Browser job reads/mutations were intercepted, not user
history. Live authenticated daemon acceptance separately created two isolated
cancelled fixtures, deleted their directories, retained the shared reference until
the last deletion, checked job/asset 404 responses and repeat-delete success, and
reopened the event store. All pre-existing job IDs survived; no inference was run.

Deployment used idle maintenance and prior-source hash guards. Event payloads and
job IDs are replaced with anonymous tombstones, preserving contiguous global SSE
cursors. Cleanup is retryable rather than falsely atomic across filesystem and
SQLite: a partial failure retains the job row. This is not forensic secure erasure.

## Optional references and Sage/Turbo correction

The previous validator incorrectly treated Turbo as unsupported for all conditioned
inputs and required at least one reference asset. Reference mode now permits zero
assets while retaining its pinned Ref2VA checkpoint, and Sage + Turbo is allowed
with references and frames. Sparse remains separately guarded only when selected.
Red tests reproduced both UI blockers and API rejection before the fix.

185 focused Python tests and five JS scripts passed. Rebuilt-native browser checks
verified an enabled Queue button with empty references + Sage/Turbo, the correct
submitted Ref2VA payload, and 375px layout; its POST was intercepted, not generated.
Screenshot: `/tmp/h3-reference-turbo-optional.png`.

Four real ConvRot INT8, 6-step, Sage + Turbo jobs completed at 512×288. Each had
independent decoding of 124 video frames and stereo 32 kHz audio, plus runtime
evidence of Turbo application and successful Sage kernel execution:

| Inputs | Job | Elapsed |
| --- | --- | --- |
| Ref2VA prompt only, no attachments | `3725b1a11f4b4671af31284d1247443e` | 149.251s |
| Reference image | `c19a1280c8eb4368b468b0d813606f5c` | 53.569s |
| First + last frames | `0f12f2caf8d64df99a4ceccfd2f6e5bb` | 56.108s |
| Reference image + video + audio | `ed74f1da26704297832748497c1a2e66` | 53.710s |

These establish execution on the target RTX 4090, not general quality, identity
fidelity or maximum-resolution compatibility. Deployment used idle maintenance and
an exact prior-source hash guard; no model, dependency or history replacement.

## Native reference assets and first/last-frame inputs

Implemented separate Text / References / First+last modes. Immutable uploads are
bounded, content-addressed, owner-only and decoded before acceptance; browser
requests retain origin/session/CSRF validation and the fixed authenticated SSH
proxy. No caller paths/URLs or hosted inference. History retains original asset
links. Frame conditioning uses FL2VA; ConvRot INT8 references use a separately
approved Ref2VA checkpoint. Initial acceptance below preceded the subsequent
Sage/Turbo correction; only sparse remains blocked for conditioned inputs.

Pinned Ref2VA original: 20,970,379,689 bytes, SHA256
`ed7e9aa541b945f44298650f0dc2e2e867bfd3b67cd07bcc90f6081c168f0d3d`.
Its 73-byte unreferenced trailer is removed only in a separate checksum-verified
runtime copy (`9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779`).
The original remains intact. No dependency, encoder, VAE or existing model upgrades.

### Real RTX 4090 acceptance

Ten owned-fixture jobs completed and were independently decoded: each contained
124 video frames at 24fps and 165,888 decoded stereo audio samples at 32kHz.
These are narrow integration tests, not general quality or memory guarantees.

| Mode | Job | Settings | Observed wall time |
|---|---|---|---|
| First + last | `ea8c5da8a70e41fbb1f35c4ccfef5972` | 512×288, 4 steps | 137.452s |
| Image + video + audio refs | `a7e6e8dcac374849bb5aa530212429c8` | 512×288, 4 steps | 167.227s |
| Audio-only refs | `5ce857f4eaa84a309e7a998f53f67af5` | 256×160, 2 steps | 48.275s |
| First only | `130c78816c2c462a9fdbe6af3bfd6867` | 256×160, 2 steps | 41.350s |
| Last only | `ee899e7f37224b85878668b0a8d154e1` | 256×160, 2 steps | 30.373s |
| Return to Text | `7db9a4e6160c470aacfd8796274a57a4` | 256×160, 2 steps | 32.008s |
| Final image + video + audio refs | `741c5d4ad47e4de3966507c32d7dd1e8` | 512×288, 20 steps | 160.328s |
| Final first only | `20e60ad6de044a4e9717eef5d58c9914` | 512×288, 20 steps | 61.090s |
| Final last only | `1a85fe0321e84efda796b37fcf36c307` | 512×288, 20 steps | 54.120s |
| Persisted checkpoint binding, all reference types | `d13488a7ef4c4d1db13939a154461898` | 512×288, 20 steps | 157.217s |

Native logs show the correct FL2VA/Ref2VA weight path and conditioning counts;
mode switches did not reuse the wrong diffusion weights. For the synthetic red
ball, supplied first/last centers were x=110/400. The both-frame test decoded
x=110.38/400.01; final single-frame tests decoded x=110.21 at the start and
x=399.93 at the end. Reference inputs guide rather than anchor the scene, so no
exact identity/motion/audio fidelity claim is made. Two-step smoke outputs are
not evidence of production image quality. Maximum asset counts, long/high-res
clips and other hardware/profiles remain unbenchmarked.

### Focused verification

281 selected Python tests passed: conditioning/asset contracts, profile and
resolution bounds, controller security, runtime IO, residency, worker, timing,
Turbo and sparse regressions. Ten JS scripts passed. A stale controller-security
expectation was updated to include its pre-existing Turbo/sparse digest fields.
No full-suite run, subagents or branch/worktree changes.

Rebuilt-native browser checks covered actual image/video/audio uploads,
authenticated previews, immutable deduplication, numbered tags, mode isolation,
draft removal without deletion, readable rejection of invalid media, and 375px
layout. Generation/estimate JSON has a scoped 96 KiB budget for full Unicode
prompts plus IDs; other JSON routes retain their 16 KiB limit. Reference requests
persist the pinned checkpoint digest and reject stale selections before execution. Image previews required adding only `img-src 'self'` to the existing CSP.
Screenshot: `/tmp/h3-reference-ui.png`. The browser did not submit inference or
installation; GPU acceptance was explicitly queued through the owned API.

Thirteen deployed source hashes matched local files. Ref2VA is installed;
nine conditioned timing records omit asset IDs, filenames and media contents.
Mode/geometry/software/cache classes remain separate. Models were left loaded;
existing preferences, weights and history were preserved.

## Extended resolution presets

Added aligned 480p/720p/1080p-size landscape and portrait options. Pruned request,
capability and independent media-validator bounds now allow 1920×1088 or portrait;
legacy bounds remain unchanged. Presets follow live daemon limits, and invalid
manual geometry is visibly blocked. Higher-memory sizes display an explicit
unbenchmarked warning. Default generation dimensions remain unchanged.

209 focused Python tests passed, including aligned HD node IO, profile-specific
API policy and video/audio-validation fixtures; JS regressions passed. Native
checks verified all three presets, portrait orientation, validity/warnings,
estimate payloads, over-area rejection and mobile layout. Live estimate endpoints
accepted both HD orientations without creating jobs; four deployed source hashes
matched. Screenshot: `/tmp/h3-hd-presets.png`.

These checks did **not** generate a full-HD GPU video. Full-HD inference speed,
memory sufficiency and output quality remain unverified; native output uses
1920×1088 rather than silently promising exact 1920×1080.

## Current tab: page-view lifetime

Current now retains queued/running jobs and jobs observed/submitted during this
page view, including completed/failed/cancelled results. Membership lives only in
JavaScript memory: normal polling, dashboard refresh and reconnect do not archive
it; reloading the document places already-finished jobs in History. Unfinished
jobs remain Current after reload. Fast jobs first seen completed after the initial
snapshot are included. Server job records and outputs are not modified.

`tests/job-tabs.cjs` covers page lifetime, fast completion, reconnection and fresh
page state. Rebuilt-native validation with browser-only GET-job fixtures confirmed
completion stays Current, reload archives finished jobs, queued jobs remain after
reload, and cards retain identity. No server jobs were created or changed.

## Resolution presets and Active/History tabs

All 15 aspect/size combinations satisfy the existing generation limits. Manual
sizes remain editable as Custom. Active contains queued/running jobs; History
contains terminal jobs. Counts, empty states and keyboard tabs were verified.
Native validation confirmed that changing presets preserves unrelated settings,
filtering tabs and background polling preserve video elements, and the 375px
layout does not overflow. No generation/install/unload submitted. Focused
`resolution-presets.cjs` / `job-tabs.cjs` tests and existing JS regressions pass.
Screenshot: `/tmp/h3-presets-tabs.png`.

## Queue submission warning regression

A Queue click no longer displays its own “Wait for the current operation” blocker.
The button shows neutral “Submitting…” text and stays disabled until the request
finishes; genuine readiness/setup/feature blockers remain visible. Focused JS
checks cover duplicate prevention and success/failure cleanup. Rebuilt-native
validation held a browser-only mocked POST, verified no warning and no duplicate
request, then verified button restoration. No GPU generation was submitted.

## Estimate refresh regression

Dashboard polling no longer restarts timing lookups for unchanged inputs or
flashes the loading message. Lookups debounce input changes; reconnect, explicit
refresh and explicit unload invalidate the previous lookup. The new
`tests/estimate-refresh.cjs` failed before the fix and passes afterward, alongside
existing estimate/blocker/inputs/toggle/timing checks. Rebuilt-native validation
observed two dashboard polls with zero estimate requests or text changes, then
exactly one lookup after changing an input. No generation/install/unload submitted.

## Resident models and measurements — 2026-09-29

Implemented opt-in resident JSONL workers for the pinned Comfy/pruned runtime,
idle-only unload, memory-aware component retention, private durable timing samples
and hardware/software/configuration-specific estimates. Experimental legacy
Diffusers remains isolated. No dependency/model upgrades were made.

RTX 4090 / ~30 GiB host RAM / 8 GiB swap acceptance included five successive jobs
in one owned worker, with independent full-frame and audio decoding:

| Job | Configuration | Measured execution |
|---|---|---:|
| `8d5dbf2dae0049918e7e86269f43c930` | Cold Turbo 6 steps + Sage, 512×288×124 | 99.782 s |
| `905f520ad7c0415b8b450bfff3d13ef5` | Same settings, retained components | 88.668 s |
| `9b207bf902a849bd8424a0352545f1e4` | Turbo removed, dense Sage, 20 steps | 52.788 s |
| `313e0d623224479ebfacb5f08b927032` | Quality sparse, Sage off, 20 steps | 84.856 s |
| `8b66efe6cc344087a69373d74d259ebf` | Same sparse preset, 576×320×141 | 55.376 s |

Cold/warm Turbo decoded video hashes matched. Removing Turbo restored the exact
decoded video of a clean cold dense baseline (`f77db3ce49f74f589c8fd53d687ae4c7`).
Both sparse runs confirmed 2,000 native kernel calls. These are observations,
not broad performance or quality claims. Cache-pressure modes remain separate.

Additional repeated-sparse testing exposed stale request-local patches. Sparse
routing/session plans now rebuild each request without reloading base weights;
old object patches are restored with `unpatch_model(unpatch_weights=False)`
before constructing the new clone. Merely ejecting injections left old cube-order
closures active and failed a real repeated-run test. After the fix,
`51f989d347b0425fa58e7a6119eed7d3` and `33ea7df4cef047b9bd71659f459cd2c2`
both decoded 141 frames plus audio: 113.616 s cold versus 69.347 s reused;
loading was 49.134 s versus 0.997 s. Sparse output is not bit-exact deterministic:
mean absolute decoded RGB difference was 1.187/255 (RMSE 2.135), comparable to
independent cold/cold runs (1.203/255, RMSE 2.147), rather than the stale-plan
prototype's 9.984/255 (RMSE 16.396). This is a narrow consistency check, not a
quality benchmark.

The initial all-components retention prototype hit a kernel-confirmed host-RAM
OOM on a reused run. Pageable memory, staged LoRA preparation, decoder eviction
and an encoder-preserving diffusion-reload fallback replaced that unsafe policy.
A later dense-to-Turbo switch also exposed host-RAM pressure during FC2 merging /
encoder offload, not encoding. Changed Turbo variants now reload reclaimable
base-weight mappings on constrained hosts while retaining the encoder. Safety-guard
failures and OOM runs were not recorded as successful performance samples.

Final memory-policy acceptance repeated sparse → dense → changed Turbo → same
Turbo in one worker: `e79853223f9f419ab6d929e54c4a8411` (112.128 s),
`1385ff42bd1644db86467cdf56dad72c` (81.898 s),
`803a8015578e4d9f860be75ffdea6145` (52.015 s), and
`25dd239b344a4ca58f4307921edb6593` (50.886 s). Every video/audio decoded;
dense and both Turbo results matched their exact clean decoded baselines.
The final Turbo runs retained the encoder and reloaded other weights as required
(`encoder_reuse`), rather than pretending full model reuse. Six deployed source
hashes matched local files; final native checks passed with models left loaded.

Real idle API/UI unload released the worker; observed device use returned to
408 MiB without deleting history/models. Running/queued unload returned 409.
Native checks covered matching estimates and per-step averages, prompt-free
hardware/configuration exports, actionable unload state, 375px layout, unchanged
preferences and no automatic generation/installation/unload. Screenshot:
`/tmp/h3-resident-estimates.png`.

146 focused Python tests, 6 selected transport tests, 3 selected setup tests,
JS estimate/blocker/inputs/toggle/timing checks and syntax/diff checks passed.
No full suite or subagents were used. Other hardware/profiles, full-resolution
resident workloads, legacy Diffusers residency and Windows-native acceptance
remain unverified.

## Optional sparse attention — 2026-09-29

Pinned existing-weight sparse attention installed and numerically GPU-probed on
RTX 4090 without dependency upgrades. All three presets completed real 512×288,
124-frame, 20-step generations with SageAttention enabled. Each recorded 2,000
native sparse kernel calls and produced an independently decoded MP4. A separate
Turbo-combination probe exposed bypassed QKV LoRA adapters, so that combination is
blocked in both UI and API rather than silently treated as compatible.

Native checks covered preference restoration, no automatic work, unchanged Sage
choices/steps, saved inputs and mobile layout. 39 focused component/preferences/
timing tests, 6 selected transport tests and 3 selected setup tests passed, along
with JS checks. No full suite, full-resolution benchmark, broad quality comparison,
other-profile or other-hardware acceptance is claimed. Details and job IDs:
[sparse implementation record](plans/2026-09-29-sparse-attention.md).

## Turbo LoRA — 2026-09-29

Pinned v4-600 EMA plus its custom node/grid installed through the native UI.
A real six-step ConvRot INT8 + SageAttention generation completed on RTX 4090:
512×288, 124 frames, 32 kHz stereo audio; independently decoded with PyAV.
The 368,075-byte output is job `9aa37ffb869341eeb37ffe30598e55dd`.
Native checks verified persistent switches, six-step defaults, pinned history
inputs, mobile layout and zero page errors. Exact install-digest rejection and
idempotent installed-preset RPC were checked on the target. 27 focused Python
checks, 6 selected transport checks and the input/timing JS checks passed.
No full suite, full-resolution Turbo benchmark, mixed-profile inference, merge-mode
VRAM/quality or comparative speedup claim. See
[the detailed Turbo record](plans/2026-09-29-turbo-lora.md).


## Remembered connection, SageAttention and duration — 2026-09-29

Direct work on main; no subagents or full test-suite run for this update.

- **9 focused checks passed** in `tests/test_preferences_sage_duration.py`: private
  credential round-trip/forget, unsafe files, strict Sage flag, duration alignment,
  exact optional-dependency allowance, and failed-probe rollback.
- Native launcher rebuilt. Real authenticated browser checks confirmed connection
  restoration after process restart, both Sage choices restored, no startup install
  or generation requests, empty password field, linked controls, no page errors,
  and a 375px mobile layout without horizontal overflow.
- Installed the wheel-hash-pinned SageAttention **1.0.6** on the real RTX 4090 target.
  Both FP16/BF16 kernels passed; the actual pinned ComfyUI optimized-attention path
  selected Sage and produced GPU output matching the PyTorch probe tolerance.
- A cold native check caught the additive package violating the base freeze.
  Fixed without changing `resolved.txt` or its record: only the exact extra Sage
  line with its environment/Torch-bound GPU receipt is accepted. Other changes
  still fail closed. Installer failures attempt removal of only the new add-on.
  Fresh real-target readiness and native auto-reconnection then passed.
- Password settings are plaintext, mode 0600 inside a private macOS controller
  directory; never projected back through the preferences API. The saved connection
  remains available. Sage selection defaults were restored to off after the checks;
  the installed package is available when explicitly selected.

No complete Sage-enabled video, quality/speed comparison, native Windows acceptance
or fresh whole-suite result is claimed. Existing model files and generation history
were preserved. Target application files were hash-guarded updates with backups,
not a claim that the active historical application wheel remains byte-identical.


## One-click integration — current main source

Application source revision: `eeb7839e084fa59a6d4515167f1f06a22de45435` on **main**
(includes backend `a5d4c6f6679f7863b4fbbbb5af50483b15313ad1`). No new branch or
worktree was created. This section supersedes the historical approval UI and
request-scoped-sudo description below; historical results are not new acceptance.

Executed locally on macOS arm64, Python 3.14.5:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q -Werror` | **858 passed, 10 skipped** |
| `cd tests/browser && npx playwright test --reporter=line` | **31 passed** (Chromium) |
| `.venv/bin/python -m pip check` | No broken requirements |
| `.venv/bin/python scripts/build_controller.py` | Native root `dist/h3-controller` rebuilt |
| `.venv/bin/python scripts/smoke_controller.py dist/h3-controller` | Both ordinary and `deploy` launch modes passed |

Native executable SHA256:
`4a2e05efe0bccbf25624134f54a8b10f35a8d557ec619b80c7264ff70c83981c`.
Embedded wheel SHA256:
`efa0093091c27af00032983dd788bbc9fd2b4f3a8679f6b3951b9b7902cf2a53`.
Smoke verifies the embedded wheel equals every current application source/resource,
including the one-click coordinator, catalog and UI; actual loopback bootstrap,
Host/Origin/CSRF, catalog/license, idle setup, assets and shutdown all pass.
The familiar launcher is `./dist/h3-controller` in the main checkout.

Browser regressions exercise real authenticated controller routes, Deployment,
installer/store, target authorization/activation and selected download records,
with only SSH/native/runtime/download IO controlled. They cover prerequisite
replan, required reboot/reconnect, activation, selected acquisition, reload during
work, cancel/reload, exact pruned digest rejection, sudo rejection, lost/undelivered
responses, stable public retry IDs, stale observations and target reset. Host-trust,
CSRF and generation/video preservation checks remain. These fixture bytes are not
weights or a generated/playable H3 video. No real server was contacted and no
remote packages, drivers, reboot, model download or GPU execution occurred.

TDD failures preceded fixes: obsolete retention checkbox; missing pre-Install
catalog license fields; lost accepted response failing to resume polling. The last
was corrected by scheduling observation after status reconciliation. Independent
review then identified a blocked-plan retry defect: repaired prerequisites were
not inspected again after reauthentication. Four failing two-attempt route tests
reproduced it across legacy/pruned selections and environment/prerequisite plans.
The correction permits one fresh discovery on a new explicit Install only when
the blocked plan has no durable launch evidence. Twelve guard cases preserve
checkpoints, matching operation/active-environment records and even partial
bootstrap launch/approval/status records. Focused coordinator/deployment tests:
**89 passed**. All full-suite and native results above were rerun after correction.
Independent re-review of this correction is **pending**, not claimed here.
Ten platform/opt-in Python tests remain skipped. Live Linux setup/reboot/GPU,
Windows native acceptance and Python physical-memory zeroization remain unproven;
offline cancellation cannot guarantee detached remote work stopped.

## Historical validation (prior design/revision)

Validated implementation revision: `7e74cc9cf99be5a74376e385c5b15a636198a208` on `feature/h3-server`.

## Executed checks

| Environment | Check | Result |
| --- | --- | --- |
| macOS arm64, Python 3.14 | Full `python -m pytest -q -W error`, independently rerun by parent | 736 passed, 10 skipped |
| Ubuntu 26.04 x86_64, Python 3.14 | Full `python -m pytest -q -W error`, executed by parent | 736 passed, 10 skipped |
| Ubuntu | Native systemd parser plus actual launcher/interpreter/argument/PID regressions | 4 passed |
| macOS, Chromium | Playwright suite, final implementation worker run | 27 passed |
| Both platforms | `python -m pip check` | No broken requirements |
| Both platforms | Native PyInstaller build | Passed |
| Both platforms | Packaged executable smoke, independently executed by parent | Passed |

Packaged smoke verifies embedded release wheel, browser assets, runtime manifest and license; actual loopback launches in both supported launch modes; one-use bootstrap, Host/Origin/CSRF protections, authenticated API, and clean shutdown. It does not generate video or contact an installation target.

Browser integration tests exercise real controller/API/store routes with controlled SSH/runtime IO. Their video fixture bytes are **not** generated or playable H3 acceptance evidence. Reboot-related tests use controlled transport loss and independent fixture continuation, not a reboot of a real machine.

The Linux source was exported with `git archive` at the exact revision above, checked by SHA256 after verified SSH transfer, and tested in an unprivileged sandbox beneath `~/MinimaxH3Server/tmp`. Python virtual-environment bootstrapping used `venv --without-pip` and a SHA256-verified pip wheel because system ensurepip was unavailable. Dependencies and build caches remained in that sandbox. No system packages, drivers, service registration/activation, reboot, or model downloads were performed.

## Resolved review findings

- Polling now preserves existing video elements and observes accepted detached installation launches without resubmitting installation.
- Rejected setup actions no longer discard existing installation observation.
- Automatic reconnect is bounded, target/release/root/digest-bound, and observation-only. SSH credential retention requires visible consent and stays in memory; sudo credentials are not retained.
- systemd uses a fixed system-Python `execv` launcher, passing the absolute environment Python path as an argument. Native parsing and actual execution cover equals signs and other special characters without a shell, PATH lookup, or PID change.
- Final narrow independent review approved the last correction. The earlier integrated review's sole remaining code finding was this systemd rendering seam.

## Not yet accepted

- Real H3 GPU execution, offload/LoRA compatibility and a playable generated/downloaded MP4.
- Actual privileged service setup, reboot recovery, or rollback on a disposable Linux machine.
- Native Windows executable execution, ACL/reparse checks and Windows packaging acceptance. Cross-platform source and CI configuration are not proof that Windows CI has run.
- Compatibility of the Ubuntu-built executable with older Linux distributions. Build on the intended baseline and run native smoke checks there.

The inspected target has approximately 29 GiB RAM; the selected recipe documents approximately 75 GB host-memory needs. Its RTX 4090 does not by itself establish that this recipe fits. Hardware suitability, explicit model-license acknowledgment and download approval remain prerequisites to real-model acceptance. There is no hosted inference fallback.

## Local launcher

From the implementation worktree:

```sh
./dist/h3-controller
```

This is the locally built macOS executable. It opens the local browser controller; launching it is not approval for privileged target changes or a model download. Build another native platform with `python scripts/build_controller.py` and verify with `python scripts/smoke_controller.py dist/h3-controller` (use the `.exe` output on Windows).

All automated results above refer to the stated implementation revision. They are not a blanket end-to-end certification.

## Additional models (2026-09-30)

- Focused model-library, controller security, worker, setup/recovery/discovery, downloads, pruned runtime/UI and optional-feature tests: **379 passed, one skipped**. No full-suite run.
- Eight focused JS checks passed. Chrome and WebKit native fixtures verified one pinned additional-model POST (no legacy setup request), visible byte progress, preserved step edits, and both selectable generation profiles. Model installation/generation requests were intercepted. WebKit reproduced `mousedown`/`mouseup` without `click` when Install text was replaced during blur; conditional label writes fixed first-click delivery.
- Temporary native helper: `/tmp/h3-model-library-native.cjs`; progress captures `/tmp/h3-model-progress-{chrome,webkit}.png`. No older controllers or joiner workspaces were stopped.
- Eight daemon files deployed with exact prior-source hashes, backups under `tmp/model-library-backup`, idle `policy: reject` maintenance and a guarded restart. Read-only acceptance: runtime ready, maintenance off, zero jobs, library idle with INT8 installed. Full existing INT8 snapshot hashes verified. Mixed has three matching shared files totaling **19,986,772,542 bytes** out of **35,889,785,333 bytes**.
- No real mixed-model download or inference was started to test this change. Cross-family real provisioning, maximum-size cancellation and inference quality are not claimed. The rebuilt controller exposes the explicit Install action for the owner.

### Follow-up: missing-weight preflight and model-specific status

The owner's first real mixed install exposed a gap in the fixture-only acceptance: `load_runtime` resolved weight filenames during an environment-only diagnostic, before the requested snapshot existed. Comfy returned `None` and `Path(None)` aborted before any download. The displayed 40,957,152,230-byte completion belonged to the earlier INT8 setup; inspection confirmed the mixed snapshot was absent and the failed attempt completed zero bytes.

Weight resolution now occurs only when preparing generation weights, where missing files still fail closed. Model-install errors retain the actual diagnostic issue. The installer marks profiles **Installed / Not installed / Installing**, always lists available models even after an additional failure, reports successful existing-file verification normally, and labels saved setup events with their original profile.

Validation: **80 focused Python tests**, nine JS checks, native Chrome/WebKit installed-label/progress/completion/selection acceptance. The real deployed mixed environment-only diagnostic now exits **0**, with `environment_ready=true`, `model_ready=false`, `model_loaded=false`, no issues and `generation_tested=false`, despite the mixed snapshot remaining absent. Two daemon files deployed under exact-source/idle maintenance guards with `tmp/model-preflight-backup`. No real model download or inference was started; existing controllers/workspaces remain running.

### All model-option labels

Removed the compatibility condition that suppressed installation labels. Every catalog option now has a status; incompatible profiles retain Installed/Not installed plus a separate “Requires different runtime” annotation. Profiles omitted by the daemon (or not yet observed) explicitly show “Installation status unknown” rather than a guessed state. Ten focused Python UI tests and three JS checks passed. Read-only native Chrome and WebKit checks verified labels on all seven options, including GGUF alternatives, with zero remote mutations. This is controller-only; no daemon restart or download was required.

## Opt-in trusted LAN controller

`--lan-ip` binds an explicit RFC1918 IPv4 interface, with a per-process private access-key file and browser login. Default loopback behavior, one-use bootstrap, exact Host/Origin, cookies, CSRF and request limits remain. LAN peers are checked independently of untrusted forwarding headers. LAN HTTP uses cryptographic `getRandomValues` IDs rather than secure-context-only `randomUUID`.

Validation: **107 focused Python tests**, three JS checks, packaged Chrome/WebKit bare-address login at the Mac's private HTTP origin (confirmed `isSecureContext=false`), repeated key login from separate browsers, owner-only key-file permissions, and no remote mutations. An independent HTTP request from the Ubuntu machine reached `192.168.1.61:8787`: access mode returned 200 and unauthenticated controller status returned 401. The running socket binds only that address. No server daemon restart, model download, generation, router change, or closure of older controllers was performed. HTTP remains unencrypted and is not an Internet-exposure feature.

## Local installation/control target

- Added an explicit Local/SSH selector. Local scans the controller host, not the LAN browser device. MacOS scan reports OS, CPU, RAM and disk and blocks installation; Local Linux uses the existing bounded, pinned setup/daemon workflows without SSH.
- **377 focused Python tests passed; two skipped**, covering local file confinement, no SSH on Local selection, deadlines, guardian EOF retention, local authorization, existing SSH security, setup/recovery/deployment and hardware probing. Nine focused JS checks passed. No full-suite run.
- Chrome and WebKit native acceptance scanned the actual Mac, verified the unsupported-install explanation, required no SSH credentials and switched back to SSH. Installation and SSH connection attempts were intercepted; zero such attempts occurred. Screenshots: `/tmp/h3-local-target-{chrome,webkit}.png`.
- On Ubuntu, temporary candidate modules verified actual Local Linux hardware probing, authenticated loopback daemon health access and an isolated owned staging guardian/immutable-file publication transaction. No SSH transport inside the controller was used. The outer test harness used the existing SSH management connection only to run that read-only/isolated acceptance. Helper: `/tmp/h3-local-linux-staging.py`.
- No OS package/driver installation, reboot, model download or inference was run. Existing daemon code and jobs were not changed. Real privileged local provisioning/reboot and a Linux-native packaged build remain unverified. Windows Local mode is explicitly unsupported; SSH mode remains available.

## Studio presentation redesign (initial checkbox-planning UI superseded below)

- Reference-inspired dark navigation/light panels, with Install, Generate + Current, History and Joiner workspaces. Stable existing form/media/timeline nodes and handler IDs are retained; controller changes are limited to serving two new presentation assets. No installer, daemon, storage or generation-business-logic changes.
- Checkbox model cards distinguish installed variants from page-local planned choices. Planning never sends mutations; Review updates the original single-profile selector. Existing explicit Install consent/digests, runtime compatibility, disabled reasons and sequential installation behavior remain authoritative.
- `node tests/studio-layout.cjs` and `H3_ENGINE=webkit node tests/studio-layout.cjs`: native workspace/draft/node preservation, checkbox multi-selection and stable poll identity, mobile overflow, 18px timeline scrollbar, keyboard focus across job-panel moves, first-click disclosure toggling. Tests reproduced missing workspaces, focus loss and the summary-focus double-toggle before their fixes.
- **58 focused Python tests passed**, covering controller UI/smoke/security/LAN/Local. **23 focused JS checks passed** for generation submission/estimates, references/reuse, model statuses, first-click delivery and joiner controls. **Four selected Playwright controller/worker fixture tests passed**, covering authenticated queue/download/cancel, stable media polling/expiry, SSH trust and installation selection. Stale pre-redesign copy/short-job-title assertions were aligned with existing behavior; no production logic was altered for those expectations.
- Packaged Chrome/WebKit acceptance: actual Mac Local scan/switch-back; intercepted model installation/progress and generation submission; reference/frames/text reuse including missing-asset atomicity; local synthetic-clip timeline seeking, cross-clip playback, scoped keys, reordering, removal and scrolling. Test preference writes and remote install/generation mutations were intercepted. No real remote generation/model download/package change or daemon deployment.
- Source UI screenshots: `/tmp/h3-studio-{install,generate}-{chrome,webkit}.png`. Helpers `/tmp/h3-studio-{models,reuse,queue,joiner}.cjs`; build log `/tmp/h3-studio-build.log`. Native Windows/Linux packaged rendering and untested browser/platform combinations are not claimed.

## Desired-state component checklist and reconciliation

- Replaces planning/Review/visible selector with one checklist grouped into models and optional SageAttention, Turbo, sparse attention and Ref2VA. Installed defaults, per-card information/license dialogs, pending additions/removals, one Apply, overall/action/download progress and Stop. Editing/polling/navigation do not submit work; stable nodes preserve first-click delivery and focus.
- New authenticated `action: synchronize` request on the existing models/install route; exact inventory and daemon-instance binding, strict consent, one idle-only maintenance owner, additions before removals, inherited recovery guards for mutating subprocesses and durable partial outcomes. Schema-2 model registry permits removing the original base or all weights without restart resurrection. Managed no-follow/unaliased/owned-file cleanup preserves unrelated files, uploaded assets, history, outputs, frozen dependencies and still-needed normalized caches.
- **203 focused Python tests passed**: components/recovery/optionals, library/preflight, remote API/worker, sparse/Turbo/Sage/preferences, references, controller security, UI, LAN and Local. Regression evidence includes a missing reconciler, absent partial-download cleanup and unbound daemon-instance inventory before fixes. No full-suite run.
- **12 focused existing JS checks passed**, plus Chrome and WebKit runs of `tests/component-checklist.cjs` and `tests/studio-layout.cjs`. **Five selected Playwright fixture tests passed**: authenticated generation proxy, stable output reconciliation, full first-time setup/reload, SSH trust, and checkbox/info UI.
- Actual **192.168.1.61:8789** Chrome/WebKit acceptance: automatic Generate default; exactly one visible checklist; real INT8/mixed and all four optionals checked; unsupported choices explained; edited removals survive polling; reset/keyboard-dismissed license dialog/mobile layout. Intercepted batch mutation additionally verifies one bound multi-change request, numeric byte/action progress, disabled Apply, enabled Stop and terminal installed-state hydration. No production install/uninstall/generation was used for acceptance.
- The daemon update used exact-prior-source checks, private backups and idle reject-policy maintenance. Initial deployment correctly rolled back because its history comparison included the deliberately volatile `timing.observed_at`; two read-only requests reproduced that sole difference. Corrected comparison excludes only that observation timestamp; persisted history then compared unchanged. Final backups: `tmp/component-sync-backup-d0a688228e9643e481108267ef4cf4c7` and `tmp/component-sync-backup-ca8bbafc6d68477bb855a8a2444a6957`.
- Existing 8789 controller process was not restarted: public assets were exact-source checked, replaced atomically and byte-verified over HTTP, preserving its private joiner workspace/session. Rebuilt `dist/h3-controller`; build log `/tmp/h3-components-build.log`. Helpers `/tmp/h3-components-{live,batch-ui}.cjs`; captures `/tmp/h3-components-{live,progress}-{chrome,webkit}.png`.
- Historical limits (cross-runtime/fresh-selection limits superseded below): additions across runtime families were disabled rather than silently switching frozen environments. Fresh provisioning remains one base model plus supported optional accelerators; Ref2VA/additional compatible models follow after runtime setup. Sage required by the frozen runtime cannot be removed separately. Real bulk multi-GB install/uninstall, privileged Linux provisioning/reboot and other native platforms were not newly exercised; destructive testing used isolated fixtures only.

## Complete isolated multi-runtime installation

- Missing runtimes are installation work, not disabled model choices. Apply provisions pinned runtime sources, separate Python environments, frozen dependencies, native environment verification and then verified model weights. The running daemon and existing environments are not replaced. Exact-family bindings route readiness, validation, commands, resident protocol and output validation; command/environment changes recycle retained workers. Legacy nonresident profiles are also dispatched separately.
- Sage package/GPU receipts and sparse GPU receipts are environment-scoped, with exact-environment legacy receipt fallback. Additions extend selected accelerators to newly installed compatible environments. Removal invalidates receipts first and refuses frozen dependencies; model removal preserves runtime environments and shared legacy files. Selected-profile generation blockers do not inherit another environment's accelerator status.
- The controller stages its current approved wheel without replacing service/setup pointers. Fresh setup preserves the complete desired set through initial bootstrap and subsequent reconciliation; durable pending intent supports explicit recovery. Extra-runtime provisioning never replays sudo/driver/reboot steps. Existing host prerequisite gaps are reported rather than silently privileged.
- **234 focused Python tests passed (26.39s)** covering runtime bindings/provisioning, cross-family worker dispatch, accelerator scopes, complete selection/setup/release staging, components/recovery, model library, remote API, workers/residency, sparse/Sage, controller security, Local and LAN. The end-to-end fixture traverses the authenticated daemon API, isolated environment creation/dependency/verification stages, binding publication, fixture downloads, registry registration and actual selected-interpreter subprocess execution. It uses fixture packages/output, **not real model/GPU inference**.
- Native Chrome/WebKit checklist and Studio layout checks passed, plus focused generation-blocker/model-label/queue/reuse/joiner-focus/estimate/reference/Turbo/preview JS checks. Five selected Playwright tests passed (proxy generation fixtures, stable output polling, first-time setup/reload, SSH trust, checklist/info UI). No full suite was run.
- Read-only Ubuntu candidate inspection confirmed all six previously installed components remain installed and the host needs no additional OS packages/driver/reboot prerequisites. Guarded exact-source daemon deployment succeeded with idle `policy: reject`, backup `tmp/multi-runtime-backup-72807c21d4464040ae8ee9434acb5273`, staged hash-verified wheel and unchanged persisted history/component inventory. No production model installation, deletion or generation was triggered.
- 8789 public assets were exact-source checked, atomically replaced and HTTP byte-verified without restarting PID 52780 or discarding its joiner workspace. A separately rebuilt controller runs at **192.168.1.61:8790** for updated Python setup orchestration; older controllers remain running. Build log: `/tmp/h3-multi-runtime-build.log`. UI backup: `/var/folders/dc/nb1_hw3j7156wv3kj95bryk40000gn/T/h3-multi-runtime-ui-backup-ad5ybq9_`.
- Actual Chrome and WebKit acceptance passed on **both 8789 and 8790**: Generate startup, six installed components retained, Q4/Q5 enabled with runtime/dependency scope, Q5 selection surviving polling, NVFP4 still hardware-blocked, removal edits/reset/info dialogs and mobile layout. All mutation routes were intercepted; no live install/uninstall/generation. Helper `/tmp/h3-multi-runtime-live.cjs`; captures `/tmp/h3-multi-runtime-live-{chrome,webkit}.png`.
- Remaining evidence limits: no real multi-GB cross-family download or cross-family GPU generation in this change; no destructive production acceptance; fresh privileged Linux/reboot and other hardware/platform installations were not newly exercised. These are implementation/fixture/browser results, not new GPU certification.

## Retained-controller upload failure after executable rebuild

- Reproduced first-frame upload HTTP 500 on preserved 8789. The identical PNG returned 201 through 8790 and the daemon directly. Asset decoding/storage were not the failure. The prior rebuild replaced `dist/h3-controller`, which older PyInstaller PYZ loaders reopen for lazy imports; preserving a process alone did not preserve its executable-backed imports. The previous deployment's preservation claim missed this dependency.
- `scripts/smoke_controller_replacement.py` reproduced the same HTTP 500 using an isolated copied executable, private fresh state and replacement of only that scratch executable. No SSH target was contacted. The corrected `noarchive=True` package passed: a late upload import after replacement reaches the normal authentication-required response instead of 500.
- The build command now refuses existing executable destinations before touching build artifacts and accepts `--distpath` for a separate output. Its guard regression failed before the fix and passed afterward. New package: `dist/upload-fixed/h3-controller`; the existing `dist/h3-controller` was not replaced again. Standard native packaging/Host/Origin/CSRF/catalog smoke passed twice; **80 focused tests passed** (packaging guard, asset store, controller security and remote API).
- Actual Chrome and WebKit first **and** last frame uploads on **8790** returned 201 and decoded their 32×32 PNG previews. Two tiny diagnostic PNGs were uploaded; no model changes, deletion or generation. Captures `/tmp/h3-upload-frames-{chrome,webkit}.png`; native build log `/tmp/h3-upload-fixed-build.log`.
- The old 8789 process is still alive to retain its joiner workspace, but its broken archived imports are not repaired by page reload or static assets. Use the verified working 8790 controller for uploads. No existing controller was restarted or workspace discarded.

## Dashboard automatic recovery after transient read failures

- A failed capabilities/jobs/events request previously exited `dashboard()` before its next timer was scheduled. The daemon/job could finish normally while the page stayed on an unavailable error until reload. Read-only inspection confirmed a healthy ready daemon and completed jobs.
- Dashboard reads now reschedule in `finally`, with 2–30 second bounded exponential backoff for transport/remote/invalid-response failures. Readiness is not assumed during failure; successful complete observation restores it and clears only the polling-owned error. Action errors remain visible. Authentication failures stop retries; target/request epochs and page lifetime fence stale timers. No generation or installation mutation is retried.
- `tests/dashboard-recovery.cjs` failed before the fix and passed afterward, covering backoff, capabilities/events failure, recovery, unrelated errors, target changes, authentication and read-only requests. Queue submission, estimate refresh, preview and checklist checks also passed.
- Actual Chrome/WebKit on 8790: intercepted one jobs GET with HTTP 502, observed the automatic-retry banner and subsequent recovery/error clearing, with no page navigation/reload and no generation resubmission. Helper `/tmp/h3-dashboard-recovery-live.cjs` tested the actual served JavaScript, not a substituted asset.
- Exact-source atomic public-asset updates were HTTP byte-verified on 8789 and 8790 without process restarts. Backup: `/var/folders/dc/nb1_hw3j7156wv3kj95bryk40000gn/T/h3-dashboard-recovery-backup-8p37lwqs`. Rebuilt separately at `dist/dashboard-recovery/h3-controller` without replacing any running executable; build log `/tmp/h3-dashboard-recovery-build.log`. Existing browser tabs need one reload to receive the new JavaScript; subsequent transient dashboard failures recover without reload.

## Consistent MP4 action and MP3 audio export

- Download MP4 now shares button styling while retaining native link/download semantics. Completed, unexpired Current/History videos offer Download MP3 on capable controllers. The MP3 action performs one explicit authenticated export, checks response type/size and target epoch, and reports missing audio rather than downloading an empty file.
- Server export uses PyAV/libmp3lame at 320 kbps, preserves mono/stereo and supported sample rates, flushes decoder/resampler/encoder, and never changes the original MP4. An owned read-only descriptor is inherited by a CPU/address-space/wall-time-bounded child. One export at a time, maximum 60 seconds of decoded audio and 4 MiB output; no caller paths/commands and no automatic polling export.
- **82 focused Python tests passed, two local PyAV-dependent tests skipped**, covering audio/last-frame export, remote API and controller security. Six focused JS checks passed for audio export, last-frame, dashboard recovery, preview stability, Current/History and submission. The native executable-replacement regression passed for the new package.
- Actual Ubuntu codec evidence: existing completed video exported to a decodable MP3; original audio 5.184 seconds and decoded MP3 5.184 seconds, 210,285 bytes. Original copy unchanged. A no-audio MP4 fixture returned the explicit missing-audio result. Evidence: `tmp/mp3-acceptance-nrmg_btn`.
- Actual Chrome/WebKit at **192.168.1.61:8791** downloaded MP3 from both Current and History and the original MP4. To test Current without generation, one existing completed job was withheld from the initial jobs response, then exposed on the next read. No generation or model changes. CSS computed values matched MP4/MP3 padding, border radius, font, background and height; MP4 had no link underline. Independent local ffprobe verified all four downloaded MP3 files as stereo, 32 kHz, 320 kbps, 5.184 seconds.
- Guarded daemon deployment used exact prior sources, idle reject maintenance and backup `tmp/mp3-export-backup-e238f8bed9ae4862a58393a6341b6905`; retained history/components compared unchanged. Rebuilt at `dist/mp3-export/h3-controller` without replacing prior executables; controller 8791 supplies the new Python endpoint. Older controllers/processes remain running; their public assets received the MP4 styling fix and hide MP3 because their backend lacks the endpoint. Asset backup: `/var/folders/dc/nb1_hw3j7156wv3kj95bryk40000gn/T/h3-download-actions-backup-rxaeq85e`.
- Helpers `/tmp/h3-mp3-{acceptance.py,deploy.py,live.cjs}`; captures `/tmp/h3-mp3-actions-{chrome,webkit}.png`; decoded browser downloads `/tmp/h3-mp3-{current,history}-{chrome,webkit}.mp3`. No new inference/GPU certification or production deletion was performed.

## Explicitly confirmed joiner conversion fallback

- Lossless stream copy remains first/default. Only an incompatibility response offers conversion confirmation; decline creates no export. Approval sends the same ordered clip IDs with strict `convert: true`. Other failures/timeouts never trigger a fallback retry. Converted results are labelled honestly, not advertised as stream copy.
- Approved fallback fits clips to the first canvas (even pixels) and frame rate using black padding, never cropping/stretching. It uses H.264 CRF 18 with a 12 Mbps ceiling and optional stereo 48 kHz/192 kbps AAC, inserting silence where needed. Compatible clips still copy even if conversion is permitted. Unsupported HDR/rotation/color/timing transformations fail explicitly rather than guessing.
- **51 focused Python tests passed**: actual FFmpeg conversion, decoded padding and order, unchanged originals, missing-audio silence, compatible-copy preservation, explicit boolean API approval, existing joiner failure/security checks. Seven JS checks passed including confirmation/cancellation, ordered intent, no retries of unrelated errors, timeline/focus controls and dashboard/audio regressions. No full suite.
- Chrome and WebKit acceptance used separate short-lived native packaged controllers with isolated fixture workspaces. Differently sized/rated clips first produced the confirmation dialog. Decline sent no conversion request or download; approval produced a real H.264/AAC 320×240, 24 fps, 2.000-second MP4. Local ffprobe independently checked both downloads. No existing workspace was cleared or modified by acceptance.
- New packaged controller at **192.168.1.61:8792**; build `dist/joiner-confirmed/h3-controller`, log `/tmp/h3-joiner-confirmed-build.log`. Existing controllers/private joiner workspaces remain running and are not migrated automatically; re-add clips in the updated controller. No remote daemon update, model change or generation was required. Served joiner JavaScript byte-verified against source.
- Native helper `/tmp/h3-joiner-confirmed-native.cjs`; screenshots `/tmp/h3-joiner-confirmed-{chrome,webkit}.png`; outputs `/tmp/h3-joiner-converted-{chrome,webkit}.mp4`. Conversion retains workspace/quota/time bounds and rejects unverifiable output; it is intentionally lossy and not sample-exact editing.
