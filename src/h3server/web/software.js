'use strict';
(() => {
  let epoch = 0;
  const hosts = new Map(), groups = new Map();
  const $ = id => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
  const core = name => /^(torch|torchvision|torchaudio|av|sageattention|transformers|diffusers|triton|h3server|cuda-toolkit|nvidia-cuda-runtime(?:-cu\d+)?|nvidia-cudnn(?:-cu\d+)?|nvidia-nccl(?:-cu\d+)?)$/.test(name.toLowerCase().replaceAll('_','-'));
  function list(rows) {
    const dl = node('dl'); dl.className = 'software-list';
    for (const row of rows) {
      const term = node('dt', row.name), value = node('dd', row.version || 'Unknown');
      if (row.evidence) value.append(node('small', row.evidence));
      if (row.pinned) value.append(node('small', 'Pinned revision: ' + row.pinned));
      dl.append(term, value);
    }
    return dl;
  }
  function details(title, rows) {
    const d = node('details'); d.className = 'observations'; d.append(node('summary', `${title} (${rows.length})`), list(rows)); return d;
  }
  function group(id) {
    if (!groups.has(id)) {
      const el = node('details'); el.className = 'component-observations';
      const summary = node('summary', 'Observed software & receipts');
      el.append(summary, node('p', 'Read-only observations, not runtime validation. Dependencies may be shared; recorded pins and receipts are not reverified.'));
      groups.set(id, {el, summary});
    }
    return groups.get(id).el;
  }
  function route() {
    for (const [id, host] of hosts) {
      const entry = groups.get(id);
      if (entry) {
        entry.summary.textContent = 'Observed software & receipts';
        if (host.firstElementChild !== entry.el) host.replaceChildren(entry.el);
      } else host.replaceChildren();
    }
    const remaining = [];
    for (const [id, entry] of groups) if (!hosts.has(id)) {
      entry.summary.textContent = `${id} · observed software & receipts`;
      remaining.push(entry.el);
    }
    const fallback = $('component-software');
    if (fallback) { fallback.replaceChildren(...remaining); fallback.hidden = remaining.length === 0; }
  }
  function attach(id, host) { hosts.set(id, host); route(); }
  function detach(id) { hosts.delete(id); route(); }
  function clear() {
    epoch++; groups.clear();
    for (const host of hosts.values()) host.replaceChildren();
    const fallback = $('component-software');
    if (fallback) { fallback.replaceChildren(); fallback.hidden = true; }
    $('software-content')?.replaceChildren();
    if ($('software-status')) $('software-status').textContent = 'Scan the target to read installed versions.';
  }
  function render(data) {
    groups.clear();
    const box = $('software-content'); box.replaceChildren();
    box.append(node('p', 'Read-only metadata, not a CUDA usability or generation test. System software may predate this tool; installation ownership is unknown.'));
    box.append(details('Observed system tools', data.tools || []), details('All observed system packages — ownership unknown', data.system_packages || []));
    for (const env of data.environments || []) {
      const section = node('section'); section.className = 'software-environment';
      section.append(node('h4', `${env.profile} · ${env.id.slice(0,12)}`));
      const summary = [{name:'Python (observed interpreter)',version:env.python},
        {name:'Python recorded at environment creation',version:env.python_recorded},
        {name:'PyTorch CUDA build target (not driver capability)',version:env.cuda_built},
        ...(env.packages || []).filter(p => core(p.name)).map(p => ({...p, name:({torch:'PyTorch',torchvision:'Torchvision',av:'PyAV',sageattention:'SageAttention','cuda-toolkit':'CUDA toolkit Python package (not nvcc on PATH)','nvidia-cuda-runtime':'CUDA runtime package'})[p.name.toLowerCase()] || p.name}))];
      if (env.model_pin) summary.push({name:'Runtime model pin (weights not reverified)',version:env.model_pin});
      section.append(list(summary), details('All installed Python packages', env.packages || []));
      if (env.sources?.length) section.append(details('Managed source revisions (observed HEAD versus pin)', env.sources));
      group(env.profile || 'Unmatched runtime').append(section);
    }
    const receipts = new Map();
    for (const row of data.components || []) {
      // Collector receipt filenames have a known optional-component prefix.
      // Model registration names are exact profile IDs. Do not infer ownership
      // of arbitrary OS packages or assign unknown archived records to a model.
      const id = /^(sageattention|turbo|sparse|reference).*receipt/.exec(row.name)?.[1] || row.name;
      if (!receipts.has(id)) receipts.set(id, []);
      receipts.get(id).push(row);
    }
    for (const [id, rows] of receipts) group(id).append(details('Registered artifacts and component receipts — not reverified', rows));
    if (!data.environments?.length) box.append(node('p', 'No managed runtime environments observed. Missing or interrupted records may make this inventory incomplete.'));
    for (const issue of data.issues || []) box.append(node('p', issue));
    route();
    $('software-status').textContent = (data.observed_at ? 'Observed ' + data.observed_at : 'Observation unavailable') + '. Refresh with Scan hardware; no automatic polling.';
  }
  async function load(request) {
    const current = ++epoch;
    $('software-status').textContent = 'Reading installed versions on the authenticated target…';
    try { const data = await request(); if (current === epoch) render(data); }
    catch { if (current === epoch) $('software-status').textContent = 'Installed software could not be observed. Scan again when connected; no versions assumed.'; }
  }
  window.H3Software = {clear, load, render, core, attach, detach};
})();
