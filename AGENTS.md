# Project operating instructions

## Development mode: deploy changes directly

The operator has explicitly enabled development mode and given standing authorization to deploy application changes directly. This replaces the earlier requirement to ask for separate deployment approval after implementation.

- Implement, test, build and deploy functional changes in the same task. Do not stop at an implementation/build checkpoint or ask “shall I deploy?”
- Standing authorization includes matching application/daemon activation when required and replacement of the controller at **http://192.168.1.61:8795**. Controller-only changes do not require unnecessary daemon activation.
- Instructions/documentation-only changes do not require rebuilding or restarting an unchanged application.
- Use the latest deployed source as the starting point, not a stale root checkout. The currently deployed feature lineage is `feature/joiner-cache`; consult its deployment record and freshly observed release identity. Keep existing feature branches and archived executables intact; do not merge or overwrite them merely to deploy.

## Keep deployments short and safe

- Reuse validated deployment/preflight/acceptance procedures. Refresh artifact hashes, active identities, process IDs and authoritative baselines; never replay historical helper constants unchanged.
- Run fresh generation/setup/library/maintenance and current Joiner-media checks. Back up current server data and transient media before replacement; preserve projects, History/output, favorites/names, authored settings, managed assets, installed components and persistent access.
- Activate idle-only with reject policy. Never cancel/drain user work, clear another owner's maintenance gate or replay a rejected mutation. If busy, wait for a safe idle window rather than request deployment approval again.
- Retain the same sole controller URL. Wait for the predecessor to exit and verify the actual exclusive socket can bind; a SO_REUSEADDR probe does not match the controller's bind policy. Use a new archived build destination rather than replacing an older executable.
- Verify live capabilities/assets, login, preservation and essential UI after deployment. Report completion concisely. Keep documentation proportional; it must not delay availability unnecessarily.
- Development mode is not authorization for unrelated destructive operations, credential disclosure, model/LoRA acquisition, driver changes or weakening validation/security. Obtain specific authorization for actions outside the requested application change.

Preserve unrelated local files, including `video_prompt.txt`. Never print credentials, cookies or API keys.
