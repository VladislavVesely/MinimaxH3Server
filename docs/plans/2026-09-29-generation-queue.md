# Generation queue and diagnostics correction

Requested UX: a stable Queue action, an actual queue with per-job progress, and
an independent terminal rather than a mixed list of lifecycle events.

- Keep last confirmed readiness during HTTP polling. A background refresh is not
  an actual loss of readiness. Backend first validation still blocks submissions;
  cached successful checks remain usable while refreshed, and refresh work is
  deferred during generation. Every execution retains its independent validation.
- Show running jobs, then waiting jobs in FIFO order, then history. Preserve video
  DOM identity. Each row has its own phase/progress and a terminal-selection action.
- Use reported phase work totals only. Loading/unmeasured work is indeterminate;
  sampling shows measured steps. No invented whole-job percentage or ETA.
- Keep per-job bounded terminal buffers, deduplicate adjacent identical messages,
  preserve manual scroll position, and avoid intermixing different jobs' failures.
- Correct missing native build prerequisites: build-essential and python3-dev.
  Runtime diagnostics compile/import the actual Triton CUDA helper before reporting
  readiness. Environment checks must not read/normalize model weights before they
  are downloaded; only actual generation prepares the checksum-bound runtime copies.

Actual target: installed exact simulated package versions without removals or
upgrades; confirmed GCC 15.2 and matching Python 3.14 headers; Triton CudaUtils
compilation succeeded. Retried the latest failed user request once (1344x768,
124 frames, 20 steps); it passed text encoding and reached denoising. Live browser
inspection showed 10/20 sampling steps, per-job terminal output, and zero Queue
button disable transitions across multiple background polls.

The real job `e012a3a279aa4960919c20655dff5bf4` subsequently completed all sampling,
video/audio decoding, mux and validation stages. Its MP4 is 1,501,240 bytes,
1344x768 with 124 frames; an independent PyAV open and first-frame decode succeeded.
No claim is made about prompt fidelity, representative peak memory or throughput.
The readiness hotfix was applied to the idle target daemon only after this job
completed, preserving the output. The rebuilt local controller was reopened.
