# Character Editor Dismissal Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Confirm user-initiated dismissal of the character editor without losing inputs on rejection.

**Architecture:** One local cancellation handler asks the approved native confirmation, “Discard character edits?”. Route the Cancel button, intercepted dialog `cancel` event (Escape), and an actual backdrop click through it. Preserve programmatic closes after successful save/deletion or target reset, and block user dismissal during active operations.

**Tech Stack:** Native HTML dialog, plain JavaScript, existing Playwright character tests.

## Approved behavior

Always ask on outside click, Escape and Cancel, including an initially empty form. Accept closes/removes the editor; decline leaves all values, selected files and variants intact. Clicks within the dialog—including its padding—are not cancellation requests. A gesture that starts inside and ends outside is not a backdrop click. Backdrop detection checks both pointer origin and click coordinates against the actual dialog bounding rectangle. Read-only asset inspectors are unchanged. Saves and server switches must not trigger a discard prompt.

## Steps

1. Add failing browser regression cases for each dismissal route: decline preserves text, file selection and variant count; accept closes. Test inside padding clicks and inside-to-outside drags do not prompt. Use the isolated character factory harness so tests do not mutate the actual server.
2. Run `H3_TEST_MEDIA_PATH=/tmp/h3-character-media tests/browser/node_modules/.bin/playwright test --config tests/browser/playwright.config.js characters.spec.js --grep dismissal` and observe missing confirmation failures.
3. Add the shared guarded handler in `src/h3server/web/characters.js`. Intercept Escape with preventDefault, change only the editor Cancel callback, and identify backdrop gestures without click-through.
4. Test dismissal refusal while a save/upload promise is pending and successful programmatic closing without confirmation. Run all character browser tests, character Node tests and targeted pytest.
5. Review diff and commit the verified change. Merge into main if continuing the live update, build a distinct native executable, and preserve older archived builds. Controller/server artifact pins must match during a live deployment; use the existing reviewed install/activation flow with reject-busy policy and preserve the existing component selection. Never cancel generation jobs or silently change model selections.

## Workspace and baseline

Worktree `.worktrees/character-dismissal`, branch `feature/character-dismissal`, base `828b060`. Targeted baseline character/API/static-asset tests: 22 passed. The previously approved unrelated full-suite failures remain outside this change.

## Verification and deployment

Commit `3c9e4de` was merged into main. All eight character browser tests passed, including real PNG/MP3 acceptance, three confirmation routes, declined text/file/variant preservation, inside-click/drag protection, and blocking dismissal during an upload while successful save closes without a prompt. Character Node tests, LAN request-ID check and 22 targeted Python tests passed. Native controller and executable-replacement smoke passed.

Built `dist/character-dismissal/h3-controller`. Installed and activated the matching server release `sha256-1066d924bef2100550e52b765adc0a755b68c3dcd91186e20d3c8dfc642a2eb5`, environment `9a968ebe2bae0ca49ade7871a1f30b22a4127873d787899af63c5b09292dd3c1`, through the reviewed update flow using reject-busy activation. No generation jobs were cancelled, drivers changed, or model weights requested. The original ConvRot INT8/mixed, SageAttention, Turbo, sparse and Ref2VA selection was reconciled successfully.

Replaced only the feature's own LAN controller instance on port 8795, leaving older archived binaries and other controller processes alone. Verified the actual LAN editor prompts through all three dismissal routes and preserves the edited name when confirmation is declined; accepting removes the editor. Live probing used a stubbed confirmation decision, while automated Playwright tests handled the actual browser confirmation dialogs. No real test characters were saved. Remote health returned HTTP 200 and runtime-ready. The renewed LAN access key remains in the normal private settings directory; no credentials are recorded here.
