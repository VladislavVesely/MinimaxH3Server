# Reuse saved generation inputs without submitting

Add a `Reuse inputs` button to the stable heading of each Current/History job,
including running, failed and expired-output jobs with recorded settings. Replace
the current form draft, not the saved job. Never submit, install, cancel, change
server settings or upload/download new copies of conditioning assets.

Copy prompt, generation profile, exact safe seed, dimensions, frame count/seconds,
sampling steps, Sage, Turbo strength/low-VRAM settings, sparse preset, supported
managed LoRA and all mode-specific assets. Clear the previous draft's unused
assets and options. Assign values directly so ordinary toggle/preset handlers do
not overwrite the recorded sampling steps or round the recorded dimensions.

Prepare atomically: require recorded settings, a selectable model, matching
active component pins and representable fields. Reject unsafe browser integers,
unknown inputs, legacy sparse switches and unrepresentable LoRA combinations
rather than silently losing values. Read and validate each unique asset's metadata
through existing authenticated GET endpoints; reuse its immutable content ID.
Recheck target epoch, authentication and current pins/profiles after reads. If a
read or compatibility check fails, leave the draft unchanged and explain why.
Normal generation readiness/installation blockers still apply after prefilling.

Validation: eight focused JS checks; 62 reference/controller-security Python tests.
Packaged Chrome/WebKit fixture acceptance covers Current/History, all asset kinds,
first/last frames, exact values (including different Turbo step counts), replacing
old drafts, polling, missing-asset rollback and mobile layout. Generation and
feature-install POSTs were intercepted and none were attempted. A real existing
reference job also restored its exact prompt, settings and original asset IDs
without generation or installation. Preserve older controller joiner workspaces
when opening the rebuilt controller.
