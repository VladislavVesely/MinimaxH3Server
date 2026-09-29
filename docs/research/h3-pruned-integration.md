# Pruned H3 controller integration

Both requested Abiray collections are now exposed through the local controller.
This is an implementation/controlled-integration record, **not real H3 inference
certification**. Main worktree and its executable remain untouched.

## Public presentation and approval contract

`GET /api/v1/controller/profiles` is authenticated, local-only catalog IO (no SSH,
network fetch, model consent or installation). It returns:

- `profiles`: five available pruned selections first, then explicit legacy choices.
  `profile_name`, human `label`, `runtime_id`, exact `artifact_set_digest`, selected
  `artifacts` (role, repository, immutable revision, path, size, hash, local path,
  URL), `weight_bytes`, `tokenizer_bytes`, `selected_bytes`, `runtime_bytes`,
  `peak_weight_disk_bytes`, runtime source pins, publisher GPU guidance, memory and
  validation status, required GPU family and license disclosures come from the
  immutable catalog. Legacy summaries have null artifact-set digest and retain
  the original snapshot total, never disguised as pruned weights.
- `gpu_eligible` is tri-state for restricted profiles; without hardware it is
  unknown, not a license to select NVFP4. The scan response includes the same
  catalog with current hardware eligibility plus all seven recommendations.
- `unavailable`: the absent pure INT4 diffusion variant and inventory explanation;
  no synthetic model URL or download option.

`Deployment.document()` returns `selection` for **plan.profile**, alongside the
exact plan/service digests and complete pinned license. The UI shows diffusion,
shared Winnougan INT4 encoder and both Abiray VAEs with individual and aggregate
bytes, repositories/revisions, expandable URLs/hashes and artifact-set digest.
It distinguishes bundled tokenizer bytes (counted once), unknown package/runtime
bytes and additional disk overhead. It presents publisher guidance separately
from unknown host RAM/peak VRAM. No 96/192 GiB legacy RAM requirement is attached
to pruned profiles. License acknowledgement explicitly includes the auxiliary
provenance disclosures; these do not remove upstream MiniMax/Qwen obligations.

`ActionRequest.artifact_set_digest` is an optional strict 64-hex field for legacy
compatibility. Pruned downloads submit the exact displayed selection digest, plan
digest and separate license/eligibility/download booleans. Existing target code
binds these to the approved plan profile, then checks catalog consent before
selected acquisition. UI changes discard plan approvals and require fresh review;
restore renders the saved selection rather than the default. Staging/setup still
does not authorize weight downloads.

## Selection and generation

GGUF Q4_K_M is the default smaller experimental choice (31,550,953,118 weight
bytes), including for RTX 4090/29 GiB. It is **not a measured fit guarantee**.
Q5_K_M, mixed INT4/INT8 and INT8 are available; NVFP4 is disabled without positive
Blackwell identification. The conservative current detector recognizes RTX 5090
and rejects RTX 4090; other names remain unknown. The backend independently
rechecks eligibility, regardless of UI state.

Generation selector is populated only from active daemon capabilities, never
from setup selection or a hard-coded legacy list. It shows the active runtime
and profiles, sets its advertised default (20 pruned / 50 legacy) when the active
selection changes, preserves user steps on later dashboard polling and submits
the exact active profile. Pruned runtimes advertise only their selected profile,
with no LoRA/sparse control or hidden fallback. Readiness and completed output
remain separate milestones. Historical completed jobs for another profile cannot
mark the active pruned profile generation-tested. Browser output tests use explicitly labelled synthetic
fixture bytes, not real H3 video.

## Build and verification

Run from this isolated worktree:

```sh
.venv/bin/python -m pytest -q -W error
(cd tests/browser && npm ci --offline && npm test)
.venv/bin/python -m pip check
.venv/bin/python scripts/build_controller.py
.venv/bin/python scripts/smoke_controller.py dist/h3-controller
git diff --check
```

Build uses installed setuptools/build (`--no-isolation`), not an implicit build
environment download. It verifies required catalog/pruned/comfy-runtime modules
and byte-for-byte equality of every packaged application Python/resource file
with source. Native smoke verifies embedded wheel digest, matching web/manifest/
license resources, full wheel/source equality and exact authenticated native
catalog equality (including both repositories, totals and selection digests).
It launches the real loopback executable without Python/Node on PATH, tests
Host/Origin/session/CSRF and clean shutdown for ordinary and `deploy` entrypoints.
No target is contacted. Native output is ignored build material in this
worktree's `dist/`, not installed over main or pushed to a release channel.

Coverage added: authenticated catalog serialization; all five exact plan-selection
bindings; strict digest forwarding; 4090/5090/unknown eligibility; wheel source
corruption rejection; browser paths through both collections, controlled install,
real target consent rejection/acceptance with downloader detach disabled, restore,
active-profile capabilities/generation, polling preserves explicit steps, historical
cross-profile output is not inference evidence, and approval invalidation. Existing browser
security/recovery, responsive layouts and legacy generation remain covered.

Pinned source/artifact evidence remains in [h3-pruned-runtime.md](h3-pruned-runtime.md)
and [h3-pruned-operation.md](h3-pruned-operation.md); this integration does not
change catalog pins, runtime composition or installer architecture. Exact final
commands/counts, executable hashes and scoped commit are in the Task 4 handoff.

## Remaining acceptance

Real Linux CUDA install/driver compatibility, full quantized loading/sampling,
quality, latency, host RAM/VRAM peaks and bounded native cancellation require
separately approved target/hardware acceptance. Prior Task 3 native CPU import
and synthetic H264/AAC smoke are not model inference. No credentials/server.txt,
real target, privileged operations, weights or GPU inference were used here.
Independent review remains required before parent-owned integration/release.
