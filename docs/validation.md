# Validation and acceptance status

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
