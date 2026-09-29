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
  let setup = { state: 'idle', busy: false };
  let pendingInstall = null;
  let pendingRetry = null;
  let inspectedSetup = null;
  let pageActive = true;
  let jobCursor = 0;
  let runtimeReady = false;
  let backendCapabilities = {};
  let profileCatalog = [];
  let activeProfileKey = null;
  let setupTimer = null;
  let jobTimer = null;
  let setupEpoch = 0;
  let setupRequest = 0;
  let dashboardEpoch = 0;
  let dashboardRequest = 0;
  const jobCards = new Map();
  const jobLogs = new Map();
  const jobPhases = new Map();
  let selectedJob = null;
  let currentJobs = [];
  let preferences = { connection: null, install_sage: false, use_sage: false };
  let preferencesSave = Promise.resolve();
  let sageState = { installed: false, busy: false, supported: false };

  function savedTargetMatches() {
    const saved = preferences.connection;
    return saved && lastTarget && saved.host === lastTarget.host && saved.port === lastTarget.port && saved.username === lastTarget.username;
  }
  function showPreferences(value) {
    preferences = value;
    $('install-sage').checked = value.install_sage;
    $('use-sage').checked = value.use_sage;
    if (value.connection) {
      for (const key of ['host', 'port', 'username']) $(key).value = value.connection[key];
    }
    $('forget-connection').hidden = !value.connection;
    $('saved-connection-note').textContent = value.connection ? 'Connection remembered locally in plaintext. Host-key verification is still required.' : '';
  }
  function savePreferences() {
    preferencesSave = preferencesSave.catch(() => {}).then(async () => {
      preferences = await request('/preferences', { install_sage: $('install-sage').checked, use_sage: $('use-sage').checked });
    });
    return preferencesSave;
  }
  function showSage(value) {
    if (value) sageState = value;
    $('sage-install-state').textContent = sageState.busy ? 'Installing and GPU-checking SageAttention…'
      : sageState.installed ? 'SageAttention 1.0.6 installed and GPU-verified.'
      : sageState.error || 'SageAttention is optional and is not installed.';
    $('sage-install-output').hidden = !sageState.log;
    replaceOutput($('sage-install-output'), sageState.log || '');
    $('sage-generation-note').textContent = sageState.installed
      ? 'SageAttention 1.0.6 is available. Supported unmasked attention uses Sage; masked/full-precision layers retain PyTorch.'
      : 'To use SageAttention, select its installation option above and click Install. You can leave it off to use normal attention.';
  }
  async function installSageNow() {
    showSage(await request('/remote/features/sageattention', {}));
    await dashboard();
  }
  async function afterAuthentication() {
    preferences = await request('/preferences');
    $('forget-connection').hidden = !preferences.connection;
    if (capabilities.scan === true && !setup.busy) await scanHardware();
    if (!setup.busy && capabilities.release_available === true) {
      try { await observeSavedSetup(); }
      catch (error) { if (error.code !== 'setup_not_found') throw error; }
    }
    if (setup.installation_ready && !setup.busy) await dashboard();
    if (!setup.busy) $('operation').textContent = 'Connected · saved state refreshed.';
  }
  function syncDuration(source) {
    const frames = $('frames'); const seconds = $('duration');
    if (source === 'seconds' && Number.isFinite(seconds.valueAsNumber) && seconds.valueAsNumber > 0) {
      frames.value = 5 + 17 * Math.min(20, Math.max(7, Math.floor((seconds.valueAsNumber * 24 - 5) / 17 + .5)));
    } else if (source === 'frames' && Number.isFinite(frames.valueAsNumber)) {
      seconds.value = Number((frames.valueAsNumber / 24).toFixed(6));
    }
    $('duration-note').textContent = `${frames.value} frames = ${(Number(frames.value) / 24).toFixed(3)} seconds at 24 fps. H3 supports 124–345 frames in steps of 17; seconds round to the nearest supported length.`;
  }

  function resetSetupPolling() {
    clearTimeout(setupTimer);
    setupEpoch++;
  }
  function resetDashboardPolling() {
    clearTimeout(jobTimer);
    dashboardEpoch++;
  }

  const messages = {
    invalid_checkpoint: 'The saved setup record is incomplete or inconsistent. Keep the checkpoint files and inspect the original plan and deployment record; nothing was started or retried.',
    setup_not_found: 'No saved setup was found on this authenticated target. Scan hardware and use Install to begin.',
    saved_connection_missing: 'No saved connection is available. Enter the SSH details and authenticate once.',
    saved_connection_mismatch: 'Saved credentials belong to a different SSH target. Enter the password for this connection.',
    sage_install_failed: 'Optional SageAttention installation or its GPU check failed. See the component output; model files have not been changed.',
    optional_install_busy_or_unsupported: 'SageAttention requires an idle pruned-profile environment. Wait for jobs to finish before installing it.',
    saved_operation_interrupted: 'The server operation stopped unexpectedly (for example after a reboot). Downloaded partial files are preserved. No new installation or retry was started.',
    saved_operation_failed: 'The saved server operation failed. Its latest measured progress and recorded diagnostics are shown below; downloaded partial files are preserved.',
    matching_release_required: 'This saved setup belongs to an unsupported release. Recovery is blocked; keep its checkpoints and use its matching trusted controller. Nothing was retried.',
    compatible_retry_contract_changed: 'Saved setup cannot be safely recovered: its approved contract or target binding changed. Keep the checkpoints and inspect the original trusted release; nothing was retried.',
    compatible_retry_not_safe: 'Saved setup recovery is blocked: completed-stage receipts, service units, active release or worker locks do not permit safe retry. Stop other controllers, wait for running work and inspect the saved records; nothing was retried.',
    setup_retry_not_safe: 'The failed stage is no longer eligible for safe retry. Inspect saved setup to refresh its checkpoint and specific next action.',
    setup_install_failed: 'Installation stopped. The failed command and latest output are retained below; no readiness is assumed.',
    setup_reauthentication_required: 'SSH is no longer authenticated. Disconnect, reconnect and authenticate, then inspect saved setup. Do not start a new installation to recover a failed stage.',
    setup_sudo_password_rejected: 'The SSH password was not accepted for sudo, or this account’s sudo policy requires administrator attention. No repeated password attempt was made.',
    setup_credential_required: 'Setup needs the SSH password in controller memory. Disconnect, reconnect and authenticate again before Install.',
    setup_in_progress: 'Setup still owns the target. Cancellation has been requested by a connection change; wait until setup stops, then retry that connection action.',
    setup_operation_outcome_unknown: 'The remote operation outcome is unknown. Ask your administrator to inspect its saved checkpoint; do not repeatedly click Install.',
    setup_remote_busy: 'The target has existing or interrupted work. No unrelated job was cancelled. Resolve it before retrying.',
    setup_plan_blocked: 'Setup prerequisites are blocked. Review the reasons below with your administrator.',
    stale_discovery_unproven: 'An older release is staged, but its never-launched state could not be proved. Nothing was recovered or replaced. Stop other controllers and ask your administrator to inspect the saved records with the matching release; do not delete checkpoints.',
    staging_lock_unavailable: 'Another controller or installer owns staging, or its lock could not be verified. Stop other controllers and wait for existing work before retrying Install.',
    staging_lock_lost: 'The staging connection or lock was lost. Reconnect and inspect saved state before retrying; no installation was replayed.',

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
    reconnect_timeout: 'Automatic reconnection timed out after 120 seconds. Disconnect, reconnect and authenticate again. Inspect the saved checkpoint before another Install.',
    recovery_failed: 'Setup recovery failed. Disconnect, reconnect and authenticate again. Inspect the saved checkpoint before another Install.',
    recovery_binding_changed: 'The saved target or setup digest changed. Automatic recovery stopped. Review the target and restore manually.',
    authentication_failed: 'Authentication failed. The password field was cleared; saved credentials are unchanged. Reconnect and enter updated credentials if needed.',
    authentication_required: 'SSH authentication is required. Reconnect and sign in again.',
    authentication_not_pending: 'Authentication is not pending. Refresh the connection state.',
    invalid_credentials: 'The password is invalid. Enter a nonempty password without null characters.',
    already_connected: 'A connection already exists. Refresh its state, or disconnect before changing servers.',
    invalid_remote_home: 'The server’s home directory could not be read. Check the SSH account and retry the scan.',
    release_unavailable: 'Launch deployment with a verified release wheel and trusted SHA256 pin. No artifact is bundled in this source-only session.',
    remote_unavailable: 'Remote daemon unavailable. Reauthenticate after disconnect or reboot, then refresh the dashboard. No readiness assumed.',
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
    $('hardware').replaceChildren();
  }

  function setConnection(data) {
    if (data.state !== connection.state || data.fingerprint !== connection.fingerprint) $('trust-check').checked = false;
    connection = data;
    $('forget-recovery').hidden = !data.recovery?.credential_retained;
    if (data.setup) showSetup(!data.setup.busy && inspectedSetup ? inspectedSetup : data.setup);
    if (data.state !== 'authenticated') {
      runtimeReady = false;
      resetDashboardPolling();
      jobCards.clear(); jobLogs.clear(); jobPhases.clear(); selectedJob = null; currentJobs = [];
      $('jobs').replaceChildren(); $('job-events').textContent = 'Waiting for a job.'; jobCursor = 0;
      if (!setup.busy && setup.state === 'idle') clearScan();
      $('readiness').textContent = 'SSH disconnected. No generation readiness assumed.';
    }
    if (data.state !== 'awaiting_authentication') $('password').value = '';
    render();
  }

  function render() {
    const state = connection.state;
    const usable = ready && !busy;
    const available = usable && !setup.busy;
    const disconnected = state === 'disconnected';
    const authPending = state === 'awaiting_authentication';
    const trustPending = state === 'awaiting_approval';
    const authenticated = state === 'authenticated';
    const labels = { recovering: 'Automatically reconnecting SSH', disconnected: 'SSH disconnected', awaiting_approval: 'Fingerprint verification needed', awaiting_authentication: 'Host trusted · sign in', authenticated: 'SSH authenticated' };
    $('ssh-state').textContent = labels[state] || 'SSH state unknown';
    $('ssh-state').dataset.state = state;
    $('target-fields').disabled = !available || !disconnected;
    $('connect').disabled = !available || !disconnected;
    $('fingerprint-panel').hidden = !trustPending;
    $('auth-form').hidden = !authPending;
    $('fingerprint').textContent = dataText(connection.fingerprint);
    $('key-type').textContent = dataText(connection.key_type);
    $('trust-check').disabled = !usable || !trustPending;
    $('approve').disabled = !usable || !trustPending || !$('trust-check').checked;
    $('authenticate').disabled = !available || !authPending;
    $('remember-connection').disabled = busy;
    $('forget-connection').disabled = busy;
    $('password').required = !savedTargetMatches();
    $('password').placeholder = savedTargetMatches() ? 'Saved locally — leave blank to reuse' : '';
    $('password').disabled = !available || !authPending;
    $('disconnect').disabled = !usable || disconnected;
    $('reconnect').disabled = !available || !disconnected || lastTarget === null;
    // Also lets an interrupted controller request be reconciled without resending it.
    $('refresh').disabled = busy;
    $('forget-recovery').disabled = busy;
    $('scan').disabled = !available || !authenticated || capabilities.scan !== true;
    $('scan-hint').textContent = authenticated
      ? (capabilities.scan === true ? 'Read-only observations only. No installation or CUDA runtime test is performed.' : 'Scanning is unavailable in this controller.')
      : 'Authenticate to enable the read-only scan.';
    for (const id of ['step-connect', 'step-scan', 'step-install']) $(id).removeAttribute('aria-current');
    $(scanned || setup.busy ? 'step-install' : authenticated ? 'step-scan' : 'step-connect').setAttribute('aria-current', 'step');
    $('progress').hidden = !busy;
    $('progress-note').hidden = !busy;
    $('connection').setAttribute('aria-busy', String(busy));
    const optionalOnlyFailure = setup.model_ready && ['sage_install_failed', 'optional_install_busy_or_unsupported'].includes(setup.error);
    $('install-profile').disabled = !available || !profileCatalog.length || !!pendingInstall || (setup.state === 'failed' && !optionalOnlyFailure);
    $('install-sage').disabled = !available || sageState.busy || !!pendingInstall || !!pendingRetry;
    const needsSage = $('install-sage').checked && !sageState.installed;
    const selectedInstall = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    const alreadyInstalled = setup.installation_ready === true && setup.model_ready === true
      && setup.profile === $('install-profile').value && !needsSage;
    const installReason = !ready ? 'Open a valid local controller session to enable Install.'
      : alreadyInstalled ? 'This environment and its model files are already installed and verified. No installation is needed.'
      : sageState.busy ? 'Optional SageAttention installation is running; progress is shown below.'
      : needsSage && currentJobs.some(job => ['queued', 'running'].includes(job.status)) ? 'Wait for the generation queue to become idle before installing SageAttention. Running jobs will not be cancelled.'
      : $('install-sage').checked && selectedInstall && !selectedInstall.runtime_id.startsWith('comfyui-h3-') ? 'SageAttention is available for pruned profiles only. Uncheck it to install a legacy profile.'
      : setup.busy ? 'Setup is already running. Wait for it to finish or cancel further actions.'
      : busy ? 'An operation is in progress. Wait for it to finish.'
      : !authenticated ? 'Authenticate to automatically scan hardware and enable Install.'
      : setup.state === 'failed' && !optionalOnlyFailure ? 'A saved setup failed. Review its diagnostics and retry eligibility below; Install cannot resume a failed stage.'
      : !connection.recovery?.setup_credential_retained ? 'Install needs memory-only setup authorization. Disconnect, reconnect and authenticate. Saved unprivileged retries do not need a sudo password.'
      : !scanned && !pendingInstall ? 'Hardware has not been scanned. Click Scan hardware; if it failed, resolve the scan error first.'
      : !$('install-profile').value ? 'Wait for the model catalog, then choose a profile.'
      : $('install-profile').selectedOptions[0]?.disabled ? 'This profile is not eligible for the observed hardware. Choose an available profile.' : '';
    $('install').disabled = !!installReason;
    $('install-disabled-reason').textContent = installReason;
    $('install-disabled-reason').hidden = !installReason;
    $('inspect-setup').disabled = !available || !authenticated || capabilities.release_available !== true;
    $('retry-setup').hidden = setup.retry?.eligible !== true;
    $('retry-setup').disabled = !available || !authenticated;
    $('retry-setup').textContent = pendingRetry ? 'Retry stage request' : 'Retry failed stage';
    $('install').textContent = alreadyInstalled ? 'Installed' : pendingInstall ? 'Retry Install request'
      : setup.model_ready && needsSage ? 'Install SageAttention' : 'Install';
    $('cancel-setup').hidden = !setup.busy || setup.observing === true;
    $('cancel-setup').disabled = !usable || setup.state === 'cancelling';
    $('setup-refresh').disabled = !usable;
    $('dashboard-refresh').disabled = !available || !authenticated;
    $('generation-fields').disabled = !available || !authenticated || !runtimeReady;
    $('generate').disabled = sageState.busy || ($('use-sage').checked && (!sageState.installed || !sageState.supported));
    for (const button of $('jobs').querySelectorAll('button')) button.disabled = !available || !authenticated;
    $('lora-field').hidden = backendCapabilities.lora?.status !== 'supported' || $('generation-profile').value.includes('int8');
  }

  async function refresh() {
    const data = await request('/status');
    if (!pageActive) return;
    csrf = data.csrf_token;
    capabilities = data.capabilities;
    ready = true;
    $('session').textContent = 'Local session secured';
    setConnection(data);
    if (setup.busy) {
      clearTimeout(setupTimer);
      setupTimer = setTimeout(() => setupProgress(), 700);
    }
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
      $('operation').textContent = setup.busy ? 'Automatic setup running · see phase and checkpoints.' : 'Idle · no operation running.';
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
  function selectionSummary(parent, selection) {
    parent.replaceChildren();
    parent.append(node('h4', `${selection.label} · experimental`),
      node('p', `Selected weight download: ${exactBytes(selection.weight_bytes)}`));
    if (!selection.artifact_set_digest) {
      parent.append(node('p', 'Legacy original snapshot · explicit heavyweight alternative. Pruned weights are not used by this profile. GPU/host memory fit, speed and generation quality are unmeasured here.'));
      return;
    }
    parent.append(node('p', selection.publisher_vram_guidance),
      node('p', 'Host RAM: unknown / unmeasured. Peak VRAM, speed and generation quality are unmeasured; source-inspected, not inference-certified.'),
      node('p', `Bundled tokenizer: ${exactBytes(selection.tokenizer_bytes)} (included once with runtime source, not a second weight download). Weights + tokenizer: ${exactBytes(selection.selected_bytes)}.`),
      node('p', 'Runtime/dependency download and installed size: unknown, additional. Fresh weight staging uses per-file rename, no second cache copy. Other snapshots, stale partials, runtime and outputs need extra disk.'));
    const components = node('ul');
    const roles = {diffusion_model: 'Diffusion model', text_encoder: 'Shared INT4 ConvRot text encoder', video_vae: 'Video VAE', audio_vae: 'Audio VAE'};
    for (const artifact of selection.artifacts) components.append(node('li', `${roles[artifact.role]}: ${exactBytes(artifact.size_bytes)}`));
    parent.append(components);
    if (selection.required_gpu_family) parent.append(node('p', 'Blackwell only: positive GPU identification required. Ada / RTX 4090 is incompatible; unknown devices are not eligible.'));
  }
  function renderCatalog(data) {
    const previous = setup.profile || $('install-profile').value;
    profileCatalog = data.profiles;
    $('install-profile').replaceChildren();
    for (const profile of profileCatalog) {
      const option = node('option', profile.label + (profile.required_gpu_family ? ' · Blackwell only' : ' · experimental'));
      option.value = profile.profile_name;
      option.disabled = !!profile.required_gpu_family && profile.gpu_eligible !== true;
      $('install-profile').append(option);
    }
    if ([...$('install-profile').options].some(o => o.value === previous && (!o.disabled || setup.profile === previous))) $('install-profile').value = previous;
    $('license-document').textContent = data.license_text;
    $('license-pin').textContent = `Pinned license SHA256: ${data.license_sha256}`;
    $('unavailable-variants').replaceChildren();
    list($('unavailable-variants'), data.unavailable.map(p => p.reason));
    showSelection();
  }
  function showSelection() {
    const selected = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    if (!selected) return;
    selectionSummary($('selected-profile'), selected);
    const roles = {diffusion_model: 'Diffusion model', text_encoder: 'Text encoder (INT4 ConvRot)', video_vae: 'Video VAE', audio_vae: 'Audio VAE'};
    const inventory = node('ul');
    $('artifact-details').replaceChildren(inventory);
    for (const artifact of selected.artifacts) {
      inventory.append(node('li', `${roles[artifact.role]}: ${artifact.path} — ${exactBytes(artifact.size_bytes)}. Source: ${artifact.repository} @ ${artifact.revision}`));
      inventory.append(detail('Pinned URL and SHA256', [artifact.url, artifact.sha256]));
    }
    if (selected.artifact_set_digest) $('artifact-details').append(detail('Exact artifact-set digest', [selected.artifact_set_digest]));
    $('license-disclosures').replaceChildren();
    list($('license-disclosures'), selected.license_disclosures);
  }
  $('install-profile').addEventListener('change', () => {
    if (setup.busy || pendingInstall) return;
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
    $('scan-results').hidden = false;
    scanned = true;
  }

  async function connect(target) {
    $('password').value = '';
    clearScan();
    pendingInstall = null; pendingRetry = null; inspectedSetup = null;
    resetSetupPolling();
    showSetup({ state: 'idle', busy: false });
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
      log('Host fingerprint approved and saved.');
      if (savedTargetMatches()) {
        setConnection(await request('/connection/authenticate-saved', {}));
        await afterAuthentication();
      } else $('auth-heading').focus();
    });
  });
  $('auth-form').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !ready) return;
    // Do not retain credentials in state. Clear even if authentication fails.
    const pending = !$('password').value && savedTargetMatches()
      ? request('/connection/authenticate-saved', {})
      : request('/authenticate', { password: $('password').value, reuse_for_setup: true, remember_connection: $('remember-connection').checked });
    $('password').value = '';
    perform('Authenticating with SSH…', async () => {
      setConnection(await pending);
      log('SSH authentication succeeded. Scanning hardware with read-only commands; no installation starts.');
      await afterAuthentication();
    });
  });
  $('forget-connection').addEventListener('click', () => perform('Forgetting saved connection…', async () => {
    showPreferences(await request('/connection/forget', {}));
    $('remember-connection').checked = false;
  }));
  $('remember-connection').addEventListener('change', async () => {
    if (!$('remember-connection').checked) {
      preferences = { ...preferences, connection: null };
      render();
      try { preferences = await request('/connection/forget', {}); $('forget-connection').hidden = true; $('saved-connection-note').textContent = ''; render(); }
      catch (error) { showError(error, false); }
    }
  });
  for (const id of ['install-sage', 'use-sage']) $(id).addEventListener('change', () => {
    render(); savePreferences().catch(error => showError(error, false));
  });
  $('duration').addEventListener('input', () => syncDuration('seconds'));
  $('duration').addEventListener('change', () => syncDuration('frames'));
  $('frames').addEventListener('input', () => syncDuration('frames'));
  $('frames').addEventListener('change', () => {
    $('frames').value = 5 + 17 * Math.min(20, Math.max(7, Math.floor((Number($('frames').value) - 5) / 17 + .5)));
    syncDuration('frames');
  });
  $('forget-recovery').addEventListener('click', () => perform('Clearing retained SSH credential…', async () => {
    await request('/recovery/forget', {});
    await refresh();
    log('Temporary setup credential cleared. Saved connection preferences are unchanged.');
  }));
  $('disconnect').addEventListener('click', () => perform('Disconnecting SSH…', async () => {
    $('password').value = '';
    const result = await request('/disconnect', {});
    pendingInstall = null; pendingRetry = null; inspectedSetup = null; resetSetupPolling();
    showSetup({ state: 'idle', busy: false });
    setConnection(result);
    log('SSH disconnected. Reconnection requires password entry.');
  }));
  $('reconnect').addEventListener('click', () => perform('Reconnecting SSH; authentication will be required…', () => connect(lastTarget)));
  $('refresh').addEventListener('click', () => perform('Checking controller connection state…', async () => {
    await refresh();
    log('Connection state refreshed from the controller.');
  }));
  async function scanHardware() {
    $('operation').textContent = 'Scanning hardware with read-only commands…';
    clearScan();
    const data = await request('/scan', {});
    // A probe may return unknown observations after transport loss; verify state.
    await refresh();
    if (connection.state !== 'authenticated') throw { code: 'authentication_required' };
    renderScan(data);
    log('Read-only scan returned. Review unknown observations and experimental profiles; no generation readiness is proven.');
    $('hardware-heading').focus();
  }
  $('scan').addEventListener('click', () => perform('Scanning hardware with read-only commands…', scanHardware));
  function eventLine(record) {
    const event = record.event;
    let text = `${event.stage}: ${event.status}`;
    if (event.message) text += ` — ${event.message}`;
    if (event.progress) text += ` · ${event.progress.completed}${event.progress.total === null ? '' : '/' + event.progress.total} ${event.progress.unit}`;
    return text;
  }
  const phases = { idle: 'Not started', planning: 'Inspecting and staging', prerequisites: 'Installing prerequisites',
    replanning: 'Rechecking prerequisites', installing: 'Installing environment', rebooting: 'Rebooting',
    reconnecting: 'Reconnecting securely', activating: 'Activating environment', downloading: 'MODEL WEIGHTS · downloading selected files', sage_attention: 'Installing optional SageAttention', completed: 'Completed' };
  const substages = { 'runtime-source': 'Acquiring runtime source', 'python-packages': 'Installing Python packages', 'runtime-checks': 'Checking runtime' };
  function seconds(value) {
    return typeof value === 'number' && Number.isFinite(value) ? `${Math.max(0, Math.floor(value))}s` : 'not reported';
  }
  function replaceOutput(element, text) {
    if (element.textContent === text) return;
    const follow = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
    element.textContent = text;
    if (follow) element.scrollTop = element.scrollHeight;
  }
  function showDiagnostics(diagnostic) {
    $('setup-diagnostics').hidden = !diagnostic;
    if (!diagnostic) return;
    const command = diagnostic.command;
    const downloading = setup.phase === 'downloading';
    $('setup-command').textContent = downloading ? 'MODEL WEIGHTS: selected artifacts (not Python packages).'
      : command?.label || (diagnostic.legacy ? 'Saved installer log · command metadata was not recorded by the previous release.' : 'Waiting for command details.');
    $('setup-timing').textContent = downloading ? 'Model download timing: not reported. Measured file progress appears below when available.'
      : `Command elapsed: ${seconds(command?.elapsed_seconds)} · Last output: ${command?.last_output_age_seconds == null ? 'not reported' : seconds(command.last_output_age_seconds) + ' ago'}.`;
    const failure = command?.error || diagnostic.failure;
    $('setup-failure').hidden = !failure && !(command?.exit_code != null && command.exit_code !== 0);
    $('setup-failure').textContent = `Exit: ${command?.exit_code ?? 'not recorded'}${failure ? ' · ' + failure : ''}`;
    $('setup-auto-retry').hidden = !diagnostic.retry || downloading;
    $('setup-auto-retry').textContent = diagnostic.retry ? `Acquisition retry ${diagnostic.retry.attempt}: ${diagnostic.retry.reason}; backoff ${diagnostic.retry.delay_seconds}s. Pins unchanged.` : '';
    const measured = (setup.snapshot?.events || []).filter(r => r.event.progress).slice(-1)[0];
    replaceOutput($('setup-output'), downloading ? (measured ? eventLine(measured) : 'Waiting for measured model download output.') : diagnostic.tail || 'No command output reported yet.');
    replaceOutput($('setup-log'), diagnostic.tail || 'No output recorded.');
    $('setup-log-reference').textContent = `Target log: ${diagnostic.log_reference || 'not reported'} · sanitized bytes ${diagnostic.start_offset ?? 0}–${diagnostic.offset ?? 0}.`;
  }
  function showSetup(data) {
    setup = data;
    if (data.sage_install) showSage(data.sage_install);
    if (!busy) $('operation').textContent = setup.busy ? 'Automatic setup running · see current phase and live output.' : 'Idle · no operation running.';
    if (setup.request_id === pendingInstall?.request_id) pendingInstall = null;
    if (setup.request_id === pendingRetry?.request_id) pendingRetry = null;
    if (setup.profile && profileCatalog.length && (setup.busy || setup.state === 'failed' || inspectedSetup || !scanned)) {
      $('install-profile').value = setup.profile;
      showSelection();
    }
    $('setup-progress').hidden = !setup.busy;
    const downloadProgress = (setup.snapshot?.events || []).filter(r => r.event.stage === 'model_download' && r.event.progress).slice(-1)[0]?.event.progress;
    if (setup.phase === 'downloading' && downloadProgress?.total > 0) {
      $('setup-progress').max = downloadProgress.total;
      $('setup-progress').value = downloadProgress.completed;
    } else $('setup-progress').removeAttribute('value');
    $('setup-events').replaceChildren();
    for (const record of setup.snapshot?.events || []) $('setup-events').append(node('li', eventLine(record)));
    const checkpoint = setup.snapshot?.setups?.find(item => item.plan_digest === setup.plan_digest);
    $('setup-checkpoint').textContent = `Environment checkpoint: ${checkpoint?.state || 'not started'}. Plan: ${setup.plan_digest || 'not selected'}.`;
    const diagnostic = setup.diagnostics;
    const phase = setup.phase === 'installing' ? substages[diagnostic?.substage] || phases.installing : phases[setup.phase] || setup.phase;
    $('setup-status').textContent = setup.state === 'idle'
      ? 'Installation, model and generation readiness have not been verified.'
      : `${phase} · ${setup.state}. Installation ready: ${setup.installation_ready === true}. Model files ready: ${setup.model_ready === true}. Generation tested: not proven.`;
    $('setup-phase-time').hidden = setup.state === 'idle';
    $('setup-phase-time').textContent = setup.observing
      ? 'Attached to saved server operation. Progress refreshes automatically; no installation was restarted.'
      : `Phase elapsed (controller observation): ${seconds(setup.phase_elapsed_seconds)}. No percentage or ETA is assumed.`;
    showDiagnostics(diagnostic);
    const recovery = $('setup-recovery');
    recovery.hidden = !setup.error && !['cancelled', 'cancelling', 'completed'].includes(setup.state);
    recovery.textContent = setup.error
      ? (messages[setup.error] || `Setup stopped (${setup.error}). No completion is assumed. Inspect saved setup before another action.`)
      : 'Cancellation stops further automatic actions, not rollback. Already-started OS/package or detached work may continue, especially offline. Wait for setup to stop before disconnecting and authenticating again.';
    if (setup.state === 'completed') recovery.textContent = setup.observing
      ? 'The saved model download completed and verified its files. Generation is still untested.'
      : 'Setup completed; the password was cleared from controller memory. Generation is still untested. To install another selection, disconnect, reconnect and authenticate again, then click Install.';
    if (setup.blockers?.length) recovery.textContent += ' ' + setup.blockers.join(' ');
    if (setup.state === 'failed' && setup.retry) recovery.textContent += ` ${setup.retry.eligible ? 'Safe stage retry available.' : 'Retry unavailable: ' + setup.retry.reason + '.'} ${setup.retry.next_action || ''}`;
    if (setup.state === 'failed' && connection.state !== 'authenticated') recovery.textContent += ' Reconnect and authenticate, then inspect saved setup.';
    if (setup.error === 'setup_sudo_password_rejected') recovery.textContent += ' After resolving sudo policy, disconnect, reconnect and authenticate before a new Install.';
    if (setup.busy) { runtimeReady = false; resetDashboardPolling(); }
    render();
  }
  async function observeSavedSetup(current = () => true) {
    const document = await request('/setup/observe', {});
    if (!current()) return;
    const snapshot = document.snapshot;
    const checkpoint = snapshot.setups.find(item => item.plan_digest === document.plan_digest);
    const operation = snapshot.operation?.plan_digest === document.plan_digest ? snapshot.operation : null;
    const running = operation?.state === 'running';
    const downloading = operation?.action === 'download';
    const completed = downloading && operation.state === 'completed';
    const failed = ['failed', 'interrupted'].includes(operation?.state) || checkpoint?.state === 'failed';
    inspectedSetup = { observing: true, busy: running, state: running ? 'running' : completed ? 'completed' : failed ? 'failed' : 'observed',
      phase: downloading ? 'downloading' : operation?.action === 'activate' ? 'activating' : 'installing',
      profile: document.plan.profile, artifact_set_digest: document.selection.artifact_set_digest,
      plan_digest: document.plan_digest, snapshot, diagnostics: snapshot.diagnostics || {}, retry: snapshot.retry,
      installation_ready: checkpoint?.state === 'ready', model_ready: completed,
      error: failed ? (operation?.state === 'interrupted' ? 'saved_operation_interrupted' : 'saved_operation_failed') : null };
    showSetup(inspectedSetup);
    if (running) {
      clearTimeout(setupTimer);
      setupTimer = setTimeout(() => setupProgress(), 2000);
    }
  }
  async function inspectSetup() {
    await observeSavedSetup();
    if (setup.busy || setup.installation_ready) return;
    let document;
    try {
      document = await request('/setup/restore', {});
    } catch (error) {
      if (error.code !== 'matching_release_required') throw error;
      // Only the backend's audited predecessor proof may cross a release boundary.
      document = await request('/setup/restore-compatible', {});
    }
    const snapshot = document.snapshot || await request('/setup/events?after=0');
    const checkpoint = snapshot.setups.find(item => item.plan_digest === document.plan_digest);
    inspectedSetup = { state: checkpoint?.state === 'failed' ? 'failed' : 'observed', busy: false,
      phase: 'installing', profile: document.plan.profile, artifact_set_digest: document.selection.artifact_set_digest,
      plan_digest: document.plan_digest, snapshot, diagnostics: snapshot.diagnostics, retry: snapshot.retry,
      error: checkpoint?.state === 'failed' ? 'setup_install_failed' : null };
    showSetup(inspectedSetup);
  }
  $('inspect-setup').addEventListener('click', () => perform('Inspecting saved setup without starting installation…', inspectSetup));
  $('retry-setup').addEventListener('click', () => perform('Retrying the eligible failed stage; completed work is preserved…', async () => {
    if (!setup.retry?.eligible) return;
    pendingRetry ||= {profile: setup.profile, artifact_set_digest: setup.artifact_set_digest,
      sage_attention: $('install-sage').checked, plan_digest: setup.plan_digest, request_id: crypto.randomUUID()};
    try {
      const result = await request('/setup/retry', pendingRetry);
      inspectedSetup = null;
      showSetup(result);
    } catch (error) {
      if (!['network_unavailable', 'invalid_response'].includes(error.code)) pendingRetry = null;
      throw error;
    }
    await setupProgress();
  }));
  function diagnosticText() { return JSON.stringify(setup.diagnostics, null, 2) + '\n'; }
  $('copy-setup-log').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(diagnosticText()); $('setup-copy-status').textContent = 'Diagnostics copied.'; }
    catch { $('setup-copy-status').textContent = 'Clipboard unavailable. Use Download diagnostics or select the log text.'; }
  });
  $('download-setup-log').addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob([diagnosticText()], {type: 'application/json'}));
    const link = node('a'); link.href = url; link.download = 'h3-setup-diagnostics.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  async function setupProgress() {
    clearTimeout(setupTimer);
    const epoch = setupEpoch;
    const sequence = ++setupRequest;
    const current = () => pageActive && epoch === setupEpoch && sequence === setupRequest;
    try {
      const data = await request('/status');
      if (!current()) return;
      setConnection(data);
      if (inspectedSetup?.observing && connection.state === 'authenticated') await observeSavedSetup(current);
    } catch (error) {
      if (!current()) return;
      showError(error, false);
      if (!ready) return;
    }
    if (current() && setup.busy) {
      clearTimeout(setupTimer);
      setupTimer = setTimeout(() => setupProgress(), setup.observing ? 2000 : 700);
    }
  }
  $('generation-profile').addEventListener('change', render);
  $('install').addEventListener('click', () => perform('Starting selected automatic setup…', async () => {
    const selected = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    // Reattach before entering install/recovery. A completed or running saved
    // download is not a failed environment and must never be reinstalled.
    try {
      await observeSavedSetup();
      if (setup.busy || (setup.profile === selected.profile_name && setup.model_ready)) {
        pendingInstall = null;
        if (!setup.busy && $('install-sage').checked && !sageState.installed) {
          await savePreferences();
          await installSageNow();
        }
        return;
      }
    } catch (error) {
      if (!['setup_not_found', 'matching_release_required'].includes(error.code)) throw error;
    }
    // Retain only the public intent ID across an ambiguous response, never secrets.
    await savePreferences();
    pendingInstall ||= { profile: selected.profile_name, artifact_set_digest: selected.artifact_set_digest,
      sage_attention: $('install-sage').checked, request_id: crypto.randomUUID() };
    try {
      const result = await request('/setup/start', pendingInstall);
      inspectedSetup = null;
      showSetup(result);
    } catch (error) {
      if (!['network_unavailable', 'invalid_response'].includes(error.code)) pendingInstall = null;
      throw error;
    }
    await setupProgress();
  }));
  $('cancel-setup').addEventListener('click', () => perform('Stopping further automatic setup actions…', async () => {
    showSetup(await request('/setup/cancel', {}));
    await setupProgress();
  }));
  $('setup-refresh').addEventListener('click', () => perform('Checking automatic setup progress…', async () => {
    if (inspectedSetup && !setup.busy) await inspectSetup();
    else await setupProgress();
  }));

  async function jobEvents(current) {
    const response = await fetch(API + '/remote/events?after=' + jobCursor, { credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
    if (!response.ok) throw { code: 'remote_unavailable' };
    const text = await response.text();
    if (!current()) return;
    for (const block of text.split('\n\n')) {
      const fields = Object.fromEntries(block.split('\n').filter(line => line.includes(':')).map(line => [line.slice(0, line.indexOf(':')), line.slice(line.indexOf(':') + 1).trim()]));
      if (fields.event === 'reset') { jobCursor = Number(JSON.parse(fields.data).minimum_cursor); jobLogs.clear(); jobPhases.clear(); }
      if (fields.event === 'job') {
        const record = JSON.parse(fields.data); jobCursor = Number(fields.id);
        if (!currentJobs.some(job => job.job_id === record.subject_id)) continue;
        const event = record.event;
        const previous = jobPhases.get(record.subject_id);
        if (event.stage !== 'diagnosis') {
          const same = previous?.stage === event.stage;
          jobPhases.set(record.subject_id, { ...event,
            progress: event.progress || (same ? previous.progress : null),
            error: (event.status === 'failed' && event.message && event.stage !== 'worker')
              ? event.message : previous?.error });
        }
        // Console details are separate from phase/progress, scoped to one job.
        const line = event.message ? `[${event.stage}] ${event.message}`
          : previous?.stage !== event.stage || event.status !== 'running' ? `${event.stage.replaceAll('_', ' ')} · ${event.status}` : '';
        if (line) {
          const lines = jobLogs.get(record.subject_id) || [];
          if (lines.at(-1) !== line) lines.push(line);
          while (lines.length > 300 || lines.join('\n').length > 64000) lines.shift();
          jobLogs.set(record.subject_id, lines);
        }
      }
    }
    reconcileJobs(currentJobs);
    showJobTerminal();
  }
  function showJobTerminal() {
    $('terminal-job').textContent = selectedJob ? `Job ${selectedJob.slice(0, 8)} · live output` : 'Select a job';
    replaceOutput($('job-events'), (jobLogs.get(selectedJob) || []).join('\n') || 'Waiting for output from this job.');
    for (const [id, card] of jobCards) card.item.classList.toggle('selected', id === selectedJob);
  }
  function updateJobTimes() {
    for (const job of currentJobs) {
      const card = jobCards.get(job.job_id);
      if (!card) continue;
      const age = Math.max(0, (performance.now() - (job.timingReceivedAt ?? performance.now())) / 1000);
      const live = ['queued', 'running'].includes(job.status);
      const stale = live && (connection.state !== 'authenticated' || age > 15);
      card.timing.textContent = H3JobTiming.timingText(job, stale ? 0 : age) + (stale ? ' (last update)' : '');
    }
  }
  const timingTimer = setInterval(() => { if (pageActive) updateJobTimes(); }, 1000);
  function reconcileJobs(jobs) {
    currentJobs = jobs;
    const queued = jobs.filter(job => job.status === 'queued').slice().reverse();
    const running = jobs.filter(job => job.status === 'running');
    const history = jobs.filter(job => !['queued', 'running'].includes(job.status));
    $('queue-summary').textContent = `${running.length} running · ${queued.length} waiting · ${history.length} finished`;
    if (!selectedJob || !jobs.some(job => job.job_id === selectedJob)) selectedJob = running[0]?.job_id || queued[0]?.job_id || jobs[0]?.job_id;
    const ordered = [...running, ...queued, ...history];
    const present = new Set(jobs.map(job => job.job_id));
    for (const [id, card] of jobCards) {
      if (!present.has(id)) { card.item.remove(); jobCards.delete(id); jobLogs.delete(id); jobPhases.delete(id); }
    }
    let nextItem = $('jobs').firstElementChild;
    for (const job of ordered) {
      let card = jobCards.get(job.job_id);
      if (!card) {
        const item = node('article', undefined, 'job-card');
        const status = node('p');
        const output = node('div');
        const heading = node('div', undefined, 'job-heading');
        const title = node('h4', `Job ${job.job_id.slice(0, 8)}`); title.title = job.job_id;
        const logs = node('button', 'View terminal', 'text-button'); logs.type = 'button';
        logs.addEventListener('click', () => { selectedJob = job.job_id; showJobTerminal(); });
        heading.append(title, logs);
        const meter = node('progress'); meter.setAttribute('aria-label', `Progress for job ${job.job_id.slice(0, 8)}`);
        const detail = node('p', undefined, 'job-detail');
        const settings = job.request?.settings;
        const caption = node('p', settings ? `${settings.width} × ${settings.height} · ${settings.num_frames} frames · ${settings.steps} sampling steps · ${settings.sage_attention ? 'SageAttention' : 'standard attention'}` : '', 'field-hint');
        const timing = node('p', undefined, 'job-timing');
        timing.title = 'Current attempt: Run includes loading, sampling and encoding. Total includes queue waiting.';
        item.append(heading, caption, status, timing, meter, detail, output);
        card = { item, status, timing, meter, detail, output, cancel: null, outputIdentity: null };
        jobCards.set(job.job_id, card);
        $('jobs').insertBefore(item, nextItem);
      }
      if (card.item !== nextItem) $('jobs').insertBefore(card.item, nextItem);
      nextItem = card.item.nextElementSibling;
      card.item.dataset.status = job.status;
      const phase = jobPhases.get(job.job_id);
      const label = (phase?.stage || 'preparing').replaceAll('_', ' ');
      card.status.textContent = job.status === 'queued' ? `Waiting · queue position ${queued.indexOf(job) + 1}`
        : job.status === 'running' ? label.charAt(0).toUpperCase() + label.slice(1)
        : job.status.charAt(0).toUpperCase() + job.status.slice(1);
      card.meter.hidden = ['failed', 'cancelled'].includes(job.status);
      if (job.status === 'completed' || job.status === 'queued') {
        card.meter.max = 1; card.meter.value = job.status === 'completed' ? 1 : 0;
        card.detail.textContent = job.status === 'completed' ? 'Video ready' : 'Will start automatically when the worker is available.';
      } else if (job.status === 'running' && phase?.progress?.total > 0) {
        card.meter.max = phase.progress.total; card.meter.value = phase.progress.completed;
        card.detail.textContent = `${phase.progress.completed} / ${phase.progress.total} ${phase.progress.unit} · current phase`;
      } else {
        card.meter.removeAttribute('value');
        card.detail.textContent = job.status === 'running' ? 'Working · this phase does not report a measurable total. See terminal for details.'
          : phase?.error || 'See this job’s terminal for the failure details.';
      }
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
    updateJobTimes();
  }
  async function dashboard() {
    clearTimeout(jobTimer);
    const epoch = dashboardEpoch;
    const sequence = ++dashboardRequest;
    const current = () => epoch === dashboardEpoch && sequence === dashboardRequest && connection.state === 'authenticated';
    // Keep the last confirmed readiness during background polling. Only a real
    // response or connection failure may disable Queue, not an in-flight read.
    const capability = await request('/remote/capabilities');
    if (!current()) return;
    const data = await request('/remote/jobs');
    for (const job of data.jobs) job.timingReceivedAt = performance.now();
    if (!current()) return;
    backendCapabilities = capability.adapter_capabilities;
    showSage(capability.optional_features?.sage_attention);
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
    const diagnostic = capability.diagnostic || {};
    const backgroundCheck = diagnostic.environment_ready === true && diagnostic.model_ready === true
      && capability.not_ready_reasons?.length === 1
      && capability.not_ready_reasons[0] === 'Runtime and verified model diagnostic is running';
    runtimeReady = (capability.runtime_ready || backgroundCheck) && profiles.length > 0;
    const generated = data.jobs.some(job => profiles.includes(job.request?.settings?.profile) && job.status === 'completed' && job.output && !job.output.expired);
    $('readiness').textContent = `Daemon connected. Active backend: ${capability.backend}; profiles: ${profiles.join(', ')}. Environment ready: ${diagnostic.environment_ready === true}. Model files ready: ${diagnostic.model_ready === true}. Runtime ready: ${runtimeReady}. Generation tested: ${generated ? 'completed job available (see output)' : 'not proven'}. ${(capability.not_ready_reasons || []).join(' ')}`;
    reconcileJobs(data.jobs);
    await jobEvents(current);
    if (!current()) return;
    render();
    jobTimer = setTimeout(() => {
      if (!current()) return;
      const nextSequence = dashboardRequest + 1;
      dashboard().catch(error => {
        if (epoch === dashboardEpoch && nextSequence === dashboardRequest) { runtimeReady = false; render(); showError(error, false); }
      });
    }, data.jobs.some(job => ['queued', 'running'].includes(job.status)) || sageState.busy ? 2000 : 5000);
  }
  $('dashboard-refresh').addEventListener('click', () => perform('Checking daemon, model and queued jobs…', dashboard));
  $('generation-form').addEventListener('submit', event => {
    event.preventDefault();
    perform('Submitting local generation job…', async () => {
      const body = { prompt: $('prompt').value, profile: $('generation-profile').value, num_frames: Number($('frames').value),
        width: Number($('width').value), height: Number($('height').value), steps: Number($('steps').value), seed: Number($('seed').value),
        sage_attention: $('use-sage').checked };
      if (!$('lora-field').hidden && $('lora').value) body.loras = [$('lora').value];
      const accepted = await request('/remote/jobs', body);
      selectedJob = accepted.job_id || accepted.job?.job_id || null;
      await dashboard();
    });
  });
  window.addEventListener('pagehide', () => { clearInterval(timingTimer); pageActive = false; $('password').value = ''; resetSetupPolling(); resetDashboardPolling(); });

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
      showPreferences(await request('/preferences'));
      syncDuration('frames');
      showSage();
      if (preferences.connection) {
        const saved = preferences.connection;
        lastTarget = { host: saved.host, port: saved.port, username: saved.username };
      }
      if (connection.state === 'disconnected' && preferences.connection) {
        try {
          setConnection(await request('/connection/restore', {}));
          if (connection.state === 'authenticated') await afterAuthentication();
        } catch (error) {
          await refresh();
          showError(error, false);
        }
      } else if (setup.busy) await setupProgress();
      else if (connection.state === 'authenticated') {
        try { await afterAuthentication(); }
        catch (error) { showError(error, false); }
      }
      if (!setup.busy && connection.state !== 'authenticated') $('operation').textContent = 'Ready for a secure SSH connection.';
      log(connection.state === 'authenticated'
        ? 'Connection authenticated. No installation or generation was started automatically.'
        : 'Local browser session secured. Authenticate, scan, select a model and click Install to begin.');
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
