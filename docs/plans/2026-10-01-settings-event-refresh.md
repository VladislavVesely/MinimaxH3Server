# Settings refresh hotfix

## Report and cause

The user reported Settings repeatedly reloading instead of updating on events.
`memory-settings.js` scheduled a GET every three seconds, set `loading=true`,
disabled the fieldset, and painted the loading message on every tick. This
interrupted focus/typing even when the saved policy had not changed.

## Fix — `1f19654`

Removed the Settings timer entirely. Read on workspace entry, target changes,
explicit reload, browser foregrounding, and observed queue/maintenance transition
to idle. Activity transitions come from existing dashboard observations; no new
server push transport or polling loop was introduced. Background event reads do
not disable the form or show a loading flash. Preserve dirty drafts, target/response
fencing and revision-checked writes. Stale cross-client updates are detected on an
event refresh or rejected by conditional Apply; they never silently overwrite data.
Suppress repeated failed initial loads from unchanged context renders. Starting
Apply invalidates any older background GET so its response cannot overwrite the
save result. Also clarified that another local operation can block Apply.

## Verification and deployment

Regression initially failed: advancing the browser clock triggered an extra GET.
After the fix, ten seconds of elapsed time trigger no request, the edited field
retains focus/value, and an idle event causes a non-blocking read which preserves a
stale draft. Five memory/access Playwright tests, 37 focused Python tests, Chrome
and WebKit layout checks, JS syntax checks, and native embedded-source/security/
replacement smoke passed.

Controller-only deployment to http://192.168.1.61:8795 uses
`dist/settings-event-refresh/h3-controller`. Its embedded candidate wheel is
`191837838617acaa0fdfbc59431fdf666506b3f74b9b93156956bda0762b3fef`; launch explicitly
pins the existing operator-verified backend wheel with `--wheel
.../dist/runtime-memory-settings/h3server-0.1.0-py3-none-any.whl --sha256
1e5a52995ef6c56d712b4924050b3a28b3bc4d41ec158be00b84a5f67e5b43f2`.
No server/environment upgrade, GPU worker restart, policy update or generation
cancellation was needed. An initial local bind attempt failed during old-socket
release; a later start succeeded. Live Chrome then confirmed zero timed Settings
GETs over ten seconds, retained draft/focus, unchanged saved policy, all six
components installed and identical private access-store digest. Only the current
8795 controller listener remains (PID 70731 at verification). User-owned untracked
`video_prompt.txt` remains untouched. Existing unrelated test failures are out of
scope; no new GPU generation benchmark was run.
