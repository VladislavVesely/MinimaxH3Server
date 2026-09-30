# Component Synchronization Implementation Plan

**Goal:** One installed-state checklist, per-item information/license dialog, one Apply changes action for supported additions/removals, and durable batch progress.

**Architecture:** A daemon-owned reconciler uses the existing authenticated models/library and models/install transport (explicit synchronize request discriminator). One maintenance owner spans all actions; new components install before removals. Exact inventory binding rejects stale requests. Existing pinned installers run under the enclosing ownership without independently releasing it. Persist an authoritative model registry, including an empty library, so removal of the original base model survives restart. Never remove runtimes, OS packages, history, outputs, uploads, or shared dependencies. Runtime-family changes remain explicitly unsupported by the batch reconciler and visibly explained; original first-time single-runtime provisioning remains supported through the existing setup coordinator.

**Tech stack:** Python/FastAPI, descriptor-relative owned IO, vanilla JS, native Chrome/WebKit.

## 1. Backend red → green
- Add `tests/test_components.py` covering inventory, multiple additions/removals, additions failing before removal, stale inventory, active jobs, empty library, original-base removal/restart, cancellation, interrupted recovery and hostile filesystem entries.
- Add `remote/components.py` with inventory, planning, durable progress, maintenance, bounded child operations and pinned managed cleanup.
- Extend `model_library.py` with schema-2 authoritative installed profiles and selection of a retained primary backend; keep schema-1 compatibility.
- Extend optional installer `_run` methods with internal enclosing-maintenance ownership and inherited guard descriptors for mutating subprocesses. Old public endpoints retain original semantics.
- Mount synchronized state under existing model library responses and discriminate synchronized install/cancel requests without relaxing authentication, body bounds or CSRF.
- Test removals only in owned isolated fixtures.

## 2. Browser red → green
- Replace planning-only cards/Review/visible selector with actual desired-state model and optional-component cards.
- Hydrate once from authoritative inventory, preserve deliberate edits on polls, reset on target change, and disable unknown or incompatible choices with explanations.
- Per-card keyboard-accessible info dialogs show exact license/provenance/download/support details. Compact consent text accompanies Apply.
- Show pending install/remove actions before Apply and durable overall/action/byte progress afterward, with honest indeterminate stages, stop-after-current-action cancellation and partial-error outcomes.
- Keep original setup coordinator, diagnostics and first-time provisioning controls internally; no duplicate visible selection surfaces.
- No mutations on navigation, restoration, checkbox editing or opening info.

## 3. Acceptance and delivery
- Focused Python/JS/native browser tests only; no full suite, no agents/worktrees.
- Daemon deploy only against exact prior source, with backups under idle reject-policy maintenance. No real model deletion/install for acceptance.
- Existing 8789 controller proxy accepts the required model request bodies: update only its public static assets to preserve its private joiner workspace and active sessions. Rebuild the persistent packaged executable as well.
- Verify actual 8789 assets/UI in Chrome and WebKit; clearly record unsupported/runtime boundaries and untested real bulk-download/removal cases.
