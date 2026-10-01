# Character retention deployment

Merged `feature/character-retention` into `main` by fast-forward to `8cd94d5` at the operator's request.

## Build and checks

- Native macOS controller: `dist/character-retention/h3-controller`.
- Matching checksums: `dist/character-retention/SHA256SUMS`.
- Embedded remote wheel SHA256: `269b437c7b4ebeadced693a54789a9ea470f2fe3c331521e606c38c0b21c8f8f`.
- Post-merge targeted tests: 30 passed.
- Native controller smoke and executable-replacement smoke passed.
- Older running controller executables were not overwritten or stopped.

## Server

Saved, host-key-verified target: `zubyubuntu`, application root `/home/zuby/MinimaxH3Server`.

Staged and installed the candidate using the supported controller deployment flow. The reviewed plan preserved drivers, had no APT package changes and required no reboot or sudo authentication. Existing license/eligibility approval receipts were present. Installation completed with all environment/dependency/native-import verification stages ready.

Activated using the reject-busy policy after checking the queue was idle; no jobs were cancelled. Activation completed, released the deployment lock, and retained the previous release and database backup for rollback.

- Active release: `sha256-269b437c7b4ebeadced693a54789a9ea470f2fe3c331521e606c38c0b21c8f8f`.
- Active environment: `3a371ab9a49d0857a1a0471696565dfdb6bd31b25e93aa29c6b2a2396f8b3308`.
- Previous release: `sha256-d7e9af0db46e5256428a0bc1ff76fc9a56ee3209b21c74758ee5b92022f7dbba`.

Synchronized the existing component selection into the candidate environment. Only SageAttention and sparse-attention installation actions were necessary; synchronization completed successfully. Both ConvRot INT8/mixed models, SageAttention, Turbo, sparse attention and Ref2VA are confirmed installed. No model weight download was requested by the environment update.

## Live checks

The updated native controller is running at `http://192.168.1.61:8795` on the trusted LAN. Its access key is kept in the normal owner-only H3Server settings directory; no credentials or launch tokens are recorded here.

Verified through its authenticated SSH proxy:

- Remote health: HTTP 200, service `ok`, runtime ready, no readiness blockers.
- Character library: HTTP 200, zero initial records (no test characters inserted).
- Character UI module: HTTP 200.
- Browser library displays the server-global character manager with enabled creation controls.

No new H3 inference was queued; historical completed outputs are not evidence of this feature's native identity/voice quality. Existing unrelated test failures remain documented in the implementation verification record. Nothing was pushed to a remote Git repository.
