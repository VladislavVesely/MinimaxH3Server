// TEST ONLY: real controller/daemon/store/worker, mocked SSH and runtime IO.
// Fixture output is a labelled byte sequence, NEVER claimed as generated video.
const { test: base, expect } = require('@playwright/test');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const path = require('node:path');
const test = base.extend({
  controlledSetup: ['', { option: true }],
  controller: async ({ controlledSetup }, use) => {
    const root = path.resolve(__dirname, '../..');
    const child = spawn(process.env.H3_TEST_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), [path.join(__dirname, 'generation_fixture.py')], {
      cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src'), H3_TEST_CONTROLLED_SETUP: controlledSetup }, stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stderr = ''; child.stderr.on('data', data => { stderr += data; });
    const lines = readline.createInterface({ input: child.stdout });
    try {
      const launch = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('TEST fixture timeout: ' + stderr)), 10000);
        lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line).launch); });
        child.once('exit', code => { clearTimeout(timer); reject(new Error(`TEST fixture exited ${code}: ${stderr}`)); });
      });
      const control = command => new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('TEST control timeout: ' + stderr)), 10000);
        lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line)); });
        child.stdin.write(JSON.stringify(command) + '\n');
      });
      await use({ launch, origin: new URL(launch).origin, control });
    } finally {
      lines.close();
      if (child.exitCode === null) { const exit = once(child, 'exit'); child.kill('SIGTERM'); await exit; }
    }
  },
});

async function login(page, controller) {
  await page.goto(controller.launch);
  await expect(page.getByRole('note')).toContainText('TEST ONLY');
  await expect(page.getByText('Local session secured', { exact: true })).toBeVisible();
  await page.getByLabel('SSH host').fill('fixture.example');
  await page.getByLabel('SSH username').fill('test');
  await page.getByRole('button', { name: 'Connect to server', exact: true }).click();
  await page.getByLabel('I compared this fingerprint with a trusted source').check();
  await page.getByRole('button', { name: 'Trust this fingerprint' }).click();
  await page.getByLabel('SSH password', { exact: true }).fill('TEST-ONLY-secret');
  await page.getByRole('button', { name: 'Authenticate', exact: true }).click();
  await expect(page.getByText('SSH authenticated', { exact: true })).toBeVisible();
}

test('wizard stages real files, binds approvals, installs via mocked IO and replays after reload', async ({ page, controller }) => {
  await login(page, controller);
  await page.getByRole('button', { name: 'Scan hardware', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Hardware observations' })).toBeVisible();
  await page.getByRole('button', { name: 'Stage release & review plan' }).click();
  await expect(page.locator('#plan-document')).toContainText('backend_revision');
  await expect(page.getByRole('button', { name: 'Approve installation', exact: true })).toBeDisabled();
  const denied = await page.evaluate(async () => {
    const csrf = (await (await fetch('/api/v1/controller/status')).json()).csrf_token;
    return (await fetch('/api/v1/controller/setup/install', { method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({plan_digest:'0'.repeat(64),license_acknowledged:true,eligibility_confirmed:true,privileged:true}) })).status;
  });
  expect(denied).toBe(409);
  await page.getByLabel('I read and acknowledge the complete pinned license and Exhibit A').check();
  await page.getByLabel('I confirm eligibility, including any necessary separate written authorization').check();
  await page.getByLabel('I approve the listed system changes, user services and privileged lingering registration').check();
  await page.getByLabel('Sudo password (optional, memory only)').fill('TEST-ONLY-sudo-secret');
  await page.getByRole('button', { name:'Approve installation', exact:true }).click();
  await expect(page.locator('#setup-status')).toContainText('ready');
  await expect(page.getByLabel('Sudo password (optional, memory only)')).toHaveValue('');
  await expect(page.locator('#setup-events')).toContainText('completed');
  await page.reload();
  await page.getByRole('button', {name:'Restore saved setup'}).click();
  await expect(page.locator('#setup-status')).toContainText('ready');
  await expect(page.getByRole('button', {name:'Approve installation', exact:true})).toBeDisabled();
  await expect(page.getByLabel('SSH password', {exact:true})).toHaveValue('');
  expect(await page.evaluate(() => [localStorage.length,sessionStorage.length])).toEqual([0,0]);
});

test('real authenticated proxy queues, replays, downloads Range and cancels test-only jobs', async ({ page, controller }) => {
  const urls = []; page.on('request', request => urls.push(request.url()));
  await login(page, controller);
  await page.getByRole('button', {name:'Refresh dashboard'}).click();
  await expect(page.locator('#readiness')).toContainText('Runtime ready: true');
  await expect(page.getByLabel('Managed LoRA ID (optional, BF16 only)')).toBeVisible();
  await page.getByLabel('Generation profile').selectOption('h3-int8-group-offload-experimental');
  await expect(page.locator('#lora-field')).toBeHidden();
  await page.getByLabel('Generation profile').selectOption('h3-bf16-cpu-offload-experimental');
  await page.getByLabel('Prompt', {exact:true}).fill('TEST ONLY fixture generation');
  await page.getByRole('button', {name:'Queue generation'}).click();
  await expect(page.getByRole('link', {name:'Download MP4'})).toBeVisible({timeout:10000});
  await expect(page.locator('#job-events')).toContainText('denoising');
  const href = await page.getByRole('link', {name:'Download MP4'}).getAttribute('href');
  const result = await page.evaluate(async href => {
    const response = await fetch(href, {headers:{Range:'bytes=0-8'}});
    return { status:response.status, range:response.headers.get('content-range'), body:await response.text() };
  }, href);
  expect(result.status).toBe(206); expect(result.body).toBe('TEST ONLY'); expect(result.range).toMatch(/^bytes 0-8\//);
  expect(urls.every(url => !url.includes('remote-token') && !url.includes('Bearer') && !url.includes('TEST-ONLY-secret'))).toBeTruthy();
  await page.getByLabel('Prompt', {exact:true}).fill('TEST ONLY cancel');
  await page.getByRole('button', {name:'Queue generation'}).click();
  await page.getByRole('button', {name:'Cancel job'}).click();
  await expect(page.locator('#jobs')).toContainText('cancelled');
});

test('invalid source settings and local proxy session protections remain enforced', async ({page,controller}) => {
  await login(page,controller);
  const result = await page.evaluate(async () => {
    const csrf = (await (await fetch('/api/v1/controller/status')).json()).csrf_token;
    const headers={'Content-Type':'application/json','X-CSRF-Token':csrf};
    const response=await fetch('/api/v1/controller/remote/jobs',{method:'POST',headers,body:JSON.stringify({prompt:'TEST ONLY',sparse_attention:true})});
    const forbidden=await fetch('/api/v1/controller/remote/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const token=await fetch('/api/v1/controller/remote/config/remote-token');
    return [response.status,forbidden.status,token.status];
  });
  expect(result).toEqual([422,403,404]);
});

async function approveControlledSetup(page, controller) {
  await login(page, controller);
  await page.getByRole('button', { name: 'Scan hardware', exact: true }).click();
  await page.getByRole('button', { name: 'Stage release & review plan' }).click();
  await page.getByLabel('I read and acknowledge the complete pinned license and Exhibit A').check();
  await page.getByLabel('I confirm eligibility, including any necessary separate written authorization').check();
  await page.getByLabel('I approve the listed system changes, user services and privileged lingering registration').check();
  await page.getByRole('button', { name: 'Approve installation', exact: true }).click();
}

for (const checkpoint of ['planned', 'awaiting_approval']) {
  test.describe(`accepted ${checkpoint} launch`, () => {
    test.use({ controlledSetup: checkpoint });
    test('polls the initial checkpoint until detached work runs and finishes', async ({ page, controller }) => {
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await approveControlledSetup(page, controller);
      await expect(page.locator('#setup-status')).toContainText(`Environment checkpoint: ${checkpoint}.`);
      await controller.control({ action: 'start-setup' });
      await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.', { timeout: 8000 });
      await controller.control({ action: 'finish-setup' });
      await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: ready.', { timeout: 8000 });
      expect(errors).toEqual([]);
    });
  });
}

test('completed video survives queued job polling; errors, expiry and deleted cards reconcile', async ({ page, controller }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  let snapshots = 0, cancellations = 0;
  page.on('request', request => { if (request.url().endsWith('/cancel')) cancellations++; });
  page.on('response', response => { if (response.url().endsWith('/remote/jobs') && response.request().method() === 'GET') snapshots++; });
  await login(page, controller);
  await page.getByRole('button', { name: 'Refresh dashboard' }).click();
  await page.getByLabel('Prompt', { exact: true }).fill('TEST ONLY stable fixture output');
  await page.getByRole('button', { name: 'Queue generation' }).click();
  const video = page.locator('#jobs video');
  await expect(video).toBeVisible({ timeout: 10000 });
  const handle = await video.elementHandle();
  const jobId = await video.evaluate(element => element.closest('.job-card').querySelector('h4').textContent);
  await handle.evaluate(element => {
    window.fixtureVideo = element;
    window.fixtureCard = element.closest('.job-card');
    window.fixtureMutations = [];
    window.fixtureObserver = new MutationObserver(records => window.fixtureMutations.push(...records));
    window.fixtureObserver.observe(element, { attributes: true });
    window.fixtureObserver.observe(document.getElementById('jobs'), { childList: true, subtree: true });
    element.dispatchEvent(new Event('error'));
    element.dispatchEvent(new Event('error'));
  });
  // Two long-running fixture jobs keep one queued while the dashboard refreshes.
  for (const prompt of ['TEST ONLY cancel running', 'TEST ONLY cancel queued']) {
    await page.getByLabel('Prompt', { exact: true }).fill(prompt);
    await page.getByRole('button', { name: 'Queue generation' }).click();
  }
  await expect(page.locator('#jobs')).toContainText('queued');
  const previous = snapshots;
  await expect.poll(() => snapshots, { timeout: 8000 }).toBeGreaterThanOrEqual(previous + 2);
  expect(await video.evaluate(element => element === window.fixtureVideo && element.closest('.job-card') === window.fixtureCard)).toBe(true);
  expect(await page.evaluate(() => window.fixtureMutations.some(record =>
    (record.type === 'attributes' && record.attributeName === 'src') ||
    [...record.removedNodes].some(node => node === window.fixtureCard || node === window.fixtureVideo)
  ))).toBe(false);
  await expect(page.getByText('Preview unavailable or output expired. Refresh the dashboard.', { exact: true })).toHaveCount(1);
  await controller.control({ action: 'expire-output', job_id: jobId });
  await expect(page.getByText('Output expired by retention policy.', { exact: true })).toBeVisible({ timeout: 8000 });
  await expect(video).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Download MP4' })).toHaveCount(0);
  await expect(page.getByText('Preview unavailable or output expired. Refresh the dashboard.', { exact: true })).toHaveCount(0);
  await controller.control({ action: 'delete-job', job_id: jobId });
  await expect(page.getByRole('heading', { name: jobId, exact: true })).toHaveCount(0, { timeout: 8000 });
  await page.locator('.job-card').filter({ hasText: 'queued' }).getByRole('button', { name: 'Cancel job' }).click();
  await expect(page.locator('#jobs')).toContainText('cancelled');
  expect(cancellations).toBe(1);
  expect(errors).toEqual([]);
});

test.describe('bounded accepted launch observation', () => {
  test.use({ controlledSetup: 'planned' });

  test('stops with an honest delayed status and manual replay can observe a late child', async ({ page, controller }) => {
    let polls = 0;
    page.on('request', request => { if (request.url().includes('/setup/events?')) polls++; });
    await page.clock.install({ time: new Date('2030-01-01T00:00:00Z') });
    await page.clock.pauseAt(new Date('2030-01-01T00:00:01Z'));
    await approveControlledSetup(page, controller);
    await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
    await page.clock.fastForward(31000);
    await expect(page.locator('#setup-status')).toContainText('Launch delayed: no running checkpoint observed within 30 seconds.');
    await expect(page.locator('#setup-status')).toContainText('No completion is assumed.');
    const stopped = polls;
    await page.clock.runFor(10000);
    expect(polls).toBe(stopped);
    await page.getByRole('button', { name: 'Replay setup progress' }).click();
    await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
    await controller.control({ action: 'start-setup' });
    await page.clock.runFor(2000);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.');
    await controller.control({ action: 'finish-setup' });
    await page.clock.runFor(2000);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: ready.');
    const completed = polls;
    await page.clock.runFor(10000);
    expect(polls).toBe(completed);
  });

  for (const end of ['disconnect', 'pagehide', 'plan']) {
    test(`clears accepted launch checks on ${end}`, async ({ page, controller }) => {
      let polls = 0;
      page.on('request', request => { if (request.url().includes('/setup/events?')) polls++; });
      await page.clock.install({ time: new Date('2030-01-01T00:00:00Z') });
      await page.clock.pauseAt(new Date('2030-01-01T00:00:01Z'));
      await approveControlledSetup(page, controller);
      await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
      // Hold an actual controller response, not fabricated checkpoint data.
      let release, received, delivered;
      const held = new Promise(resolve => { release = resolve; });
      const responseReceived = new Promise(resolve => { received = resolve; });
      const responseDelivered = new Promise(resolve => { delivered = resolve; });
      const eventRoute = '**/setup/events?*';
      let intercepted = false;
      await page.route(eventRoute, async route => {
        if (intercepted) return route.continue();
        intercepted = true;
        const response = await route.fetch();
        received(); await held;
        await route.fulfill({ response }); delivered();
      });
      await page.clock.runFor(2000);
      await responseReceived;
      if (end === 'disconnect') {
        await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
        await expect(page.getByText('SSH disconnected', { exact: true })).toBeVisible();
      } else if (end === 'pagehide') {
        await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
      } else {
        await page.getByRole('button', { name: 'Stage release & review plan' }).click();
        await expect(page.getByRole('button', { name: 'Stage release & review plan' })).toBeEnabled();
        // Merely reviewing/replaying a planned checkpoint is not launch acceptance.
        await page.getByRole('button', { name: 'Replay setup progress' }).click();
        await expect(page.locator('#setup-status')).not.toContainText('Launch accepted');
      }
      const stopped = polls;
      const status = await page.locator('#setup-status').textContent();
      release(); await responseDelivered;
      await page.unroute(eventRoute);
      await page.clock.runFor(10000);
      expect(polls).toBe(stopped);
      await expect(page.locator('#setup-status')).toHaveText(status);
    });
  }
});

test.describe('rejected setup action preserves installation observation', () => {
  test.use({ controlledSetup: 'planned' });

  async function rejectActivation(page) {
    const rejected = page.waitForResponse(response => response.url().endsWith('/setup/activate'));
    await page.getByRole('button', { name: 'Approve activation / update' }).click();
    const response = await rejected;
    expect(response.status()).toBe(502);
    expect(await response.json()).toEqual({ error: { code: 'deployment_operation_failed' } });
    await expect(page.locator('#error')).toContainText('The target rejected or could not execute this operation.');
    await expect(page.getByText('SSH authenticated', { exact: true })).toBeVisible();
  }

  for (const checkpoint of ['planned', 'running']) {
    test(`rejected activation at ${checkpoint} does not stop automatic progress`, async ({ page, controller }) => {
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      let installs = 0;
      page.on('request', request => { if (request.url().endsWith('/setup/install')) installs++; });
      await approveControlledSetup(page, controller);
      await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
      if (checkpoint === 'running') {
        await controller.control({ action: 'start-setup' });
        await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.', { timeout: 8000 });
      }
      await rejectActivation(page);
      if (checkpoint === 'planned') {
        await controller.control({ action: 'start-setup' });
        await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.', { timeout: 8000 });
      }
      await controller.control({ action: 'finish-setup' });
      await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: ready.', { timeout: 8000 });
      expect(installs).toBe(1);
      expect(errors).toEqual([]);
    });
  }

  test('late rejection cannot revive observation after page exit', async ({ page, controller }) => {
    let polls = 0;
    page.on('request', request => { if (request.url().includes('/setup/events?')) polls++; });
    await page.clock.install({ time: new Date('2030-01-01T00:00:00Z') });
    await page.clock.pauseAt(new Date('2030-01-01T00:00:01Z'));
    await approveControlledSetup(page, controller);
    await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
    let release, received;
    const held = new Promise(resolve => { release = resolve; });
    const responseReceived = new Promise(resolve => { received = resolve; });
    await page.route('**/setup/activate', async route => {
      const response = await route.fetch();
      expect(response.status()).toBe(502);
      received(); await held;
      await route.fulfill({ response });
    });
    await page.getByRole('button', { name: 'Approve activation / update' }).click();
    await responseReceived;
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    const stopped = polls;
    release();
    await expect(page.locator('#error')).toContainText('The target rejected or could not execute this operation.');
    await page.clock.runFor(10000);
    expect(polls).toBe(stopped);
  });

  test('rejection neither drops nor extends the accepted launch deadline', async ({ page, controller }) => {
    await page.clock.install({ time: new Date('2030-01-01T00:00:00Z') });
    await page.clock.pauseAt(new Date('2030-01-01T00:00:01Z'));
    await approveControlledSetup(page, controller);
    await expect(page.locator('#setup-status')).toContainText('Launch accepted; waiting');
    await page.clock.fastForward(20000);
    await rejectActivation(page);
    await page.clock.fastForward(11000);
    await expect(page.locator('#setup-status')).toContainText('Launch delayed: no running checkpoint observed within 30 seconds.');
  });
});

test.describe('automatic setup connection recovery', () => {
  test.use({ controlledSetup: 'planned' });
  test('delayed transport restoration replays the same setup without another install', async ({ page, controller }) => {
    const posts = [], errors = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    page.on('pageerror', error => errors.push(error.message));
    await approveControlledSetup(page, controller);
    await controller.control({ action: 'start-setup' });
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.', { timeout: 8000 });
    const plan = await page.locator('#plan-document').textContent();
    await controller.control({ action: 'lose-setup-connection' });
    await expect(page.locator('#setup-status')).toContainText('Automatic setup recovery', { timeout: 8000 });
    await expect(page.getByRole('button', { name: 'Disconnect', exact: true })).toBeEnabled();
    await controller.control({ action: 'finish-setup' });
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: ready.', { timeout: 12000 });
    await expect(page.locator('#plan-document')).toHaveText(plan);
    await expect(page.locator('#setup-events')).toContainText('completed');
    expect(posts.filter(url => url.endsWith('/setup/install'))).toHaveLength(1);
    expect(posts.filter(url => /\/setup\/(resume|reboot|restore)$/.test(url))).toHaveLength(0);
    expect(posts.filter(url => url.endsWith('/authenticate'))).toHaveLength(1);
    expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
    await expect(page.locator('body')).not.toContainText('TEST-ONLY-secret');
    expect(errors).toEqual([]);
  });
});

test.describe('approved reboot observation recovery', () => {
  test.use({ controlledSetup: 'reboot' });
  test('reconnects automatically while the independent bootstrap completes without duplicate commands', async ({ page, controller }) => {
    const posts = [], errors = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    page.on('pageerror', error => errors.push(error.message));
    await approveControlledSetup(page, controller);
    await expect(page.locator('#setup-status')).toContainText('waiting_for_reboot');
    const plan = await page.locator('#plan-document').textContent();
    await page.getByLabel('Separately approve reboot of this server for this plan').check();
    await page.getByRole('button', { name: 'Request approved reboot', exact: true }).click();
    await expect(page.locator('#setup-status')).toContainText('Automatic setup recovery');
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: ready.', { timeout: 12000 });
    await expect(page.locator('#plan-document')).toHaveText(plan);
    await expect(page.locator('#setup-events')).toContainText('completed');
    for (const action of ['install', 'reboot']) expect(posts.filter(url => url.endsWith('/setup/' + action))).toHaveLength(1);
    expect(posts.filter(url => /\/setup\/(resume|restore)$/.test(url))).toHaveLength(0);
    expect(posts.filter(url => url.endsWith('/authenticate'))).toHaveLength(1);
    expect(errors).toEqual([]);
  });
});
