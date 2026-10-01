# Character editor LAN HTTP fix

## Root cause and regression

The deployed `New character` click failed with `crypto.randomUUID is not a function`. Chrome on the actual private-LAN HTTP origin reported `isSecureContext: false`, `randomUUID: undefined`, and an available `crypto.getRandomValues` function. Localhost acceptance tests had masked the secure-context restriction.

Commit `49965ae` replaces both character form-field and variant ID generation with 16 cryptographically random bytes encoded as 32 hexadecimal characters, matching the existing application request-ID approach and server variant-ID schema. No weak-random fallback was introduced.

A new editor regression explicitly disables `randomUUID`; it failed before the fix and passes afterward. The existing full character/media acceptance test also now runs with `randomUUID` unavailable, covering variant addition and valid saved IDs.

## Verification

- All three character Playwright tests passed, including native PNG/MP3 acceptance.
- Character Node tests and LAN request-ID check passed.
- Targeted character/API/static-asset pytest: 22 passed.
- Native controller and executable-replacement smoke tests passed.
- The real deployed LAN page opens the editor, adds a second variant, and reports no uncaught browser errors after reload.

## Deployment

Merged the fix into `main`. Built `dist/character-retention-lanfix/h3-controller` without overwriting existing executables. Restarted only the controller instance previously launched for this feature on `192.168.1.61:8795`; the new LAN access key is stored in the normal private H3Server settings directory. No credentials are recorded here.

The controller/server release pin requires matching deployment artifacts. Installed and activated the matching server package through the standard update flow with reject-busy policy, no job cancellation, no driver changes and no reboot. Active release: `sha256-49bd6e32431401089c4961e82db939ca3e1f63566e8a581574a4d1b2d93d9f4c`; environment: `7ef5f4b8d395edfe9e5bbef4e0fdbab6c032975a9f12db5ff8acb7d562700b7d`.

Reconciled the unchanged existing selection: ConvRot INT8/mixed, SageAttention, Turbo, sparse attention and Ref2VA. Synchronization completed without errors. After a fresh browser reload the server is runtime-ready and reconnect no longer reports a release-pin mismatch. No test characters or inference jobs were created on the actual server.
