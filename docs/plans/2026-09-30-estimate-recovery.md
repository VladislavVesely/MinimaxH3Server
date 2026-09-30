# Recover and reuse measured estimates

Investigation found three independent limitations:

- A restored page never set pageActive back to true after pagehide. Replaying
  pagehide/pageshow in a native browser reproduced an estimate response arriving
  successfully while the UI remained at Checking measurements indefinitely.
- The input-only estimate key never changed when a new successful run saved a
  measurement. Polling could therefore retain an obsolete no-samples answer.
- Server matching required an identical prompt-length bucket, so prompt-only
  changes could exclude otherwise identical measured configurations.

Restore read-only polling and timing ticks on persisted pageshow. Key estimates
by prompt-length bucket rather than prompt wording, completed-job membership and
residency, while preserving deduplication across ordinary dashboard polls. Abort
superseded lookups and apply a 30-second deadline with an explicit retry message.

Prefer exact server matches. On a miss, select the nearest measured prompt-length
bucket whose canonical configuration, hardware/software, cache and actual
attention route still match. Return the original measured statistics, not an
invented/scaled duration; label the prompt-length approximation in the UI. Never
combine cold/warm classes or reuse deleted samples.

Validation: six focused JS checks; 59 performance/deletion/resident/security
Python checks; Chrome/WebKit fixture checks for completion-driven refresh,
prompt-only reuse, approximation labels, restored-page lifecycle and no generation
POSTs. The real server returned a response before and after the lifecycle repro;
the repaired UI no longer drops it. At inspection time its retained jobs/samples
were both zero; no old durations were reconstructed. The performance module was
deployed with exact prior-source guard and idle reject-on-busy maintenance, then
hash-checked and exercised in an isolated temporary statistics store. No generation,
installation or original-file deletion was used. The idle service restart releases
resident memory normally; models and durable files remain intact.
