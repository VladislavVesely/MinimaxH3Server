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
    trust_required: 'Verify and approve the server fingerprint before authenticating.',
    authentication_failed: 'Authentication failed. The password was cleared. Reconnect and enter your credentials again.',
    authentication_required: 'SSH authentication is required. Reconnect and sign in again.',
    authentication_not_pending: 'Authentication is not pending. Refresh the connection state.',
    invalid_credentials: 'The password is invalid. Enter a nonempty password without null characters.',
    already_connected: 'A connection already exists. Refresh its state, or disconnect before changing servers.',
    invalid_remote_home: 'The server’s home directory could not be read. Check the SSH account and retry the scan.',
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
      const code = data.error?.code;
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
    connection = data;
    if (data.state !== 'authenticated') clearScan();
    if (data.state !== 'awaiting_authentication') $('password').value = '';
    render();
  }

  function render() {
    const state = connection.state;
    const usable = ready && !busy;
    const disconnected = state === 'disconnected';
    const authPending = state === 'awaiting_authentication';
    const trustPending = state === 'awaiting_approval';
    const authenticated = state === 'authenticated';
    const labels = { disconnected: 'SSH disconnected', awaiting_approval: 'Fingerprint verification needed', awaiting_authentication: 'Host trusted · sign in', authenticated: 'SSH authenticated' };
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
    $('scan').disabled = !usable || !authenticated || capabilities.scan !== true;
    $('scan-hint').textContent = authenticated
      ? (capabilities.scan === true ? 'Read-only observations only. No installation or CUDA runtime test is performed.' : 'Scanning is unavailable in this controller.')
      : 'Authenticate to enable the read-only scan.';
    for (const id of ['step-connect', 'step-scan', 'step-review']) $(id).removeAttribute('aria-current');
    $(scanned ? 'step-review' : authenticated ? 'step-scan' : 'step-connect').setAttribute('aria-current', 'step');
    $('progress').hidden = !busy;
    $('progress-note').hidden = !busy;
    $('connection').setAttribute('aria-busy', String(busy));
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

  function renderScan(data) {
    clearScan();
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
      item.append(node('h3', recommendation.profile_name));
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
    const pending = request('/authenticate', { password: $('password').value });
    $('password').value = '';
    perform('Authenticating with SSH…', async () => {
      setConnection(await pending);
      log('SSH authentication succeeded. Hardware has not been scanned yet.');
      $('scan-heading').tabIndex = -1;
      $('scan-heading').focus();
    });
  });
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
  window.addEventListener('pagehide', () => { $('password').value = ''; });

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
      $('operation').textContent = 'Ready for a secure SSH connection.';
      log('Local browser session secured. Only read-only setup is available.');
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
