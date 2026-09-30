# Extended native resolution presets

Request: extend the resolution selector with typical sizes including 1080p.

Use native aligned canvases rather than introducing an unrequested upscaler:
landscape 864×480, 1280×736 and 1920×1088; portrait swaps axes. Preserve defaults,
Custom inputs and all unrelated generation choices. Labels expose actual sizes.

Expand only pruned service bounds to a 1920-pixel axis and 2,088,960-pixel area.
Share policy between normalized settings, capability responses and media
validation; bind parent validation to its selected profile. Legacy limits stay
unchanged. Controller uses live daemon bounds to disable unavailable presets and
show actionable invalid-canvas reasons, including against older daemons.

High-memory options warn that target GPU inference has not been benchmarked.
No inference/upscaling/installation is automatically submitted. Deployment is
hash-guarded and idle-maintenance-only. Source replacement does not modify models,
preferences, completed jobs or output files.

Verification: 209 focused Python tests; preset/blocker/tab/submission/estimate/
input/timing JS checks; rebuilt-native desktop/mobile testing and live readonly
HD estimate API validation. No full suite, subagents or GPU-generation benchmark.
See ../validation.md for acceptance scope and limitations.
