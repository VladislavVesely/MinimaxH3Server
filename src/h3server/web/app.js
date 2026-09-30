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
  let submittingGeneration = false;
  let jobsTab = 'current';
  const currentPageJobs = new Set();
  const seenPageJobs = new Set();
  let pageJobsInitialized = false;
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
  let modelState = {};
  let performanceAvailable = false;
  let estimateTimer = null;
  let estimateEpoch = 0;
  let estimateKey = null;
  let estimateContext = '';
  let estimateAbort = null;
  let backendCapabilities = {};
  let profileCatalog = [];
  let modelLibrary = null;
  let modelLicenseDigest = null;
  let activeProfileKey = null;
  let setupTimer = null;
  let jobTimer = null;
  let setupEpoch = 0;
  let setupRequest = 0;
  let dashboardEpoch = 0;
  let dashboardRequest = 0;
  const jobCards = new Map();
  const videoPreviews = new WeakMap();
  let previewObserver = null;
  const jobLogs = new Map();
  const jobPhases = new Map();
  let selectedJob = null;
  let currentJobs = [];
  let preferences = { connection: null, install_sage: false, use_sage: false };
  let preferencesSave = Promise.resolve();
  let sageState = { installed: false, busy: false, supported: false };
  let turboState = { installed: false, busy: false, supported: false };
  let sparseState = { installed: false, busy: false, supported: false };
  let inputModes = ['text'];
  let referenceState = {};
  let assetEpoch = 0;
  const assetDimensionUpdates = [];
  const assetDraft = {reference_images:[], reference_videos:[], reference_audios:[], first_frame:[], last_frame:[]};
  const turboProfiles = ['h3-convrot-int8', 'h3-convrot-mixed'];
  const joiner = window.H3Joiner?.create({api:API, getCsrf:() => csrf, onChange:() => render()});
  const jobSelection = window.H3JobSelection?.create({
    run: action => perform('Deleting selected jobs and their files…', action),
    remove: async id => {
      const result = await request('/remote/jobs/' + id + '/delete', {});
      const removed = new Set(result.removed_assets || []);
      for (const key of Object.keys(assetDraft)) assetDraft[key] = assetDraft[key].filter(asset => !removed.has(asset.id));
      showInputAssets();
      currentPageJobs.delete(id); seenPageJobs.delete(id);
      if (selectedJob === id) selectedJob = null;
      estimateKey = null;
    },
    refresh: () => dashboard()
  });

  function savedTargetMatches() {
    const saved = preferences.connection;
    return saved && lastTarget && saved.host === lastTarget.host && saved.port === lastTarget.port && saved.username === lastTarget.username;
  }
  function showPreferences(value) {
    preferences = value;
    $('install-sage').checked = value.install_sage;
    $('use-sage').checked = value.use_sage;
    $('install-turbo').checked = value.install_turbo;
    $('use-turbo').checked = value.use_turbo;
    $('install-sparse').checked = value.install_sparse;
    $('sparse-preset').value = value.sparse_preset || 'off';
    if (value.connection) {
      for (const key of ['host', 'port', 'username']) $(key).value = value.connection[key];
    }
    $('forget-connection').hidden = !value.connection;
    $('saved-connection-note').textContent = value.connection ? 'Connection remembered locally in plaintext. Host-key verification is still required.' : '';
  }
  function savePreferences() {
    preferencesSave = preferencesSave.catch(() => {}).then(async () => {
      preferences = await request('/preferences', { install_sage: $('install-sage').checked, use_sage: $('use-sage').checked,
        install_turbo: $('install-turbo').checked, use_turbo: $('use-turbo').checked,
        install_sparse: $('install-sparse').checked, sparse_preset: $('sparse-preset').value });
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
      ? 'SageAttention 1.0.6 is available for compatible dense attention; masked/full-precision layers retain PyTorch. Sparse calls use their own kernel without changing this setting.'
      : 'To use SageAttention, select its installation option above and click Install. You can leave it off to use normal attention.';
  }
  function showTurbo(value) {
    if (value) {
      const matching = value.artifact_digest === capabilities.turbo_artifact_digest;
      turboState = { ...value, installed: value.installed && matching, supported: value.supported && matching,
        error: matching ? value.error : 'Controller and daemon Turbo pins differ; update to matching versions before use.' };
    }
    $('turbo-install-state').textContent = turboState.busy
      ? `Downloading Turbo: ${bytes(turboState.completed || 0)} / ${bytes(turboState.total)}`
      : turboState.installed ? 'Turbo v4-600 EMA downloaded. Pinned hashes are checked again before use.'
      : turboState.error || 'Optional Turbo LoRA is not installed.';
    $('turbo-progress').hidden = !turboState.busy;
    $('turbo-progress').max = turboState.total || 1;
    $('turbo-progress').value = turboState.completed || 0;
  }
  function showSparse(value) {
    if (value) {
      const matching = value.artifact_digest === capabilities.sparse_artifact_digest;
      sparseState = {...value, installed: value.installed && matching, supported: value.supported && matching,
        error: matching ? value.error : 'Sparse component pins differ; update the controller and daemon to matching versions.'};
    }
    $('sparse-install-state').textContent = sparseState.busy
      ? `Sparse setup: ${bytes(sparseState.completed || 0)} / ${bytes(sparseState.total)} · download, verification and GPU check`
      : sparseState.installed ? 'Sparse kernels installed and GPU-verified. No extra model weights required.'
      : sparseState.error || 'Optional sparse component is not installed.';
    $('sparse-progress').hidden = !sparseState.busy;
    $('sparse-progress').max = sparseState.total || 1;
    $('sparse-progress').value = sparseState.completed || 0;
    $('sparse-install-output').hidden = !sparseState.log;
    replaceOutput($('sparse-install-output'), sparseState.log || '');
  }
  async function installOptional(name, show) {
    let feature = await request('/remote/features/' + name, {});
    show(feature); render();
    while (feature.busy && pageActive) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      feature = await request('/remote/features/' + name);
      show(feature); render();
    }
    if (pageActive && !feature.installed) throw { code: name === 'turbo' ? 'turbo_install_failed' : name === 'sparse' ? 'sparse_install_failed' : 'sage_install_failed' };
    if (pageActive) await dashboard();
  }
  async function afterAuthentication() {
    preferences = await request('/preferences');
    $('forget-connection').hidden = !preferences.connection;
    if (capabilities.scan === true && !setup.busy) await scanHardware();
    if (connection.mode === 'local' && !connection.local_supported) {
      $('operation').textContent = 'Local scan complete. Installation requires supported Linux/NVIDIA hardware; this system is not supported. No changes made.';
      return;
    }
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
    asset_count_limit: 'Too many attachments. Use at most 9 reference images, 3 videos, 3 audio files, or one image per first/last-frame slot. Remove an attachment first.',
    asset_size_limit: 'Each attachment must contain 1 byte–64 MiB. Reduce the file size and try again.',
    asset_invalid_or_limits_exceeded: 'The server rejected this attachment. Check its type and integrity: PNG/JPEG/static WebP, MP4/WebM, or WAV/FLAC/MP3. Clips must be 0.2–15 seconds; video at most 1920×1088 area and 60 fps, images at most 16 megapixels. The server asset budget is 8 GiB.',
    asset_target_changed: 'The connection changed during upload. Select the file again for the current server.',
    asset_upload_timeout: 'Upload or media validation timed out. Try a smaller file and check the connection.',
    models_busy_wait_for_queue_and_maintenance: 'Models cannot be unloaded while jobs are running or queued, or during maintenance. Wait for these operations to finish.',
    invalid_checkpoint: 'The saved setup record is incomplete or inconsistent. Keep the checkpoint files and inspect the original plan and deployment record; nothing was started or retried.',
    setup_not_found: 'No saved setup was found on this authenticated target. Scan hardware and use Install to begin.',
    saved_connection_missing: 'No saved connection is available. Enter the SSH details and authenticate once.',
    saved_connection_mismatch: 'Saved credentials belong to a different SSH target. Enter the password for this connection.',
    turbo_selection_changed: 'The controller and daemon Turbo pins differ. Update to matching versions; no download was started.',
    model_install_failed: 'The additional model was not installed. See the download status; existing models are unchanged. Click Install to verify and resume.',
    sparse_install_failed: 'Sparse installation or GPU verification failed. See its output above; base models and SageAttention are unchanged. Retry Install to resume.',
    sparse_selection_changed: 'Sparse pins differ between controller and daemon. No installation was started.',
    turbo_install_failed: 'Optional Turbo download failed. Its status shows the reason; click Install to retry/resume without reinstalling the base model.',
    sage_install_failed: 'Optional SageAttention installation or its GPU check failed. See the component output; model files have not been changed.',
    optional_install_busy_or_unsupported: 'The optional component requires a supported, idle environment. Wait for jobs to finish; Turbo currently supports ConvRot INT8/mixed.',
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

    local_install_unsupported: 'Local scanning is available, but installation requires a supported Linux/NVIDIA system.',
    local_target_unsupported: 'Local target mode currently supports macOS scanning and Linux installation/control.',
    local_target_required: 'Select and connect the Local target first.',
    lan_login_invalid: 'Incorrect LAN access key. Read the key file on the controller computer.',
    lan_login_rate_limited: 'Too many login attempts. Wait one minute and retry.',
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
    last_frame_unavailable: 'The video is missing, expired or cannot be decoded. No frame was exported.',
    last_frame_export_busy: 'Another last-frame export is still running. Wait for it to finish, then try again.',
    last_frame_export_timeout: 'Frame extraction timed out. No lower-quality fallback was used; try again when the server is less busy.',
    last_frame_precision_unsupported: 'This video has more than 8 bits per channel. Export was refused rather than reducing its precision.',
    frame_inputs_unsupported: 'First-frame inputs require a supported pruned profile. You can still download the PNG.',
    required_generation_file_missing: 'An input or required runtime file is missing. Re-upload any deleted references in this draft, or refresh model readiness.',
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

  async function request(path, body, {signal} = {}) {
    let response;
    try {
      response = await fetch(API + path, {
        method: body === undefined ? 'GET' : 'POST', signal,
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
    if (data.mode === 'local') lastTarget = {mode:'local'};
    if (data.state !== 'disconnected') $('target-mode').value = data.mode === 'local' ? 'local' : 'ssh';
    $('forget-recovery').hidden = !data.recovery?.credential_retained;
    if (data.setup) showSetup(!data.setup.busy && inspectedSetup ? inspectedSetup : data.setup);
    if (data.state !== 'authenticated') {
      runtimeReady = false;
      modelState = {}; performanceAvailable = false;
      showModelLibrary(null);
      clearTimeout(estimateTimer); estimateEpoch++; estimateKey = null;
      $('generation-estimate').textContent = 'Connect to obtain a timing estimate.';
      $('estimate-details').hidden = true;
      sageState = { installed: false, busy: false, supported: false };
      turboState = { installed: false, busy: false, supported: false };
      sparseState = { installed: false, busy: false, supported: false };
      showSage(); showTurbo(); showSparse();
      referenceState = {}; inputModes = ['text']; assetEpoch++;
      for (const key of Object.keys(assetDraft)) assetDraft[key] = [];
      showInputAssets();
      resetDashboardPolling();
      jobSelection?.clear();
      jobCards.clear(); jobLogs.clear(); jobPhases.clear(); selectedJob = null; currentJobs = [];
      releaseVideoPreviews($('jobs'));
      $('jobs').replaceChildren(); $('job-events').textContent = 'Waiting for a job.'; jobCursor = 0;
      renderJobTabs();
      if (!setup.busy && setup.state === 'idle') clearScan();
      $('readiness').textContent = 'SSH disconnected. No generation readiness assumed.';
    }
    if (data.mode === 'local' && data.state === 'authenticated' && !runtimeReady) {
      $('readiness').textContent = data.local_supported ? 'Local daemon readiness has not been confirmed.' : 'This local system is unsupported for installation/generation; supported Linux/NVIDIA hardware is required.';
    }
    if (data.state !== 'awaiting_authentication') $('password').value = '';
    if (data.state !== 'authenticated' || data.mode !== 'local') $('local-sudo-password').value = '';
    render();
  }

  function generationBlockReasons() {
    const reasons = conditioningReasons();
    if (!ready) reasons.push('Open a valid local controller session.');
    if (connection.state !== 'authenticated') reasons.push('Connect and authenticate to the server.');
    if (setup.busy) reasons.push('Wait for setup to finish.');
    if (modelLibrary?.busy || modelState.maintenance) reasons.push('Wait for model installation or maintenance to finish. Existing jobs are not cancelled.');
    if (busy && !submittingGeneration) reasons.push('Wait for the current operation to finish.');
    if (!runtimeReady) reasons.push('Generation readiness is not confirmed. Check the readiness status above or click Refresh dashboard.');
    if (!canvasFits(Number($('width').value), Number($('height').value))) reasons.push('Resolution exceeds this daemon’s canvas limits or is not aligned to 32 pixels. Choose an enabled preset or correct Width and Height.');
    const sparseOn = $('sparse-preset').value !== 'off';
    if (sparseOn && $('generation-profile').value !== 'h3-convrot-int8') reasons.push('Sparse attention requires the ConvRot INT8 generation profile.');
    if ($('use-turbo').checked && !['h3-convrot-int8','h3-convrot-mixed'].includes($('generation-profile').value)) reasons.push('Turbo requires ConvRot INT8 or mixed.');
    if (sparseOn && $('use-turbo').checked) reasons.push('Turbo and sparse attention cannot be combined. Turn off Turbo or select Sparse: Off.');
    for (const [name, state, selected, off] of [
      ['Sparse attention', sparseState, sparseOn, 'select Sparse: Off'],
      ['Turbo LoRA', turboState, $('use-turbo').checked, 'turn off Turbo'],
      ['SageAttention', sageState, $('use-sage').checked, 'turn off SageAttention']
    ]) {
      if (state.busy) reasons.push(`${name} setup is running. Wait for its download and checks to finish.`);
      else if (selected && !state.supported) reasons.push(`${name} is not supported or verified for this model/component version. ${off.charAt(0).toUpperCase() + off.slice(1)} or check its setup status above.`);
      else if (selected && !state.installed) reasons.push(`${name} is not installed and verified. Select its installation option above and click Install, or ${off}.`);
    }
    return reasons;
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
    const localTarget = $('target-mode').value === 'local';
    $('target-mode').disabled = !available || !disconnected;
    $('ssh-target-details').hidden = localTarget;
    for (const id of ['host','port','username']) $(id).disabled = localTarget || !available || !disconnected;
    $('local-authorize-form').hidden = connection.mode !== 'local' || !authenticated || !connection.local_supported || !!connection.recovery?.setup_credential_retained;
    $('local-authorize').disabled = !available;
    const connectLabel = localTarget ? 'Use this machine and scan' : 'Connect to server →';
    if ($('connect').textContent !== connectLabel) $('connect').textContent = connectLabel;
    $('ssh-state').textContent = connection.mode === 'local' ? (authenticated ? 'Local machine selected' : 'Local target disconnected') : labels[state] || 'SSH state unknown';
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
    $('scan-hint').textContent = authenticated && connection.mode === 'local' && !connection.local_supported
      ? 'This controller machine is not supported for installation. Local scan is read-only; use supported Linux/NVIDIA hardware to install.'
      : authenticated
      ? (capabilities.scan === true ? 'Read-only observations only. No installation or CUDA runtime test is performed.' : 'Scanning is unavailable in this controller.')
      : 'Authenticate to enable the read-only scan.';
    for (const id of ['step-connect', 'step-scan', 'step-install']) $(id).removeAttribute('aria-current');
    $(scanned || setup.busy ? 'step-install' : authenticated ? 'step-scan' : 'step-connect').setAttribute('aria-current', 'step');
    $('progress').hidden = !busy;
    $('progress-note').hidden = !busy;
    $('connection').setAttribute('aria-busy', String(busy));
    const optionalOnlyFailure = setup.model_ready && ['sage_install_failed', 'turbo_install_failed', 'sparse_install_failed', 'optional_install_busy_or_unsupported'].includes(setup.error);
    $('install-profile').disabled = !available || !profileCatalog.length || !!pendingInstall || (setup.state === 'failed' && !optionalOnlyFailure && !modelLibrary?.supported);
    $('install-sage').disabled = !available || sageState.busy || !!pendingInstall || !!pendingRetry;
    $('install-turbo').disabled = !available || sageState.busy || turboState.busy || !!pendingInstall || !!pendingRetry;
    $('install-sparse').disabled = !available || sageState.busy || turboState.busy || sparseState.busy || !!pendingInstall || !!pendingRetry;
    const needsSparse = $('install-sparse').checked && !sparseState.installed;
    const needsSage = $('install-sage').checked && !sageState.installed;
    const needsTurbo = $('install-turbo').checked && !turboState.installed;
    const selectedInstall = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    const additionalModel = modelLibrary?.supported && modelLibrary.profiles.some(p => p.profile === selectedInstall?.profile_name && p.compatible);
    const alreadyInstalled = (additionalModel
      ? runtimeReady && modelLibrary.profiles.some(p => p.profile === selectedInstall.profile_name && p.installed)
      : setup.installation_ready === true && setup.model_ready === true && setup.profile === $('install-profile').value)
      && !needsSage && !needsTurbo && !needsSparse;
    const installReason = !ready ? 'Open a valid local controller session to enable Install.'
      : modelLibrary?.busy ? 'A model download is running; see its progress below. Installed models are preserved.'
      : alreadyInstalled ? 'This environment and its model files are already installed and verified. No installation is needed.'
      : sparseState.busy ? 'Optional sparse setup is running; download and GPU-check status is shown below.'
      : $('install-sparse').checked && selectedInstall && selectedInstall.profile_name !== 'h3-convrot-int8' ? 'Sparse currently supports ConvRot INT8 only. Uncheck it for this profile.'
      : turboState.busy ? 'Optional Turbo download is running; actual bytes are shown below.'
      : $('install-turbo').checked && selectedInstall && !turboProfiles.includes(selectedInstall.profile_name) ? 'Turbo currently supports ConvRot INT8/mixed only. Uncheck it for this profile.'
      : sageState.busy ? 'Optional SageAttention installation is running; progress is shown below.'
      : (additionalModel || needsSage || needsTurbo || needsSparse) && currentJobs.some(job => ['queued', 'running'].includes(job.status)) ? 'Wait for the generation queue to become idle before installing models or optional components. Running jobs will not be cancelled.'
      : $('install-sage').checked && selectedInstall && !selectedInstall.runtime_id.startsWith('comfyui-h3-') ? 'SageAttention is available for pruned profiles only. Uncheck it to install a legacy profile.'
      : setup.busy ? 'Setup is already running. Wait for it to finish or cancel further actions.'
      : busy ? 'An operation is in progress. Wait for it to finish.'
      : !authenticated ? 'Authenticate to automatically scan hardware and enable Install.'
      : setup.state === 'failed' && !optionalOnlyFailure && !additionalModel ? 'A saved setup failed. Review its diagnostics and retry eligibility below; Install cannot resume a failed stage.'
      : connection.mode === 'local' && !connection.local_supported ? 'Local installation is not supported on this platform. Use supported Linux/NVIDIA hardware.'
      : connection.mode === 'local' && !additionalModel && !connection.recovery?.setup_credential_retained ? 'Authorize local installation with your sudo password above. Scanning does not require authorization.'
      : !additionalModel && !connection.recovery?.setup_credential_retained ? 'Install needs memory-only setup authorization. Disconnect, reconnect and authenticate. Saved unprivileged retries do not need a sudo password.'
      : !additionalModel && !scanned && !pendingInstall ? 'Hardware has not been scanned. Click Scan hardware; if it failed, resolve the scan error first.'
      : !$('install-profile').value ? 'Wait for the model catalog, then choose a profile.'
      : $('install-profile').selectedOptions[0]?.disabled ? 'This profile is not eligible for the observed hardware. Choose an available profile.' : '';
    $('install').disabled = !!installReason;
    $('install-disabled-reason').textContent = installReason;
    $('install-disabled-reason').hidden = !installReason;
    $('inspect-setup').disabled = !available || !authenticated || capabilities.release_available !== true;
    $('retry-setup').hidden = setup.retry?.eligible !== true;
    $('retry-setup').disabled = !available || !authenticated;
    $('retry-setup').textContent = pendingRetry ? 'Retry stage request' : 'Retry failed stage';
    const installLabel = alreadyInstalled ? 'Installed' : pendingInstall ? 'Retry Install request'
      : setup.model_ready && needsSparse ? 'Install optional components'
      : setup.model_ready && needsSage && needsTurbo ? 'Install optional components'
      : setup.model_ready && needsTurbo ? 'Install Turbo LoRA'
      : setup.model_ready && needsSage ? 'Install SageAttention' : 'Install';
    if ($('install').textContent !== installLabel) $('install').textContent = installLabel;
    $('cancel-setup').hidden = !setup.busy || setup.observing === true;
    $('cancel-setup').disabled = !usable || setup.state === 'cancelling';
    $('setup-refresh').disabled = !usable;
    $('dashboard-refresh').disabled = !available || !authenticated;
    $('generation-fields').disabled = !available || !authenticated || !runtimeReady;
    for (const field of Object.keys(assetDraft)) $('upload-' + field).disabled = busy || referenceState.busy;
    for (const update of assetDimensionUpdates) update();
    $('install-reference').disabled = !available || !authenticated || busy || setup.busy || referenceState.busy || referenceState.installed || !referenceState.supported;
    const sparseOn = $('sparse-preset').value !== 'off';
    $('sparse-generation-state').textContent = $('use-turbo').checked ? 'Turbo selected: keep sparse Off, or turn Turbo off to use sparse (20 steps).'
      : !sparseState.installed ? 'To use sparse, select its installation option above and click Install.'
      : !sparseState.supported ? 'Sparse is not verified for this active model profile.' : 'Ready. Quality-first is the recommended starting preset; actual runtime/fallback appears in job output.';
    $('use-turbo').disabled = sparseOn && !$('use-turbo').checked;
    const generationReasons = generationBlockReasons();
    $('generate').disabled = busy || generationReasons.length > 0;
    const generationLabel = submittingGeneration ? 'Submitting…' : 'Queue generation';
    // A blur/change render can run between mouse-down and mouse-up. Replacing
    // the unchanged text node here makes WebKit discard that pending click.
    if ($('generate').textContent !== generationLabel) $('generate').textContent = generationLabel;
    $('generate-disabled-reason').hidden = generationReasons.length === 0;
    $('generate-disabled-reason').textContent = generationReasons.length ? 'Cannot queue generation: ' + generationReasons.join(' ') : '';
    $('models-status').textContent = !authenticated ? 'Resident model status unavailable while disconnected.'
      : !modelState.supported ? 'Retained models are not available for this daemon/runtime.'
      : modelState.loaded ? (modelState.busy ? 'Generation active. Last completed-run cache: ' : 'Retained components: ') + modelState.components.join(', ').replaceAll('_', ' ') + '. GPU/CPU placement is managed automatically.'
      : modelState.busy ? 'Generation active; retained-component status will update on completion.'
      : 'No models currently retained. The next generation loads them; they remain available between jobs where memory permits.';
    $('unload-models').disabled = !available || !authenticated || !modelState.can_unload;
    $('unload-models-reason').textContent = !authenticated ? 'Connect to check retained models.'
      : busy ? 'Wait for the current operation.' : modelState.busy ? 'Cannot unload while jobs are running or queued. Wait for the queue to finish.'
      : modelState.maintenance ? 'Cannot unload during installation or maintenance.'
      : !modelState.loaded ? 'No retained models to unload.' : 'Releases retained models from memory, not model files. The next generation will start cold.';
    $('export-timings').disabled = !available || !authenticated || !performanceAvailable;
    $('steps').min = $('use-turbo').checked ? 4 : 2;
    $('steps').max = $('use-turbo').checked ? 8 : 1000;
    $('turbo-strength').disabled = !$('use-turbo').checked;
    $('turbo-low-vram').disabled = !$('use-turbo').checked;
    for (const button of $('jobs').querySelectorAll('button')) {
      button.disabled = !available || !authenticated || (button.dataset.requiresFrames === 'true' && !inputModes.includes('frames')) || (button.dataset.joiner === 'true' && !joiner?.canAdd()) || button.dataset.reuseMissing === 'true';
      if (button.dataset.joiner === 'true') button.title = joiner?.blocker() || 'Video joiner is unavailable.';
    }
    if (typeof jobSelection !== 'undefined') jobSelection?.update(currentJobs.filter(job => jobMatchesTab(job, jobsTab)), available && authenticated);
    $('lora-field').hidden = backendCapabilities.lora?.status !== 'supported' || $('generation-profile').value.includes('int8');
    globalThis.H3Studio?.update({profiles:profileCatalog, library:modelLibrary, connection, setup, runtimeReady, busy,
      licenseReady:!!modelLicenseDigest, applyComponents, cancelComponents});
  }

  async function refresh() {
    const data = await request('/status');
    if (!pageActive) return;
    csrf = data.csrf_token;
    capabilities = data.capabilities;
    ready = true;
    void joiner?.init();
    $('session').textContent = 'Local session secured';
    setConnection(data);
    if (setup.busy) {
      clearTimeout(setupTimer);
      setupTimer = setTimeout(() => setupProgress(), 700);
    }
  }

  async function perform(label, action, { generationSubmission = false } = {}) {
    if (busy) return;
    busy = true;
    submittingGeneration = generationSubmission;
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
      submittingGeneration = false;
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
    modelLicenseDigest = data.license_sha256;
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
    showModelLibrary(modelLibrary);
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
    $('local-sudo-password').value = '';
    clearScan();
    pendingInstall = null; pendingRetry = null; inspectedSetup = null;
    resetSetupPolling();
    showSetup({ state: 'idle', busy: false });
    lastTarget = target;
    if (target.mode === 'local') {
      setConnection(await request('/connection/local', {}));
      await afterAuthentication();
      return;
    }
    setConnection(await request('/connect', target));
    log(connection.state === 'awaiting_approval' ? 'SSH handshake received. Fingerprint approval required; no password sent.' : 'Stored host key matched. Password authentication required.');
    (connection.state === 'awaiting_approval' ? $('fingerprint-heading') : $('auth-heading')).focus();
  }

  $('connect-form').addEventListener('submit', event => {
    event.preventDefault();
    const local = $('target-mode').value === 'local';
    perform(local ? 'Selecting this controller machine; no SSH connection…' : 'Connecting to SSH; no credentials sent…', () => connect(local ? {mode:'local'} : { host: $('host').value.trim(), port: Number($('port').value), username: $('username').value.trim() }));
  });
  $('target-mode').addEventListener('change', render);
  $('local-authorize-form').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !ready) return;
    const password = $('local-sudo-password').value;
    $('local-sudo-password').value = '';
    perform('Checking local sudo authorization; no installation started…', async () => {
      setConnection(await request('/connection/local/authorize', {password}));
    });
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
  for (const id of ['install-sage', 'use-sage', 'install-turbo', 'use-turbo']) $(id).addEventListener('change', () => {
    if (id === 'use-turbo') {
      if (!$('use-turbo').checked) $('steps').value = 20;
      else if (![4,5,6,7,8].includes(Number($('steps').value))) $('steps').value = 6;
    }
    render(); savePreferences().catch(error => showError(error, false));
  });
  for (const id of ['install-sparse', 'sparse-preset']) $(id).addEventListener('change', () => {
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
  $('reconnect').addEventListener('click', () => perform(lastTarget?.mode === 'local' ? 'Reconnecting to this controller machine…' : 'Reconnecting SSH; authentication will be required…', () => connect(lastTarget)));
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
    // Automatic read-only discovery must not interrupt an active local edit.
    if (!$('joiner').contains(document.activeElement)) $('hardware-heading').focus();
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
    reconnecting: 'Reconnecting securely', activating: 'Activating environment', downloading: 'MODEL WEIGHTS · downloading selected files', sage_attention: 'Installing optional SageAttention', turbo_lora: 'Downloading optional Turbo LoRA', sparse_attention: 'Installing and GPU-checking sparse attention', completed: 'Completed' };
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
    if (data.turbo_install) showTurbo(data.turbo_install);
    if (data.sparse_install) showSparse(data.sparse_install);
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
    $('setup-events-model').textContent = setup.profile ? `Setup history for ${profileLabel(setup.profile)} — these events do not describe another selected model.` : 'Saved setup history';
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
      sage_attention: $('install-sage').checked, turbo_lora: $('install-turbo').checked, sparse_attention: $('install-sparse').checked, plan_digest: setup.plan_digest, request_id: requestId()};
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
  function modelInstallSummary(value) {
    if (!value?.supported) return '';
    const installed = value.profiles.filter(p => p.installed).map(p => profileLabel(p.profile)).join(', ') || 'none';
    const available = `Available models: ${installed}. `;
    if (value.busy) return available + `${profileLabel(value.profile)} — ${value.state === 'verifying' ? 'verifying runtime / existing files' : 'downloading / verifying model files'}: ${bytes(value.completed || 0)} / ${bytes(value.total || 0)}. Existing models are preserved.`;
    if (value.error) return available + `Last ${profileLabel(value.profile)} installation ${value.state === 'cancelled' ? 'cancelled' : 'failed'}: ${value.error}. Click Install to retry; installed models remain available.`;
    if (value.state === 'installed') return available + `${profileLabel(value.profile)} is installed and available in Generation profile. Existing verified files are reused, not downloaded again.`;
    return available + 'Installed models are available in Generation profile. Select a model marked Not installed and click Install to add it; shared files are verified and reused.';
  }
  function requestId() {
    // getRandomValues remains available on private-LAN HTTP; randomUUID does not.
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
  }
  function modelOptionStatus(value, name) {
    if (value?.busy && value.profile === name) return 'Installing…';
    const profile = value?.profiles?.find(p => p.profile === name);
    if (!profile) return 'Installation status unknown';
    const status = profile.installed ? 'Installed' : 'Not installed';
    return status + (profile.compatible === false ? ' · Requires different runtime' : '');
  }
  function showModelLibrary(value) {
    modelLibrary = value;
    $('model-library-progress').hidden = !value?.busy;
    $('cancel-model-download').hidden = !value?.busy;
    $('model-library-progress').max = value?.total || 1;
    $('model-library-progress').value = value?.completed || 0;
    $('model-library-status').textContent = modelInstallSummary(value);
    for (const option of $('install-profile').options) {
      option.dataset.baseLabel ||= option.textContent;
      const label = `${option.dataset.baseLabel} — ${modelOptionStatus(value, option.value)}`;
      if (option.textContent !== label) option.textContent = label;
    }
  }
  async function applyComponents(selection) {
    return perform('Applying selected component changes…', async () => {
      const result = await request('/remote/models/install', {action:'synchronize', ...selection,
        license_sha256:modelLicenseDigest,license_acknowledged:true,eligibility_confirmed:true,download_approved:true});
      globalThis.H3Studio?.syncAccepted(result.operation);
      await dashboard();
    });
  }
  async function cancelComponents(operation) {
    return perform('Stopping further component changes…', async () => {
      await request('/remote/models/install/cancel', {operation});
      await dashboard();
    });
  }
  async function installAdditionalModel(selected) {
    if (!(runtimeReady && modelLibrary.profiles.some(p => p.profile === selected.profile_name && p.installed))) {
      let state = await request('/remote/models/install', {profile:selected.profile_name,artifact_set_digest:selected.artifact_set_digest,
        license_sha256:modelLicenseDigest,license_acknowledged:true,eligibility_confirmed:true,download_approved:true});
      showModelLibrary(state);
      while (pageActive && state.busy) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        state = await request('/remote/models/library'); showModelLibrary(state);
      }
      if (!pageActive) return;
      if (state.state !== 'installed') throw {code:'model_install_failed'};
    }
    await dashboard();
    $('generation-profile').value = selected.profile_name;
    render(); scheduleEstimate();
    if ($('install-sage').checked && !sageState.installed) await installOptional('sageattention', showSage);
    if (selected.profile_name === 'h3-convrot-int8' && $('install-sparse').checked && !sparseState.installed) await installOptional('sparse', showSparse);
    if ($('install-turbo').checked && !turboState.installed) await installOptional('turbo', showTurbo);
  }
  $('cancel-model-download').addEventListener('click', async () => {
    if (!modelLibrary?.operation || !confirm('Cancel this model download? Partial files can be resumed; installed models stay unchanged.')) return;
    try { showModelLibrary(await request('/remote/models/install/cancel', {operation:modelLibrary.operation})); }
    catch (error) { showError(error); }
  });
  $('install').addEventListener('click', () => perform('Starting selected automatic setup…', async () => {
    const selected = profileCatalog.find(p => p.profile_name === $('install-profile').value);
    if (modelLibrary?.supported && modelLibrary.profiles.some(p => p.profile === selected.profile_name && p.compatible)) {
      await savePreferences();
      await installAdditionalModel(selected);
      return;
    }
    // Reattach before entering install/recovery. A completed or running saved
    // download is not a failed environment and must never be reinstalled.
    try {
      await observeSavedSetup();
      if (setup.busy || (setup.profile === selected.profile_name && setup.model_ready)) {
        pendingInstall = null;
        if (!setup.busy) {
          await savePreferences();
          if ($('install-sage').checked && !sageState.installed) await installOptional('sageattention', showSage);
          if (pageActive && $('install-sparse').checked && !sparseState.installed) await installOptional('sparse', showSparse);
          if (pageActive && $('install-turbo').checked && !turboState.installed) await installOptional('turbo', showTurbo);
        }
        return;
      }
    } catch (error) {
      if (!['setup_not_found', 'matching_release_required'].includes(error.code)) throw error;
    }
    // Retain only the public intent ID across an ambiguous response, never secrets.
    await savePreferences();
    pendingInstall ||= { profile: selected.profile_name, artifact_set_digest: selected.artifact_set_digest,
      sage_attention: $('install-sage').checked, turbo_lora: $('install-turbo').checked, sparse_attention: $('install-sparse').checked, request_id: requestId() };
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
  function inputValue(value) {
    if (value === null || value === undefined) return 'Not specified';
    if (typeof value === 'boolean') return value ? 'On' : 'Off';
    if (Array.isArray(value) && !value.length) return 'None';
    if (typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)) return 'Outside exact browser integer range';
    return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  }
  function prepareJobReuse(record, options) {
    if (!record?.settings || typeof record.prompt !== 'string') throw Error('Complete generation inputs were not recorded for this job.');
    const supported = new Set('profile seed width height num_frames steps sage_attention turbo_lora turbo_strength turbo_low_vram turbo_artifact_digest sparse_preset sparse_artifact_digest input_mode reference_images reference_videos reference_audios first_frame last_frame reference_artifact_digest'.split(' '));
    if (Object.keys(record.settings).some(key => !supported.has(key)) || Object.keys(record).some(key => !['prompt','settings','loras','sparse_attention'].includes(key))) throw Error('The saved job contains inputs this generation form cannot reproduce.');
    const settings = {input_mode:'text', reference_images:[], reference_videos:[], reference_audios:[], first_frame:null, last_frame:null,
      sage_attention:false, turbo_lora:false, turbo_strength:1, turbo_low_vram:false, sparse_preset:'off', ...record.settings};
    if (!options.profiles.includes(settings.profile)) throw Error('The saved model is not available on this daemon. The draft was not changed.');
    for (const key of ['seed','width','height','num_frames','steps']) {
      if (!Number.isSafeInteger(settings[key])) throw Error(`The saved ${key} is missing or outside the exact browser integer range.`);
    }
    if (!['text','frames','references'].includes(settings.input_mode) || !['off','quality','balanced','draft'].includes(settings.sparse_preset)) throw Error('The saved input mode or sparse preset is unsupported.');
    for (const [enabled, saved, active] of [
      [settings.turbo_lora, settings.turbo_artifact_digest, options.turbo],
      [settings.sparse_preset !== 'off', settings.sparse_artifact_digest, options.sparse],
      [settings.input_mode === 'references', settings.reference_artifact_digest, options.reference]
    ]) if (enabled && (!saved || saved !== active)) throw Error('The saved component pin differs from the active version. Reuse would change the combination; the draft was not changed.');
    const loras = record.loras || [];
    if (!Array.isArray(loras) || loras.length > 1 || (loras.length && (!options.lora || settings.profile.includes('int8') || !/^[A-Za-z0-9_-]{1,128}$/.test(loras[0])))) throw Error('The saved LoRA combination cannot be represented by this generation form.');
    if (record.sparse_attention != null) throw Error('This older job uses a legacy sparse-attention input that this form cannot reproduce.');
    for (const [key, maximum] of [['reference_images',9],['reference_videos',3],['reference_audios',3],['first_frame',1],['last_frame',1]]) {
      const values = key.startsWith('reference_') ? settings[key] : settings[key] ? [settings[key]] : [];
      if (!Array.isArray(values) || values.length > maximum || values.some(id => typeof id !== 'string' || !/^[a-f0-9]{64}$/.test(id))) throw Error('Saved asset identifiers or counts are invalid.');
    }
    return {prompt:record.prompt, settings, lora:loras[0] || ''};
  }
  async function reuseJobInputs(job) {
    const notice = $('reuse-inputs-status');
    notice.hidden = false; notice.textContent = 'Reading saved inputs and checking assets…';
    const epoch = assetEpoch;
    let applied = false;
    const options = () => ({profiles:Array.from($('generation-profile').options, option => option.value),
      turbo:capabilities.turbo_artifact_digest, sparse:capabilities.sparse_artifact_digest,
      reference:referenceState.artifact_digest, lora:backendCapabilities.lora?.status === 'supported'});
    try {
      const draft = prepareJobReuse(job.request, options()), assets = {}, checked = new Map();
      for (const key of Object.keys(assetDraft)) {
        assets[key] = [];
        const ids = key.startsWith('reference_') ? draft.settings[key] : draft.settings[key] ? [draft.settings[key]] : [];
        for (const id of ids) {
          if (!checked.has(id)) {
            let asset;
            try { asset = await request('/remote/assets/' + id); }
            catch { throw Error('A saved asset is missing or could not be checked. Reconnect or restore the asset before reusing this job.'); }
            checked.set(id, asset);
          }
          const asset = checked.get(id), kind = key === 'reference_videos' ? 'video' : key === 'reference_audios' ? 'audio' : 'image';
          if (asset.id !== id || asset.kind !== kind) throw Error('Saved asset metadata does not match the job.');
          assets[key].push({...asset});
        }
      }
      if (epoch !== assetEpoch || connection.state !== 'authenticated') throw Error('The connection changed while reading inputs. Try again on the original server.');
      prepareJobReuse(job.request, options()); // Recheck pins/profiles after asynchronous metadata reads.
      const s = draft.settings;
      applied = true;
      $('prompt').value = draft.prompt; $('generation-profile').value = s.profile;
      for (const [id,key] of [['seed','seed'],['width','width'],['height','height'],['frames','num_frames'],['steps','steps'],['turbo-strength','turbo_strength'],['input-mode','input_mode'],['sparse-preset','sparse_preset']]) $(id).value = String(s[key]);
      for (const [id,key] of [['use-sage','sage_attention'],['use-turbo','turbo_lora'],['turbo-low-vram','turbo_low_vram']]) $(id).checked = s[key];
      $('lora').value = draft.lora;
      assetEpoch++;
      for (const key of Object.keys(assetDraft)) assetDraft[key] = assets[key];
      syncDuration('frames'); syncResolutionPresets(); showInputAssets(); render(); scheduleEstimate();
      notice.textContent = `Inputs from job ${job.job_id.slice(0,8)} copied into the generation draft. Nothing was queued or installed. Review the settings, then click Queue generation when ready.`;
    } catch (error) { notice.textContent = 'Could not reuse inputs. ' + (error.message || 'The saved inputs are unavailable.') + (applied ? ' Review the draft before queueing; its display could not be fully refreshed.' : ' Existing draft unchanged.'); }
    globalThis.H3Studio?.show('generate');
    notice.scrollIntoView({block:'center'}); notice.focus({preventScroll:true});
  }
  function jobInputs(request) {
    const details = node('details', undefined, 'job-inputs');
    details.append(node('summary', 'Inputs'));
    if (!request) { details.append(node('p', 'Inputs were not recorded.')); return details; }
    details.append(node('h5', 'Prompt'), node('p', request.prompt ?? 'Not recorded', 'job-prompt'));
    const fields = node('dl');
    const labels = { profile: 'Model', seed: 'Seed', width: 'Width', height: 'Height', num_frames: 'Frames',
      steps: 'Sampling steps', turbo_lora: 'Turbo LoRA v4-600 EMA', turbo_strength: 'Turbo strength', turbo_low_vram: 'Turbo low VRAM', turbo_artifact_digest: 'Turbo pinned artifact set', sage_attention: 'SageAttention', sparse_attention: 'Sparse attention', sparse_preset: 'Sparse attention preset', sparse_artifact_digest: 'Sparse pinned component', loras: 'LoRAs' };
    const add = (key, value) => fields.append(node('dt', labels[key] || key.replaceAll('_', ' ')), node('dd', inputValue(value)));
    for (const [key, value] of Object.entries(request.settings || {})) {
      if (['reference_images','reference_videos','reference_audios','first_frame','last_frame'].includes(key)) {
        const ids = Array.isArray(value) ? value : value ? [value] : [];
        if (ids.length) {
          fields.append(node('dt', key.replaceAll('_', ' ')));
          const links = node('dd');
          ids.forEach((id, i) => { if (!/^[a-f0-9]{64}$/.test(id)) return; const a = node('a', `Asset ${i + 1} · ${id.slice(0, 12)}`); a.href = API + '/remote/assets/' + id + '/content'; a.target = '_blank'; a.rel = 'noopener'; links.append(a, node('br')); });
          fields.append(links);
        }
        continue;
      }
      if (key === 'reference_artifact_digest' && request.settings.input_mode !== 'references') continue;
      if (key === 'sparse_artifact_digest' && request.settings.sparse_preset === 'off') continue;
      if (key.startsWith('turbo_') && key !== 'turbo_lora' && !request.settings.turbo_lora) continue;
      const presetNames = {off: 'Off — original full attention', quality: 'Quality-first — 50%', balanced: 'Balanced — 30%', draft: 'Fast draft — 15%'};
      add(key, key === 'sparse_preset' ? presetNames[value] || value : value);
    }
    if (!request.settings) details.append(node('p', 'Generation settings were not recorded for this older job.'));
    const loras = request.settings?.turbo_lora ? ['Turbo v4-600 EMA'] : [];
    for (const lora of request.loras || []) loras.push(inputValue(lora));
    add('loras', loras.length ? loras.join('\n') : request.settings ? 'None' : 'Not recorded');
    for (const [key, value] of Object.entries(request)) {
      if (['prompt', 'settings', 'loras'].includes(key)) continue;
      if (key === 'sparse_attention' && value == null) continue;
      add(key, value);
    }
    details.append(fields);
    return details;
  }
  function updateJobTimes() {
    for (const job of currentJobs) {
      const card = jobCards.get(job.job_id);
      if (!card) continue;
      const age = Math.max(0, (performance.now() - (job.timingReceivedAt ?? performance.now())) / 1000);
      const live = ['queued', 'running'].includes(job.status);
      const stale = live && (connection.state !== 'authenticated' || age > 15);
      card.timing.textContent = H3JobTiming.timingText(job, stale ? 0 : age) + (stale ? ' (last update)' : '');
      if (live && job.estimate?.samples > 0) {
        card.timing.textContent += ' · ' + estimateText(job.estimate);
        const t = job.timing;
        if (job.status === 'running' && t && Number.isFinite(t.started_at) && Number.isFinite(t.observed_at)) {
          const remaining = job.estimate.mean_seconds - (t.observed_at + (stale ? 0 : age) - t.started_at);
          card.timing.textContent += remaining > 0 ? ' · Roughly ' + timeLabel(remaining) + ' remaining' : ' · Taking longer than the measured average';
        }
      }
    }
  }
  let timingTimer = setInterval(() => { if (pageActive) updateJobTimes(); }, 1000);
  function observePageJobs(jobs) {
    for (const job of jobs) {
      if (['queued', 'running'].includes(job.status) || (pageJobsInitialized && !seenPageJobs.has(job.job_id))) {
        currentPageJobs.add(job.job_id);
      }
      seenPageJobs.add(job.job_id);
    }
    pageJobsInitialized = true;
  }
  function jobMatchesTab(job, tab) {
    const current = ['queued', 'running'].includes(job.status) || currentPageJobs.has(job.job_id);
    return current === (tab === 'current');
  }
  function renderJobTabs() {
    for (const tab of ['current', 'history']) {
      const button = $('jobs-tab-' + tab);
      const count = currentJobs.filter(job => jobMatchesTab(job, tab)).length;
      button.textContent = `${tab === 'current' ? 'Current' : 'History'} (${count})`;
      button.setAttribute('aria-selected', String(tab === jobsTab));
      button.tabIndex = tab === jobsTab ? 0 : -1;
    }
    $('jobs').setAttribute('aria-labelledby', 'jobs-tab-' + jobsTab);
    for (const [id, card] of jobCards) card.item.hidden = !jobMatchesTab({job_id: id, status: card.item.dataset.status}, jobsTab);
    const visible = currentJobs.filter(job => jobMatchesTab(job, jobsTab));
    if (typeof jobSelection !== 'undefined') jobSelection?.update(visible, ready && !busy && connection.state === 'authenticated');
    $('jobs-empty').hidden = visible.length > 0;
    $('jobs-empty').textContent = jobsTab === 'current' ? 'No jobs in this page view. Queue a generation to get started.' : 'No earlier finished jobs.';
    if (!visible.some(job => job.job_id === selectedJob)) {
      selectedJob = visible.find(job => job.status === 'running')?.job_id
        || (jobsTab === 'current' ? visible.find(job => job.status === 'queued')?.job_id : null) || visible[0]?.job_id || null;
    }
    showJobTerminal();
  }
  function prepareVideoPreview(video, url, notice) {
    video.preload = 'none'; video.playsInline = true;
    notice.textContent = 'Preview loads when visible.'; notice.hidden = false;
    videoPreviews.set(video, {url, notice, started:false});
    video.addEventListener('loadeddata', () => { notice.hidden = true; });
    video.addEventListener('error', () => {
      notice.hidden = false;
      notice.textContent = video.error?.code === 3 || video.error?.code === 4
        ? 'This browser could not decode the video preview. The original MP4 is available through Download MP4.'
        : 'Preview could not load. Check the connection or whether the output expired; Download MP4 remains available for existing outputs.';
    });
    if (typeof IntersectionObserver === 'undefined') { startVideoPreview(video); return; }
    if (!previewObserver) previewObserver = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) startVideoPreview(entry.target);
    }, {rootMargin:'240px 0px'});
    previewObserver.observe(video);
  }
  function startVideoPreview(video) {
    const state = videoPreviews.get(video);
    if (!state || state.started) return;
    state.started = true;
    previewObserver?.unobserve(video);
    state.notice.textContent = 'Loading video preview…'; state.notice.hidden = false;
    // WebKit metadata-only preload leaves a gray player without a decoded frame.
    // Only nearby visible players load media, not every card in hidden history.
    video.preload = 'auto'; video.src = state.url; video.load();
  }
  function releaseVideoPreviews(root) {
    for (const video of root.querySelectorAll('video')) {
      previewObserver?.unobserve(video); videoPreviews.delete(video);
      video.pause(); video.removeAttribute('src'); video.load();
    }
  }
  async function fetchLastFrame(jobId) {
    const response = await fetch(API + '/remote/jobs/' + jobId + '/last-frame', {credentials:'same-origin', cache:'no-store', redirect:'error'});
    if (!response.ok) {
      let data = {}; try { data = await response.json(); } catch {}
      throw {code:data.detail || data.error?.code || 'last_frame_unavailable'};
    }
    if (!response.headers.get('content-type')?.startsWith('image/png')) throw {code:'invalid_response'};
    const blob = await response.blob();
    if (!blob.size || blob.size > 32 * 1024 * 1024) throw {code:'invalid_response'};
    return blob;
  }
  function lastFrameButton(jobId, reuse) {
    const button = node('button', reuse ? 'Use last frame as first frame' : 'Download last frame (PNG)', 'secondary'); button.type = 'button';
    if (reuse) button.dataset.requiresFrames = 'true';
    button.disabled = busy || (reuse && !inputModes.includes('frames'));
    button.title = reuse ? 'Replace the draft’s first frame; other settings stay unchanged. Requires a pruned profile.' : 'Uncompressed PNG at native video resolution; no resizing or JPEG conversion';
    button.addEventListener('click', () => perform(reuse ? 'Preparing last frame for the next generation…' : 'Exporting uncompressed last frame…', async () => {
      if (reuse && !inputModes.includes('frames')) throw {code:'frame_inputs_unsupported'};
      const epoch = assetEpoch;
      const blob = await fetchLastFrame(jobId);
      if (epoch !== assetEpoch) throw {code:'asset_target_changed'};
      const name = jobId + '-last-frame.png';
      if (reuse) {
        await uploadInputAssets('first_frame', [new File([blob], name, {type:'image/png'})]);
        $('input-mode').value = 'frames'; showInputAssets(); render(); scheduleEstimate();
        globalThis.H3Studio?.show('generate');
        $('frame-assets').scrollIntoView({block:'center'});
      } else {
        const url = URL.createObjectURL(blob), link = node('a');
        link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    }));
    return button;
  }
  function reconcileJobs(jobs) {
    observePageJobs(jobs);
    currentJobs = jobs;
    const queued = jobs.filter(job => job.status === 'queued').slice().reverse();
    const running = jobs.filter(job => job.status === 'running');
    const history = jobs.filter(job => !['queued', 'running'].includes(job.status));
    $('queue-summary').textContent = `${running.length} running · ${queued.length} waiting · ${history.length} finished`;
    if (!selectedJob || !jobs.some(job => job.job_id === selectedJob)) selectedJob = running[0]?.job_id || queued[0]?.job_id || jobs[0]?.job_id;
    const ordered = [...running, ...queued, ...history];
    const present = new Set(jobs.map(job => job.job_id));
    for (const [id, card] of jobCards) {
      if (!present.has(id)) {
        releaseVideoPreviews(card.output);
        jobSelection?.forget(id);
        card.item.remove(); jobCards.delete(id); jobLogs.delete(id); jobPhases.delete(id);
      }
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
        const selectionLabel = node('label', undefined, 'job-selection-label');
        const select = node('input'); select.type = 'checkbox'; select.setAttribute('aria-label', `Select job ${job.job_id.slice(0, 8)}`);
        selectionLabel.append(select, node('span', 'Select'));
        jobSelection?.register(job.job_id, select);
        const remove = node('button', 'Delete job', 'secondary'); remove.type = 'button';
        remove.addEventListener('click', () => {
          if (busy || remove.disabled || !confirm('Permanently delete this job, its result video, logs, timing records and references? References used by other saved jobs will be kept. This cannot be undone.')) return;
          perform('Deleting selected job and its files…', async () => {
            const result = await request('/remote/jobs/' + job.job_id + '/delete', {});
            const removed = new Set(result.removed_assets || []);
            for (const key of Object.keys(assetDraft)) assetDraft[key] = assetDraft[key].filter(asset => !removed.has(asset.id));
            showInputAssets();
            currentPageJobs.delete(job.job_id); seenPageJobs.delete(job.job_id);
            if (selectedJob === job.job_id) selectedJob = null;
            estimateKey = null;
            await dashboard();
          });
        });
        const reuse = node('button', 'Reuse inputs', 'secondary'); reuse.type = 'button';
        reuse.dataset.reuseMissing = String(!job.request?.settings);
        reuse.disabled = busy || !job.request?.settings;
        reuse.title = job.request?.settings ? 'Replace the generation draft with this job’s saved inputs. Does not queue generation.' : 'Complete generation inputs were not recorded for this job.';
        reuse.addEventListener('click', () => {
          if (busy || reuse.disabled) return;
          void perform('Prefilling saved generation inputs…', () => reuseJobInputs(currentJobs.find(value => value.job_id === job.job_id) || job));
        });
        heading.append(selectionLabel, title, logs, reuse, remove);
        const meter = node('progress'); meter.setAttribute('aria-label', `Progress for job ${job.job_id.slice(0, 8)}`);
        const detail = node('p', undefined, 'job-detail');
        const settings = job.request?.settings;
        const caption = node('p', settings ? `${settings.width} × ${settings.height} · ${settings.num_frames} frames · ${settings.steps} sampling steps · Seed ${inputValue(settings.seed)} · ${settings.sage_attention ? 'SageAttention' : 'standard attention'}${settings.turbo_lora ? ' · Turbo v4-600 EMA' : ''}${settings.sparse_preset && settings.sparse_preset !== 'off' ? ' · Sparse requested: ' + settings.sparse_preset : ''}` : '', 'field-hint');
        const timing = node('p', undefined, 'job-timing');
        timing.title = 'Current attempt: Run includes loading, sampling and encoding. Total includes queue waiting.';
        item.append(heading, caption, jobInputs(job.request), status, timing, meter, detail, output);
        card = { item, status, timing, meter, detail, output, remove, cancel: null, outputIdentity: null };
        jobCards.set(job.job_id, card);
        $('jobs').insertBefore(item, nextItem);
      }
      if (card.item !== nextItem) $('jobs').insertBefore(card.item, nextItem);
      nextItem = card.item.nextElementSibling;
      card.item.dataset.status = job.status;
      card.remove.disabled = busy || ['queued', 'running'].includes(job.status);
      card.remove.title = ['queued', 'running'].includes(job.status) ? 'Cancel this job first, then wait for it to stop before deleting.' : 'Permanently delete this job and its unshared files';
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
      releaseVideoPreviews(card.output);
      card.output.replaceChildren();
      if (available) {
        const url = API + '/remote/jobs/' + job.job_id + '/video';
        const video = node('video'); video.controls = true;
        const dimensions = job.request?.settings;
        if (dimensions?.width > 0 && dimensions?.height > 0) { video.width = dimensions.width; video.height = dimensions.height; }
        const error = node('p'); error.setAttribute('role', 'status');
        prepareVideoPreview(video, url, error);
        const link = node('a', 'Download MP4'); link.href = url + '?download=true'; link.download = job.job_id + '.mp4';
        const actions = node('div', undefined, 'job-output-actions');
        const addToJoiner = node('button', 'Add to joiner'); addToJoiner.type = 'button'; addToJoiner.dataset.joiner = 'true';
        addToJoiner.addEventListener('click', () => { void joiner?.addHistory(job); });
        actions.append(addToJoiner);
        actions.append(link, lastFrameButton(job.job_id, false), lastFrameButton(job.job_id, true));
        card.output.append(video, actions,
          node('p', 'Last frame: uncompressed PNG, native resolution. No additional image loss; existing video compression cannot be undone.', 'field-hint'), error);
      } else if (job.output?.expired) card.output.append(node('p', 'Output expired by retention policy.'));
    }
    renderJobTabs();
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
    modelState = capability.resident_models || {};
    showModelLibrary(capability.model_library || null);
    performanceAvailable = capability.performance_schema === 1;
    setResolutionLimits(capability.service_limits || {});
    showSage(capability.optional_features?.sage_attention);
    showTurbo(capability.optional_features?.turbo_lora);
    showSparse(capability.optional_features?.sparse_attention);
    inputModes = capability.input_modes || ['text'];
    referenceState = capability.optional_features?.reference || {};
    showInputAssets(false);
    const profiles = capability.profiles || [];
    const profileKey = JSON.stringify([capability.backend, profiles]);
    if (profileKey !== activeProfileKey) {
      activeProfileKey = profileKey;
      const previousProfile = $('generation-profile').value;
      $('generation-profile').replaceChildren();
      for (const name of profiles) {
        const option = node('option', profileLabel(name)); option.value = name; $('generation-profile').append(option);
      }
      if (profiles.includes(previousProfile)) $('generation-profile').value = previousProfile;
      if (!previousProfile) $('steps').value = $('use-turbo').checked ? 6 : capability.service_limits.steps_default;
    }
    const diagnostic = capability.diagnostic || {};
    const backgroundCheck = diagnostic.environment_ready === true && diagnostic.model_ready === true
      && capability.not_ready_reasons?.length === 1
      && capability.not_ready_reasons[0] === 'Runtime and verified model diagnostic is running';
    runtimeReady = (capability.runtime_ready || backgroundCheck) && profiles.length > 0;
    const generated = data.jobs.some(job => profiles.includes(job.request?.settings?.profile) && job.status === 'completed' && job.output && !job.output.expired);
    $('readiness').textContent = `Daemon connected. Active backend: ${capability.backend}; profiles: ${profiles.join(', ')}. Environment ready: ${diagnostic.environment_ready === true}. Model files ready: ${diagnostic.model_ready === true}. Runtime ready: ${runtimeReady}. Generation tested: ${generated ? 'completed job available (see output)' : 'not proven'}. ${(capability.not_ready_reasons || []).join(' ')}`;
    reconcileJobs(data.jobs);
    // New/deleted measurements and residency changes invalidate an otherwise
    // unchanged lookup; ordinary polling must not flash or repeat it.
    estimateContext = JSON.stringify([data.jobs.filter(job => job.status === 'completed').map(job => job.job_id).sort(), modelState.loaded, modelState.profile, modelState.components]);
    scheduleEstimate();
    await jobEvents(current);
    if (!current()) return;
    render();
    jobTimer = setTimeout(() => {
      if (!current()) return;
      const nextSequence = dashboardRequest + 1;
      dashboard().catch(error => {
        if (epoch === dashboardEpoch && nextSequence === dashboardRequest) { runtimeReady = false; render(); showError(error, false); }
      });
    }, data.jobs.some(job => ['queued', 'running'].includes(job.status)) || sageState.busy || turboState.busy || modelLibrary?.busy || modelLibrary?.synchronization?.busy ? 2000 : 5000);
  }
  $('dashboard-refresh').addEventListener('click', () => perform('Checking daemon, model and queued jobs…', async () => {
    estimateKey = null;
    await dashboard();
  }));
  function resolutionPresets() {
    return [
      {id:'landscape', label:'Landscape ≈16:9', sizes:{small:[512,288], p480:[864,480], medium:[1024,576], p720:[1280,736], large:[1344,768], p1080:[1920,1088]}},
      {id:'portrait', label:'Portrait ≈9:16', sizes:{small:[288,512], p480:[480,864], medium:[576,1024], p720:[736,1280], large:[768,1344], p1080:[1088,1920]}},
      {id:'square', label:'Square 1:1', sizes:{small:[512,512], medium:[768,768], large:[992,992]}},
      {id:'landscape-4-3', label:'Landscape 4:3', sizes:{small:[512,384], medium:[768,576], large:[1152,864]}},
      {id:'portrait-3-4', label:'Portrait 3:4', sizes:{small:[384,512], medium:[576,768], large:[864,1152]}}
    ];
  }
  function canvasFits(width, height) {
    const axis = Number($('width').max) || 1344;
    const pixels = Number($('width').dataset.canvasPixels) || 1344 * 768;
    return [width, height].every(n => Number.isInteger(n) && n >= 32 && n <= axis && n % 32 === 0) && width * height <= pixels;
  }
  function setResolutionLimits(limits) {
    const axis = limits.axis_max || 1344, pixels = limits.canvas_pixels || 1344 * 768;
    if ($('width').max === String(axis) && $('width').dataset.canvasPixels === String(pixels)) return;
    $('width').max = $('height').max = String(axis);
    $('width').dataset.canvasPixels = String(pixels);
    $('canvas-limit-note').textContent = `Canvas area at most ${pixels.toLocaleString('en-US')} pixels; axes must be multiples of 32, up to ${axis}. 24 fps, video plus audio.`;
    syncResolutionPresets();
  }
  function resolutionOptions(aspect, tier) {
    const select = $('resolution-preset');
    select.replaceChildren();
    for (const [name, size] of Object.entries(resolutionPresets().find(p => p.id === aspect)?.sizes || {})) {
      const label = {p480:'480p-size', p720:'720p-size (aligned)', p1080:'1080p-size (aligned)'}[name] || name.charAt(0).toUpperCase() + name.slice(1);
      const supported = canvasFits(...size);
      const option = node('option', `${label} · ${size[0]} × ${size[1]}${supported ? '' : ' · exceeds daemon limit'}`);
      option.value = name; option.disabled = !supported; select.append(option);
    }
    const custom = node('option', 'Custom'); custom.value = 'custom'; select.append(custom);
    select.value = tier;
  }
  function syncResolutionPresets() {
    const width = Number($('width').value), height = Number($('height').value);
    const warning = !canvasFits(width, height) ? 'These dimensions exceed this daemon’s limits or are not aligned to 32 pixels. Select an enabled preset or correct the dimensions.'
      : width > 1344 || height > 1344 || width * height > 1344 * 768 ? 'Higher-memory native generation: this size is not GPU-benchmarked here and may run out of memory. Start with a short clip. 1080p-size uses 1920 × 1088 (or portrait), not an upscaled or cropped 1920 × 1080 output.' : '';
    $('resolution-warning').textContent = warning; $('resolution-warning').hidden = !warning;
    for (const preset of resolutionPresets()) {
      for (const [tier, size] of Object.entries(preset.sizes)) {
        if (size[0] === width && size[1] === height) {
          $('aspect-ratio').value = preset.id; resolutionOptions(preset.id, tier); return;
        }
      }
    }
    $('aspect-ratio').value = 'custom'; resolutionOptions('custom', 'custom');
  }
  function applyResolutionPreset(aspect, tier) {
    if (tier === 'custom') return;
    const sizes = resolutionPresets().find(p => p.id === aspect)?.sizes;
    const size = sizes?.[tier] || sizes?.medium;
    if (!size) return;
    $('width').value = size[0]; $('height').value = size[1];
    syncResolutionPresets();
  }
  function assetDimensions(asset) {
    if (!['image', 'video'].includes(asset.kind) || ![asset.width, asset.height].every(n => Number.isSafeInteger(n) && n > 0)) return null;
    const width = Math.max(32, Math.round(asset.width / 32) * 32);
    const height = Math.max(32, Math.round(asset.height / 32) * 32);
    const exact = width === asset.width && height === asset.height;
    const fits = canvasFits(width, height);
    const message = `Source ${asset.width} × ${asset.height} → output ${width} × ${height}. `
      + (exact ? 'Exact 32 px-aligned match. ' : 'Rounded to the nearest 32 px (ties round up, minimum 32); not an exact match. Aspect ratio may change. ')
      + 'Source file unchanged.'
      + (fits ? '' : ' This size exceeds the daemon’s axis or canvas-area limits. Choose a smaller output size manually.');
    return {width, height, exact, fits, message};
  }
  function applyAssetDimensions(asset) {
    const size = assetDimensions(asset);
    if (busy || !size?.fits) return false;
    $('width').value = size.width; $('height').value = size.height;
    syncResolutionPresets(); render(); scheduleEstimate();
    return true;
  }
  function conditioningBody() {
    const mode = $('input-mode').value;
    if (mode === 'text') return {};
    if (mode === 'frames') return {input_mode:mode, first_frame:assetDraft.first_frame[0]?.id || null, last_frame:assetDraft.last_frame[0]?.id || null};
    return {input_mode:mode, reference_artifact_digest:referenceState.artifact_digest, reference_images:assetDraft.reference_images.map(a=>a.id), reference_videos:assetDraft.reference_videos.map(a=>a.id), reference_audios:assetDraft.reference_audios.map(a=>a.id)};
  }
  function conditioningReasons() {
    const mode = $('input-mode').value, reasons = [];
    if (referenceState.busy) reasons.push('Ref2VA download is running. Wait for its integrity checks to finish.');
    if (mode === 'text') return reasons;
    if (!inputModes.includes(mode)) reasons.push('This daemon/profile does not support the selected input mode. Use a supported pruned profile or Text mode.');
    if ($('sparse-preset').value !== 'off') reasons.push('Sparse attention is not verified with reference or frame inputs. Select Sparse: Off.');
    if (mode === 'frames' && !assetDraft.first_frame.length && !assetDraft.last_frame.length) reasons.push('Upload a first or last frame image (or both).');
    if (mode === 'references') {
      if ($('generation-profile').value !== 'h3-convrot-int8') reasons.push('Reference mode requires the ConvRot INT8 generation profile. Select INT8 or switch to Text / First-last frames.');
      if (!referenceState.installed) reasons.push('Install the separate Ref2VA checkpoint using the button below.');
      if (assetDraft.reference_videos.reduce((s,a)=>s+(a.duration||0),0)>15.1) reasons.push('Reference videos must total at most 15 seconds. Remove a clip.');
    }
    return reasons;
  }
  function showInputAssets(rebuild = true) {
    const mode = $('input-mode').value;
    $('reference-assets').hidden = mode !== 'references'; $('frame-assets').hidden = mode !== 'frames';
    $('asset-rules').hidden = mode === 'text';
    $('input-mode-status').hidden = inputModes.includes(mode);
    $('input-mode-status').textContent = inputModes.includes(mode) ? '' : 'This daemon/profile does not support this input mode. First/last frames require a pruned profile; references currently require ConvRot INT8.';
    $('reference-model-status').textContent = referenceState.installed ? 'Ref2VA INT8 checkpoint verified and available.' : referenceState.busy ? `Downloading Ref2VA: ${Math.round((referenceState.completed || 0)/1048576)} / ${Math.round((referenceState.total || 0)/1048576)} MiB` : referenceState.error || 'References require a separate ~21 GB INT8 checkpoint (~42 GB disk with its verified runtime copy). Your FL2VA model is preserved.';
    if (!rebuild) { for (const update of assetDimensionUpdates) update(); return; }
    assetDimensionUpdates.length = 0;
    for (const [field, assets] of Object.entries(assetDraft)) {
      const root = $('assets-' + field); root.replaceChildren();
      assets.forEach((asset,index) => {
        const card = node('div', undefined, 'asset-preview');
        const label = field === 'first_frame' ? 'First frame' : field === 'last_frame' ? 'Last frame' : `<${{image:'Picture',video:'Video',audio:'Audio'}[asset.kind]} ${index + 1}>`;
        card.append(node('strong', label), node('span', asset.name || asset.id.slice(0,12)));
        const preview = node(asset.kind === 'image' ? 'img' : asset.kind);
        preview.src = API + '/remote/assets/' + asset.id + '/content';
        if (asset.kind === 'image') { preview.alt = label; preview.loading = 'lazy'; }
        else { preview.controls = true; preview.preload = 'metadata'; }
        card.append(preview);
        if (assetDimensions(asset)) {
          const dimensions = node('button', 'Use dimensions', 'secondary'); dimensions.type = 'button';
          const note = node('p', undefined, 'field-hint asset-dimensions'); note.setAttribute('role', 'status');
          note.id = `asset-dimensions-${field}-${index}`; dimensions.setAttribute('aria-describedby', note.id);
          const update = () => {
            const size = assetDimensions(asset);
            dimensions.disabled = busy || !size.fits;
            dimensions.title = size.fits ? 'Copy these aligned dimensions into the generation Width and Height' : 'Rounded dimensions exceed the daemon’s limits';
            const current = Number($('width').value) === size.width && Number($('height').value) === size.height;
            const message = size.message + (current ? ' Current output uses this size.' : '');
            if (note.textContent !== message) note.textContent = message;
          };
          dimensions.addEventListener('click', () => { applyAssetDimensions(asset); update(); });
          assetDimensionUpdates.push(update); update(); card.append(dimensions, note);
        }
        const remove = node('button', 'Remove', 'secondary'); remove.type = 'button';
        remove.addEventListener('click', () => { assetDraft[field].splice(index,1); showInputAssets(); render(); scheduleEstimate(); });
        card.append(remove); root.append(card);
      });
    }
  }
  async function uploadInputAssets(field, files) {
    const maximum = field === 'reference_images' ? 9 : field.startsWith('reference_') ? 3 : 1;
    const kind = field === 'reference_videos' ? 'video' : field === 'reference_audios' ? 'audio' : 'image';
    if (files.length + (maximum === 1 ? 0 : assetDraft[field].length) > maximum) throw {code:'asset_count_limit'};
    const epoch = assetEpoch;
    for (const file of files) {
      if (!file.size || file.size > 64*1024*1024) throw {code:'asset_size_limit'};
      let response, value;
      try { response = await fetch(API + '/remote/assets/' + kind, {method:'POST', credentials:'same-origin', cache:'no-store', redirect:'error', headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf}, body:file}); }
      catch { throw {code:'network_unavailable'}; }
      try { value = await response.json(); } catch { throw {code:'invalid_response'}; }
      if (!response.ok) throw {code:value.detail || value.error?.code || value.error};
      if (epoch !== assetEpoch) throw {code:'asset_target_changed'};
      if (maximum === 1) assetDraft[field] = [];
      assetDraft[field].push({...value, name:file.name}); showInputAssets();
    }
    render(); scheduleEstimate();
  }
  function generationBody() {
      const body = { ...conditioningBody(), prompt: $('prompt').value, profile: $('generation-profile').value, num_frames: Number($('frames').value),
        width: Number($('width').value), height: Number($('height').value), steps: Number($('steps').value), seed: Number($('seed').value),
        ...(sparseState.artifact_digest === capabilities.sparse_artifact_digest ? {
          sparse_preset: $('sparse-preset').value, sparse_artifact_digest: $('sparse-preset').value !== 'off' ? capabilities.sparse_artifact_digest : null
        } : {}),
        sage_attention: $('use-sage').checked, turbo_lora: $('use-turbo').checked,
        turbo_strength: Number($('turbo-strength').value), turbo_low_vram: $('turbo-low-vram').checked,
        turbo_artifact_digest: $('use-turbo').checked ? capabilities.turbo_artifact_digest : null };
      if (!$('lora-field').hidden && $('lora').value) body.loras = [$('lora').value];
      return body;
  }
  function timeLabel(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return 'unavailable';
    const s = Math.round(seconds);
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }
  function estimateText(value) {
    if (!value || !(value.samples > 0) || !Number.isFinite(value.mean_seconds)) return 'No estimate yet. ' + (value?.reason || 'No matching measured runs.');
    const spread = value.samples > 1 ? `; observed ${timeLabel(value.minimum_seconds)}–${timeLabel(value.maximum_seconds)}` : '; provisional';
    return `Estimate ~${timeLabel(value.mean_seconds)} (${value.cache === 'warm_partial' ? 'reused base/encoder, decoder reload' : value.cache === 'encoder_reuse' ? 'encoder retained, other weights reloaded' : value.cache}; ${value.samples} matching ${value.samples === 1 ? 'run' : 'runs'}${spread}).${value.match === 'nearby_prompt_length' ? ' Approximate: matching settings, different prompt length.' : ''} Queue delay excluded.`;
  }
  function scheduleEstimate() {
    const body = generationBody();
    const promptLength = Math.max(1, Math.min(16000, Array.from(body.prompt).length));
    const key = JSON.stringify([connection.state, performanceAvailable, estimateContext, {...body, prompt:Math.ceil(promptLength / 256)}]);
    // Dashboard polling observes jobs; it must not restart an unchanged lookup
    // or replace the displayed estimate with a periodic loading message.
    if (key === estimateKey) return;
    estimateKey = key;
    clearTimeout(estimateTimer);
    estimateAbort?.abort();
    const epoch = ++estimateEpoch;
    $('estimate-details').hidden = true;
    if (!performanceAvailable || connection.state !== 'authenticated') {
      $('generation-estimate').textContent = 'Timing estimates are not available from this daemon yet.';
      return;
    }
    $('generation-estimate').textContent = 'Checking measurements for these settings…';
    estimateTimer = setTimeout(async () => {
      const controller = new AbortController(); estimateAbort = controller;
      const deadline = setTimeout(() => controller.abort(), 30000);
      try {
        // Only length affects timing lookup; do not send an unsubmitted prompt.
        body.prompt = 'x'.repeat(promptLength);
        const estimate = await request('/remote/performance/estimate', body, {signal:controller.signal});
        if (!pageActive || epoch !== estimateEpoch) return;
        $('generation-estimate').textContent = estimateText(estimate);
        $('estimate-details').hidden = !(estimate.samples > 0);
        $('estimate-phases').textContent = Object.entries(estimate.stages || {}).map(([name, value]) => `${name.replaceAll('_', ' ')}: ~${timeLabel(value.mean_seconds)} (${value.samples} samples)`).join(' · ');
        $('estimate-steps').textContent = (estimate.sampling_steps || []).map((value, i) => `Step ${i + 1}: ${value.mean_seconds.toFixed(2)}s (${value.samples} samples)`).join('\n');
      } catch {
        if (pageActive && epoch === estimateEpoch) $('generation-estimate').textContent = 'Estimate unavailable or lookup timed out. Click Refresh dashboard to retry; no generation was submitted.';
      } finally {
        clearTimeout(deadline);
        if (estimateAbort === controller) estimateAbort = null;
      }
    }, 450);
  }
  $('input-mode').addEventListener('change', () => { showInputAssets(); render(); scheduleEstimate(); });
  for (const field of Object.keys(assetDraft)) $('upload-' + field).addEventListener('change', event => {
    const files = Array.from(event.target.files); event.target.value = '';
    if (files.length) perform('Uploading and validating reference assets…', () => uploadInputAssets(field, files));
  });
  $('install-reference').addEventListener('click', () => {
    if (!confirm('Download the additional ~21 GB pinned Ref2VA INT8 checkpoint under the MiniMax H3 Community License? Allow ~42 GB disk for the original and verified runtime copy. Existing models are kept. The queue must be idle.')) return;
    perform('Starting Ref2VA installation…', async () => {
      await request('/remote/features/reference', {artifact_digest:referenceState.artifact_digest}); await dashboard();
    });
  });
  showInputAssets();
  $('generation-form').addEventListener('input', () => { render(); scheduleEstimate(); });
  $('generation-form').addEventListener('change', () => { render(); scheduleEstimate(); });
  for (const preset of resolutionPresets()) {
    const option = node('option', preset.label); option.value = preset.id; $('aspect-ratio').append(option);
  }
  syncResolutionPresets();
  $('aspect-ratio').addEventListener('change', () => {
    const aspect = $('aspect-ratio').value;
    const tier = $('resolution-preset').value === 'custom' ? 'medium' : $('resolution-preset').value;
    if (aspect === 'custom') resolutionOptions('custom', 'custom');
    else applyResolutionPreset(aspect, tier);
  });
  $('resolution-preset').addEventListener('change', () => applyResolutionPreset($('aspect-ratio').value, $('resolution-preset').value));
  for (const id of ['width', 'height']) $(id).addEventListener('input', syncResolutionPresets);
  for (const tab of ['current', 'history']) {
    const button = $('jobs-tab-' + tab);
    button.addEventListener('click', () => { jobSelection?.reset(); jobsTab = tab; renderJobTabs(); });
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      jobsTab = event.key === 'Home' ? 'current' : event.key === 'End' ? 'history' : tab === 'current' ? 'history' : 'current';
      jobSelection?.reset(); renderJobTabs(); $('jobs-tab-' + jobsTab).focus();
    });
  }
  $('unload-models').addEventListener('click', () => perform('Releasing retained models…', async () => {
    modelState = await request('/remote/models/unload', {});
    estimateKey = null; // Explicit unload changes cold/reused timing semantics.
    await dashboard();
  }));
  $('export-timings').addEventListener('click', () => perform('Preparing local timing data…', async () => {
    const data = await request('/remote/performance');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
    const link = node('a'); link.href = url; link.download = 'h3-performance.json'; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }));
  $('generation-form').addEventListener('submit', event => {
    event.preventDefault();
    perform('Submitting local generation job…', async () => {
      const accepted = await request('/remote/jobs', generationBody());
      selectedJob = accepted.job_id || accepted.job?.job_id || null;
      if (selectedJob) currentPageJobs.add(selectedJob);
      jobsTab = 'current';
      await dashboard();
    }, { generationSubmission: true });
  });
  window.addEventListener('pagehide', () => { clearInterval(timingTimer); clearTimeout(estimateTimer); estimateAbort?.abort(); estimateEpoch++; pageActive = false; $('password').value = ''; $('local-sudo-password').value = ''; resetSetupPolling(); resetDashboardPolling(); });
  window.addEventListener('pageshow', event => { if (!event.persisted || pageActive) return; pageActive = true; estimateKey = null; timingTimer = setInterval(() => { if (pageActive) updateJobTimes(); }, 1000); scheduleEstimate(); void refresh().then(() => { if (connection.state === 'authenticated' && setup.installation_ready && !setup.busy) return dashboard(); }).catch(error => showError(error, false)); });

  $('lan-login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const token = $('lan-access-key').value;
    $('lan-access-key').value = '';
    try {
      csrf = (await request('/lan-login', {token})).csrf_token;
      $('lan-access').hidden = true;
      $('error').hidden = true;
      await start();
    } catch (error) { showError(error); }
  });
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
      if (preferences.connection && connection.mode !== 'local') {
        const saved = preferences.connection;
        lastTarget = { host: saved.host, port: saved.port, username: saved.username };
      }
      if (connection.mode === 'local') $('target-mode').value = 'local';
      if (connection.state === 'disconnected' && preferences.connection && connection.mode !== 'local') {
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
      try {
        if ((await request('/access')).lan) {
          $('lan-access').hidden = false;
          $('lan-access-key').focus();
          if (['session_required', 'bootstrap_invalid'].includes(error.code)) {
            $('error').hidden = true;
            $('operation').textContent = 'Enter the LAN access key to unlock this controller.';
            return;
          }
        }
      } catch (_) { /* Original session error remains authoritative. */ }
      showError(error);
    } finally {
      bootstrapToken = null;
      busy = false;
      render();
    }
  }
  start();
})();
