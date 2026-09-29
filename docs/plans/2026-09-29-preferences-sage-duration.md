# Preferences, SageAttention and duration Implementation Plan

> Implement directly on main in this session. No subagents, worktrees, or full test-suite runs (operator instructions).

**Goal:** Remember the connection and optional SageAttention choices, reconnect safely on launch, support actual optional SageAttention installation/use, and link duration/frames.

**Architecture:** Owner-only plaintext local preferences are explicitly requested by the operator; never return saved passwords to the browser or log them. Authenticate saved credentials only after a known host key matches. A pinned optional SageAttention package is installed into the existing remote environment under a maintenance gate; generation records its explicit choice. Seconds/frames use the server's actual 24fps and H3 frame alignment.

**Tech Stack:** Existing Python/FastAPI/Paramiko controller and remote daemon; packaged vanilla JavaScript; SageAttention's pure-Triton 1.0.6 distribution.

**Status:** Implemented and validated with 9 focused checks, real RTX 4090 FP16/BF16 and ComfyUI attention probes, and native restart/mobile checks. No full Sage video or full suite was run. See `docs/validation.md` for scope. The cold readiness check exposed the frozen-dependency interaction; normal verification now permits only the exact GPU-receipted add-on, and failed installation probes roll back only that addition. Original resolved records/model files are unchanged.

## 1. Local preferences and automatic reconnection
- Create `src/h3server/controller/preferences.py`: bounded strict JSON, owner-only directory/file, reject symlinks and broad permissions, atomic replacement, public projection without password, explicit forget.
- Extend `controller/app.py` with preference GET/update and saved-connect/auth endpoints, inside existing local session/CSRF and operation guards. Remember only successfully authenticated target credentials and bind reuse to that exact host/port/user; no automatic host-key approval.
- Extend `web/app.js` and `web/index.html`: persisted host/user, saved-password indicator (not password readback), explicit plaintext disclosure/forget, automatic connect/auth/scan/observe/dashboard. Persist both Sage switches. No automatic installation or generation at startup.
- Focused checks: file mode/no secret readback/target mismatch/changed host refusal; native restart resumes actual dashboard without typing password.

## 2. Optional SageAttention installation and actual generation use
- Inspect/pin official SageAttention wheel metadata and verify a small actual GPU kernel before claiming support. Prefer pure-Triton SageAttention 1.0.6, which supports RTX4090 and avoids a large CUDA SDK build; label version honestly, not SageAttention2/3.
- Create `backends/sage_attention.py`: exact version+wheel hash, bounded optional install/probe, private state/log tail, no dependency upgrades, no hosted inference or arbitrary package URLs.
- Add authenticated remote feature installation/status, maintenance/idle guard, and controller proxy support. Installation choice joins explicit Install intent; already-installed environments can add this optional component without rerunning OS setup or model downloads.
- Add `GenerationSettings.sage_attention` default false, backward-compatible stored-job normalization, API boolean, selected-runtime validation and live feature capability. Set ComfyUI's Sage flag before attention modules import; never silently replace a selected unsupported feature with the default.
- Keep sparse attention separate. Unsupported legacy profiles reject Sage rather than falsely advertising support.
- Focused validation: default jobs retain existing behavior, requested-but-unavailable fails concretely, actual Sage GPU kernel works, native UI persists options and sends selected boolean.

## 3. Linked seconds and frames; release handoff
- Shared frame conversion: nearest `17*n+5` within 124..345 at 24fps. Preserve the user's currently edited input; show effective rounded duration and update the other field. Server duration normalization uses the same rule.
- Update UI details/disclosures, package and source equality checks via native build; only focused checks/live validation, not full suites.
- Preserve current work and MP4s. Do not restart an active generation to deploy changes; patch/restart idle daemon only, with backups and known-source checks. Reopen the rebuilt controller after confirming the new flows.
