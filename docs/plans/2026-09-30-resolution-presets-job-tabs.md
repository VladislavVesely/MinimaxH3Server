# Resolution presets and queue tabs

## Requested behavior
- Aspect-ratio and resolution selectors above manual Width/Height.
- Generation queue split into Active and History tabs.

## Design
Use two linked selectors rather than a long flat list: landscape/portrait
(approximately 16:9/9:16), square, 4:3 and 3:4, each with small/medium/large
sizes. Dimensions remain the authoritative generation inputs. Presets respect
32-pixel alignment, the 1344-pixel axis limit and 1,032,192-pixel area limit;
approximate ratios are labeled. Manual edits become Custom. No changes to
steps, duration, seed, attention, saved preferences, backend or model files.

Active contains queued/running jobs; History contains all terminal statuses.
Default to Active and return there after submitting a generation. Counts, empty
states, selected terminal and ARIA keyboard navigation track the selected tab.
Filter existing cards rather than recreating videos or expanded saved inputs.
Background updates do not switch tabs or reset playback DOM.

## Verification
Focused red-to-green JS tests cover all 15 presets, custom synchronization,
status classification, counts, empty states and stable card identity. Existing
estimate/blocker/submission/inputs/timing/toggle regressions remain green.
Rebuilt-native checks cover preset changes, unchanged unrelated settings,
actual history filtering, video DOM stability across polling/tab changes,
keyboard navigation and 375px layout. No generation/install/unload submitted.
Screenshot: `/tmp/h3-presets-tabs.png`.
