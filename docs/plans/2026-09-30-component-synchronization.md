# Requested component synchronization — pending implementation

The operator rejected the current planning-checkbox + Review + profile selector UI. Required replacement: one actual-state checklist for models and optional accelerators, one Synchronize action, additions for checked missing components and removal of unchecked managed components. Remove the duplicate selector/Review flow and prominent license/provenance blocks; retain required consent semantics behind a compact disclosure/link.

This is NOT implemented by the studio-startup fix. Do not describe the existing planning checkboxes as a synchronizer.

Required safety/design work:
- Authoritative inventory, including installed inactive-runtime variants; unknown inventory must never be treated as an empty desired state.
- Capture a version-bound desired state and explicit sync intent. Hold idle-only maintenance ownership across the entire reconciliation; never cancel jobs implicitly.
- Reuse existing pinned installers and consent guards. Cross-runtime provisioning/reboots require durable controller coordination, not an in-memory loop over model installs.
- Install/verify additions before removals; preserve successfully installed components on failure. Report partial progress honestly and recover without replaying privileged actions.
- Model removal must handle the active base profile/readiness, registry reconstruction on restart, shared weights/normalization caches, and descriptor-anchored no-follow cleanup. Preserve history, outputs, uploaded reference assets, unrelated files and dependencies still needed by retained components.
- Optional accelerators belong in the same checklist. Sage removal must distinguish an optional add-on from a frozen base dependency; Turbo/sparse/reference cleanup must respect pinned inventories and retained dependencies.
- Test destructive behavior only in isolated fixtures. Live deployment requires exact-source checks, backups and idle maintenance. Preserve the running controller/joiner workspaces on port 8789.

Delivered separately: initial workspace now switches to Generate when authenticated model/runtime readiness is confirmed, once per page, unless the user deliberately navigates or interacts with Install. Tested against the actual 8789 listener in Chrome and WebKit without remote mutations.
