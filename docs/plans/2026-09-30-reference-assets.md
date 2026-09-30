# Reference Assets Implementation Plan

> **REQUIRED SUB-SKILL:** Use executing-plans for task-by-task execution; current operator requests direct work on main, no delegation/worktrees/full-suite runs.

**Goal:** Secure native reference image/video/audio and first/last-frame generation, retaining source assets with job history.

**Architecture:** Immutable content-addressed owner-only uploads over the authenticated controller/SSH API; strict normalized settings contain asset IDs, never paths or caller URLs. Pinned Comfy nodes handle FL2VA and Ref2VA separately. An explicitly installed, digest-bound Ref2VA checkpoint complements existing FL2VA weights; caches switch diffusion weights rather than keeping two huge models resident.

**Tech Stack:** Python/FastAPI/Paramiko, bounded PyAV/Pillow decode subprocess, Torch in the isolated runtime only, vanilla browser UI, existing optional-component downloader and maintenance policy.

## Accepted design / evidence

User approved implementing both modes and downloading the extra ~21 GB checkpoint for real GPU acceptance. Pinned Comfy nodes expose MiniMaxH3ImageToVideo(first_frame,last_frame) and MiniMaxH3ReferenceToVideo(ref_images,ref_videos,ref_video_audios,ref_audios). Use native image/video/audio tags and stable per-type ordering. Video soundtracks are omitted explicitly in the initial reference UI: users may upload standalone audio, keeping Audio tags unambiguous. Decode videos to 24fps, disclose temporal alignment/truncation; no unbounded codecs or user workflow execution.

Ref2VA INT8: repository Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot, revision 908eccad7e68751190d04c171956f163bfeed741, MiniMax_H3_Ref2VA_pruned_int8_convrot.safetensors, 20,970,379,689 bytes, SHA256 ed7e9aa541b945f44298650f0dc2e2e867bfd3b67cd07bcc90f6081c168f0d3d. Preserve original bytes; any necessary packing repair must be exact-hash pinned after independent inspection. Shared encoder/VAEs are reused.

## Task 1 — contract and model acquisition
- Tests: tests/test_reference_assets.py (strict modes/IDs/counts/combinations, pinned source and integrity).
- Modify domain.py, remote/app.py; create backends/reference.py and remote/reference.py; extend _download_transport.py allowlist explicitly.
- Settings default to text for historical requests. First/last frames need at least one image. References need at least one image/video/audio. Unsupported legacy/profile/adapter combinations fail explicitly; no silent fallback to FL2VA.
- Ref2VA initially supported for ConvRot INT8; FL2VA images supported by pruned profiles. Optional model download requires approved digest, idle maintenance and existing profile consent; restoration never downloads.
- Run the new tests red, implement, run green with selected existing API/transport checks.

## Task 2 — secure assets
- Create assets.py: content-addressed immutable media, no-follow owner-only files, bounded bytes/duration/decoded pixels, validated media types, timeout-isolated probing. Retain originals for history; removing a draft attachment never removes history files.
- Authenticated raw upload and ranged read endpoints in remote/app.py and controller/app.py; proxy allowlist only logical hex IDs, unchanged CSRF/origin/SSH validation. Bounded uploads before parsing. No arbitrary remote URL/path inputs.
- Add tests for truncation/type confusion/invalid bytes/oversize, forged IDs, symlink escapes, history reuse, unauthorized uploads, proxy routes and range reads.

## Task 3 — runtime and timings
- Modify backends/pruned.py and resident.py as necessary: decode managed assets, provide both VAEs for reference conditioning, release incompatible cached diffusion variants, preserve encoder safely, load exact Ref2VA model only for reference mode, validate decoded outputs.
- Performance configuration removes content IDs; separate modes and resolved conditioning geometry prevent mixing text and conditioned measurements. Runtime success does not assert identity fidelity.
- Tests: mock node IO for all three kinds and each frame position, mode switches, missing assets/checkpoint, privacy and statistical grouping.

## Task 4 — controller UX
- Modify web/app.js/index.html/styles.css: Text / References / First+last modes, uploads/previews/remove, numbered tags, model availability/install action, genuine blockers, draft selection independent of preferences. Preserve unrelated generation controls and existing queue/history.
- Saved inputs show mode and retained attachment links. History can preview uploaded originals. No automatic inference, installation or credential exposure.
- JS focused tests and native desktop/mobile browser acceptance; deduplicated estimates must remain stable under polling.

## Task 5 — deployment and GPU acceptance
- Hash-guarded source deployment under idle maintenance; explicit approved model acquisition; no dependency/model replacement or active-job interruption.
- Small existing-weight first/last-frame generation and Ref2VA image/video/audio requests using owned synthetic fixtures. Independently decode outputs and inspect actual conditioning evidence, mode switches and retained history. Document memory or compatibility blockers honestly.
- Run focused Python/JS tests only, rebuild native launcher, validate real browser, record evidence and commit on main without push.
