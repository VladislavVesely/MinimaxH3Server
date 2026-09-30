# Local installation target

## Intent
Choose Local or SSH from the same controller. Local always means the machine running the controller, including when a LAN browser is on another device. macOS provides a read-only unsupported-platform scan; supported Linux uses the existing installation/control workflow without sshd.

## Design and boundaries
- Keep one stable connection object and existing setup/proxy/recovery integrations. Local selects an OS-account-bound home and process/file transport; SSH retains dedicated trust-store verification and password authentication unchanged.
- Fixed command builders, artifact pins, consent, stage ownership, privilege guardians, service registration and at-most-once recovery remain shared. No HTTP shell-command or filesystem-path endpoint is added.
- Local files use descriptor-relative no-follow access, owner-only/single-link checks and write confinement to the account's `~/MinimaxH3Server`. Immutable staging publication refuses replacement. Read-only ancestor metadata supports existing staging validation.
- Ordinary local child processes have deadlines and owned process-group cleanup. Staging/privileged guardians instead receive EOF: they retain locks until their current pipeline completes. The controller observes transport loss immediately, without killing or replaying privileged work.
- Existing daemon access reads its private token and connects only to `127.0.0.1:8765`. There is no SSH connection to localhost.
- Local selection/scanning needs no password. Explicit Linux sudo authorization uses the existing fixed password check, retains credentials only in memory, and never starts installation. macOS authorization/Install are blocked. Switching targets requires disconnect/idle setup and clears old target state; no implicit job cancellation.
- Local controller recovery reopens the local transport and revalidates durable bindings. A machine reboot terminates a local controller; restart and inspect the saved checkpoint rather than promising in-process reconnection across reboot.

## Verification
Focused connection, deadline, file-confinement, authorization, installer/recovery/security tests. Actual macOS Chrome/WebKit Local scan and switch-back acceptance, with install/SSH mutation boundaries intercepted. Actual Ubuntu local probe and authenticated daemon GET; isolated owned unprivileged staging guardian/publication fixture. No real privileged install, driver change, reboot, model download or inference used for testing. Windows Local mode is unsupported; Windows SSH mode is unchanged. Linux requires a Linux-native build, not the macOS executable.
