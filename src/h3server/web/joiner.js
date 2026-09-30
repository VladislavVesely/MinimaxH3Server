'use strict';
(() => {
  const LIMIT = 64 * 1024 * 1024;
  const messages = {
    joiner_tools_missing:'Install FFmpeg and ffprobe locally, then reload the controller. Nothing is installed automatically.',
    joiner_busy:'Another joiner operation is running. Wait for it to finish.',
    joiner_format_unsupported:'Use MP4 with one H.264/H.265 video track and optionally one AAC audio track (up to one hour per clip). Other tracks/formats are not silently converted or dropped.',
    joiner_media_invalid:'This MP4 could not be read or joined safely. Originals are unchanged; try another compatible clip.',
    joiner_keyframe_required:'Each clip must begin with a video keyframe for an independent, lossless join.',
    joiner_file_limit:'Each clip must be no larger than 64 MiB.',
    joiner_clip_limit:'The workspace holds at most 32 clips. Remove unused clips or clear the workspace.',
    joiner_storage_limit:'The temporary workspace limit is 1 GiB, including exports. Download what you need, then clear the workspace.',
    joiner_clip_missing:'A temporary clip is no longer available. Add it again.',
    joiner_selection_invalid:'Select between 2 and 32 clips to export.',
    joiner_duration_limit:'The total timeline must be at most one hour. Export shorter groups of clips.',
    joiner_export_failed:'Export verification failed. No partial result was kept; originals are unchanged.',
    joiner_timeout:'Media processing exceeded its time limit. Try fewer or shorter clips.',
    session_required:'The local session expired. Reopen the controller.',
    csrf_forbidden:'The local session needs refreshing. Reload the page.',
    body_too_large:'Each clip must be no larger than 64 MiB.'
  };
  function errorMessage(error) {
    if (error?.code === 'joiner_incompatible') return 'Cannot join without conversion. Clips differ in: ' + (error.fields || []).join(', ') + '. Remove incompatible clips; no quality-reducing fallback will be used.';
    return messages[error?.code] || 'Joiner operation failed. Check the connection and available disk space, then try again.';
  }
  function reorder(items, from, before) {
    const item = items.find(x => x.id === from);
    if (!item || from === before || (before !== null && !items.some(x => x.id === before))) return items.slice();
    const result = items.filter(x => x.id !== from);
    result.splice(before === null ? result.length : result.findIndex(x => x.id === before), 0, item);
    return result;
  }
  function create({api, getCsrf, onChange = () => {}}) {
    const $ = id => document.getElementById(id);
    const player = $('joiner-player');
    let items = [], selected = null, busy = false, available = false, initialized = false;
    let sequence = false, retained = 0, retainedExports = 0, dragging = null;
    const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
    function tell(text) { $('joiner-status').textContent = text; }
    function invalidateExport() { $('joiner-download').hidden = true; $('joiner-download').removeAttribute('href'); }
    function stop() { sequence = false; player.pause(); }
    function select(id) {
      stop(); selected = id;
      const item = items.find(x => x.id === id);
      player.hidden = !item;
      if (item) {
        player.src = api + '/joiner/clips/' + item.id + '/content'; player.load();
        $('joiner-now').textContent = 'Preview: ' + item.label;
      } else { player.removeAttribute('src'); player.load(); $('joiner-now').textContent = 'Add clips to preview them.'; }
      render();
    }
    function render() {
      $('joiner-files').disabled = busy || !available;
      $('joiner-play').disabled = busy || !items.length;
      $('joiner-export').disabled = busy || !available || items.length < 2;
      $('joiner-clear').disabled = busy || (!retained && !retainedExports && !items.length);
      const label = busy ? 'Working…' : 'Export joined video';
      if ($('joiner-export').textContent !== label) $('joiner-export').textContent = label;
      $('joiner-summary').textContent = `${items.length} clips · ${items.reduce((n,x) => n + x.duration, 0).toFixed(2)}s total (approximate) · ${retained} temporary copies, ${retainedExports} exports retained`;
      $('joiner-empty').hidden = items.length > 0;
      const list = $('joiner-timeline'); list.replaceChildren();
      items.forEach((item, index) => {
        const li = el('li'); li.className = 'joiner-clip'; li.draggable = !busy; li.dataset.clip = item.id;
        const heading = el('strong', `${index + 1}. ${item.label}`);
        li.append(heading, el('p', `${item.width}×${item.height} · ${item.duration.toFixed(2)}s · ${item.codec.toUpperCase()} · ${item.audio ? 'with audio' : 'silent'}`));
        const controls = el('div'); controls.className = 'joiner-clip-actions';
        function button(text, action, disabled = false) {
          const b = el('button', text); b.type = 'button'; b.disabled = busy || disabled; b.addEventListener('click', action); controls.append(b); return b;
        }
        button(selected === item.id ? 'Selected' : 'Preview', () => select(item.id)).setAttribute('aria-pressed', String(selected === item.id));
        button('← Earlier', () => move(item.id, items[index-1].id), index === 0).setAttribute('aria-label', `Move ${item.label} earlier`);
        button('Later →', () => move(item.id, items[index+2]?.id || null), index === items.length-1).setAttribute('aria-label', `Move ${item.label} later`);
        button('Remove', () => perform(async () => {
          await request('/joiner/clips/' + item.id + '/remove', {});
          stop(); items = items.filter(x => x.id !== item.id); retained = Math.max(0, retained - 1); invalidateExport();
          if (selected === item.id) select(items[Math.min(index, items.length-1)]?.id || null);
          tell('Removed the temporary copy only. Your original file and History job are unchanged.');
        })).setAttribute('aria-label', `Remove ${item.label}`);
        li.append(controls);
        li.addEventListener('dragstart', e => { if (busy) { e.preventDefault(); return; } dragging = item.id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('application/x-h3-joiner', item.id); });
        li.addEventListener('dragend', () => { dragging = null; });
        li.addEventListener('dragover', e => { e.preventDefault(); });
        li.addEventListener('drop', e => {
          if (e.dataTransfer.files.length) return;
          e.preventDefault(); e.stopPropagation();
          move(e.dataTransfer.getData('application/x-h3-joiner') || dragging, item.id); dragging = null;
        });
        list.append(li);
      });
      onChange();
    }
    function move(from, before) {
      if (busy) return;
      stop(); items = reorder(items, from, before); invalidateExport(); render();
      tell('Timeline order updated. Originals are unchanged.');
    }
    async function request(path, body, binary = false) {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 180000);
      try {
        const response = await fetch(api + path, {method: body === undefined ? 'GET' : 'POST', signal:controller.signal,
          headers:body === undefined ? {} : {'Content-Type': binary ? 'application/octet-stream' : 'application/json', 'X-CSRF-Token':getCsrf()},
          body:body === undefined ? undefined : binary ? body : JSON.stringify(body)});
        const value = await response.json();
        if (!response.ok) throw new Error(errorMessage(value.error));
        return value;
      } finally { clearTimeout(timer); }
    }
    async function perform(action) {
      if (busy) return;
      busy = true; render();
      try { await action(); }
      catch (error) { tell(error.name === 'AbortError' ? 'The request timed out. An export may still be finishing; wait before retrying or clearing the workspace.' : error.message); }
      finally { busy = false; render(); }
    }
    async function upload(file, label) {
      if (file.size > LIMIT) throw new Error(messages.joiner_file_limit);
      if (retained >= 32) throw new Error(messages.joiner_clip_limit);
      tell('Adding ' + label + '…');
      const clip = await request('/joiner/clips', file, true);
      items.push({...clip, label:label.slice(0,240)}); retained++; invalidateExport();
      if (!selected) select(clip.id);
    }
    function addFiles(files) {
      if (!available || busy) return;
      return perform(async () => {
        for (const file of files) await upload(file, file.name || 'Video');
        tell('Clips added. Drag to reorder, or use Earlier / Later. Export checks full stream compatibility.');
      });
    }
    async function init() {
      if (initialized) return;
      initialized = true;
      try {
        const status = await request('/joiner'); available = status.available;
        retained = status.retained_clips; retainedExports = status.retained_exports;
        tell(!available ? messages.joiner_tools_missing : retained || retainedExports ? 'An earlier page left temporary media. Clear workspace to release it before starting again.' : 'Add local MP4 files or use Add to joiner on a History clip.');
      } catch (error) { initialized = false; tell(error.message); }
      render();
    }
    async function addHistory(job) {
      if (!available || busy) return;
      $('joiner').scrollIntoView({behavior:'smooth', block:'start'});
      return perform(async () => {
        tell('Copying the original History video to the local joiner…');
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 180000);
        try {
          const response = await fetch(api + '/remote/jobs/' + encodeURIComponent(job.job_id) + '/video', {signal:controller.signal});
          if (!response.ok) throw new Error('History video unavailable. Check the SSH connection and whether the output still exists.');
          if (Number(response.headers.get('content-length')) > LIMIT) { await response.body.cancel(); throw new Error(messages.joiner_file_limit); }
          const reader = response.body.getReader(), chunks = []; let size = 0;
          try {
            while (true) { const {value, done} = await reader.read(); if (done) break; size += value.length; if (size > LIMIT) throw new Error(messages.joiner_file_limit); chunks.push(value); }
          } finally { await reader.cancel(); }
          await upload(new Blob(chunks, {type:'video/mp4'}), 'History ' + job.job_id.slice(0,8));
          tell('History clip copied. Removing it here never deletes its job or original output.');
        } finally { clearTimeout(timer); }
      });
    }
    $('joiner-files').addEventListener('change', async e => { const files = Array.from(e.target.files); e.target.value = ''; await addFiles(files); });
    for (const id of ['joiner-drop', 'joiner-timeline']) {
      const area = $(id);
      area.addEventListener('dragover', e => { e.preventDefault(); });
      area.addEventListener('drop', e => {
        e.preventDefault();
        if (e.dataTransfer.files.length) addFiles(Array.from(e.dataTransfer.files));
        else if (id === 'joiner-timeline') move(e.dataTransfer.getData('application/x-h3-joiner') || dragging, null);
        dragging = null;
      });
    }
    $('joiner-play').addEventListener('click', async () => {
      if (!items.length || busy) return;
      select(items[0].id); sequence = true;
      try { await player.play(); } catch { sequence = false; tell('Your browser could not play this clip. Export may still work without re-encoding.'); }
    });
    player.addEventListener('ended', async () => {
      if (!sequence) return;
      const next = items[items.findIndex(x => x.id === selected) + 1];
      if (!next) { sequence = false; tell('Sequence preview finished.'); return; }
      select(next.id); sequence = true;
      try { await player.play(); } catch { sequence = false; tell('Press Play to continue the preview.'); }
    });
    player.addEventListener('error', () => { if (selected) { sequence = false; tell('This browser cannot load/decode the preview. Check the connection; supported clips can still be exported without re-encoding.'); } });
    $('joiner-export').addEventListener('click', () => perform(async () => {
      stop(); tell('Joining original streams without re-encoding…');
      const result = await request('/joiner/export', {clips:items.map(x => x.id)});
      retainedExports++;
      const link = $('joiner-download'); link.href = api + '/joiner/exports/' + result.id; link.hidden = false;
      tell('Export ready. Video/audio streams were copied without re-encoding. Download to keep the file.');
      link.click();
    }));
    $('joiner-clear').addEventListener('click', () => {
      if (busy || !window.confirm('Clear all temporary joiner clips and exports? Download any exports you want to keep first. Original files and History jobs are not deleted.')) return;
      perform(async () => { stop(); await request('/joiner/clear', {}); items = []; retained = retainedExports = 0; select(null); invalidateExport(); tell('Temporary workspace cleared. Originals are unchanged.'); });
    });
    return {init, addHistory, canAdd:() => available && !busy && retained < 32,
      blocker:() => busy ? messages.joiner_busy : retained >= 32 ? messages.joiner_clip_limit : !available ? 'Local FFmpeg is not ready. Open Video joiner for details.' : 'Copy the original video into the local joiner. The History job is unchanged.'};
  }
  window.H3Joiner = {create, reorder, errorMessage};
})();
