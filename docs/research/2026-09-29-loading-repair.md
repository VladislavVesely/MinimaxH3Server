# Actual target loading failure and repair

The first real ConvRot INT8 generation failed inside `safetensors.safe_open`
with `SafetensorError: incomplete metadata, file not fully covered`.
Actual on-disk sizes and SHA256 matched the pinned publisher artifacts; this was
not a truncated download. Parsed tensor offsets ended before EOF:

| Artifact | Unreferenced trailer | SHA256 of corrected file |
| --- | ---: | --- |
| INT8 diffusion | 72 bytes | `e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a` |
| Video VAE | 61 bytes | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| Audio VAE | 61 bytes | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |

`backends/safetensors_compat.py` applies only to three exact source checksums.
Original approved model files stay untouched. Private derived files are copied
under `cache/safetensors-compat/<source-sha>/`, validated by source and derived
checksums and by safetensors before atomic publication. Cached derived files
are checked again before loading. This consumes about 26.8 GB additional disk;
no tensor data is altered and no network/model download is involved.

On the actual RTX4090 target, diffusion, encoder, video VAE and audio VAE all
loaded successfully together after this correction. This is loading acceptance,
not a completed generation/MP4, performance, or peak-memory claim.

Pruned workers now frame bounded Python console output and concrete exception
types/messages as job events. The scheduler redacts prompts/credential-like
content instead of discarding every message. Process failures include exit code
or signal. Unframed native C/C++ stderr is still discarded. Prior job diagnosis
was appended without changing the failed job's status or claiming generation
success. The target's application files were hotfixed with checked prior hashes
and backups under `tmp/loading-hotfix-backup`, while the idle user service was
stopped; the service was restarted. No system packages or model originals changed.
