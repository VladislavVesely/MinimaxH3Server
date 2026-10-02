# Server-persistent generation favorites

## Approved design

Top-right outline/filled gold star on generation cards in both Current and History.
Favorites use a subtle gold card background/border and accessible pressed-state
labels. Store on the generation server, shared across clients and restarts. Do not
reorder cards, change job inputs/lifecycle/playback or prevent explicit deletion.
No additional favorite filter/sorting requested.

## Implementation

Add strict boolean `Job.favorite`, default false for existing rows. Persist true
as additive `_favorite` job metadata alongside the existing `_timing` metadata,
not inside executable `JobRequest`. SQLite write transactions serialize favorite
and lifecycle updates; lifecycle transitions preserve the marker. Explicit false
removes it, and deletion removes the row normally. Older releases which predate
this metadata cannot decode marked rows: clear favorites before such a rollback.
No table/schema rebuild or rewriting of untouched historical jobs is needed.

Authenticated strict-boolean POST `/api/v1/jobs/{job_id}/favorite` sets a desired
state (not a server-side toggle), returning ID/state; GET list/detail include the
boolean. Controller forwards only this exact allowlisted route with existing
Host/Origin/session/CSRF defenses. Unknown jobs return 404, malformed inputs 422.

UI marks only confirmed saves, disables pending operations, retains stable star
text nodes during unchanged polling, and supersedes pre-save dashboard reads.
Existing job refresh shares changes across clients; no new timer. Failed saves do
not falsely unmark cards. No autoplay or changes to card grouping/selection.

## Verification plan

Test legacy defaults, strict values, restart persistence, concurrent lifecycle
writes, metadata/output integrity, unchanged ordering, deletion, authenticated API
and controller CSRF/ID validation. Browser test covers Current → completion →
History after reload, outline/filled styling, unmark/re-mark, failed saves, and
mobile overflow. Preserve Settings event-driven behavior, persistent credentials,
installed components, GPU memory policy, and unrelated `video_prompt.txt`.

Deploy matching verified controller/server only with idle/reject-busy activation.
Retain all six current components; rebind environment-scoped SageAttention/sparse
if a code update creates a new environment. Do not launch a real GPU generation
or cancel jobs. Native embedded-source/security/replacement smoke required.
