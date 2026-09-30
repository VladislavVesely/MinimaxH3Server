# Resident models and timing estimates

**Goal:** Retain reusable diffusion/text-encoder/VAE components between jobs where memory permits; provide explicit idle-only unload and hardware-bound timing records/estimates.

**Architecture:** Keep native inference in an owned child process, but accept multiple bounded, job-ID-bound requests over private pipes. No network listener, arbitrary workflow, or external commands. Preserve daemon ownership inheritance, cancellation/deadlines, immutable output validation, queue/maintenance serialization. Model management may offload CPU/GPU tensors; retaining model objects is not a promise of permanent VRAM residency. Native errors, forced cancellation and shutdown may require process recycling. Settings changes must use fresh/reversible prepared variants, never stack adapters.

**Tech stack:** Existing Python/ComfyUI/Diffusers workers, SQLite, FastAPI/SSH controller and vanilla JS. Direct main-branch implementation; no subagents, worktrees, pushes or full suites.

## 1. Persistent protocol and component cache
- Add focused failing tests in `tests/test_resident.py` for two requests/one process, job-bound completion, idle unload, busy rejection and fault cleanup.
- Add `backends/resident.py`; extend `remote/worker.py` with opt-in resident backends, bounded result frames and strict lifecycle handling. Existing injected one-shot backends remain unchanged.
- Update `backends/pruned.py` to retain runtime, base model, encoder and VAEs; reuse identical prepared variants and rebuild changed adapters from base model state using pinned Comfy patcher semantics. Attention routing must remain per-request. Fix sparse tracing so wrappers never accumulate.
- Keep profile/artifact identity binding and verify private file signatures before warm reuse; invalidate rather than silently reuse changed assets.
- Keep the experimental legacy `backends/h3.py` isolated until separate residency acceptance; advertise that restriction explicitly rather than enabling unverified persistent Diffusers state.
- Run focused tests (not entire suite). Under RAM pressure, retain the encoder while releasing/reloading other components if decoder eviction alone is insufficient; distinguish this in measurements.

## 2. Measurements and durable records
- Add `performance.py` with separate private SQLite schema, one successful sample per job, public configuration/hardware/software identity, stage and individual sampling-step measurements, warm/cold separation, mean/sample deviation/count.
- CPU/GPU stage boundaries use synchronized monotonic timing. Record only actually observed phases; failed/cancelled/incomplete work does not contribute to successful averages.
- No prompts, credentials, hostname, GPU UUID/serial, paths or machine IDs in timing export. Include numeric prompt-length bucket, selected/actual attention, model/artifact versions and runtime source identity.
- Estimates require a matching measured hardware/software/configuration/cache group, expose sample count/observed spread, and never treat queue delay as generation time. No fabricated historic timings or automatic uploads.

## 3. API and controller
- Authenticated GET models/status; POST models/unload (409 for running/queued/maintenance work); GET paginated performance samples; POST performance/estimate. Exact controller proxy allowlist and CSRF on mutations.
- Show resident component status, unload action and clear disabled reason. Show measured estimates and sample count, with explicit unavailable state. Running jobs show elapsed versus expected; overruns remain overruns, not fake countdowns.

## 4. Real acceptance and packaging
- Rebuild native launcher and check controls, persistence and mobile layout without changing saved choices or automatically submitting jobs.
- Idle/hash-guarded deployment only. Verify consecutive real generations, actual retained versus memory-reloaded components, explicit unload, sample persistence, and cold/reuse-mode estimate separation. Exercise attention/LoRA changes before claiming compatibility.
- Preserve existing histories, models, packages and feature preferences. Record actual evidence, remaining limits and final commit.

## Outcome

Implemented and validated on the existing RTX 4090 / ~30 GiB RAM target. Sparse
plans rebuild per request and old object patches are restored before cloning.
Memory-aware retention distinguishes full reuse, decoder reload and encoder-only
reuse; changed Turbo variants can require reclaimable base-weight mappings.
146 focused Python tests plus selected transport/setup and JS/native checks pass.
Real repeated and option-switching runs, independent decoding, API/UI unload,
matching estimates and private exports are documented in [validation](../validation.md).
Legacy Diffusers residency and other hardware/full-resolution resident workloads
remain unverified. No full suite, subagents, worktrees or push were used.
