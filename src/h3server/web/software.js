'use strict';
(() => {
  let epoch = 0;
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
  function clear() {
    epoch++;
    $('software-content')?.replaceChildren();
    if ($('software-status')) $('software-status').textContent = 'Scan the target to read installed versions.';
  }
  function render(data) {
    const box = $('software-content'); box.replaceChildren();
    box.append(node('p', 'Read-only metadata, not a CUDA usability or generation test. System software may predate this tool; installation ownership is unknown.'));
    const tools = details('Observed system tools', data.tools || []); tools.open = true; box.append(tools);
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
      box.append(section);
    }
    if (!data.environments?.length) box.append(node('p', 'No managed runtime environments observed. Missing or interrupted records may make this inventory incomplete.'));
    box.append(details('Registered models and component receipts — not reverified', data.components || []));
    box.append(details('All observed system packages — ownership unknown', data.system_packages || []));
    for (const issue of data.issues || []) box.append(node('p', issue));
    $('software-status').textContent = (data.observed_at ? 'Observed ' + data.observed_at : 'Observation unavailable') + '. Refresh with Scan hardware; no automatic polling.';
  }
  async function load(request) {
    const current = ++epoch;
    $('software-status').textContent = 'Reading installed versions on the authenticated target…';
    try { const data = await request(); if (current === epoch) render(data); }
    catch { if (current === epoch) $('software-status').textContent = 'Installed software could not be observed. Scan again when connected; no versions assumed.'; }
  }
  window.H3Software = {clear, load, render, core};
})();
