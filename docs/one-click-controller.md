# One-click controller contract

## Reusing a generation combination

Click **Reuse inputs** on a job in Current or History to replace the generation
form's draft with its recorded prompt, model, seed, resolution, length, steps,
Sage/Turbo/sparse settings, supported LoRA and reference/first/last-frame assets.
This only prefills the form: **nothing is queued or installed**. Review it, then
click Queue generation yourself. Original jobs and asset files stay unchanged.

Assets are reused by their original immutable IDs after metadata checks. Missing
assets, unavailable models, changed component pins, unsafe seed precision or inputs
this form cannot represent leave the draft unchanged with an explanation. Jobs
without recorded settings have a disabled button. Previous draft assets/options
are replaced, not merged; ordinary readiness blockers remain in effect.

The primary browser flow uses a single composite Install intent. It does not call
`setup/plan`, `setup/install`, `setup/reboot`, `setup/activate` or `setup/download`
separately. Those low-level APIs still require their existing explicit approvals.
No arbitrary command, package list, busy-job policy or replacement artifact comes
from the composite request.

All paths below are relative to `/api/v1/controller`. Existing bootstrap cookie,
exact local Origin/Host, CSRF header, body limits and SSH host-key verification
apply unchanged. Never put credentials in browser storage, URLs or logs.

## Authentication

`POST /authenticate` accepts:

```json
{"password":"<SSH password>","reuse_for_setup":true,"remember_connection":true}
```

The UI distinguishes operation-scoped memory reuse from explicit persistent
remembering. `remember_connection` defaults false at the API. When selected, the
successfully authenticated host/port/user/password are atomically saved in the
controller state directory's owner-only `preferences.json`. This is plaintext,
not Keychain storage or encryption, as requested by the operator. The browser
receives only public connection fields and a `password_saved` boolean.
`POST /connection/forget` removes the saved connection. Session expiry and setup
completion still clear temporary in-memory reuse, but not the opted-in disk copy.

`GET/POST /preferences` reads public preferences/updates `install_sage` and
`use_sage`. `POST /connection/restore` reconnects and authenticates the remembered
target only after stored host-key verification. A new fingerprint pauses for
approval; a changed fingerprint blocks. `POST /connection/authenticate-saved`
requires an exact current host/port/user match. Startup never installs or generates.

Both `reuse_for_setup` and the legacy `retain_for_setup_recovery` default to false.
The legacy flag alone never authorizes sudo reuse. Authentication still requires
an established, verified SSH connection awaiting authentication. To authenticate
again after a terminal setup, disconnect/connect first (saved unchanged host keys
remain trusted; new or changed keys still require the existing security handling).

Authentication and general status include `recovery.credential_retained` and
`recovery.setup_credential_retained` booleans, never a credential. Credentials are
bound to the authenticated target and browser session. Setup keeps no independent
password copy between calls. Python strings cannot be reliably zeroized; clearing
drops owned references and cancellation interrupts in-flight SSH IO where safe.

## Delete several jobs

In **Current** or **History**, check the jobs you want to remove, or use
**Select all in this tab**, then **Delete selected**. One confirmation covers the
selected jobs and their videos/logs/timing records/unshared references. Shared
references remain protected by the existing deletion checks. Independent local
video-joiner copies are unaffected.

Only stopped jobs can be selected. Cancel queued/running jobs explicitly and wait
for them to stop first. Selections survive dashboard polling, but changing tabs
or connections clears them. Tabs are locked while the batch runs. Deletions run
sequentially; failures are reported and eligible failed items remain selected.
Connection/session failures stop the remaining attempts. Retry explicitly after
resolving the problem; completed deletions are not rolled back.

## Installed software on the target

Open **Install → Target system information** and use **Scan hardware**. The
**Installed software** section loads separately from hardware discovery and shows
observed versions without installing anything, importing Torch or loading models.

Each managed runtime has its own Python version, key package versions, Torch CUDA
build target, expandable full Python dependency list and source revisions. Actual
package metadata is used, not a requirements file or stale dependency freeze.
Python's recorded creation version is labelled separately from its observed
interpreter version. CUDA runtime packages, the CUDA toolkit Python package,
`nvcc` on PATH and NVIDIA driver compatibility are distinct observations.

Registered model/component pins are shown as receipts, **not reverified weights**.
System tools and the full dpkg package list are labelled as observed with unknown
installation ownership; pre-existing software is not falsely attributed to this
tool. Missing tools or partial records are explicit. Refresh using Scan hardware;
there is no continuous inventory polling. Changing targets clears old results.

## Video joiner

Open **Video joiner** in the sidebar. Choose/drop local MP4 files, or use
**Add to joiner** on a Current/History output. The visual timeline shows
thumbnail-filled blocks whose widths follow clip duration. Drag a whole block
before/after another (or use Earlier/Later), select and remove unwanted clips,
and select **Export joined video**. Click a block or scrub the time ruler to seek
the large preview; use Timeline zoom and the visible horizontal scrollbar for
longer sequences. The scrollbar appears only when the track overflows and stays
in sync with native scrolling and resizing. The ruler also supports arrow keys,
Home and End. While focus is in the timeline, **Space plays/pauses from the current
playhead**, and **Delete/Backspace removes the selected clip** without needing a
right-click. Enter selects a keyboard-focused block. Holding a key does not
repeatedly delete clips or toggle playback, and typing elsewhere is unaffected.
Automatic hardware discovery does not steal focus from the joiner. No split/trim operations are performed;
export always includes whole clips regardless of the preview playhead.

Filmstrip thumbnails are browser-generated display previews, cached only in page
memory. Their small display size does not affect originals or exported quality.
Unsupported browser codecs show a preview placeholder without disabling export. The download starts when ready; the Download joined MP4
link remains available until the timeline changes. Play sequence previews the
original clips in order; playback can briefly pause between files.

Joining happens locally with installed `ffmpeg` and `ffprobe`, without GPU work,
remote installation or a daemon restart. Missing tools are explained and never
installed automatically. Matching clips use stream copy, not decoded/re-encoded
frames: the default path has **no additional compression loss**. Source files and History jobs
are unchanged. Only compatible MP4 H.264/H.265 with one video track and optional
AAC audio are accepted. Stream layout, codec configuration, dimensions, rates,
pixel/color/audio parameters and display metadata must match for stream copy.
If they do not, the joiner asks whether to **Convert and join**. Declining creates
no new export. Confirming explicitly permits lossy H.264/AAC re-encoding to the
first clip's size (rounded up to even pixels) and frame rate. Images fit with
black padding, not cropping/stretching; frame-rate conversion can duplicate/drop
frames. Audio is normalized to stereo 48 kHz AAC, with silence for clips lacking
audio when other clips have audio. Originals and prior exports are unchanged.
Compatible clips still use stream copy even if conversion permission is supplied.

Conversion currently supports SDR, square-pixel clips up to 1920×1088 area,
matching color metadata and zero starting offsets; HDR, rotation, unusual color
or timing metadata require an external editor rather than guessed transformations.
It uses CRF 18 H.264 with a 12 Mbps ceiling and 192 kbps AAC, a 150-second encoding
deadline and the workspace budget. Output geometry, track layout and duration are
verified; failed/partial exports are removed. Stream-copy exports retain their
packet-count/decoder-metadata verification.

Limits: 2–32 timeline clips, 64 MiB per file, one hour total, 1 GiB temporary
workspace including exports. Stream copy retains original encoded quality, but MP4
container timestamps/metadata may change and AAC encoder padding can affect audio
seams. This is not a sample-exact audio editor; no trimming or transitions.

Temporary copies are private and controller-session scoped. Removing a timeline
item deletes only its temporary copy. **Clear workspace** removes all temporary
clips/exports after confirmation; it never deletes original files or History jobs.
Normal controller exit also cleans the workspace. Reloading loses timeline order;
retained copies can be cleared from the new page. Download exports you want to
keep. A forced process kill/crash may leave owner-only OS temporary files.

## Video previews

Current and History load video previews near the visible viewport, rather than
preloading the whole history. The player loads a decoded frame before Play, shows
loading/error feedback and retains playback through dashboard refreshes. Preview
and Download MP4 use the same original video; no lower-quality transcode is made.

## Download video or audio

Completed, unexpired videos in both Current and History offer **Download MP4**
with the same styling as the other action buttons. It downloads the original MP4.
**Download MP3** exports only its audio as a 320 kbps MP3, preserving mono/stereo
and supported sample rates (otherwise resampling to 48 kHz). MP3 is a lossy
re-encode, not lossless extraction; no normalization or original-video modification
is performed. Videos without an audio track show a clear error, not an empty file.

Export is explicit and authenticated, reads an owned output descriptor, and runs
in a bounded codec subprocess (60-second audio limit, 4 MiB MP3 cap, 25-second
wall timeout). One MP3 export runs at a time. Browser polling never exports audio.
A controller with the audio-export endpoint is required; older controllers hide
that action rather than offering a broken link. Existing jobs remain on the server
and are available in History when opening a new controller/page.

## Export the last video frame

Available completed videos in Current and History offer **Download last frame
(PNG)** and **Use last frame as first frame**. Export uses the actual final decoded
frame, not a screenshot or approximate seek: native resolution, RGB PNG with
compression level zero, no resizing, palette conversion or JPEG encoding. It adds
no image loss but cannot undo compression already present in the video. Current H3
outputs are 8-bit; higher-bit-depth outputs are refused rather than reduced.

Reuse uploads the exact PNG through the normal managed-asset path, replaces the
first-frame draft slot and selects Frames mode. Prompt, dimensions, last-frame
choice and other settings remain unchanged; **Use dimensions** can copy the frame
size if wanted. No generation starts automatically. Reuse requires a supported
pruned profile. A reused image is an independent input, so deleting its source
video does not remove the copy from a subsequent job.

Export is CPU-only, bounded and read-only; no permanent export cache is created.
Only one export runs at a time. Missing/expired outputs, timeouts and unsupported
precision produce errors, never a lower-quality fallback.

## Delete jobs and their files

Every card in **Current** and **History** has a confirmed **Delete job** action.
Cancel queued/running jobs first and wait until they have fully stopped; Delete
never cancels implicitly. It removes the job record, output/temporary directory,
log contents, timing sample and references not used by another saved job. Averages
are recomputed without that sample. Shared references remain until their last job
is deleted. Models, preferences and unrelated jobs are not removed.

This cannot be undone. Downloads/backups outside the daemon are not erased, and
an already-open download can finish. Draft-only references in another browser are
not durable shared-job references and may need re-uploading. Failed cleanup keeps
the job listed for retry; repeating a successful deletion is safe.

## Saved generation inputs

Queue/history cards show the seed directly alongside geometry and steps. Expand
**Inputs** to inspect the full untruncated prompt, model, dimensions, frames,
steps, seed, SageAttention, LoRAs and sparse-attention selection. These come from
that job's persisted request, not the current form; existing history works too.
Expansion and video elements survive dashboard polling. Prompt text is rendered
as text, never HTML. Missing legacy settings are explicitly marked unavailable.
Conditioned jobs also retain their mode and immutable asset IDs. The Inputs
section links to each original upload through authenticated SSH proxy routes.
Removing a draft attachment does not delete anything referenced by history.

## Reference assets and first/last frames

The input-mode selector offers **Text**, **References**, and **First / last
frames**. First/last-frame mode accepts either image or both and uses the existing
FL2VA weights on pruned profiles. The first image stretches to the selected canvas;
the last image is center-cropped. Matching aspect ratios avoids distortion.

Reference mode currently requires **ConvRot INT8** and a separate, explicitly
approved Ref2VA checkpoint from the pinned Abiray revision. It is approximately
21 GB to download and 42 GB on disk including its verified compatibility copy;
existing FL2VA, encoder and VAE files are preserved. The download button requires
an idle queue and confirms the MiniMax H3 Community License. Restore/connect never
starts a download. Runtime caching switches diffusion weights and retains the
encoder where safe rather than keeping two large diffusion models resident.

Reference attachments are optional: with none selected, the prompt runs on the
selected Ref2VA checkpoint (not an implicit switch to FL2VA). Upload up to 9
images, 3 videos and 3 audio files. Use `<Picture 1>`, `<Video 1>`
and `<Audio 1>` tags in prompts; numbering follows the order within each type.
Each visual preview (first/last frame, reference image or video) offers **Use
dimensions**. It copies the source width/height to the output, independently rounded
to the nearest multiple of 32 (ties upward, minimum 32). The preview shows original
→ output dimensions and explains when this is not an exact match; aspect ratio may
change. Oversized results are disabled with an explanation, not silently downscaled.
Audio has no dimensions button. This changes the generation canvas, never the
source file, and updates presets and timing estimates.

Removing an attachment renumbers its type, so adjust prompt tags. Video references
use visuals only; upload audio separately for sound references. Videos resample
to 24fps, are limited to the output length and shorten to H3's 17n+5 frame grid.
Audio-only references are supported by this local runtime. References guide the
model; they do not promise exact copies or identity fidelity.

Uploads are capped at 64 MiB each and 8 GiB total retained originals. Supported:
PNG/JPEG/static WebP (up to 16 megapixels, 8192 pixels per axis), MP4/WebM (up to
1920×1088 pixel area and 60fps), WAV/FLAC/MP3 mono/stereo. Video/audio must be
0.2–15 seconds; reference videos total at most 15 seconds. Clips are independently
decoded in a bounded subprocess before acceptance. Caller URLs and filesystem
paths are never accepted. Source bytes and metadata live under owner-only
`~/MinimaxH3Server/assets/`, addressed by content hash and protected against
symlink substitution and changed content. Browser uploads retain CSRF/origin and
SSH host-key protections; authenticated previews support byte ranges.

SageAttention and Turbo can be used with reference and frame inputs. Turbo keeps
its existing supported-profile, 4–8-step and pinned-adapter requirements.
Sparse attention remains blocked for conditioned inputs; its warning appears
only when sparse is actually selected. SageAttention follows the existing
compatible-kernel fallback policy. Larger
assets increase memory pressure; target acceptance used small fixtures, not the
maximum counts/resolution/duration. Timing exports separate input modes and
conditioning geometry, omitting source hashes, filenames and media contents.

Daemon endpoints: `POST /api/v1/assets/{image|video|audio}` (raw media),
`GET /api/v1/assets/{sha256}`, `GET /api/v1/assets/{sha256}/content`, and
`GET/POST /api/v1/features/reference` (digest-bound explicit installation).
Generation adds `input_mode`, `reference_images`, `reference_videos`,
`reference_audios`, `first_frame`, and `last_frame`; all references are managed
SHA256 IDs. Reference requests also persist `reference_artifact_digest`; a queued
job cannot silently run against a different checkpoint after a software update. The controller exposes these through its authenticated `/remote/`
proxy. Legacy jobs default to Text without rewriting historical records.

## Resolution presets

Landscape/portrait presets include 480p-size (864×480), 720p-size (1280×736)
and 1080p-size (1920×1088), with swapped dimensions for portrait. These are native
H3-aligned canvases, not upscaled/cropped standard-raster exports. Exact dimensions
are shown in each option; manual Custom input remains available.

Pruned profiles accept up to a 1920-pixel axis and 2,088,960-pixel canvas, in
multiples of 32. Legacy Diffusers keeps the previous 1344-axis / 1,032,192-pixel
policy. The connected daemon advertises its limits; unavailable presets are
disabled and incompatible manual dimensions show an actionable blocker.
Higher-resolution generation is **unbenchmarked on the target GPU** and can exceed
available GPU/host memory, especially for longer clips. No full-HD inference or
quality acceptance is claimed from API/UI validation alone.

## Retained models and measured estimates

Comfy/pruned generation uses an owned resident child process: the base model,
text encoder and VAEs remain available between jobs with no idle expiry. This is
object/component retention, not a claim that every weight stays in VRAM. Standard
GPU/CPU offloading still applies. On constrained hosts, decoder caches may be
released immediately before encoder transfer and reloaded for decoding. If this
is insufficient, the encoder is retained while diffusion weights are reloaded
after encoding. Changing Turbo variants can also reload the base on constrained
hosts: merged weights plus anonymous CPU-offload copies require additional RAM;
reloading restores reclaimable disk-backed weights while keeping the encoder.
Sparse request-local routing patches rebuild without reloading base weights.
No downloaded files are deleted. Pageable host memory avoids
locking most RAM. An unsafe transfer fails clearly rather than exhausting the host.

An explicit **Unload models** action releases the idle worker and its GPU/CPU
allocations, not downloaded files or history. It rejects running/queued work or
maintenance. Profile/service changes start a fresh worker. Forced cancellation,
native faults and shutdown are safety exceptions to retention. The legacy
Diffusers adapter keeps its previous isolated behavior; its residency is not
advertised as supported.

APIs (authenticated daemon; corresponding controller `/remote` routes require the
local session and CSRF for POST):
- `GET /api/v1/models`: supported/loaded/components/profile/busy/can_unload.
- `POST /api/v1/models/unload`, body `{}`: idle release; 409 when busy, no force flag.
- `POST /api/v1/performance/estimate`: normalized generation input; performs no
  generation, model loading or downloads. The UI sends only placeholder text of
  equivalent codepoint length, not an unsubmitted prompt.
- `GET /api/v1/performance?offset=0`: latest 50 successful raw samples, schema version
  and pagination cursor. The UI downloads one page; nothing uploads automatically.

`state/performance.sqlite` stores unique per-job successful measurements and
transactional running averages/counts/variance/min/max. It is independent of the
existing job database and survives output retention. Failures/cancellations never
enter successful averages; old untimed runs are not reconstructed. Runtime estimate failures do not block generation; save failures are reported
separately without invalidating a completed video.

Records contain model/artifact identity, all generation settings, LoRA/attention
choices and actual attention route, prompt-length bucket (not text), GPU/VRAM,
CPU/RAM, OS, driver/CUDA/Torch/Triton/Sage and runtime-source/environment identities.
Exports omit job IDs, prompts, credentials, hostnames, device serials/UUIDs and
filesystem paths. They are self-reported measurements, not trusted community
attestations. Seeds are recorded but do not split average groups; irrelevant
inactive Turbo settings are ignored when matching. Exact prompt-length buckets
are preferred; if none exists, the nearest measured prompt-length bucket with
otherwise matching settings, hardware, cache and actual attention can be used.
The UI explicitly labels that estimate approximate/different prompt length.
Prompt wording alone does not restart the lookup within the same length bucket.
New completed/deleted jobs and residency changes refresh the lookup automatically.
Returning from the browser's page cache resumes observations; a 30-second lookup
deadline replaces indefinite checking with an explicit retry message. Deleting a
job deletes its timing sample too—missing/deleted measurements are not fabricated.

Cold, warm, `warm_partial` (base/encoder retained) and `encoder_reuse` (encoder
retained, other weights reloaded) samples remain separate. Estimates require the same
measured hardware/software/configuration group; sample count and observed spread
are shown, not a guaranteed confidence interval. The first sampling-step duration
includes GPU transfers and sampler/adapter preparation. Missing phases stay
unavailable; CUDA phase/step boundaries are synchronized. Total execution time
includes worker startup and independent output validation, excludes queue delay.
Running jobs can exceed their measured average; the UI says so instead of freezing
an artificial countdown at zero.

## Optional sparse attention

Select **Install optional sparse attention**, then Install. Existing environments
only acquire the optional component; fresh setup installs it after base models
and optional SageAttention. About 22 MB is downloaded (archive plus extracted
files use about 33 MB); no model weights, pip packages or system packages are added.
The install is idle-only, checksum-bound and GPU-probed. Source/native files are
verified again before generation. Changed file signatures invalidate fast readiness.

Choose a remembered preset: Off, Quality-first (50%), Balanced (30%) or Fast draft
(15%). Start with Quality-first, 20 steps and a short clip; compare results before
lowering the budget. The early ramp starts with at least 50% attention. These are
video-attention budgets, not claimed speedup percentages. Sparse changes model
computation and can affect motion, detail, prompt adherence and the joint output.
Text/audio connections remain dense, but this does not promise identical audio.
Off bypasses the patch entirely. No preset changes steps, Sage choices or geometry.

SageAttention remains selectable for compatible dense work. Sparse calls use their
own kernels; this is not two kernels accelerating the same call. Turbo + sparse is
rejected in both UI and API after a GPU test detected bypassed Turbo QKV adapters.
Only ConvRot INT8 is enabled; other profiles/hardware are not assumed compatible.

Preferences add `install_sparse` and `sparse_preset`; older documents migrate to
false/off. `/setup/start` accepts `sparse_attention:true` for installation only.
Local GET/POST `/remote/features/sparse` proxies the authenticated daemon feature
endpoint with the controller's exact artifact digest. Generation uses
`sparse_preset` and `sparse_artifact_digest` (not the legacy `sparse_attention`
boolean). Published preset meanings are immutable; changed policies require a
new identity. Stored requests/history contain the selected preset and component
identity; execution events distinguish actual native launches from an unconfirmed
sparse/fallback path. Reconnect never starts installation or generation.

See [implementation evidence](plans/2026-09-29-sparse-attention.md).

## Optional Turbo LoRA

The UI offers independent remembered install/use flags (`install_turbo`,
`use_turbo` in preferences). `POST /setup/start` accepts `turbo_lora:true` to acquire
it after the base environment, model and optional SageAttention setup. Existing
ready installs use `GET/POST /remote/features/turbo` on the local controller.
The controller supplies its fixed artifact digest; the authenticated daemon's
`POST /api/v1/features/turbo` requires `{artifact_digest: <exact pinned digest>}`.
A mismatched preset is rejected before any mutation. This is not an arbitrary URL,
plugin or LoRA installation endpoint. Running/queued jobs block installation.

Generation accepts `turbo_lora`, `turbo_strength` (0.8–1.2), `turbo_low_vram`, and
`turbo_artifact_digest`. The UI defaults to six steps when enabling Turbo and
bounds it to 4–8. Settings/preset identity persist in each job's inputs; loading
checks the requested set and all hashes again. Standard generation does not load
Turbo code. Native ConvRot INT8/mixed are the current supported profile scope;
only INT8 received the real GPU acceptance check. See
[Turbo implementation and evidence](plans/2026-09-29-turbo-lora.md).

## Optional SageAttention

`POST /setup/start` accepts `sage_attention: true` to add the pinned optional
component after the ordinary environment/model setup, before reporting completion.
The choice participates in request-id binding. Existing ready environments use
`GET/POST /remote/features/sageattention`; POST is explicit consent to install and
GPU-probe the pinned pure-Triton 1.0.6 wheel. It uses daemon-owned maintenance with
reject-if-busy policy, never cancelling jobs or changing model downloads/units.
Live bounded output and installation state survive controller restarts. Generation
uses an independent strict boolean and fails validation if selected but unavailable.
Both UI choices persist. Sage is not the unsupported sparse-attention feature.

## Start

`POST /setup/start` → HTTP 202 with a status document:

```json
{
  "profile":"h3-gguf-q4-k-m",
  "artifact_set_digest":"<exact digest from /profiles or /scan>",
  "request_id":"<crypto.randomUUID()>"
}
```

For a legacy profile, omit `artifact_set_digest` or supply null. Pruned profiles
require the exact catalog digest. `request_id` is 8–64 ASCII letters, digits,
underscores or hyphens. Keep the same ID when retrying an ambiguous start response;
a genuinely new attempt uses a new ID. Same ID/target/selection returns the same
operation; conflicting reuse returns `setup_request_conflict`. A second distinct
request while work is active returns `setup_in_progress`. At most 32 distinct
operations are accepted per controller process. Selecting a profile, scanning,
loading or reloading a page must never submit this request.

Before the Install action, show the selected profile/artifact/license disclosures
and bytes, plus one concise scope statement: setup may install required packages,
drivers and user services, reboot/reconnect, activate the selected environment and
download the selected artifacts. Associate the action with license eligibility and
terms without claiming to grant eligibility. Do not add replacement approval
checkboxes. The coordinator generates internal exact-digest approvals only for
this source-owned workflow; activation uses `policy: reject`, never job cancellation.

## Observe

`GET /setup/status` returns immediately from controller memory; it never polls SSH.
`GET /status` also includes this document under `setup`. While setup owns SSH,
general status uses the last observed SSH state rather than waiting on its IO lock.

Status fields:

- `state`: `idle`, `running`, `cancelling`, `completed`, `failed`, or `cancelled`.
- `busy`: worker still owns the target, including final cleanup. Wait for false
  before reconnecting/re-authenticating or starting a new attempt.
- `phase`: `planning`, `prerequisites`, `replanning`, `installing`, `rebooting`,
  `reconnecting`, `activating`, `downloading`, `completed` (or `idle`). A terminal
  failure retains the last actual phase.
- `operation_id`: opaque controller operation ID; null when idle.
- `request_id`, `profile`, `artifact_set_digest`, `plan_digest`: original selection
  and latest internally approved plan binding, present once started.
- `snapshot`: latest durable target state, or null before discovery. It contains
  `setups`, `operation`, `active_release`, `cursor`, `has_more`, and a rolling last
  100 real events. No timer-derived percentage. Show phases/indeterminate progress
  and available real event/download byte information; do not dump the raw plan.
- `installation_ready`, `model_ready`, `generation_tested`: independent facts.
  Completion requires a ready environment, matching active environment and durable
  selected-download completion. `generation_tested` remains false.
- `error`: safe fixed code, null otherwise. `blockers` contains source-owned
  prerequisite explanations when `error` is `setup_plan_blocked`.

Page reload can recover this in-memory status. Target disconnect/replacement
resets visible status to idle. Controller restart loses the browser session and
in-memory composite progress. A remembered connection authenticates after host-key
verification and automatically observes saved work; otherwise authenticate manually.
**Inspect saved setup** also recovers the original selection and durable diagnostics. A failed setup
is never implicitly resumed by Install: only backend-eligible unprivileged failures
expose **Retry failed stage**, preserving completed privileged work and caches.
See [diagnostics/retry contract](setup-diagnostics-api.md) for exact bindings and the
narrow audited predecessor recovery path across a launcher rebuild.

## Cancel and connection changes

`POST /setup/cancel {}` → HTTP 202. It immediately signals cancellation and drops
retained credentials. Already-started OS/package work is not killed unsafely, and
no unrelated job is cancelled. Further automatic actions stop. If this Install
owns a known remote download operation ID, it requests cancellation of exactly
that ID; transport loss can prevent delivery, so verified/partial files and an
already-detached remote operation may remain. Do not claim cancellation rolled
back installation or that offline remote work was killed.

While busy, low-level mutation/proxy routes and authentication reject with
`setup_in_progress` instead of waiting on a global operation lock. Explicit
`connect`, `disconnect` and `recovery/forget` also signal setup cancellation and
clear credentials, but return that conflict until the worker has released the
old target. Poll `busy`, then retry the requested connection action. This avoids
an old worker operating on a replacement connection.

## Errors and conservative recovery

- `setup_sudo_password_rejected`: “The SSH password was not accepted for sudo, or
  this account's sudo policy requires administrator attention.” This is a specific
  recovery error, not a reason to show a second normal password field. No automatic
  repeated password attempt occurs. Advanced low-level repair retains its explicit
  password/approval API; the composite endpoint has no alternate-password field.
- `setup_credential_required`: reconnect/authenticate with disclosed setup reuse.
- `selection_changed`, `recovery_binding_changed`, `matching_release_required`:
  stop; do not switch profile, artifacts, target or release silently.
- `host_key_changed`, `trust_required`, `authentication_failed`: preserve connection
  security handling; do not automatically approve a host key.
- `setup_remote_busy`: existing/interrupted target operation or changed operation
  ownership; no unrelated operation was cancelled.
- `setup_operation_outcome_unknown`: a mutation's result could not be proved.
  Observe/perform explicit advanced repair; do not repeatedly click Install.
- `setup_plan_blocked`, `update_prerequisite_maintenance_required`: administrator
  intervention is required; do not cancel jobs or bypass privilege policy.
  After repairing a blocked target, disconnect/reconnect/authenticate and submit
  a new explicit Install. A same-selection blocked plan is freshly inspected only
  when it has no real checkpoint, matching operation/active environment, or
  bootstrap launch/approval/status record. Even partial launch records prevent
  rediscovery, so a new digest cannot bypass an ambiguous privileged operation.
  A hardware scan alone does not refresh installation prerequisites.
- `setup_install_failed`, `setup_activation_failed`, `setup_operation_failed`,
  `setup_replan_stalled`, `setup_replan_limit`, `setup_reboot_not_observed`,
  `setup_timeout`, `setup_credential_check_failed`, `setup_failed`: stop with safe
  error text and available checkpoint progress, never claim readiness.

There are at most four prerequisite replans, one unprivileged resume per digest,
one reboot request per digest/operation (also guarded by the target's durable
boot record), bounded reconnect recovery, a 30-second launch-observation grace,
a 180-second unchanged-reboot limit and a 24-hour overall workflow budget.
`bootstrap/<digest>/composite-install.json` is an exclusive, private, credential-free
launch marker written before composite installation. Even a partial marker blocks
automatic replay across controller restarts; ambiguous privileged launches require
observation or explicit low-level repair. Existing low-level APIs do not silently
inherit the composite approvals and are not globally weakened.

## Local test boundary

`tests/test_setup_coordinator.py` exercises real authenticated/CSRF routes,
Deployment, staging and host-key/reconnect handling with controlled SSH command and
SFTP fixtures. Other focused tests cover the actual fixed sudo transport and
operation-ID-bound target cancellation. No real server, package install, reboot,
model download or GPU operation is used by these tests.


## Browser integration

The primary page removes step03 comparison and the old approval controls entirely.
Step04 renders the selected profile's bytes, GPU guidance, unknown memory fit and
auxiliary components; pinned license/provenance/technical details are collapsed.
`GET /profiles` and `/scan` now include verified `license_text` and `license_sha256`
for disclosure before Install, without staging or any target mutation.

Authentication sends `reuse_for_setup:true` and the explicit `remember_connection` choice next to the plaintext-storage disclosure.
Only Install sends `/setup/start`; an ambiguous request keeps its public ID in
page memory until reconciliation or a same-ID retry. No browser storage is used.
Selection and duplicate Install are disabled while setup owns the target. The page
polls `/status` for the setup document even during SSH recovery. Reload restores
that progress and exact selection without a plan/install/restore mutation.
Authentication automatically performs the read-only hardware scan (never Install).
Every disabled Install has an adjacent reason/action, including scan errors and
missing memory-only setup authorization. Sudo rejection has a specific policy/password
message, not a normal second field. Safe unprivileged retry does not require a new
sudo password; reconnect/authenticate is requested only when SSH is actually lost.

The primary progress view shows one human phase, active command/package/source,
controller-observed phase elapsed time and target-computed command elapsed/last-output
age. MODEL WEIGHTS takes precedence over the last dependency command. Measured model
file events remain separate; no percentage/ETA is inferred. Internal lifecycle and
checkpoint events are collapsed under Technical checkpoint details.

Live output is a replacement-friendly bounded sanitized tail with follow-at-bottom
scrolling, an expanded log, and JSON copy/download. Failures retain command label,
exit/error and output rather than replacing them with a generic recovery message.
Historical logs explicitly report unavailable command/timing metadata. The export
is not a full audit archive. **Inspect saved setup** is optional recovery, not a
new install approval wall: ordinary restore reads only saved state, and the narrow
compatible fallback may stage the trusted observer/payload but never starts work.
Unknown releases, changed contracts/units and active locks fail closed with reasons.

`tests/browser/generation_fixture.py` and `target_fixture.py` compose real controller,
installer, target authorization/activation and durable store logic while controlling
SSH, native process/service and model-download boundaries. Browser tests preserve
host verification/CSRF, low-level digest rejection and generation/video regressions.
Native smoke additionally checks that obsolete approval fields are absent and the
embedded application source/resources equal the current checkout. Browser diagnostics
regressions use real authenticated controller routes, installer/Recovery/store and
real local child output/nonzero exits at controlled process IO boundaries (no HTTP
fakes). They cover av==18.1.0 failure, finite fresh-index recovery, permanent stop,
explicit retry, reload/export, cancellation, unsafe receipt rejection and original
trusted predecessor recovery with a legacy log. They do not run pip, CUDA, codecs,
model downloads or real server installation, and do not establish the original
transient PyAV lookup cause.

## Studio workspaces

The sidebar separates **Install**, **Generate**, **History**, and **Video joiner**. On a fresh page, confirmed authenticated runtime/model readiness opens Generate automatically. Unknown/not-ready targets stay on Install. Explicit navigation or interaction with installation controls prevents a later automatic switch. Generate keeps the existing **Current** queue beside the form on wide screens (below it on mobile); History uses the same existing historical-job filter. Switching views does not submit work, reset your draft or clear the joiner. Existing Current/History selection-clearing and page-local membership rules are unchanged.

Install has one checklist, grouped into **Models** and **Optional components** (SageAttention, Turbo, sparse attention and Ref2VA). Installed components start checked. Check additions and uncheck removals, review the pending-change summary, then click **Apply changes** once. The ⓘ button on every card opens its download, compatibility, provenance and license details; there is no visible Review button or second selector.

The daemon performs supported additions before removals under one idle-only maintenance gate. Overall action progress and current download bytes are reported; closing the page does not stop the batch. Stop changes preserves completed work, and failures never silently roll back successful additions or delete the old model after a failed addition. Reopen Install to inspect progress; interrupted work requires explicit review and Apply again. Polling preserves edited selections; a changed target, daemon instance or inventory requires a fresh selection rather than silently removing newly discovered components.

Only pinned, owned component files and their private partial downloads are removed. Shared normalized weight caches, uploaded references, videos, history, runtimes and unrelated files are preserved while needed. The original base model and even all model weights can be removed without being resurrected on restart. SageAttention cannot be separately removed when it is a frozen base dependency.

**Complete model installation:** selecting a missing runtime provisions pinned sources, an isolated Python environment, frozen dependencies and native environment verification before downloading/verifying weights. GGUF and ConvRot coexist; generation dispatches to each verified interpreter without switching the daemon or upgrading an existing environment. Exact compatible families may reuse an environment. Sage packages/GPU receipts and sparse GPU receipts are environment-scoped; generation checks the selected profile's availability. Hardware-ineligible options (for example NVFP4 without positively identified Blackwell hardware) remain disabled.

The packaged controller stages its current verified release wheel for new runtimes without changing service/setup pointers. Fresh setup retains the full checklist: one base model bootstraps the guarded OS prerequisites and service, then the desired set is reconciled. A persistent pending selection survives interruption/reboot; recovery observes it and explicit Apply resumes remaining work rather than silently starting installation. Batch additions to an existing host never perform sudo/driver changes; if host prerequisites are missing or a reboot is required, they fail with a prerequisite explanation rather than mutating the host without authorization. Existing verified runtime environments are retained when model weights are removed.

Python controller changes require a new process; static asset replacement alone cannot update setup orchestration. Older live controllers may continue managing existing-server batches using an explicitly staged daemon release, but first-time multi-model setup requires the rebuilt controller. Preserve older processes until their private joiner work is exported.

Custom dimensions/frame counts and acceleration controls are expandable in Generate; all original settings remain available. Active acceleration choices appear in the disclosure heading. Canvas warnings, generation blockers, compact installation consent, progress and errors remain visible; full license details are in the per-component information dialogs. Model memory controls, timing exports and session diagnostics have dedicated expandable sections. Keyboard timeline controls and lossless joining are unchanged.

## Local or SSH installation target

Use **Installation target** to choose **This controller machine (Local)** or **Remote machine (SSH)**. Disconnect the current target before changing modes. For Local, click **Use this machine and scan**; no SSH service, host-key approval or SSH password is needed. Local refers to the computer running the controller, not the device viewing its page over LAN.

macOS reports real OS/CPU/RAM/disk observations and clearly blocks installation. On supported Linux/NVIDIA systems, Local uses the same pinned installer, approvals, maintenance guards and model/history controls, with local processes/files and authenticated loopback daemon access instead of SSH. Files remain under the current OS account's `~/MinimaxH3Server`, except explicitly approved OS packages/service registration. Already-installed daemon control and scanning need no sudo password.

For a new Linux installation, use **Authorize local installation** to check your sudo password. It stays in memory, is not saved as an SSH connection, and does not itself install anything. Then check a base model and click **Set up selected components**. Existing SSH preferences remain available for switching back. Unsupported platforms cannot use local installation authorization.

Run the controller as your normal user, not with sudo. A local-machine reboot also stops the controller: restart it, select Local and inspect the saved setup; guarded recovery still decides what can resume. Build the native Linux executable with `python scripts/build_controller.py` in the documented build environment when moving to Linux—do not copy the macOS executable. Windows currently supports SSH targets only.

Local target mode can be combined with `--lan-ip`: a browser on another device can control the Linux controller's own machine without SSH. All browsers on that controller share target/connection state and retain the existing full-control access-key security model.

## Trusted LAN access (optional)

Localhost remains the default. For direct access from other devices on your trusted LAN:

```sh
./dist/h3-controller --lan-ip 192.168.1.61 --port 8787
```

Replace the IP with an RFC1918 IPv4 address assigned to the controller computer. Open `http://192.168.1.61:8787` on the other device. No SSH tunnel or macOS Remote Login is needed for browser access. The existing controller-to-GPU-server SSH connection is unchanged.

The launcher prints the path of the owner-only `lan-access.txt` file in its state directory (on macOS, `~/Library/Application Support/H3Server/`). Enter that file's access key once per browser. The key and remembered browser access survive restarts and have no application timeout. An opaque browser grant restores cookies automatically if the browser drops them; neither the actual access key nor SSH passwords are stored in browser storage. Clearing browser data requires entering the key again. Start with `--reset-access-key` to deliberately replace the key and revoke previous browser grants. The key grants **full control**, including saved-server access; browsers share controller connection/workspace state, not separate user accounts.

**HTTP is unencrypted**, including login/session data and anything entered into the page. Use only on a trusted LAN; never configure router port forwarding. If macOS asks, allow incoming connections for this executable. Guest Wi-Fi isolation or firewall rules can still block another device. Only the supplied private IPv4 interface is bound; exact Host/Origin checks, cookies, CSRF and private-peer checks remain enabled. Forwarded headers are not trusted. Public IPs, wildcard binding and arbitrary hostnames are refused.

Start another controller rather than stopping one holding joiner work. To disable sharing, stop only that LAN controller. Stopping does not revoke the saved key: it will still work at the next launch. Use `--reset-access-key` when revocation is intended; do not delete private credential state merely to stop sharing.

## Installing another model

Select another profile and click **Install**. Compatible profiles (for example ConvRot INT8 and mixed INT4/INT8) reuse the verified runtime instead of entering failed-setup recovery. The progress bar counts downloaded, verified and reused bytes. Matching pinned encoder/VAE files are verified and privately reflinked or copied; originals are never hardlinked, removed or overwritten. Mixed currently shares about 20 GB of its roughly 35.9 GB snapshot with INT8.

Installation requires an idle queue and explicit approval. **Cancel model download** retains resumable partials and existing models; a running bounded runtime diagnostic may finish before cancellation takes effect. Restarted interrupted downloads require another explicit Install. Once complete, both compatible models appear in **Generation profile**. Switching profiles never starts generation; resident processes recycle safely when a queued job actually needs another model. Ref2VA and sparse attention still require INT8.

Different runtime families use the normal full setup after a completed-active-checkpoint ownership check. Existing weights and environments stay on disk. The generation selector lists models compatible with the active runtime, not every inactive environment. Failed or ambiguous setup checkpoints still require their original guarded recovery; they are not bypassed.
