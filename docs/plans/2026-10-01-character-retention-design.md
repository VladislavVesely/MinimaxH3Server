# Server-side character retention design

## Approved scope

Characters live on the generation server, not in controller preferences or browser storage. A character has a stable ID, name, identity description, optional voice asset and voice description, and named image variants with descriptions. Each generation selects one variant per character. Model selection as a list of variant IDs internally leaves room for future multi-image support, but enforce one today. Include nullable `project_id` in persisted records; only null/global scope is exposed initially.

## Persistence and lifecycle

Reuse validated immutable AssetStore media and authenticated asset previews. Store versioned character metadata under the server's owner-only state directory using existing safe filesystem helpers and atomic writes. Character mutations and job asset deletion use the same asset lock; revalidate referenced media inside the lock. Include optimistic revisions to avoid overwriting edits from another client. Character deletion removes metadata, not media required by historical jobs. Job deletion must subtract character-library asset IDs from its removable assets and report only actually removed assets. No project management, face analysis, voice synthesis, or guarantee of exact model retention is added.

## UI and generation

Add a global Characters library with create/edit/delete, named variant upload/preview, and optional MP3 upload/playback. Generation offers character/variant cards and previews. Selecting a character adds one image plus its optional voice to the existing reference draft and selects Reference mode, without installing models silently. Check total reference limits before changing draft state. Support multiple characters; do not exceed the existing nine-image/three-audio limits. Deselect only references owned by selection, not independently uploaded references. Clear server-specific library and selections on target change.

## Prompt authoring

Use the supplied MiniMax guides' exact case-sensitive canonical labels. Subject numbering is distinct from actual image/audio positions. A character-only image is cited within `<Subject N>` definition, not given a standalone concrete-frame definition. Audio is a timbre/delivery reference, not copied dialogue. Prefill the six full-reference sections in order: subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music. Keep scene text editable and preserve user edits on selection changes. Do not allocate speaker IDs by subject number; IDs follow actual vocal order. Generated empty templates do not invent dialogue or speaker ordering.

Typing `<` opens keyboard-accessible suggestions for available subject, picture, video, audio labels with a thumbnail/name. A companion safe text-rendered preview makes labels clickable, opening visual/voice details. Existing freeform prompts remain supported. Changes that would invalidate labels in edited text require an explicit refresh/review; flag missing or stale bindings rather than silently reassigning characters. Do not submit known stale character bindings without review. Reused historical jobs remain self-contained prompts plus immutable asset IDs, independent of current character metadata.

## Verification

Test persistence across instances, revisions, asset-kind validation, global scope, locking/cleanup protection, authentication and controller proxying. JS tests cover attachment limits, ownership, tag mappings, editable prompt preservation, renumber warnings and autocomplete insertion. Browser tests cover library creation, variant and MP3 preview, picker autopopulation, keyboard suggestions, tag inspection and target switching. Inference quality is not verifiable with fixture backends; report that separately.
