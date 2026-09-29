'use strict';

(() => {
  // Fragments never reach access logs. Remove the one-time token before any IO.
  let bootstrapToken = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', location.pathname);
  const API = '/api/v1/controller';
  const $ = id => document.getElementById(id);
  let csrf = null;
  let ready = false;
  let busy = false;
  let connection = { state: 'disconnected' };
  let capabilities = {};
  let lastTarget = null;
  let scanned = false;
  let planReview = null;
  let setupCursor = 0;
  let jobCursor = 0;
  let runtimeReady = false;
  let backendCapabilities = {};
  let profileCatalog = [];
  let activeProfileKey = null;
  let setupTimer = null;
  let recoveryTimer = null;
  let recoveryEpoch = 0;
  let recoveredCycle = 0;
  let jobTimer = null;
  let setupEpoch = 0;
  let setupRequest = 0;
  let acceptedLaunch = null;
  let dashboardEpoch = 0;
  let dashboardRequest = 0;
  const jobCards = new Map();
  // An accepted detached launch is not evidence that its child has started.
  const LAUNCH_WAIT_MS = 30000;

  function resetSetupPolling() {
    clearTimeout(setupTimer);
    setupEpoch++;
    acceptedLaunch = null;
  }
  function resetDashboardPolling() {
    clearTimeout(jobTimer);
    dashboardEpoch++;
  }

  const messages = {
    bootstrap_invalid: 'Session token is invalid or expired. Restart the local controller and open its new launch URL.',
    session_required: 'Local session unavailable or expired. Open a fresh launch URL from the local controller.',
    csrf_forbidden: 'The local session changed. Reload this page before trying again.',
    host_forbidden: 'This address is not the controller’s exact loopback address. Open its launch URL.',
    origin_forbidden: 'This request did not come from the controller’s own page. Open its launch URL.',
    invalid_request: 'Some connection details are invalid. Check the form and try again.',
    invalid_target: 'Check the SSH host, port (1–65535) and account username.',
    connection_failed: 'Could not reach the SSH server. Check the address, port and network, then reconnect.',
    host_key_changed: 'The server’s host key has changed. Connection blocked. Verify the change with your administrator; do not bypass this warning.',
    state_unavailable: 'The controller’s private host-key trust store is unavailable. Check local permissions and platform support; trust cannot be bypassed.',
    approval_expired: 'The fingerprint challenge expired. Reconnect and compare the new challenge before approving.',
    fingerprint_mismatch: 'Fingerprint approval did not match. Refresh the connection and verify the server again.',
    approval_not_pending: 'No fingerprint approval is pending. Refresh the connection state.',
    trust_required: 'The host key is unknown. Automatic recovery stopped. Reconnect and verify the fingerprint with a trusted source before approving it.',
    recovery_in_progress: 'Automatic setup recovery is in progress. You can disconnect to cancel.',
    reconnect_timeout: 'Automatic reconnection timed out after 120 seconds. Reconnect, authenticate and restore saved setup manually. No operation was repeated.',
    recovery_failed: 'Setup recovery failed. Reconnect and restore the saved checkpoint; no operation was repeated.',
    recovery_binding_changed: 'The saved target or setup digest changed. Automatic recovery stopped. Review the target and restore manually.',
    authentication_failed: 'Authentication failed. The password was cleared. Reconnect and enter your credentials again.',
    authentication_required: 'SSH authentication is required. Reconnect and sign in again.',
    authentication_not_pending: 'Authentication is not pending. Refresh the connection state.',
    invalid_credentials: 'The password is invalid. Enter a nonempty password without null characters.',
    already_connected: 'A connection already exists. Refresh its state, or disconnect before changing servers.',
    invalid_remote_home: 'The server’s home directory could not be read. Check the SSH account and retry the scan.',
    fresh_prerequisite_approval_required: 'Prerequisite recovery requires fresh approval. Review the restored plan and re-enter a sudo password if required; no sudo password was retained.',
    release_unavailable: 'Launch deployment with a verified release wheel and trusted SHA256 pin. No artifact is bundled in this source-only session.',
    approval_required: 'Fresh, exact plan, license, eligibility and service approvals are required. Review the plan again.',
    remote_unavailable: 'Remote daemon unavailable. Reauthenticate after disconnect or reboot, then restore setup and replay progress. No readiness assumed.',
    deployment_operation_failed: 'The target rejected or could not execute this operation. Replay progress; restore the matching release and reauthenticate if needed.',
    update_prerequisite_maintenance_required: 'Update requires OS/driver changes. Complete these with your administrator in a separate maintenance window, then rescan.',
    operation_failed: 'Operation failed. Check target permissions, exact approvals and the saved checkpoint; no completion is assumed.',
    backend_not_ready: 'The local runtime or verified model is not ready. Review diagnostics before submitting.',
    insufficient_storage: 'Insufficient server disk space. Free space or adjust retention before retrying.',
    queue_full: 'The server job queue is full. Wait for existing jobs or cancel one explicitly.',
    output_unavailable_or_expired: 'This output expired or is unavailable. It cannot be previewed or downloaded.',
    unsupported_request: 'The requested settings or managed LoRA are unsupported by this runtime.',
    network_unavailable: 'The local controller could not be reached. No success is assumed. Restore it, then refresh the connection; restart and use a new launch URL if needed.',
  };

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function log(message) {
    const item = node('li');
    const now = new Date();
    const time = node('time', now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    time.dateTime = now.toISOString();
    item.append(time, node('span', message));
    $('activity-log').append(item);
    // Session summaries only: never request bodies, credentials or wire errors.
    if ($('activity-log').children.length > 50) $('activity-log').firstElementChild.remove();
  }

  function showError(error, focus = true) {
    const message = messages[error.code] || 'The controller could not complete this operation. Refresh the connection and retry. No success is assumed.';
    $('error').textContent = message;
    $('error').hidden = false;
    if (focus) $('error').focus();
    log(message);
  }

  async function request(path, body) {
    let response;
    try {
      response = await fetch(API + path, {
        method: body === undefined ? 'GET' : 'POST',
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw { code: 'network_unavailable' };
    }
    let data;
    try { data = await response.json(); } catch { throw { code: 'invalid_response' }; }
    if (!response.ok) {
      const code = data.error?.code || data.detail || (typeof data.error === 'string' ? data.error : null);
      if (code === 'session_required' || code === 'csrf_forbidden') {
        ready = false;
        csrf = null;
        $('session').textContent = 'Local session unavailable';
      }
      throw { code };
    }
    return data;
  }

  function clearScan() {
    scanned = false;
    $('scan-results').hidden = true;
    $('review-panel').hidden = true;
    $('hardware').replaceChildren();
    $('profiles').replaceChildren();
  }

  function setConnection(data) {
    if (data.state !== connection.state || data.fingerprint !== connection.fingerprint) $('trust-check').checked = false;
    const recovering = ['connecting', 'backoff'].includes(data.recovery?.state);
    clearTimeout(recoveryTimer);
    connection = recovering ? { ...data, state: 'recovering' } : data;
    $('forget-recovery').hidden = !data.recovery?.credential_retained;
    if (recovering) {
      resetSetupPolling(); resetDashboardPolling();
      runtimeReady = false;
      $('sudo-password').value = '';
      $('setup-status').textContent = `Automatic setup recovery: ${data.recovery.state}. Retrying for up to 120 seconds with the SSH credential in local controller memory. No install or reboot command is repeated.`;
      const epoch = recoveryEpoch;
      recoveryTimer = setTimeout(async () => {
        try {
          const status = await request('/status');
          if (epoch === recoveryEpoch) setConnection(status);
        } catch (error) {
          if (epoch === recoveryEpoch) { showError(error, false); setConnection({ state: 'disconnected' }); }
        }
      }, 1000);
    } else {
      recoveryEpoch++;
    }
    if (!recovering && data.state !== 'authenticated') {
      clearScan();
      runtimeReady = false;
      resetSetupPolling(); resetDashboardPolling();
      jobCards.clear(); $('jobs').replaceChildren();
      planReview = null; setupCursor = 0; jobCursor = 0;
      $('plan-review').hidden = true;
      $('sudo-password').value = '';
      $('readiness').textContent = 'SSH disconnected. Reauthenticate and restore saved setup; automatic recovery is unavailable.';
      $('setup-status').textContent = 'Setup observation stopped. Reauthenticate and restore saved setup to replay durable progress.';
    }
    if (data.state !== 'awaiting_authentication') $('password').value = '';
    if (data.recovery?.state === 'failed') showError({ code: data.recovery.error }, false);
    if (data.state === 'authenticated' && data.recovery?.state === 'recovered' && data.recovery.cycle !== recoveredCycle && planReview) {
      recoveredCycle = data.recovery.cycle;
      setupCursor = 0; $('setup-events').replaceChildren();
      $('setup-status').textContent = 'SSH recovered. Replaying the same durable setup; no operation repeated.';
      setupProgress().catch(error => showError(error, false));
    }
    render();
  }

  function render() {
    const state = connection.state;
    const usable = ready && !busy;
    const disconnected = state === 'disconnected';
    const authPending = state === 'awaiting_authentication';
    const trustPending = state === 'awaiting_approval';
    const authenticated = state === 'authenticated';
    const labels = { recovering: 'Automatically reconnecting SSH', disconnected: 'SSH disconnected', awaiting_approval: 'Fingerprint verification needed', awaiting_authentication: 'Host trusted · sign in', authenticated: 'SSH authenticated' };
    $('ssh-state').textContent = labels[state] || 'SSH state unknown';
    $('ssh-state').dataset.state = state;
    $('target-fields').disabled = !usable || !disconnected;
    $('connect').disabled = !usable || !disconnected;
    $('fingerprint-panel').hidden = !trustPending;
    $('auth-form').hidden = !authPending;
    $('fingerprint').textContent = dataText(connection.fingerprint);
    $('key-type').textContent = dataText(connection.key_type);
    $('trust-check').disabled = !usable || !trustPending;
    $('approve').disabled = !usable || !trustPending || !$('trust-check').checked;
    $('authenticate').disabled = !usable || !authPending;
    $('password').disabled = !usable || !authPending;
    $('disconnect').disabled = !usable || disconnected;
    $('reconnect').disabled = !usable || !disconnected || lastTarget === null;
    // Also lets an interrupted controller request be reconciled without resending it.
    $('refresh').disabled = busy;
    $('forget-recovery').disabled = busy;
    $('scan').disabled = !usable || !authenticated || capabilities.scan !== true;
    $('scan-hint').textContent = authenticated
      ? (capabilities.scan === true ? 'Read-only observations only. No installation or CUDA runtime test is performed.' : 'Scanning is unavailable in this controller.')
      : 'Authenticate to enable the read-only scan.';
    for (const id of ['step-connect', 'step-scan', 'step-review']) $(id).removeAttribute('aria-current');
    $(scanned ? 'step-review' : authenticated ? 'step-scan' : 'step-connect').setAttribute('aria-current', 'step');
    $('progress').hidden = !busy;
    $('progress-note').hidden = !busy;
    $('connection').setAttribute('aria-busy', String(busy));
    $('install-profile').disabled = !usable || !profileCatalog.length;
    $('plan').disabled = !usable || !authenticated || !scanned || !$('install-profile').value || $('install-profile').selectedOptions[0]?.disabled;
    for (const id of ['restore-setup', 'setup-refresh', 'dashboard-refresh']) $(id).disabled = !usable || !authenticated;
    const consent = $('license-check').checked && $('eligibility-check').checked;
    $('install').disabled = !usable || !authenticated || !planReview || !consent || !$('system-check').checked || !!planReview?.plan.blockers.length;
    $('reboot').disabled = !usable || !authenticated || !planReview || !$('reboot-check').checked || !consent;
    $('download-model').disabled = !usable || !authenticated || !planReview || !consent || !$('download-check').checked;
    for (const id of ['activate', 'repair', 'resume-setup', 'cancel-download']) $(id).disabled = !usable || !authenticated || !planReview;
    $('generation-fields').disabled = !usable || !authenticated || !runtimeReady;
    for (const button of $('jobs').querySelectorAll('button')) button.disabled = !usable || !authenticated;
    $('lora-field').hidden = backendCapabilities.lora?.status !== 'supported' || $('generation-profile').value.includes('int8');
  }

  async function refresh() {
    const data = await request('/status');
    csrf = data.csrf_token;
    capabilities = data.capabilities;
    ready = true;
    $('session').textContent = 'Local session secured';
    setConnection(data);
  }

  async function perform(label, action) {
    if (busy) return;
    busy = true;
    $('error').hidden = true;
    $('operation').textContent = label;
    log(label);
    render();
    try {
      await action();
      $('operation').textContent = 'Idle · no operation running.';
    } catch (error) {
      $('password').value = '';
      // Reconcile after failures; never assume the previous SSH session survived.
      try { await refresh(); } catch (refreshError) {
        ready = false;
        setConnection({ state: 'unknown' });
        $('session').textContent = 'Local session unavailable';
        if (refreshError.code === 'session_required') error = refreshError;
      }
      $('operation').textContent = 'Operation failed. Review the message and retry.';
      showError(error);
    } finally {
      busy = false;
      render();
    }
  }

  function dataText(value) {
    return value === null || value === undefined || value === '' ? 'Unknown' : String(value);
  }
  function bytes(value) {
    if (value === null || value === undefined) return 'Unknown';
    return (value / (1024 ** 3)).toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' GiB';
  }
  function booleanObservation(value) {
    return value === true ? 'Reported usable' : value === false ? 'Reported unusable' : 'Unknown · not runtime-tested';
  }
  function card(title, value, detail) {
    const item = node('div', undefined, 'hardware-card');
    item.append(node('h4', title), node('p', value));
    if (detail) item.append(node('small', detail));
    $('hardware').append(item);
  }
  function list(parent, values) {
    for (const value of values) parent.append(node('li', value));
  }
  function detail(title, values) {
    const details = node('details');
    const items = node('ul');
    list(items, values);
    details.append(node('summary', title), items);
    return details;
  }

  function exactBytes(value) {
    return value === null || value === undefined ? 'Unknown' : `${value.toLocaleString('en-US')} bytes (${bytes(value)})`;
  }
  function profileLabel(name) {
    return profileCatalog.find(p => p.profile_name === name)?.label || name;
  }
  function selectionSummary(parent, selection, inventory = true) {
    parent.replaceChildren();
    parent.append(node('h4', `${selection.label} · experimental`),
      node('p', `Selected weight download: ${exactBytes(selection.weight_bytes)}`));
    if (!selection.artifact_set_digest) {
      parent.append(node('p', 'Legacy original snapshot · explicit heavyweight alternative. Pruned weights are not used by this profile.'));
      return;
    }
    parent.append(node('p', selection.publisher_vram_guidance),
      node('p', 'Host RAM: unknown / unmeasured. Peak VRAM, speed and generation quality are unmeasured; source-inspected, not inference-certified.'),
      node('p', `Bundled tokenizer: ${exactBytes(selection.tokenizer_bytes)} (included once with runtime source, not a second weight download). Weights + tokenizer: ${exactBytes(selection.selected_bytes)}.`),
      node('p', 'Runtime/dependency download and installed size: unknown, additional. Fresh weight staging uses per-file rename, no second cache copy. Other snapshots, stale partials, runtime and outputs need extra disk.'));
    if (selection.required_gpu_family) parent.append(node('p', 'Blackwell only: positive GPU identification required. Ada / RTX 4090 is incompatible; unknown devices are not eligible.'));
    if (inventory) {
      const artifacts = node('ul');
      const roles = {diffusion_model: 'Diffusion model', text_encoder: 'Text encoder (INT4 ConvRot, not NVFP4)', video_vae: 'Video VAE', audio_vae: 'Audio VAE'};
      for (const artifact of selection.artifacts) {
        const item = node('li', `${roles[artifact.role]}: ${artifact.path} — ${exactBytes(artifact.size_bytes)}. Source: ${artifact.repository} @ ${artifact.revision}`);
        item.append(detail('Pinned URL and SHA256', [artifact.url, artifact.sha256]));
        artifacts.append(item);
      }
      parent.append(artifacts, detail('Exact artifact-set approval digest', [selection.artifact_set_digest]));
    }
  }
  function renderCatalog(data) {
    const previous = $('install-profile').value;
    profileCatalog = data.profiles;
    $('install-profile').replaceChildren();
    for (const profile of profileCatalog) {
      const option = node('option', profile.label + (profile.required_gpu_family ? ' · Blackwell only' : ' · experimental'));
      option.value = profile.profile_name;
      option.disabled = !!profile.required_gpu_family && profile.gpu_eligible !== true;
      $('install-profile').append(option);
    }
    if ([...$('install-profile').options].some(o => o.value === previous && !o.disabled)) $('install-profile').value = previous;
    $('unavailable-variants').replaceChildren();
    list($('unavailable-variants'), data.unavailable.map(p => p.reason));
    showSelection();
  }
  function showSelection() {
    const selected = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    if (selected) selectionSummary($('selected-profile'), selected);
  }
  $('install-profile').addEventListener('change', () => {
    resetSetupPolling(); planReview = null;
    $('plan-review').hidden = true;
    for (const id of ['license-check', 'eligibility-check', 'system-check', 'download-check']) $(id).checked = false;
    $('sudo-password').value = '';
    showSelection(); render();
  });

  function renderScan(data) {
    clearScan();
    renderCatalog(data);
    const hardware = data.probe.hardware;
    card('Operating system', `${dataText(hardware.distribution)} ${dataText(hardware.distribution_version)}`, `Architecture: ${dataText(hardware.architecture)} · Kernel: ${dataText(data.probe.kernel)}`);
    card('Processor', dataText(hardware.cpu), `Logical CPUs: ${dataText(hardware.cpu_count)}`);
    card('System memory', bytes(hardware.ram_bytes), 'Observed RAM, not a model memory-fit guarantee.');
    const gpus = hardware.gpus;
    const gpuText = gpus?.length ? gpus.map(gpu => `${dataText(gpu.name)} — ${bytes(gpu.vram_bytes)} VRAM`).join('\n')
      : hardware.gpu_inventory_complete ? 'No GPUs observed' : 'Unknown';
    card('Graphics', gpuText, hardware.gpu_inventory_complete ? 'Inventory reported complete. VRAM is per device.' : 'Inventory incomplete. Unobserved GPUs may exist.');
    card('NVIDIA driver', dataText(hardware.driver_version), `Driver CUDA compatibility: ${dataText(data.probe.cuda.driver_compatibility_version)} (not a runtime test)`);
    card('CUDA runtime', booleanObservation(hardware.cuda_available), `Toolkit: ${dataText(data.probe.cuda.toolkit_version)}. Toolkit presence does not prove runtime usability.`);
    card('Free disk', bytes(hardware.disk_free_bytes), `Measured at ${dataText(data.probe.disk_path)}. Model and staging budgets are compared separately.`);
    card('Service manager', dataText(hardware.service_manager), 'Observation only; no service has been installed.');
    $('observation-notes').replaceChildren();
    list($('observation-notes'), [...data.probe.issues, ...hardware.evidence]);
    if (!$('observation-notes').children.length) list($('observation-notes'), ['No additional observation notes were reported.']);
    $('compatibility').textContent = data.compatibility;
    for (const recommendation of data.recommendations) {
      const item = node('article', undefined, 'profile');
      const selection = profileCatalog.find(p => p.profile_name === recommendation.profile_name);
      item.append(node('h3', selection.label));
      if (selection.artifact_set_digest) {
        item.append(node('p', selection.artifacts[0].repository));
        const summary = node('div'); selectionSummary(summary, selection, false); item.append(summary);
      }
      item.append(node('span', `Assessment: ${recommendation.status}`, 'profile-status'));
      item.append(node('p', `Required free disk: ${bytes(recommendation.disk?.required_bytes)}`, 'disk-estimate'));
      item.append(detail('Assessment reasons', recommendation.reasons));
      item.append(detail('Research & disk estimate evidence', [...recommendation.evidence, ...(recommendation.disk?.evidence || [])]));
      $('profiles').append(item);
    }
    if (!data.recommendations.length) $('profiles').append(node('p', 'No profiles are available from this controller.'));
    $('scan-results').hidden = false;
    $('review-panel').hidden = false;
    scanned = true;
  }

  async function connect(target) {
    $('password').value = '';
    clearScan();
    lastTarget = target;
    setConnection(await request('/connect', target));
    log(connection.state === 'awaiting_approval' ? 'SSH handshake received. Fingerprint approval required; no password sent.' : 'Stored host key matched. Password authentication required.');
    (connection.state === 'awaiting_approval' ? $('fingerprint-heading') : $('auth-heading')).focus();
  }

  $('connect-form').addEventListener('submit', event => {
    event.preventDefault();
    perform('Connecting to SSH; no credentials sent…', () => connect({ host: $('host').value.trim(), port: Number($('port').value), username: $('username').value.trim() }));
  });
  $('trust-check').addEventListener('change', render);
  $('approve').addEventListener('click', () => {
    if (!$('trust-check').checked) return;
    perform('Saving explicit host-key approval…', async () => {
      setConnection(await request('/approve', { fingerprint: connection.fingerprint }));
      log('Host fingerprint approved and saved. No password has been sent yet.');
      $('auth-heading').focus();
    });
  });
  $('auth-form').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !ready) return;
    // Do not retain credentials in state. Clear even if authentication fails.
    const pending = request('/authenticate', { password: $('password').value, retain_for_setup_recovery: $('retain-recovery').checked });
    $('password').value = '';
    perform('Authenticating with SSH…', async () => {
      setConnection(await pending);
      log('SSH authentication succeeded. Hardware has not been scanned yet.');
      $('scan-heading').tabIndex = -1;
      $('scan-heading').focus();
    });
  });
  $('forget-recovery').addEventListener('click', () => perform('Clearing retained SSH credential…', async () => {
    await request('/recovery/forget', {});
    await refresh();
    log('Setup recovery credential cleared. Reconnection now requires manual authentication.');
  }));
  $('disconnect').addEventListener('click', () => perform('Disconnecting SSH…', async () => {
    $('password').value = '';
    setConnection(await request('/disconnect', {}));
    log('SSH disconnected. Reconnection requires password entry.');
  }));
  $('reconnect').addEventListener('click', () => perform('Reconnecting SSH; authentication will be required…', () => connect(lastTarget)));
  $('refresh').addEventListener('click', () => perform('Checking controller connection state…', async () => {
    await refresh();
    log('Connection state refreshed from the controller.');
  }));
  $('scan').addEventListener('click', () => perform('Scanning hardware with read-only commands…', async () => {
    clearScan();
    const data = await request('/scan', {});
    // A probe may return unknown observations after transport loss; verify state.
    await refresh();
    if (connection.state !== 'authenticated') throw { code: 'authentication_required' };
    renderScan(data);
    log('Read-only scan returned. Review unknown observations and experimental profiles; no generation readiness is proven.');
    $('hardware-heading').focus();
  }));
  function showPlan(data) {
    resetSetupPolling();
    setupCursor = 0; $('setup-events').replaceChildren();
    planReview = data;
    $('install-profile').value = data.plan.profile;
    showSelection();
    selectionSummary($('plan-selection'), data.selection, false);
    selectionSummary($('download-selection'), data.selection);
    $('license-disclosures').replaceChildren();
    list($('license-disclosures'), data.selection.license_disclosures);
    $('plan-review').hidden = false;
    $('plan-document').textContent = JSON.stringify({ plan: data.plan, service: data.service }, null, 2);
    $('license-document').textContent = data.license_text;
    $('setup-status').textContent = 'Plan reviewed; no new operation launched. Replay setup progress to inspect durable checkpoints.';
    for (const id of ['license-check', 'eligibility-check', 'system-check', 'reboot-check', 'download-check', 'cancel-check']) $(id).checked = false;
    $('sudo-password').value = '';
    render();
  }
  function approval() {
    const data = { plan_digest: planReview.plan_digest, license_acknowledged: $('license-check').checked,
      eligibility_confirmed: $('eligibility-check').checked, privileged: $('system-check').checked,
      reboot: false, service_digest: planReview.service_digest };
    if ($('sudo-password').value) data.sudo_password = $('sudo-password').value;
    $('sudo-password').value = '';
    return data;
  }
  function eventLine(record) {
    const event = record.event;
    let text = `${event.stage}: ${event.status}`;
    if (event.message) text += ` — ${event.message}`;
    if (event.progress) text += ` · ${event.progress.completed}${event.progress.total === null ? '' : '/' + event.progress.total} ${event.progress.unit}`;
    return text;
  }
  async function setupProgress() {
    clearTimeout(setupTimer);
    const epoch = setupEpoch;
    const sequence = ++setupRequest;
    const current = () => epoch === setupEpoch && sequence === setupRequest && connection.state === 'authenticated';
    let data;
    try {
      data = await request('/setup/events?after=' + setupCursor);
    } catch (error) {
      if (epoch !== setupEpoch || sequence !== setupRequest) return;
      await refresh();
      if (connection.state === 'recovering' || epoch !== setupEpoch || sequence !== setupRequest) return;
      throw error;
    }
    if (!current()) return;
    if (data.reset) { setupCursor = 0; $('setup-events').replaceChildren(); }
    for (const record of data.events) $('setup-events').append(node('li', eventLine(record)));
    setupCursor = data.cursor;
    while ($('setup-events').children.length > 100) $('setup-events').firstElementChild.remove();
    const setup = data.setups.find(item => item.plan_digest === planReview?.plan_digest);
    $('setup-status').textContent = `Environment checkpoint: ${setup?.state || 'not started'}. Operation: ${data.operation?.action || 'none'} ${data.operation?.state || ''}. Active release: ${data.active_release?.release || 'none'}. Model and real generation are checked separately in the dashboard.`;
    const pending = ['running', 'reconnecting', 'waiting_for_reboot'].includes(setup?.state) || data.operation?.state === 'running';
    const awaitingStart = !setup || ['planned', 'awaiting_approval'].includes(setup.state);
    if (pending || !awaitingStart) acceptedLaunch = null;
    if (acceptedLaunch) {
      acceptedLaunch.delayed = Date.now() >= acceptedLaunch.deadline;
      $('setup-status').textContent += acceptedLaunch.delayed
        ? ' Launch delayed: no running checkpoint observed within 30 seconds. Automatic launch checks paused; use Replay setup progress to check again. No completion is assumed.'
        : ' Launch accepted; waiting for the initial running checkpoint (up to 30 seconds).';
    }
    if (data.has_more || pending || (acceptedLaunch && !acceptedLaunch.delayed)) setupTimer = setTimeout(() => {
      if (!current()) return;
      const nextSequence = setupRequest + 1;
      setupProgress().catch(error => {
        if (epoch === setupEpoch && nextSequence === setupRequest) { clearTimeout(setupTimer); showError(error, false); }
      });
    }, data.has_more ? 100 : 2000);
  }
  async function setupAction(action, data) {
    const epoch = setupEpoch;
    // Keep observing existing work until the new action succeeds. A rejection
    // must neither discard its accepted-launch window nor renew its deadline.
    const result = await request('/setup/' + action, data);
    if (epoch !== setupEpoch || connection.state !== 'authenticated') return;
    resetSetupPolling();
    if (result.review) showPlan(result.review);
    if (result.state === 'accepted') acceptedLaunch = { deadline: Date.now() + LAUNCH_WAIT_MS, delayed: false };
    $('setup-status').textContent = `Request ${result.state}. This is not completion; replaying durable target progress.`;
    await setupProgress();
  }
  for (const id of ['license-check', 'eligibility-check', 'system-check', 'reboot-check', 'download-check', 'generation-profile']) $(id).addEventListener('change', render);
  async function loadPlan(path, data) {
    resetSetupPolling();
    const epoch = setupEpoch;
    const review = await request(path, data);
    if (epoch !== setupEpoch || connection.state !== 'authenticated') return false;
    showPlan(review);
    return true;
  }
  $('plan').addEventListener('click', () => perform('Staging verified release and inspecting exact changes…', () =>
    loadPlan('/setup/plan', { profile: $('install-profile').value, sudo_password_available: $('sudo-available').checked })));
  $('restore-setup').addEventListener('click', () => perform('Restoring durable setup after authentication…', async () => {
    if (await loadPlan('/setup/restore', {})) await setupProgress();
  }));
  $('install').addEventListener('click', () => perform('Launching explicitly approved installation…', () => setupAction('install', approval())));
  $('reboot').addEventListener('click', () => perform('Requesting separately approved reboot…', async () => {
    const data = approval(); data.reboot = $('reboot-check').checked;
    const result = await request('/setup/reboot', data);
    if (result.reauthentication_required) {
      $('setup-status').textContent = 'Reboot request accepted, not completion. Memory retention is off: reconnect, authenticate and restore saved setup manually after the server returns.';
    } else {
      // The controller owns reconnection and digest-bound restore. Only replay
      // durable events here; never resend installation, resume or reboot.
      await refresh();
    }
  }));
  $('resume-setup').addEventListener('click', () => perform('Resuming previously approved stages…', () => setupAction('resume', { plan_digest: planReview.plan_digest })));
  $('activate').addEventListener('click', () => perform('Requesting release activation with rollback…', () => setupAction('activate', {
    plan_digest: planReview.plan_digest, activation_approved: true, policy: $('busy-policy').value, cancel_confirmed: $('cancel-check').checked,
  })));
  $('repair').addEventListener('click', () => perform('Recovering interrupted activation from its durable commit state…', () => setupAction('repair', { plan_digest: planReview.plan_digest, rollback_approved: true })));
  $('download-model').addEventListener('click', () => perform('Starting approved pinned model acquisition…', () => setupAction('download', {
    plan_digest: planReview.plan_digest, artifact_set_digest: planReview.selection.artifact_set_digest,
    license_sha256: planReview.license_sha256, license_acknowledged: $('license-check').checked,
    eligibility_confirmed: $('eligibility-check').checked, download_approved: $('download-check').checked,
  })));
  $('cancel-download').addEventListener('click', () => perform('Requesting model download cancellation…', () => setupAction('cancel-download', { plan_digest: planReview.plan_digest })));
  $('setup-refresh').addEventListener('click', () => perform('Replaying durable setup events…', () => {
    if (acceptedLaunch?.delayed) acceptedLaunch = { deadline: Date.now() + LAUNCH_WAIT_MS, delayed: false };
    return setupProgress();
  }));

  async function jobEvents(current) {
    const response = await fetch(API + '/remote/events?after=' + jobCursor, { credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
    if (!response.ok) throw { code: 'remote_unavailable' };
    const text = await response.text();
    if (!current()) return;
    for (const block of text.split('\n\n')) {
      const fields = Object.fromEntries(block.split('\n').filter(line => line.includes(':')).map(line => [line.slice(0, line.indexOf(':')), line.slice(line.indexOf(':') + 1).trim()]));
      if (fields.event === 'reset') { jobCursor = Number(JSON.parse(fields.data).minimum_cursor); $('job-events').replaceChildren(); }
      if (fields.event === 'job') {
        const record = JSON.parse(fields.data); jobCursor = Number(fields.id);
        $('job-events').append(node('li', eventLine(record)));
      }
    }
    while ($('job-events').children.length > 100) $('job-events').firstElementChild.remove();
  }
  function reconcileJobs(jobs) {
    const present = new Set(jobs.map(job => job.job_id));
    for (const [id, card] of jobCards) {
      if (!present.has(id)) { card.item.remove(); jobCards.delete(id); }
    }
    let nextItem = $('jobs').firstElementChild;
    for (const job of jobs) {
      let card = jobCards.get(job.job_id);
      if (!card) {
        const item = node('article', undefined, 'job-card');
        const status = node('p');
        const output = node('div');
        item.append(node('h4', job.job_id), status, output);
        card = { item, status, output, cancel: null, outputIdentity: null };
        jobCards.set(job.job_id, card);
        $('jobs').insertBefore(item, nextItem);
      }
      nextItem = card.item.nextElementSibling;
      if (card.status.textContent !== job.status) card.status.textContent = job.status;
      if (['queued', 'running'].includes(job.status)) {
        if (!card.cancel) {
          card.cancel = node('button', 'Cancel job', 'secondary'); card.cancel.type = 'button';
          card.cancel.addEventListener('click', () => perform('Cancelling selected job…', async () => {
            await request('/remote/jobs/' + job.job_id + '/cancel', {}); await dashboard();
          }));
          card.item.insertBefore(card.cancel, card.output);
        }
        card.cancel.disabled = busy;
      } else if (card.cancel) { card.cancel.remove(); card.cancel = null; }
      const available = job.status === 'completed' && job.output && !job.output.expired;
      // Output records are immutable except retention expiry. Never detach an
      // unchanged video or reassign src: either can interrupt browser playback.
      const identity = available ? JSON.stringify([job.output.filename, job.output.size_bytes, job.output.media_type])
        : job.output?.expired ? 'expired' : null;
      if (identity === card.outputIdentity) continue;
      card.outputIdentity = identity;
      card.output.replaceChildren();
      if (available) {
        const url = API + '/remote/jobs/' + job.job_id + '/video';
        const video = node('video'); video.controls = true; video.preload = 'metadata'; video.src = url;
        const error = node('p', 'Preview unavailable or output expired. Refresh the dashboard.'); error.hidden = true;
        video.addEventListener('error', () => { error.hidden = false; });
        const link = node('a', 'Download MP4'); link.href = url + '?download=true'; link.download = job.job_id + '.mp4';
        card.output.append(video, link, error);
      } else if (job.output?.expired) card.output.append(node('p', 'Output expired by retention policy.'));
    }
  }
  async function dashboard() {
    clearTimeout(jobTimer);
    const epoch = dashboardEpoch;
    const sequence = ++dashboardRequest;
    const current = () => epoch === dashboardEpoch && sequence === dashboardRequest && connection.state === 'authenticated';
    runtimeReady = false; render();
    const capability = await request('/remote/capabilities');
    if (!current()) return;
    const data = await request('/remote/jobs');
    if (!current()) return;
    backendCapabilities = capability.adapter_capabilities;
    const profiles = capability.profiles || [];
    const profileKey = JSON.stringify([capability.backend, profiles]);
    if (profileKey !== activeProfileKey) {
      activeProfileKey = profileKey;
      $('generation-profile').replaceChildren();
      for (const name of profiles) {
        const option = node('option', profileLabel(name)); option.value = name; $('generation-profile').append(option);
      }
      $('steps').value = capability.service_limits.steps_default;
    }
    runtimeReady = capability.runtime_ready && profiles.length > 0;
    const diagnostic = capability.diagnostic || {};
    const generated = data.jobs.some(job => profiles.includes(job.request?.settings?.profile) && job.status === 'completed' && job.output && !job.output.expired);
    $('readiness').textContent = `Daemon connected. Active backend: ${capability.backend}; profiles: ${profiles.join(', ')}. Environment ready: ${diagnostic.environment_ready === true}. Model files ready: ${diagnostic.model_ready === true}. Runtime ready: ${runtimeReady}. Generation tested: ${generated ? 'completed job available (see output)' : 'not proven'}. ${(capability.not_ready_reasons || []).join(' ')}`;
    reconcileJobs(data.jobs);
    await jobEvents(current);
    if (!current()) return;
    render();
    if (data.jobs.some(job => ['queued', 'running'].includes(job.status))) jobTimer = setTimeout(() => {
      if (!current()) return;
      const nextSequence = dashboardRequest + 1;
      dashboard().catch(error => {
        if (epoch === dashboardEpoch && nextSequence === dashboardRequest) { runtimeReady = false; render(); showError(error, false); }
      });
    }, 2000);
  }
  $('dashboard-refresh').addEventListener('click', () => perform('Checking daemon, model and queued jobs…', dashboard));
  $('generation-form').addEventListener('submit', event => {
    event.preventDefault();
    perform('Submitting local generation job…', async () => {
      const body = { prompt: $('prompt').value, profile: $('generation-profile').value, num_frames: Number($('frames').value),
        width: Number($('width').value), height: Number($('height').value), steps: Number($('steps').value), seed: Number($('seed').value) };
      if (!$('lora-field').hidden && $('lora').value) body.loras = [$('lora').value];
      await request('/remote/jobs', body); await dashboard();
    });
  });
  window.addEventListener('pagehide', () => { $('password').value = ''; $('sudo-password').value = ''; resetSetupPolling(); resetDashboardPolling(); clearTimeout(recoveryTimer); recoveryEpoch++; });

  async function start() {
    busy = true;
    render();
    try {
      if (bootstrapToken) {
        const pending = request('/bootstrap', { token: bootstrapToken });
        bootstrapToken = null;
        csrf = (await pending).csrf_token;
      }
      await refresh();
      renderCatalog(await request('/profiles'));
      $('operation').textContent = 'Ready for a secure SSH connection.';
      log('Local browser session secured. Authenticate, scan and explicitly approve changes before installation.');
    } catch (error) {
      ready = false;
      $('session').textContent = 'Local session unavailable';
      $('operation').textContent = 'A valid local session is required.';
      showError(error);
    } finally {
      bootstrapToken = null;
      busy = false;
      render();
    }
  }
  start();
})();
