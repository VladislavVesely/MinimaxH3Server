# Queue and history timing

Implemented directly on main, without subagents or a full test-suite run.

- Timestamp acceptance, worker claim, and terminal transitions transactionally in
  the existing SQLite job JSON. No SQL schema change or history backfill.
- Display live queue waiting and running elapsed time; history retains run, wait,
  and total elapsed time. Run includes loading, sampling, encoding and validation.
- Requeued interrupted jobs start a new attempt. An interrupted job has no invented
  end time (daemon restart is not proof of when execution actually stopped).
- Old records lacking timestamps show **Timing unavailable (not recorded)**.
- Live counters use server observation time plus the browser monotonic clock,
  freeze at the last snapshot if stale/disconnected, and never reset video DOM.
- Verification: 3 focused Python persistence/API tests and 7 JS display assertions
  passed. Native browser checked five real legacy history entries and fixture-based
  live/final timers, with no page errors and no mobile horizontal overflow.
- Rebuilt launcher; deployed daemon support only after acquiring reject-if-busy
  maintenance. Existing jobs, outputs, preferences and model files were preserved.
