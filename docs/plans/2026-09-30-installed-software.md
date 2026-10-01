# Target installed-software inventory

Approved layout: core versions plus expandable full inventories, grouped by managed runtime; distinguish system observations from tool-managed files. Read-only; no model loading, installation or job interruption.

Implementation:
1. Ship a fixed observer in the controller. Execute over the existing authenticated target transport; do not require a daemon update. Read only owned, no-follow installation records and selected metadata fields. Enumerate actual dist-info versions rather than requested requirements or frozen pins. Isolated `-I -S -B` interpreter version probes avoid site hooks and package imports. Do not import Torch or initialize CUDA.
2. Report each managed environment's observed/recorded Python, installed packages, Torch CUDA build target, observed source HEAD and pinned revision. Separately report registered model/component pins without claiming weight revalidation. Report system tool/package versions with unknown installation ownership, not an invented claim that the tool installed them.
3. Authenticated GET `/software`, separate from hardware scanning. UI loads after a successful scan, preserves other workspaces, fences stale results on target/scan changes, renders metadata as text, and shows incomplete/unavailable observations honestly. No periodic polling.
4. Focused tests: actual metadata versus stale freeze; no imports/writes; symlink rejection; CRLF/group-writable package metadata (observation only, not a trust receipt); authenticated target-bound API; text rendering and stale-response fencing. Real target read-only observation and Chrome/WebKit acceptance. Build a new immutable controller destination; retain all existing private joiner workspaces.

Limits: eight environments, bounded directory/package/system inventories, 8 KiB metadata headers, bounded command output and per-command/overall observation deadlines. System package listing uses dpkg; unsupported package managers are explicitly unavailable. Missing CLI tools do not imply bundled Python libraries are absent. No version is inferred from a requested install alone.
