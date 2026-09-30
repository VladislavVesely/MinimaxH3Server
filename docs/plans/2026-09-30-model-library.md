# Additional model installation

## Cause and scope
A completed active installation was routed into failed-stage recovery after selecting a different profile. Completed checkpoints correctly fail that retry contract; the error was the choice of workflow, not a reason to relax recovery checks.

## Implementation
- Compatible curated runtime families get a separate explicit, authenticated model-install API and persistent model library. Native ConvRot variants share a runtime; GGUF Q4/Q5 share theirs. Cross-family installs remain new, separately approved full setups, admitted only after rechecking a completed active checkpoint under staging ownership. Old checkpoints/environments are retained.
- Preserve base runtime receipts and frozen dependencies. Additional profiles require their own exact artifact/license consent and an environment-only GPU/runtime diagnostic; no implicit dependency installation or inference.
- Idle maintenance rejects queued/running jobs rather than cancelling them. A private install lock, matching cancellation operation and interrupted-install recovery protect ownership. Downloads retain resumable partials.
- Reuse matching pinned files from other curated snapshots only after verifying size/hash and owner-only no-follow/single-link rules. Reflink where supported, otherwise private copy; never hardlink or modify originals. Progress includes verified/reused bytes, not solely network traffic.
- Persist approved additional profiles in `config/model-library.json`; advertise those compatible with the active runtime, retain other-family entries, dispatch each requested profile to its bound child process. Existing residency recycling handles profile changes.
- Install shows progress/cancellation and selects the newly installed generation profile without submitting a generation. Existing optional-component-only installs skip redundant model downloads. Polling preserves generation selection and step edits. Ref2VA/sparse retain INT8-only restrictions.
- Stable Install button text fixes independently reproduced WebKit mousedown/mouseup without click on blur/poll.

## Verification boundaries
Focused Python and JS tests plus Chrome/WebKit fixture acceptance. Fixtures intercept model installation and generation; no new real model download or inference is authorized solely for testing. Production rollout is exact-prior-source checked, idle-maintenance protected, with backups and an idle daemon restart. Existing controllers/joiner workspaces remain running. Full-suite, cross-family real provisioning and mixed-model inference are not claimed.
