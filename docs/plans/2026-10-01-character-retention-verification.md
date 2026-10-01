# Character retention verification

Implementation branch: `feature/character-retention`, based on `47c0385`.

## Evidence

- Full pytest: **1235 passed, 13 skipped, 2 failed**. Both failures reproduce on the original baseline: CLI port-error wording and concurrent maintenance drain timeout. The operator explicitly approved leaving them untouched.
- Latest targeted server/security/assets tests: **109 passed**.
- Both character Node checks pass; existing Node checks pass except `tests/job-deletion.cjs`. That test's outdated `reuse.dataset` mock failure also reproduces unchanged in the original workspace.
- Full existing browser run plus initial native character test: **40 passed, 3 failed**. All three failures were rerun in the original workspace and reproduce there: controlled cancellation after reload, undelivered Install retry button, and read-only unlaunched-plan inspection. No unrelated fixes were made.
- Latest character browser tests: **2 passed**, including real PNG and MP3 validation/serving, variant selection, unchanged authored scene text, keyboard completion, clickable character/voice inspection, explicit stale-label review, reload persistence, disconnect isolation and historical-draft detach.
- Native codec acceptance used Pillow/PyAV in the isolated `/tmp/h3-character-media` directory plus installed ffmpeg. No dependencies in the project's original virtual environment were changed.
- Wheel build succeeds and includes `h3server/characters.py` and `h3server/web/characters.js`.
- `git diff --check` passes.
- Browser tool inspection confirmed the editor fits a 390×844 viewport without horizontal page or dialog overflow. Desktop and mobile editor screenshots: `/tmp/h3-character-editor.png`, `/tmp/h3-character-editor-mobile.png`. Native library/selection screenshot: `/tmp/h3-character-library.png`. These are local temporary artifacts, not source files.

## Review notes

Server metadata is owner-only, atomically persisted, revision checked, globally scoped with nullable future `project_id`, and serialized with asset deletion. Corrupt library metadata fails closed. Stored records have bounded variant counts and serialized sizes. API paths and the real proxy allowlist confine IDs; existing authentication and CSRF protection remain intact.

Selection is one variant per character, internally represented as `variant_ids` for future expansion. Attachments are deduplicated and ownership-aware; manual references survive deselection. Reference labels use actual attachment positions, with distinct subject numbering. Shared voice assets have one definition linking their subjects. Changed character/variant/revision or attachment bindings flag authored labels instead of silently changing meanings. Managed definitions can be regenerated while preserving the detailed scene/sound sections, and prompt-size overflow is rejected before text replacement. Historical job reuse clears library selections without introducing a false stale-reference blocker.

No native H3 generation/identity retention or voice similarity claim is made: fixture workers only verify workflow integration. Nothing has been deployed remotely, pushed, or merged into the original working branch. Integration remains separate from the pre-existing test failures.
