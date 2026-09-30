# Reliable timeline deletion, Space playback and visible scrolling

Reproduced: clicking a filmstrip calls select/render, replacing the focused clip
node. document.activeElement becomes BODY; Delete does nothing and Space scrolls
the page. Right-clicking the replacement clip happens to restore focus. A second
reproduced focus interruption came from automatic read-only hardware discovery
unconditionally focusing its heading while a local edit was underway.

Use a stable timeline keyboard region and explicitly focus it after clip clicks
or drops. Delegate Delete/Backspace and Space to that region, not the document;
ignore held-key repeats and modified shortcuts. Keep Enter for keyboard clip
selection. Space toggles playback from the existing playhead, including continuing
to the next clip; it never resets the selected time. Dragging selects the moved
clip, so immediate Delete targets the moved block. Pause before asynchronous
removal and retain timeline focus for further key presses. Hardware discovery
must not move focus out of an active joiner interaction.

Add an explicit horizontal scrollbar for overflow, synchronized with native
scrolling, zoom, resize and removal. Do not rely on OS overlay scrollbar settings.
Keep native touch/wheel scrolling. Reserve preview aspect ratio so changing video
sources cannot temporarily shift the ruler/control positions (observed in WebKit
as a 448→374→448px height change while metadata arrived).

Evidence: failing native keyboard/scroll checks and focus unit test became green.
Six focused JS checks, 47 Python joiner/security checks, packaged Chrome/WebKit
checks for click/Delete without context menus, key-repeat safety, Backspace,
Space resume/pause at the selected time, cross-clip playback, ruler focus, text
input isolation, drag-then-delete, scrollbar mouse/keyboard/native synchronization,
conditional visibility and mobile layout. Existing filmstrip/export checks also
pass. Only owned synthetic clips were removed. No daemon change or original-file
mutation; preserve existing controller workspaces when opening the updated build.
