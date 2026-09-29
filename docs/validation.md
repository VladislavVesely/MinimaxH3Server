# Validation and acceptance status

## Turbo LoRA — 2026-09-29

Pinned v4-600 EMA plus its custom node/grid installed through the native UI.
A real six-step ConvRot INT8 + SageAttention generation completed on RTX 4090:
512×288, 124 frames, 32 kHz stereo audio; independently decoded with PyAV.
The 368,075-byte output is job `9aa37ffb869341eeb37ffe30598e55dd`.
Native checks verified persistent switches, six-step defaults, pinned history
inputs, mobile layout and zero page errors. Exact install-digest rejection and
idempotent installed-preset RPC were checked on the target. 27 focused Python
checks, 6 selected transport checks and the input/timing JS checks passed.
No full suite, full-resolution Turbo benchmark, mixed-profile inference, merge-mode
VRAM/quality or comparative speedup claim. See
[the detailed Turbo record](plans/2026-09-29-turbo-lora.md).


## Remembered connection, SageAttention and duration — 2026-09-29

Direct work on main; no subagents or full test-suite run for this update.

- **9 focused checks passed** in `tests/test_preferences_sage_duration.py`: private
  credential round-trip/forget, unsafe files, strict Sage flag, duration alignment,
  exact optional-dependency allowance, and failed-probe rollback.
- Native launcher rebuilt. Real authenticated browser checks confirmed connection
  restoration after process restart, both Sage choices restored, no startup install
  or generation requests, empty password field, linked controls, no page errors,
  and a 375px mobile layout without horizontal overflow.
- Installed the wheel-hash-pinned SageAttention **1.0.6** on the real RTX 4090 target.
  Both FP16/BF16 kernels passed; the actual pinned ComfyUI optimized-attention path
  selected Sage and produced GPU output matching the PyTorch probe tolerance.
- A cold native check caught the additive package violating the base freeze.
  Fixed without changing `resolved.txt` or its record: only the exact extra Sage
  line with its environment/Torch-bound GPU receipt is accepted. Other changes
  still fail closed. Installer failures attempt removal of only the new add-on.
  Fresh real-target readiness and native auto-reconnection then passed.
- Password settings are plaintext, mode 0600 inside a private macOS controller
  directory; never projected back through the preferences API. The saved connection
  remains available. Sage selection defaults were restored to off after the checks;
  the installed package is available when explicitly selected.

No complete Sage-enabled video, quality/speed comparison, native Windows acceptance
or fresh whole-suite result is claimed. Existing model files and generation history
were preserved. Target application files were hash-guarded updates with backups,
not a claim that the active historical application wheel remains byte-identical.


## One-click integration — current main source

Application source revision: `eeb7839e084fa59a6d4515167f1f06a22de45435` on **main**
(includes backend `a5d4c6f6679f7863b4fbbbb5af50483b15313ad1`). No new branch or
worktree was created. This section supersedes the historical approval UI and
request-scoped-sudo description below; historical results are not new acceptance.

Executed locally on macOS arm64, Python 3.14.5:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q -Werror` | **858 passed, 10 skipped** |
| `cd tests/browser && npx playwright test --reporter=line` | **31 passed** (Chromium) |
| `.venv/bin/python -m pip check` | No broken requirements |
| `.venv/bin/python scripts/build_controller.py` | Native root `dist/h3-controller` rebuilt |
| `.venv/bin/python scripts/smoke_controller.py dist/h3-controller` | Both ordinary and `deploy` launch modes passed |

Native executable SHA256:
`4a2e05efe0bccbf25624134f54a8b10f35a8d557ec619b80c7264ff70c83981c`.
Embedded wheel SHA256:
`efa0093091c27af00032983dd788bbc9fd2b4f3a8679f6b3951b9b7902cf2a53`.
Smoke verifies the embedded wheel equals every current application source/resource,
including the one-click coordinator, catalog and UI; actual loopback bootstrap,
Host/Origin/CSRF, catalog/license, idle setup, assets and shutdown all pass.
The familiar launcher is `./dist/h3-controller` in the main checkout.

Browser regressions exercise real authenticated controller routes, Deployment,
installer/store, target authorization/activation and selected download records,
with only SSH/native/runtime/download IO controlled. They cover prerequisite
replan, required reboot/reconnect, activation, selected acquisition, reload during
work, cancel/reload, exact pruned digest rejection, sudo rejection, lost/undelivered
responses, stable public retry IDs, stale observations and target reset. Host-trust,
CSRF and generation/video preservation checks remain. These fixture bytes are not
weights or a generated/playable H3 video. No real server was contacted and no
remote packages, drivers, reboot, model download or GPU execution occurred.

TDD failures preceded fixes: obsolete retention checkbox; missing pre-Install
catalog license fields; lost accepted response failing to resume polling. The last
was corrected by scheduling observation after status reconciliation. Independent
review then identified a blocked-plan retry defect: repaired prerequisites were
not inspected again after reauthentication. Four failing two-attempt route tests
reproduced it across legacy/pruned selections and environment/prerequisite plans.
The correction permits one fresh discovery on a new explicit Install only when
the blocked plan has no durable launch evidence. Twelve guard cases preserve
checkpoints, matching operation/active-environment records and even partial
bootstrap launch/approval/status records. Focused coordinator/deployment tests:
**89 passed**. All full-suite and native results above were rerun after correction.
Independent re-review of this correction is **pending**, not claimed here.
Ten platform/opt-in Python tests remain skipped. Live Linux setup/reboot/GPU,
Windows native acceptance and Python physical-memory zeroization remain unproven;
offline cancellation cannot guarantee detached remote work stopped.

## Historical validation (prior design/revision)

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
