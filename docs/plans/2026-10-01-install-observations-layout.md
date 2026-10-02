# Sidebar navigation and collapsed install observations

## Approved design

- Only sidebar Generate/History switches the job view; no visible or keyboard-accessible duplicate Current/History tab row. Preserve grouping, selection resets and focus when the shared job panel moves.
- Observed system tools and unowned OS packages stay in System scan, collapsed by default.
- Managed runtime Python/package/source observations and model/optional-component receipts live in collapsed disclosures under their matching Install component cards. Never claim observation is validation or invent dependency ownership. Archived/unmatched components retain their own read-only, collapsed observations under Install.
- Info controls have equal fixed width/height, centered glyphs and circular borders.

## Implementation and verification

1. Add browser layout regressions for sidebar-only switching, focus/node preservation, closed scan disclosures, closed matching component details and equal info-button dimensions. Watch them fail before changes.
2. Keep private job view controls as internal navigation hooks, hidden from layout/accessibility; label the displayed job list as a region rather than a tab panel. Move software observations into stable per-component hosts with epoch fencing and read-only fallback hosts. Preserve text-only rendering and metadata disclaimers.
3. Verify Node job grouping, selection, software inventory and Chrome/WebKit studio layout; run targeted Python/browser tests and native embedded-source smoke.
4. Merge/deploy matching release on existing port 8795 with the saved key/browser trust unchanged, reject-busy activation, existing models/add-ons retained, and no other controller instances left running.

## Verification

Chrome and WebKit studio regressions pass: no visible tabs in Generate/History, preserved shared-panel focus/node identity, closed scan and component disclosures, receipt placement, stable disclosure state under polling, circular 30×30 info buttons and mobile layout. Node job grouping/selection and software inventory checks pass, including safe text rendering, late component-host attachment, archived fallback routing, detachment and target clearing. All 61 targeted Python tests pass.

All 36 character, persistent-access and generation browser tests outside the three previously recorded baseline failures pass. The broader initial attempt reproduced cancellation-after-reload and undelivered-Install failures before timing out; the known read-only unlaunched-plan failure was also excluded from the completed run. Updated an outdated generation test to allow only the intentionally persisted opaque browser grant, while still excluding SSH passwords and setup secrets from storage. Native embedded-source equality, authenticated controller and replacement/late-import smoke pass for `dist/install-observations-layout/h3-controller`.
