# Bulk deletion from Current / History

Use page-local checkboxes and a toolbar: Select all in this tab, Clear selection,
and Delete selected (count). Only completed/failed/cancelled jobs are eligible;
queued/running jobs must be explicitly cancelled and fully stopped first.
Selections survive polling but are cleared on tab/connection changes and pruned
when a job disappears. Tabs are disabled during deletion.

One confirmation describes irreversible job/video/log/timing/unshared-reference
removal. Keep the existing authenticated, CSRF-protected per-job delete API and
worker/SQLite/asset locking, rather than introducing another bulk mutation API.
Requests execute sequentially against a confirmed snapshot; there is no implied
all-or-nothing filesystem/database transaction. Successful responses clean draft
asset references and timing estimates, with one final dashboard refresh. Failed
eligible items remain selected. Connection/session failures stop further attempts;
remaining selected jobs can be retried explicitly. No automatic cancellation,
retry, generation, or deletion of local joiner copies.

Verification: focused JS state/partial failure/retry/network-stop checks plus
existing job/UI regressions, 41 Python deletion/security tests, packaged native
Chrome and WebKit interception-based acceptance for both tabs, polling, selection,
confirmation cancellation, partial failure/retry and mobile layout. Every native
mutation is intercepted; no actual user job is deleted. No server deployment.
