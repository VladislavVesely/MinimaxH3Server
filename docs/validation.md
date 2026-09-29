# Validation and acceptance status

Validated implementation revision: `7e74cc9cf99be5a74376e385c5b15a636198a208` on `feature/h3-server`.

## Executed checks

| Environment | Check | Result |
| --- | --- | --- |
| macOS arm64, Python 3.14 | Full `python -m pytest -q -W error`, independently rerun by parent | 736 passed, 10 skipped |
| Ubuntu 26.04 x86_64, Python 3.14 | Full `python -m pytest -q -W error`, executed by parent | 736 passed, 10 skipped |
| Ubuntu | Native systemd parser plus actual launcher/interpreter/argument/PID regressions | 4 passed |
| macOS, Chromium | Playwright suite, final implementation worker run | 27 passed |
| Both platforms | `python -m pip check` | No broken requirements |
| Both platforms | Native PyInstaller build | Passed |
| Both platforms | Packaged executable smoke, independently executed by parent | Passed |

Packaged smoke verifies embedded release wheel, browser assets, runtime manifest and license; actual loopback launches in both supported launch modes; one-use bootstrap, Host/Origin/CSRF protections, authenticated API, and clean shutdown. It does not generate video or contact an installation target.

Browser integration tests exercise real controller/API/store routes with controlled SSH/runtime IO. Their video fixture bytes are **not** generated or playable H3 acceptance evidence. Reboot-related tests use controlled transport loss and independent fixture continuation, not a reboot of a real machine.

The Linux source was exported with `git archive` at the exact revision above, checked by SHA256 after verified SSH transfer, and tested in an unprivileged sandbox beneath `~/MinimaxH3Server/tmp`. Python virtual-environment bootstrapping used `venv --without-pip` and a SHA256-verified pip wheel because system ensurepip was unavailable. Dependencies and build caches remained in that sandbox. No system packages, drivers, service registration/activation, reboot, or model downloads were performed.

## Resolved review findings

- Polling now preserves existing video elements and observes accepted detached installation launches without resubmitting installation.
- Rejected setup actions no longer discard existing installation observation.
- Automatic reconnect is bounded, target/release/root/digest-bound, and observation-only. SSH credential retention requires visible consent and stays in memory; sudo credentials are not retained.
- systemd uses a fixed system-Python `execv` launcher, passing the absolute environment Python path as an argument. Native parsing and actual execution cover equals signs and other special characters without a shell, PATH lookup, or PID change.
- Final narrow independent review approved the last correction. The earlier integrated review's sole remaining code finding was this systemd rendering seam.

## Not yet accepted

- Real H3 GPU execution, offload/LoRA compatibility and a playable generated/downloaded MP4.
- Actual privileged service setup, reboot recovery, or rollback on a disposable Linux machine.
- Native Windows executable execution, ACL/reparse checks and Windows packaging acceptance. Cross-platform source and CI configuration are not proof that Windows CI has run.
- Compatibility of the Ubuntu-built executable with older Linux distributions. Build on the intended baseline and run native smoke checks there.

The inspected target has approximately 29 GiB RAM; the selected recipe documents approximately 75 GB host-memory needs. Its RTX 4090 does not by itself establish that this recipe fits. Hardware suitability, explicit model-license acknowledgment and download approval remain prerequisites to real-model acceptance. There is no hosted inference fallback.

## Local launcher

From the implementation worktree:

```sh
./dist/h3-controller
```

This is the locally built macOS executable. It opens the local browser controller; launching it is not approval for privileged target changes or a model download. Build another native platform with `python scripts/build_controller.py` and verify with `python scripts/smoke_controller.py dist/h3-controller` (use the `.exe` output on Windows).

All automated results above refer to the stated implementation revision. They are not a blanket end-to-end certification.
