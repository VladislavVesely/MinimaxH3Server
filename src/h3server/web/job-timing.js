/* Lifecycle durations, independent of the video's playback length. */
(function (root) {
  const valid = x => typeof x === 'number' && Number.isFinite(x);
  function elapsed(start, end) {
    return valid(start) && valid(end) && end >= start ? end - start : null;
  }
  function duration(seconds) {
    if (!valid(seconds)) return 'unavailable';
    const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return h ? `${h}h ${m}m ${s % 60}s` : m ? `${m}m ${s % 60}s` : `${s}s`;
  }
  function timingText(job, sinceSnapshot = 0) {
    const t = job.timing;
    if (!t) return 'Timing unavailable (not recorded)';
    const live = ['queued', 'running'].includes(job.status);
    const end = live && valid(t.observed_at) ? t.observed_at + Math.max(0, sinceSnapshot) : t.finished_at;
    const wait = elapsed(t.queued_at, t.started_at ?? end);
    if (job.status === 'queued') return `Waiting ${duration(wait)}`;
    const run = !valid(t.started_at) && valid(t.queued_at) ? 'Not started' : `Run ${duration(elapsed(t.started_at, end))}`;
    return `${run} · Wait ${duration(wait)} · Total ${duration(elapsed(t.queued_at, end))}`;
  }
  const api = { timingText };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.H3JobTiming = api;
})(typeof window !== 'undefined' ? window : globalThis);
