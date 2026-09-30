# Clone dimensions from visual inputs

Add Use dimensions to first/last-frame, reference-image and reference-video
previews. Audio has no spatial size and gets no button. Read the daemon-validated
asset metadata, round each axis independently to the nearest multiple of 32
(ties upward, minimum 32), and set generation Width/Height only on explicit click.
Display source → aligned output and an exact/mismatch notice including possible
aspect-ratio change. Never resize the stored source, silently cap/downscale an
oversized source or bypass profile axis/area limits. Explain and disable sizes
outside those limits. Keep controls responsive to busy state and updated limits
without rebuilding media previews on background polls.

Reuse preset synchronization, blocker rendering and input-driven estimates.
Focused JS tests cover rounding, small inputs, portrait, exact sizes, video,
nonvisual/invalid metadata, live limits and no mutation when disabled. Rebuilt
native browser acceptance uses intercepted upload metadata and no GPU work.
