# One-click setup implementation plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Replace the approval-heavy browser installation flow with selected-model details, one Install button and automatic progress; reuse the authenticated SSH password for sudo.

**Architecture:** A bounded controller-side setup coordinator owns the selected profile/session/target and advances existing plan/install/replan/reboot/reconnect/activate/download operations. Internal digest binding, authentication, CSRF, host verification and privileged-command confinement remain intact; separate UI approval checkboxes disappear. Existing low-level routes remain compatible where useful, but the main UI uses the single-action flow.

**Tech Stack:** Existing FastAPI controller, SSH connection/recovery, installer checkpoints, vanilla browser UI, pytest and Playwright.

## Explicit user direction

Work directly on main: no worktrees. Hide step03 comparison. Display details when a profile is selected in step04. One Install button initiates installation and displays progress. SSH password is also sudo password; do not ask for it twice. No design interview or merge-choice round.

The Install action visibly states its scope once: selected download totals and license/provenance details, required package/service installation and possible reboot. A concise adjacent statement can associate the action with license eligibility/terms; do not introduce replacement checkboxes, repeated dialogs or claim the application can confer legal eligibility. Preserve unknown/changed SSH host-key protections: those are connection security, not repetitive installation approval.

Credentials remain only in controller memory, tied to the authenticated target/session, and are never returned to the browser, logged, stored in checkpoints, command arguments, local/sessionStorage or disk. Explain memory-only reuse at authentication. Clear on disconnect, target replacement, session expiry, cancellation/shutdown and failures as appropriate. If sudo rejects the SSH password, show a specific recoverable error; any alternate-password input is only an error recovery path, not the normal flow.

## Task 1 — Controller flow and credential reuse

Owner: controller app/deploy/reconnection plus a new setup coordinator if appropriate, narrowly necessary installer/SSH integrations, backend tests. No UI mutation yet.

1. Inspect existing recovery/deployment transaction locks, authentication and operation routes.
2. Add failing tests for one request advancing the fixed selected profile through staging, prerequisites/replan, installation, necessary reboot/reconnect, activation and selected model download. Include already-installed/resumable states, duplicate requests, cancellation, transient connection loss, session/target replacement, sudo rejection and terminal failures.
3. Implement bounded coordinator start/status/cancel operations protected by existing authentication and CSRF. Use real durable states/events, never timer-derived percentages. Retain exact artifact binding and approved profile across replans. No duplicate privileged operation/reboot after ambiguous transport loss; use recorded checkpoints/recovery. No silent profile or weight fallback.
4. Reuse the authenticated SSH credential via a narrow target/session-bound memory-only interface. Never expose or serialize it. Auth APIs can keep explicit backward-compatible defaults; browser authentication will request setup reuse visibly.
5. Composite Install intent supplies internal approvals for the disclosed selected workflow; it must not disable low-level authorization globally or permit arbitrary privileged commands. Default busy update policy rejects rather than cancels existing jobs; destructive maintenance remains explicit advanced handling.
6. Keep flow observation asynchronous so the browser gets progress immediately. Session reload can restore progress, but a controller restart cannot recover a password from disk and must ask for authentication.
7. Run focused and full security/lifecycle regressions, commit scoped work, and provide precise route/status/credential contracts for UI owner.

## Task 2 — Simple UI and rebuilt launcher

Owner: web assets, browser fixtures/tests, controller presentation plumbing only as required, docs and packaging. Previous backend owner completed before this owner starts.

1. Add browser regressions for hidden step03, per-selection details and total bytes, a single Install action with no approval checkboxes or normal sudo field, and fully automatic progress through running/reconnect/activation/download/completion using real controller routes with controlled remote IO.
2. Replace old plan/approval/reboot/activation/download controls in the primary flow. Display selected profile explanation/GPU guidance/unknown memory, auxiliary files and size, collapsed license/provenance/technical details. Do not print a giant raw plan by default.
3. One Install click starts Task1's coordinator. Preserve selected profile during execution, prevent duplicate clicks, display actual phases/events and honest indeterminate progress. Expose Cancel and retry/re-authentication only when appropriate. Do not submit installation from merely selecting a profile or loading the page.
4. Authentication visibly explains memory-only SSH-password reuse for setup/sudo/reconnection; remove the repeated normal sudo password field. Keep host-key verification, safe error text, session lifecycle and responsive layout.
5. Update affected existing browser expectations rather than keeping old approval UI solely for tests. Preserve generation/video behavior and backend low-level security regression coverage.
6. Run full pytest -Werror, browser suite, pip check, rebuild root dist/h3-controller and run native packaged smoke. Ensure embedded source/resources equal main. Commit, no push or remote actions.

## Review / acceptance

Fresh read-only review of complete committed diff: user-visible single-button behavior, truthful action scope, password target binding/cleanup/redaction, CSRF/trust, durable at-most-once progression and cancellation, no unexpected job cancellation or model/profile fallback. Concrete findings receive bounded corrective passes from the same writer context.

No actual server connection, package/driver installation, reboot, full model download or GPU generation during delegated tests. Native package/dependency tests are allowed locally. Keep source on main and rebuild the familiar root launcher; no additional worktree or feature branch. Parent independently verifies tests and packaged launch before reporting completion.
