# API v1

The controller and remote daemon are **different security boundaries**. Never
publish either listener. There is no hosted inference endpoint. A future Pi
extension should use these versioned contracts through verified SSH/session auth;
no Pi integration or bypass token transport is shipped yet. Generated OpenAPI is
authenticated; application import alone neither starts a listener nor loads weights.

## Local controller: `/api/v1/controller`

Launcher binds `http://127.0.0.1:<reserved-port>`. Exact Host/Origin checks, no CORS,
no WebSockets, strict request bodies and a 16 KiB limit apply. Public resources are
only `/` and the two allowlisted `/assets/app.js`, `/assets/styles.css` files.
Browser launch uses `/#token=...`: the UI removes the fragment before POSTing
`{"token":"..."}` to `/bootstrap` with its exact Origin. It is single-use, expires
after 120s, and returns an HttpOnly SameSite=Strict `h3_session` cookie plus
`csrf_token`. Never put tokens/passwords in query strings. Cookies are local HTTP,
not transport-encrypted remote credentials. Default sessions have no server deadline
and survive controller shutdown/restart using owner-only private access state. Keep
the application restricted to loopback or explicitly enabled trusted LAN mode.
Login/bootstrap/status also return an opaque `remember_token`, which the UI stores
in localStorage (never the access key or SSH password). `POST /session/restore`
with `{token}` and the exact Origin restores a missing cookie without re-entering
the key. Cookies are HttpOnly/SameSite=Strict, refreshed on authenticated successful
status responses; the remembered grant handles browsers' own cookie lifetime limits.
`--reset-access-key` explicitly rotates the key and revokes old cookies/grants.

Apart from bootstrap, LAN login and browser restoration, other endpoints require that cookie. Unsafe methods additionally require exact
`Origin`, `X-CSRF-Token` and no query string. Fetch `/status` to recover the CSRF
value in an already authenticated session. The controller's errors are
`{"error":{"code":"stable_code"}}`; validation does not reflect secrets. Common
statuses: 401 `session_required`/`bootstrap_invalid`, 403 `host_forbidden`/
`origin_forbidden`/`csrf_forbidden`, 413 `body_too_large`, 422 `invalid_request`,
409 state/approval conflicts and 503 `state_unavailable`. Errors/logs intentionally
suppress arbitrary Paramiko/remote exception text.

| Method / suffix | Purpose |
|---|---|
| GET `/status`, `/fingerprint` | SSH state, challenge fingerprint; status includes capabilities and release availability |
| POST `/connect` | `{host, port:22, username}`; handshake only, no auth |
| POST `/approve` | `{fingerprint}`; exact pending SHA256 challenge, expires in 120s |
| POST `/authenticate` | `{password, reuse_for_setup:false, retain_for_setup_recovery:false}` after host-key verification; setup reuse must be explicitly disclosed |
| POST `/disconnect` | `{}`; cancel/join recovery, clear retained credentials and close SSH |
| POST `/recovery/forget` | `{}`; opt out, clear retained SSH credentials and cancel/join recovery |
| POST `/scan` | `{}`; real read-only probes and experimental profile recommendations |
| GET `/profiles` | Catalog selection/digests, verified full `license_text` and `license_sha256`; no target IO |
| POST `/setup/start` | `{profile, artifact_set_digest, request_id}`; HTTP 202, single disclosed Install intent |
| GET `/setup/status` | Nonblocking composite phases, safe error, rolling real events/checkpoints and independent readiness |
| POST `/setup/cancel` | `{}`; HTTP 202, clear credential, stop subsequent automatic actions; not rollback |
| POST `/setup/plan` | `{profile, sudo_password_available:false}`; explicit release staging and plan inspection |
| POST `/setup/install` | Matching `plan_digest`, `service_digest`; explicit `license_acknowledged`, `eligibility_confirmed`, `privileged`; optional request-only `sudo_password` |
| POST `/setup/restore` | `{}`; reauthenticate first, restore durable plan/state |
| GET `/setup/events?after=N` | Exclusive setup event cursor, snapshots, retention resets, operation/activation state |
| POST `/setup/reboot` | Matching plan approval, `reboot:true`, separate privileged/eligibility/license approvals; only valid waiting-for-reboot checkpoint |
| POST `/setup/activate` | `plan_digest`, `activation_approved:true`, `policy:reject\|drain\|cancel`; cancel also requires `cancel_confirmed:true` |
| POST `/setup/download` | `plan_digest`, `license_sha256`, `license_acknowledged:true`, `eligibility_confirmed:true`, `download_approved:true` |
| POST `/setup/cancel-download` | `plan_digest`; cooperative cancellation, not assumed immediate completion |
| POST `/setup/resume` | `plan_digest`; existing approved environment operation (prerequisites require replan) |
| POST `/setup/repair` | `plan_digest`, `rollback_approved:true`; repair unresolved activation journal |
| GET `/remote/health`, `/remote/capabilities`, `/remote/jobs` | Allowlisted SSH proxy; job list currently bounded to 1000 |
| POST `/remote/jobs` | Forward strict remote generation schema below |
| POST `/remote/jobs/{id}/cancel` | `{}`; cancel actual job |
| GET `/remote/events?after=N` | Finite remote SSE replay page; UI polls for subsequent pages |
| GET `/remote/jobs/{id}/video[?download=true]` | Preview/download; allowlisted Range header forwarded |
| GET `/openapi.json` | Actual request schemas; same session auth |

`/status` includes `setup` and safe `recovery: {state, error, cycle, credential_retained, setup_credential_retained}`.
The primary UI uses [the composite controller contract](one-click-controller.md),
not the separate low-level approval sequence. While composite setup is busy,
mutations reject with `setup_in_progress`; connect/disconnect/forget also signal
cancellation. Retry the connection action only after `busy:false`.

The underlying reconnect observer remains bounded and observation-only:
States are `idle`, `connecting`, `backoff`, `recovered`, `failed`; `cycle` changes
for each recovery attempt so clients can replay once after a rapid recovery.
Recovery runs for at most 120 seconds including restore; it observes the original
binding and never sends install/resume/reboot again. During recovery, other SSH
operations return `409 recovery_in_progress`; explicit connect/authenticate/
disconnect and opt-out cancel/join the worker. Terminal trust/authentication,
timeout or binding failures clear the credential and require manual intervention.
`setup/reboot` returns `reauthentication_required` plus safe `recovery` status.

Booleans default false where consent is involved. The UI discloses memory-only
reuse inline at authentication and sends `reuse_for_setup:true`; there is no
retention checkbox or second normal sudo field. A plan digest binds approval to
exact changes. The composite coordinator supplies fresh exact-digest approvals
only within the original selected Install intent; low-level callers must still
supply each route’s explicit approvals. `installer` or
`generation` capability means that the controller has the path implemented, not
that a target runtime/model is ready. The controller reads `config/remote-token`
over SFTP and injects it server-side; browser code never receives the remote token.

## Remote daemon: `/api/v1`

Binds only `127.0.0.1:8765` by packaged default. Every request, including health,
SSE, MP4 and `/openapi.json`, needs `Authorization: Bearer <private-token>`. Origin
headers are rejected: this is an SSH-transport API, not a browser CORS endpoint.
Read the 0600 owner-only `config/remote-token` only on the target or through verified
SFTP. Never pass it as a CLI argument, URL/query string or log it. Do not use shell
examples that expose a literal bearer token to process listings. The controller
proxy is the recommended client transport.

| Method / path | Contract |
|---|---|
| GET `/api/v1/health` | Service health separate from `runtime_ready`, reasons, diagnostics; `generation_tested:false` is not a smoke result |
| GET `/api/v1/capabilities` | Selected profiles, source support vs live adapter capabilities, fps/geometry/queue/storage limits |
| POST `/api/v1/jobs` | Durable accepted job + events before 202 response |
| GET `/api/v1/jobs?limit=100&offset=0` | `{jobs, next_offset}`; limit 1..1000 |
| GET `/api/v1/jobs/{id}` | Request/settings, state, effective aligned duration, output record |
| POST `/api/v1/jobs/{id}/cancel` | Cancel queue entry or isolated worker group; remove partial output |
| GET `/api/v1/events?after=0&follow=true` | Replayable SSE; `Last-Event-ID` takes precedence |
| GET `/api/v1/jobs/{id}/video?download=false` | MP4 inline/attachment; supports single byte Range (206), invalid/unsatisfied range 416 |
| GET `/api/v1/maintenance` | Durable deployment-owned gate and pending/idle state |
| POST `/api/v1/maintenance/begin` | `{operation:<32 lowercase hex>, policy:reject\|drain\|cancel, cancel_confirmed:false}` |
| POST `/api/v1/maintenance/end` | Matching `operation`; cannot release another deployment's gate |
| GET `/openapi.json` | Generated bearer-authenticated schema |

Example generation body (send through an authenticated client, not a public URL):

```json
{
  "prompt": "A red paper boat floating on a calm pond",
  "num_frames": 124,
  "width": 960,
  "height": 544,
  "steps": 50,
  "seed": 42,
  "profile": "h3-int8-group-offload-experimental",
  "loras": []
}
```

Use `num_frames` **or** `duration`, not both. Alignment rounds up to `17*n+5`,
124..345 frames at 24 fps; nominal 15 seconds rounds too far and is rejected.
Width/height must be multiples of 32 within schema limits and the configured
canvas-area budget. `steps` is sigma-grid points; measured denoising calls are one
less. Seed does not guarantee cross-hardware reproducibility. Unknown fields,
negative prompts/guidance, reference inputs and any supplied `sparse_attention`
(including false) are rejected, never silently ignored. LoRAs are admitted logical
identifiers, not filesystem/URL inputs; unsupported formats/options fail validation.
Consult live capability/readiness before submitting.

### Server-wide runtime memory policy

Authenticated `GET /api/v1/settings/memory` returns `settings`, canonical `revision`,
`supported`, `busy`, `loaded`, and `applies_to: "next_gpu_worker"`.
`POST` accepts exactly `{ "settings": { "reserve_vram_gib": null,
"offload_mode": "automatic", "expandable_segments": false }, "revision": "<64 hex>" }`.
Headroom is Auto (`null`) or a finite number from 0 to 64 GiB; offloading is
`automatic` or `low_vram`; allocator enablement is strictly boolean. Defaults are
unchanged. Unknown fields/invalid values return 422. Busy/maintenance, unsupported
backends, or changed revisions return 409; malformed stored policy returns 503.
Changed settings are persisted and idle resident models released under the same
lock as queue submission. No job is cancelled. Identical settings are a no-op.

Controller browser routes are `/api/v1/controller/remote/settings/memory`.
GET also returns a `target_key`; POST must include that key alongside the remote
body. It binds the draft to the connected SSH identity/local account and is checked
under the connection lock before forwarding; changing targets returns 409 without
forwarding. Host/Origin/session/CSRF protections still apply. No arbitrary process
environment, runtime arguments, paths or allocator strings are accepted.

### Events, errors and retention

SSE `event: job` includes numeric `id` and a durable event record. Cursors are
exclusive; reconnect from the last processed ID. Heartbeats are SSE comments, not
progress. `event: reset` carries `minimum_cursor` and `refresh`; refresh the jobs
snapshot and resume from that cursor after retention gaps. The controller's finite
proxy pages avoid pretending an SSH disconnect cancelled a job. Setup JSON event
pages use the analogous exclusive cursor, `has_more` and snapshot/reset contract.

Remote validation/auth/internal errors use `{"error":"invalid_request"}`,
`{"error":"unauthorized"}` or `{"error":"internal_error"}`. Typed HTTP failures
use FastAPI's `{"detail":"stable_code"}`: 422 `unsupported_request`, 503
`backend_not_ready`, 429 `queue_full` (`Retry-After: 5`), 507
`insufficient_storage`, 404 `job_not_found`, 410 `output_unavailable_or_expired`,
416 `invalid_range`, and 409 maintenance ownership/consent conflicts. Clients must
check status and the relevant documented envelope, not assume controller and daemon
errors have identical shapes. On daemon restart interrupted work is reconciled,
never marked completed without output. Downloads are record-resolved and confined;
there is no path-based file API. See [operations](operations.md) for retention and
resource defaults. No fixtures/source validation substitute for real MP4 acceptance.
