"""Production installer integration contracts (no import-time IO).

1. ``planner.inspect_prerequisites(argv_runner)`` performs fixed read-only probes,
   including distro driver selection and APT simulation. ``build_plan`` returns
   a serializable document; show every change/blocker and the full pinned license.
2. ``Approval`` binds the canonical Store digest, license/eligibility, privileged
   consent and separately reboot consent. This component never reboots/downloads
   weights, edits repositories, disables Secure Boot, or starts a daemon.
3. ``runner.Installer.create/run`` persists ordered Store stages and holds an OS
   lock inherited by its subprocesses. Failed/interrupted stages can be retried
   with the SAME plan. Changed APT metadata requires a new plan and approval.
4. ``stage_bootstrap`` writes a fixed POSIX script, JSON inputs, log and status.
   ``launch_detached`` disconnects it from the SSH session. For a Python-less
   metadata-refresh round, read bootstrap/<digest>/status=waiting_for_replan;
   once Python exists, Store is authoritative. Never interpret launch as ready.
5. WAITING_FOR_REPLAN means prerequisite-only completion: the controller must
   automatically rescan/replan in its SAME wizard and obtain fresh approval.
   WAITING_FOR_REBOOT is handed to the separately consented reboot owner; after
   reconnect, run again. READY certifies imports/CUDA/codec checks only. Model
   acquisition, actual H3 execution and service registration are later tasks.

Ubuntu 24.04/26.04 and Debian 12/13 x86_64 are the explicit installer matrix,
not native-tested or model-certified distributions. Debian sources and Secure
Boot enrollment need explicit administrator action followed by reinspection.
"""
