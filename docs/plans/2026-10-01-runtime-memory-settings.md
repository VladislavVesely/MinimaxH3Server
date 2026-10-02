# Server-wide Runtime Memory Settings

## Approved design

A fifth Settings workspace in the sidebar configures GPU headroom (Auto or explicit GiB), automatic/low-VRAM offloading, and expandable CUDA allocations (`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`). Persist settings on the generation server and share them across clients. Keep defaults unchanged until explicit Apply. Applying requires an idle queue and no maintenance; release resident models and apply settings to the next GPU child before PyTorch imports. Never cancel work, alter job geometry or silently retry failed generations.

## Architecture and tasks

1. Validated schema-1 `config/runtime-memory.json`, owner-only atomic storage, defaults and canonical revision digest. GET/POST authenticated daemon routes with optimistic revision checks. Apply and job submit/claim share Worker._lock, checking queued/running/active work and maintenance before write/unload. Invalid/corrupt state fails closed.
2. Whitelist-only child environment carries normalized policy and the requested allocator flag. Pruned runtime configures pinned ComfyUI flags before model-management import. Investigated the actual pinned source: `args.lowvram` selects LOW_VRAM, `args.reserve_vram` replaces EXTRA_RESERVED_VRAM in GiB; minimum inference memory adds another 0.8 GiB. Do not promise headroom is a hard allocation cap or an OOM guarantee. Distinguish timing measurements when policy changes; record effective policy in job events.
3. Exact allowlisted controller proxy and typed request body. Settings workspace has load/refresh, Auto/manual headroom, bounded number field, mode select, allocator checkbox, explicit idle-only Apply, defaults draft, truthful speed/RAM tradeoff and stale-state errors. No arbitrary environment editor. Preserve drafts during unrelated polling. Make target changes invalidate old state and responses; no auto-apply.
4. Test defaults/persistence/validation/CAS/busy and maintenance gates, real resident release, startup environment/flags, timing isolation, API authorization/Host/Origin/CSRF, and browser selection/apply/reload. Verify native embedded package/assets. Deploy matching server/controller on 8795 only when idle, retain all model/add-on selections and existing key/browser trust. Do not launch real generation to benchmark without explicit consent.

## Implementation and targeted verification

Implemented policy validation/storage, authenticated routes, target-bound controller forwarding, idle-only Apply, runtime flags and allocator environment, policy-isolated timing estimates, and the fifth Settings workspace. Target identity is checked under the SSH connection lock; cross-client polling preserves drafts and blocks stale saves. CPU-only fixtures never submit generation jobs.

117 focused Python tests passed. Chrome and WebKit layout tests passed, including five workspace links and a wrapping mobile navigation layout. Eleven memory/access/character browser tests passed (the optional real-media case was skipped in that run). Settings persistence, explicit-only mutations, cross-client stale edits and late old-target responses are covered. Visual previews: `/tmp/h3-memory-settings.png`, `/tmp/h3-memory-settings-mobile.png`.

The initial broader Python run reproduced the already-known concurrent maintenance-drain failure (`test_maintenance_serializes_concurrent_submit_and_drains_queued_work`); a second run will exclude this exact baseline plus the CLI wording baseline, rather than excluding the unrelated passing drain test.

Unrelated `video_prompt.txt` on main is user-owned and must remain untouched. Existing CLI port wording, maintenance-drain timeout and three browser baseline failures remain out of scope.
