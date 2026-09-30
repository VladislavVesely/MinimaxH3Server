# Last-frame export and reuse

Add Download last frame (PNG) and Use last frame as first frame to available
completed outputs. Decode to the final displayed frame (including codec flush),
not a browser screenshot or approximate seek. Export native-resolution RGB PNG
with compression level zero, no optimization or palette conversion. Existing
video compression cannot be undone. Current H3 output is 8-bit; refuse higher-bit
depth rather than silently reducing precision.

Authenticated read-only GET jobs/{id}/last-frame, forwarded through the SSH proxy.
Use the worker's verified output descriptor. Isolated CPU decoder with time,
address-space, frame-count and geometry limits; one export at a time, including
when its awaiting HTTP coroutine times out. No permanent export cache or files.

Download the binary PNG locally, or explicitly upload the same bytes through the
existing CSRF-protected asset endpoint and replace only the draft's first frame.
Switch to Frames mode but keep prompt, dimensions, last-frame choice and other
settings unchanged. Check target epoch before reuse. Never submit generation
automatically. Imported copies are independent managed inputs, protected by normal
job reference sharing; deleting the source video does not invalidate a reused copy.

Verify pixel equality against independently decoded final RGB pixels, PNG stored
DEFLATE blocks, auth/unavailable paths, concurrency and no precision fallback.
Local codec test may skip without PyAV; require real deployed codec verification.
Use controlled native UI fixtures for download/reuse; no GPU inference needed.
