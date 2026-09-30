# Visual whole-clip joiner timeline

User approved arranging whole files only, using a filmstrip editor reference.
No splitting, trimming, transitions or export changes.

Replace text cards with adjacent duration-proportional filmstrip blocks. Put the
large preview above a horizontal time ruler, gold playhead, zoom and scroll area.
Click a block or scrub the ruler to preview a point in the sequence; dragging a
block moves the complete original before/after another block. Selected-clip
Earlier/Later/Remove controls and keyboard selection/deletion remain available.

Generate six representative, letterboxed 128×72 thumbnails per clip in the
browser, with one bounded-time decoder at a time. Cache only in page memory and
release decoder sources. Missing/unsupported previews are visible placeholders,
never export blockers. Limit visual timeline width to 24,000 px and derive both
block widths and playhead coordinates from the same scale. No added server APIs,
remote writes, installation, persistent thumbnail files or altered originals.

Export still sends only the ordered list of whole-clip IDs to the existing
verified stream-copy endpoint. Preview seeking and thumbnail scaling cannot trim
or reduce quality of exported media.

Evidence: focused time mapping/reorder tests, existing bulk/queue tests, 47
Python joiner/security tests, rebuilt Chrome and WebKit checks for real thumbnail
pixels, duration ratios, native block reorder, live ruler scrubbing, keyboard
seeking, zoom, playback, removal/export and mobile layout. WebKit's missing first
pointerdown after native drag was reproduced and fixed with mouse-scrub/click
handling. Exported synthetic and existing H3 videos retain exact encoded packet
payloads and decoded video frames. No original video was changed or job generated.
