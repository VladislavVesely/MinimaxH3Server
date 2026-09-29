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
not transport-encrypted remote credentials. Sessions expire after eight hours or
controller shutdown; keep the application restricted to loopback.

All other endpoints require that cookie. Unsafe methods additionally require exact
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
| POST `/authenticate` | `{password}` only after host-key verification; request-scoped |
| POST `/disconnect` | `{}`; close channels/session credentials |
| POST `/scan` | `{}`; real read-only probes and experimental profile recommendations |
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

Booleans default false where consent is involved. A plan digest binds approval to
exact changes; approving a different/replanned digest is mandatory. `installer` or
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
