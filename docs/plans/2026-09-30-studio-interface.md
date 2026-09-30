# Studio Interface Implementation Plan

**Goal:** Redesign presentation around Install, Generate + Current, History and Joiner without changing controller/daemon business logic.

**Architecture:** Keep every existing control ID, form, handler and media node. A separate presentation module moves stable DOM nodes into workspaces, exposes advanced details, and adapts the existing single-profile review control into checkbox model cards. No new API requests, installation batching, mutations on navigation, generated demo media or invented readiness. CSS supplies the reference's light panels, dark navigation and violet controls.

**Tech Stack:** Existing vanilla HTML/JS/CSS, native Chrome/WebKit acceptance, focused Python/JS regressions. Direct work on main; no subagents/worktrees or full-suite runs.

## Tasks
1. Write native browser presentation tests against the existing HTML: workspace switching preserves form/node identity; Generate retains Current; History uses existing history filtering; model checkboxes allow multiple planned/installed variants without starting installs. Watch these fail before implementation.
2. Add `web/studio.js`, `web/studio.css`; wire assets in `web/index.html`. Keep bootstrap fragments untouched. Navigation changes presentation only, keeps the job panel/media stable and does not reset selection on same-view visits. Existing reuse/last-frame actions reveal Generate before focusing their original controls.
3. Add optional presentation snapshots to existing render/model-catalog code, using current model statuses and eligibility (never inferred installation). Cards may hold multiple planned variants; Review selects one existing installation profile, and the original Install button approves exactly that profile with unchanged license, optional-feature and maintenance gates. Installed checkboxes are checked/read-only; unchecking a plan never uninstalls. Explain this boundary directly in UI.
4. Build a responsive two-column setup and generation/current layout; advanced disclosures retain all existing controls and truthful caveats. No faux text-to-image/negative-prompt/CFG/pause/transcode features.
5. Verify native desktop/mobile Chrome and WebKit, intercepted install/generation requests, existing job/reuse/joiner/estimate/first-click regressions and targeted controller asset/security tests. Rebuild and launch alongside old controllers, preserving all workspaces. Document verification and limitations, commit without pushing.
