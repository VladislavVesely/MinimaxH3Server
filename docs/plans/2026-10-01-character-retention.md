# Character Retention Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Add a persistent server-global character library, variant/voice attachment selection and guide-aligned prompt assistance.

**Architecture:** Reuse AssetStore for immutable media and introduce server-owned versioned character metadata with nullable future project scope. Authenticated controller routes proxy remote CRUD; a separate browser module integrates with the existing generation asset draft without changing inference payloads. Shared locking protects library media during job cleanup, and authoring bindings preserve prompts without silently reassigning reference labels.

**Tech Stack:** Python, FastAPI/Pydantic, existing safe filesystem helpers, plain JavaScript/CSS, pytest, Node assertion tests and Playwright.

---

Prerequisite: baseline on 47c0385 has two failures (CLI port-message expectation and concurrent maintenance timeout). Obtain permission to continue without fixing unrelated failures before production changes. Workspace: `.worktrees/character-retention`, branch `feature/character-retention`.

### Task 1: Persistent library and cleanup safety

**Files:**
- Create: `src/h3server/characters.py`
- Modify: `src/h3server/store.py`, `src/h3server/remote/worker.py`
- Test: `tests/test_characters.py`, `tests/test_job_delete.py`

1. Write failing tests using real AppPaths, immutable test media and two CharacterStore instances. Assert create/list/get/update/delete, UUID IDs, revision conflicts, independent persistence, global `project_id: null`, and one-or-more named variants referencing only image assets. Voice must be an optional validated MP3 asset. Reject bad names, descriptions, duplicate variant IDs, wrong media kinds, unsupported scope and unbounded metadata.
2. Run `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_characters.py -q`; confirm missing library functionality causes failure.
3. Implement strict records (schema_version, revision, character ID, nullable project_id, name, description, voice_asset_id, voice_description, variants). Each variant stores ID, name, description and image_asset_id. Use `atomic_json`, `regular_file`, `directory`, and the existing `state/assets.lock` for synchronized bounded metadata reads/mutations. Require optimistic revision on update/delete. Never accept caller filesystem paths.
4. Add a failing terminal-job deletion test: a library character points at the job's image/voice; deletion retains those assets while deleting unpinned ones. Add deletion-after-edit and deletion-after-character-removal tests proving historical jobs still preserve shared assets.
5. Extend Store.delete_job with an optional protected asset set. Under worker asset lock, collect all character references and subtract them before cleanup and API return. Character removal never directly deletes immutable media.
6. Run `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_characters.py tests/test_job_delete.py tests/test_asset_store.py -q`. Review concurrent lock ordering and fail-closed behavior for corrupt character state.
7. Commit this independently verified server storage slice.

### Task 2: Authenticated remote API and controller proxy

**Files:**
- Modify: `src/h3server/remote/app.py`, `src/h3server/controller/app.py`
- Test: `tests/test_characters_api.py`, `tests/test_controller_security.py`, `tests/test_web_assets.py`

1. Write failing TestClient tests for authenticated GET `/api/v1/characters`, GET by ID, POST create, POST `/{id}/update`, POST `/{id}/delete`. Include expected 401 without auth, 422 strict shape validation, 404 unknown ID, 409 revision conflict, wrong asset kind rejected and missing/corrupt asset failure without reflected sensitive values.
2. Run targeted tests and confirm routes are missing.
3. Add strict Pydantic inputs and remote routes backed by CharacterStore; return bounded records and revision errors. Existing asset upload/content routes remain the media transport.
4. Write controller tests that unauthenticated/CSRF-invalid mutations fail, request bodies forward only to the connected target, and opaque IDs cannot inject proxy paths.
5. Add matching `/api/v1/controller/remote/characters` routes using existing proxy request conventions. Keep request bodies within controller BODY_LIMIT; explicitly cap descriptions/variant count to fit that budget. No arbitrary server URL inputs or controller-local character persistence.
6. Run `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_characters_api.py tests/test_controller_security.py tests/test_remote_api.py tests/test_web_assets.py -q`.
7. Commit the verified API slice.

### Task 3: Library UI, selection and prompt assistance

**Files:**
- Create: `src/h3server/web/characters.js`, `tests/characters.cjs`, `tests/browser/characters.spec.js`
- Modify: `src/h3server/web/index.html`, `src/h3server/web/studio.css`, `src/h3server/web/app.js`, `src/h3server/controller/app.py`, `tests/browser/generation_fixture.py`, `README.md`

1. Add failing Node tests for pure binding/template helpers: multiple characters, one variant each, mappings to actual attachment positions, shared voice assets, global speaker-order neutrality, six-section guide template, typed `<sub` completion, safe insertion at caret and reference count overflow. Assert canonical `<Subject N>`, `<Picture N>`, `<Audio N>` labels and no voice-source dialogue copying.
2. Run `node tests/characters.cjs` and observe missing module/helper failure.
3. Implement a separately served `characters.js` module with pure helpers and a factory receiving existing request, draft and render callbacks. Avoid app-global coupling and HTML injection (textContent/DOM nodes only).
4. Add failing integration/browser tests for CRUD variants/MP3, selecting/deselecting with manually attached assets, prompt preservation, stale label warning/blocking, reference previews, keyboard completion, revision conflicts, unavailable Ref2VA and clearing bindings on target change/job reuse.
5. Build server-backed library UI and generation picker. Upload through existing validated asset endpoint; save only after successful validation. Retain errors in editor, handle revision conflicts without discarding edits, and show actual voice/image previews. Check limits atomically before attachments change.
6. Generate scaffold only for empty prompt or explicit action. Track last generated header and bindings; update only unedited managed sections. If changing assignments would affect authored label meaning, require an explicit refresh/review. Preserve scene/dialogue exactly. Use reference-generation/audio-reference classifications and fully_preserved/reference retention markers; do not pretend pictures are keyframes or speaker IDs match subjects.
7. Implement listbox suggestions with arrows/Enter/Escape, pointer selection and caret insertion. Render a companion clickable text preview with subject/asset inspectors and voice playback; do not replace the plain textarea with a fragile contenteditable.
8. Integrate lifecycle hooks at authentication/target changes, draft mutation, job reuse, generation validation and busy rendering. Handle older servers with clear library-unavailable state without breaking normal generation. Register asset serving and include script before app initialization.
9. Run `node tests/characters.cjs`; run all existing `tests/*.cjs` checks and targeted pytest; run Playwright with fixture backend using the existing browser package configuration. Use browser tools for visual verification, save screenshots and inspect console errors.
10. Document server-global scope, future project field, storage/MP3 duration limits, guide syntax and the fact that character references guide identity/voice rather than guarantee exact retention.
11. Run full pytest, Node and browser suites, compare failures against baseline, review diff/security/lock safety, then commit verified UI slice. Report what was tested without claiming fixture inference proves retention quality.

## Review checkpoints

After the server/API batch, report verification evidence and pause for feedback before UI implementation if requested or if unexpected blockers occur. Final review must cover existing job reuse/deletion behavior, target isolation, edited prompt preservation, stale tag handling and server storage across different controller clients. Keep branch/worktree intact until the user chooses integration.
