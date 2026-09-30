'use strict';
(() => {
  function create({run, remove, refresh}) {
    const $ = id => document.getElementById(id), selected = new Set(), boxes = new Map();
    let jobs = [], enabled = false, deleting = false;
    const stopped = job => ['completed','failed','cancelled'].includes(job.status);
    function render() {
      const eligible = new Set(jobs.filter(stopped).map(j => j.job_id));
      for (const id of selected) if (!eligible.has(id)) selected.delete(id);
      for (const [id, box] of boxes) {
        box.checked = selected.has(id);
        box.disabled = !enabled || deleting || !eligible.has(id);
        box.title = !eligible.has(id) ? 'Cancel this job first and wait for it to stop before deleting.' : 'Select this job for deletion';
      }
      for (const id of ['jobs-tab-current','jobs-tab-history']) { const tab = $(id); if (tab) tab.disabled = deleting; }
      $('jobs-select-all').disabled = !enabled || deleting || !eligible.size;
      $('jobs-clear-selection').disabled = deleting || !selected.size;
      $('jobs-delete-selected').disabled = !enabled || deleting || !selected.size;
      const label = deleting ? 'Deleting…' : `Delete selected (${selected.size})`;
      if ($('jobs-delete-selected').textContent !== label) $('jobs-delete-selected').textContent = label;
    }
    function reset() { selected.clear(); $('jobs-selection-status').textContent = ''; render(); }
    $('jobs-select-all').addEventListener('click', () => {
      if (!enabled || deleting) return;
      for (const job of jobs) if (stopped(job)) selected.add(job.job_id);
      $('jobs-selection-status').textContent = 'Only stopped jobs in this tab are selected. Queued/running jobs must be cancelled first.';
      render();
    });
    $('jobs-clear-selection').addEventListener('click', reset);
    $('jobs-delete-selected').addEventListener('click', async () => {
      if (!enabled || deleting || !selected.size) return;
      const ids = [...selected];
      if (!window.confirm(`Permanently delete ${ids.length} selected jobs, their videos, logs, timing records and unshared references? References used by other saved jobs will be kept. Originals copied into the video joiner are unaffected. This cannot be undone.`)) return;
      deleting = true; render();
      try {
        await run(async () => {
          let deleted = 0; const failed = [];
          for (const id of ids) {
            $('jobs-selection-status').textContent = `Deleting ${deleted + failed.length + 1} of ${ids.length}…`;
            try { await remove(id); selected.delete(id); deleted++; }
            catch (error) {
              failed.push(id.slice(0,8));
              if (['network_unavailable','remote_unavailable','session_required','csrf_forbidden','invalid_response'].includes(error?.code)) break;
            }
          }
          const pending = ids.length - deleted - failed.length;
          $('jobs-selection-status').textContent = `${deleted} deleted; ${failed.length} failed.${pending ? ' ' + pending + ' not attempted after the connection/session failure.' : ''}${failed.length ? ' Failed jobs: ' + failed.join(', ') + '. Failed/unattempted items remain selected. Check the connection and ensure these jobs have fully stopped, then retry.' : ''}`;
          await refresh();
        });
      } finally { deleting = false; render(); }
    });
    return {
      register(id, box) {
        boxes.set(id, box);
        box.addEventListener('change', () => {
          if (!enabled || deleting || box.disabled) { render(); return; }
          if (box.checked) selected.add(id); else selected.delete(id);
          $('jobs-selection-status').textContent = ''; render();
        });
      },
      forget(id) { selected.delete(id); boxes.delete(id); },
      update(visible, available) { jobs = visible; enabled = available; render(); },
      reset,
      isDeleting: () => deleting,
      clear() { selected.clear(); boxes.clear(); jobs = []; reset(); }
    };
  }
  window.H3JobSelection = {create};
})();
