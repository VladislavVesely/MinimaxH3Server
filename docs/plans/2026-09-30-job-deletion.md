# Explicit job deletion

User-approved policy: cancel first; no implicit cancellation. Add confirmed Delete
in both Current and History. Queued/running buttons are disabled with an explanation,
and the daemon rejects active work even after its cancellation status is published.

Delete the job directory, private request/output record, event contents, timing
sample and references not used by any other durable job. Preserve shared assets,
models, preferences and unrelated jobs. Reference IDs are SHA-addressed and shared.
Draft-only attachments are not durable job references; a deleted attachment must be
re-uploaded in another browser draft. The initiating page removes deleted draft IDs.

Implementation: authenticated POST jobs/{id}/delete through the CSRF-protected SSH
proxy; worker lock and assets flock; SQLite write transaction for the reference
snapshot and final record removal. Validate new submissions under the same worker
lock. Descriptor-relative no-follow cleanup, never path-based recursive fallback.
Keep the job row until cleanup finishes: partial deletion is retryable, missing files
are tolerated, and a repeated successful delete is idempotent. Replace log contents
with anonymous tombstones to preserve the global contiguous SSE replay sequence.
Recompute affected timing averages after removing the selected sample.

This is application-level deletion, not secure forensic erasure or deletion of
external downloads/backups. Already-open file descriptors may finish serving bytes.

Validation: focused red/green tests for shared refs, cancellation and active process
barriers, safe symlink handling, retry/reopen/replay, timing averages, API auth and
controller CSRF. Native UI checks use controlled fixtures, not user history. Deploy
only under idle, hash-guarded maintenance; rebuild/reopen the packaged controller.
