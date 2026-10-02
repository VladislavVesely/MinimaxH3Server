# Persistent Local Access Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Set the controller access key once and remember trusted browsers without an application session timeout or restart logout.

**Architecture:** Persist private controller access credentials under the existing state directory, reuse them on launches, and keep browser cookies stable across normal shutdown. An opaque remembered-browser token (not the access key or SSH password) restores the cookie automatically if the browser drops it. Retain exact Host/Origin checks, CSRF protection and explicit access reset.

**Tech Stack:** Python/FastAPI, owner-only atomic state files and process locks, plain JavaScript/localStorage, pytest and Playwright.

## Approved behavior and boundaries

- No default server session deadline; old injected finite lifetimes remain supported for embedding/tests.
- The LAN key survives controller restarts, in a stable private key file. A deliberate reset rotates the key, cookie, CSRF and remembered token, revoking old browser grants across updated controller processes sharing the state directory.
- Browser cookies remain HttpOnly/SameSite=Strict and are refreshed during authenticated use. Browsers impose cookie lifetime limits, so an opaque non-expiring localStorage grant silently restores cookies; the actual access key and SSH credentials are not stored there.
- A restore endpoint requires exact Origin and a valid private browser grant. It does not start jobs or installation. Never retry an ambiguous mutation automatically; automatic restoration retries only rejected reads.
- Shutdown closes in-memory credentials/connections without revoking persistent access. Explicit invalidation/reset still revokes it.
- Bootstrap URLs remain one-use startup conveniences; their short lifetime is not a session/key expiration.
- Clearing browser data or resetting access requires the key again. HTTP remains restricted to trusted LAN/loopback binding; no public listener or authentication-free API is added.

## Tasks

1. Write failing tests for non-expiring default sessions, stable private credentials, concurrent initialization, restart cookie/CSRF validity, restore grants and explicit reset across instances. Implement bounded no-follow/owner-only private storage with atomic writes and cross-process serialization, and persistent session loading.
2. Write failing real ASGI tests for restart restoration, long-lived sessions, private restore endpoint Origin enforcement and retained CSRF/Host checks. Update normal shutdown, stable CLI key export/reset and browser token capture/read restoration. Update outdated documentation and tests describing eight-hour/restart expiration.
3. Add browser acceptance for dropped-cookie restoration without re-entering a key, and no mutation replay. Run targeted security/session/CLI suites, browser tests and native smoke/build checks. Review before merging; preserve the known unrelated baseline failures.
4. If deploying the live update, seed persistent credentials from the currently authorized LAN key/cookie when possible, rather than forcing another login. Stage/activate matching controller/server release using reject-busy policy and retain the complete existing model/add-on selection. Verify normal restart does not change the stored key/cookie or require another login.

## Verification before deployment

All 1,247 Python tests outside the two known unrelated failures passed; 13 platform/media tests skipped. The unfiltered run reproduced the existing CLI port-error wording and maintenance-drain timeout. Scoped cookie renewal to authenticated status responses so remote media proxy responses retain their strict upstream-header stripping contract. All ten character/persistent-access Playwright tests passed, including real PNG/MP3 media, missing-cookie restoration, stale launch bookmarks, invalid-grant removal and no mutation replay. Character, LAN request-ID, dashboard-recovery and generation-blocker Node checks passed.

The initial worktree native build was rejected by embedded-resource smoke: the shared editable virtualenv made PyInstaller's data collector import main's old web assets. Investigation confirmed the imported source paths. Rebuilding with explicit worktree `PYTHONPATH` fixed the source/data split without application changes. The verified binary is `dist/persistent-local-access-verified/h3-controller`; embedded source equality, native authenticated controller smoke and replacement/late-import smoke passed. The unverified archived build was never launched against the live target.
