// TEST ONLY: real controller/daemon/store/worker, mocked SSH and runtime IO.
// Fixture output is a labelled byte sequence, NEVER claimed as generated video.
const { test: base, expect } = require('@playwright/test');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const path = require('node:path');
const test = base.extend({
  controlledSetup: ['', { option: true }],
  prunedProfile: ['', { option: true }],
  controller: async ({ controlledSetup, prunedProfile }, use) => {
    const root = path.resolve(__dirname, '../..');
    const child = spawn(process.env.H3_TEST_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), [path.join(__dirname, 'generation_fixture.py')], {
      cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src'), H3_TEST_CONTROLLED_SETUP: controlledSetup, H3_TEST_PRUNED_PROFILE: prunedProfile }, stdio: ['pipe', 'pipe', 'pipe'],
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

test.describe('pruned dashboard isolation', () => {
  test.use({ prunedProfile: 'h3-gguf-q4-k-m' });
  test('legacy completed metadata is not pruned inference evidence; polling preserves explicit steps', async ({page,controller}) => {
    await login(page, controller);
    await controller.control({action:'historical-legacy-output'});
    await page.locator('#dashboard-refresh').click();
    await expect(page.locator('#readiness')).toContainText('Generation tested: not proven');
    await expect(page.locator('#steps')).toHaveValue('20');
    await page.locator('#steps').fill('23');
    await page.locator('#dashboard-refresh').click();
    await expect(page.locator('#steps')).toHaveValue('23');
  });
});

async function install(page, controller, profile = 'h3-bf16-cpu-offload-experimental') {
  await login(page, controller);
  await page.locator('#scan').click();
  await expect(page.getByRole('heading', { name: 'Hardware observations' })).toBeVisible();
  await page.locator('#install-profile').selectOption(profile);
  await page.getByRole('button', { name: 'Install', exact: true }).click();
}
async function controllerStatus(page) {
  return page.evaluate(async () => (await fetch('/api/v1/controller/status')).json());
}
async function completed(page) {
  await expect(page.locator('#setup-status')).toContainText('Completed · completed', {timeout:20000});
  await expect(page.locator('#setup-status')).toContainText('Model files ready: true');
  await expect(page.locator('#setup-status')).toContainText('Generation tested: not proven');
  await expect(page.locator('#setup-progress')).toBeHidden();
}

test('one Install stages real files, activates, downloads and restores completion on reload', async ({page,controller}) => {
  const posts = [], errors = [];
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
  page.on('pageerror', e => errors.push(e.message));
  await install(page, controller);
  await completed(page);
  await expect(page.locator('#setup-events')).toContainText('completed');
  const status = await controllerStatus(page);
  expect(status.recovery.setup_credential_retained).toBe(false);
  expect(status.setup.profile).toBe('h3-bf16-cpu-offload-experimental');
  const calls = (await controller.control({action:'calls'})).calls;
  expect(calls.filter(c => c === 'prepare')).toHaveLength(1);
  expect(calls.filter(c => c === 'activate')).toHaveLength(1);
  expect(calls.filter(c => c === 'download')).toHaveLength(1);
  expect(calls.filter(c => c === 'sudo-check')).toHaveLength(1);
  await page.reload();
  await completed(page);
  await expect(page.locator('#install-profile')).toHaveValue(status.setup.profile);
  expect(posts.filter(p => p.endsWith('/setup/start'))).toHaveLength(1);
  expect(posts.filter(p => /\/setup\/(plan|install|reboot|activate|download|restore)$/.test(p))).toHaveLength(0);
  expect(await page.evaluate(() => [localStorage.length,sessionStorage.length])).toEqual([0,0]);
  await expect(page.locator('body')).not.toContainText('TEST-ONLY-secret');
  expect(errors).toEqual([]);
});

for (const checkpoint of ['planned', 'awaiting_approval']) {
  test.describe(`composite observes ${checkpoint}`, () => {
    test.use({controlledSetup:checkpoint});
    test('locks selection, restores running progress on reload and needs no further actions', async ({page,controller}) => {
      let starts = 0;
      page.on('request', r => { if (r.url().endsWith('/setup/start')) starts++; });
      await install(page,controller);
      await expect(page.locator('#setup-status')).toContainText(`Environment checkpoint: ${checkpoint}.`);
      await expect(page.locator('#setup-progress')).toBeVisible();
      await expect(page.locator('#setup-progress')).not.toHaveAttribute('value', /.+/);
      await expect(page.locator('#install-profile')).toBeDisabled();
      await expect(page.locator('#install')).toBeDisabled();
      await page.reload();
      await expect(page.locator('#setup-status')).toContainText(`Environment checkpoint: ${checkpoint}.`);
      await expect(page.locator('#install-profile')).toHaveValue('h3-bf16-cpu-offload-experimental');
      await controller.control({action:'start-setup'});
      await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.');
      await controller.control({action:'finish-setup'});
      await completed(page);
      expect(starts).toBe(1);
    });
  });
}

for (const mode of ['reboot', 'replan']) {
  test.describe(`automatic ${mode}`, () => {
    test.use({controlledSetup:mode});
    test('advances exact selection through recovery/replan, activation and download without prompts', async ({page,controller}) => {
      const posts = [];
      page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
      await install(page,controller);
      if (mode === 'reboot') await expect(page.locator('#setup-status')).toContainText('Reconnecting securely', {timeout:10000});
      await completed(page);
      const calls = (await controller.control({action:'calls'})).calls;
      expect(calls.filter(c => c === 'prepare')).toHaveLength(1);
      expect(calls.filter(c => c === (mode === 'reboot' ? 'reboot' : 'prerequisites'))).toHaveLength(1);
      expect(calls.slice(-2)).toEqual(['activate','download']);
      expect(posts.filter(p => p.endsWith('/setup/start'))).toHaveLength(1);
      expect(posts.filter(p => p.endsWith('/authenticate'))).toHaveLength(1);
      expect(posts.filter(p => /\/setup\/(install|reboot|activate|download|restore|resume)$/.test(p))).toHaveLength(0);
    });
  });
}

test.describe('sudo recovery', () => {
  test.use({controlledSetup:'sudo-rejected'});
  test('specific failure has no second normal password or automatic password retry', async ({page,controller}) => {
    await install(page,controller);
    await expect(page.locator('#setup-recovery')).toContainText('not accepted for sudo');
    await expect(page.locator('#setup-recovery')).toContainText('disconnect, reconnect and authenticate');
    await expect(page.locator('#sudo-password')).toHaveCount(0);
    await expect(page.locator('#setup-progress')).toBeHidden();
    expect((await controllerStatus(page)).recovery.setup_credential_retained).toBe(false);
    expect((await controller.control({action:'calls'})).calls).not.toContain('prepare');
    await page.locator('#disconnect').click();
    await page.locator('#reconnect').click();
    await expect(page.locator('#authenticate')).toBeEnabled();
    await expect(page.locator('#fingerprint-panel')).toBeHidden();
  });
});

test.describe('controlled download cancellation', () => {
  test.use({controlledSetup:'download'});
  test('cancel after reload stops owned download, clears memory and never promises rollback', async ({page,controller}) => {
    await install(page,controller);
    await expect(page.locator('#setup-status')).toContainText('Downloading selected model', {timeout:15000});
    await page.reload();
    await expect(page.locator('#setup-status')).toContainText('Downloading selected model');
    await page.getByRole('button',{name:'Cancel setup',exact:true}).click();
    await expect(page.locator('#setup-status')).toContainText('cancelled');
    await expect(page.locator('#setup-recovery')).toContainText('not rollback');
    await expect(page.locator('#setup-progress')).toBeHidden();
    const status = await controllerStatus(page);
    expect(status.recovery.credential_retained).toBe(false);
    expect(status.setup.busy).toBe(false);
    expect((await controller.control({action:'calls'})).calls.filter(c=>c==='cancel-download')).toHaveLength(1);
    await page.reload();
    await expect(page.locator('#setup-status')).toContainText('cancelled');
    await page.locator('#disconnect').click();
    await expect(page.locator('#setup-status')).toContainText('have not been verified');
  });
});

test.describe('transport recovery and interruption', () => {
  test.use({controlledSetup:'planned'});
  test('connection loss resumes same digest without resubmitting installation', async ({page,controller}) => {
    await install(page,controller);
    await controller.control({action:'start-setup'});
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: running.');
    const original = (await controllerStatus(page)).setup;
    await controller.control({action:'lose-setup-connection'});
    await expect(page.locator('#setup-status')).toContainText('Reconnecting securely', {timeout:10000});
    await controller.control({action:'finish-setup'});
    await completed(page);
    const final = (await controllerStatus(page)).setup;
    expect(final.plan_digest).toBe(original.plan_digest);
    expect(final.request_id).toBe(original.request_id);
    expect((await controller.control({action:'calls'})).calls.filter(c=>c==='prepare')).toHaveLength(1);
  });
  test('page exit discards stale status response and stops browser polling without cancelling controller', async ({page,controller}) => {
    await install(page,controller);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: planned.');
    // Delay an actual response only; no fabricated HTTP fixture or coordinator.
    let received, release, delivered;
    const arrived = new Promise(r=>received=r), held = new Promise(r=>release=r), done = new Promise(r=>delivered=r);
    await page.route('**/api/v1/controller/status', async route => {
      const response = await route.fetch(); received(); await held; await route.fulfill({response}); delivered();
    });
    await arrived;
    const text = await page.locator('#setup-status').textContent();
    await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
    release(); await done; await page.unroute('**/api/v1/controller/status');
    await controller.control({action:'start-setup'}); await controller.control({action:'finish-setup'});
    await page.waitForTimeout(1600);
    await expect(page.locator('#setup-status')).toHaveText(text);
    await page.reload(); await completed(page);
  });
  test('low-level unauthorized mutation remains denied and does not stop automatic observation', async ({page,controller}) => {
    await install(page,controller);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: planned.');
    const denied = await page.evaluate(async () => {
      const csrf = (await (await fetch('/api/v1/controller/status')).json()).csrf_token;
      return (await fetch('/api/v1/controller/setup/install', {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({plan_digest:'0'.repeat(64),license_acknowledged:true,eligibility_confirmed:true,privileged:true})})).status;
    });
    expect(denied).toBe(409);
    await controller.control({action:'start-setup'}); await controller.control({action:'finish-setup'});
    await completed(page);
  });
});

for (const profile of ['h3-gguf-q4-k-m','h3-convrot-mixed']) {
  test.describe(`pruned selected ${profile}`, () => {
    test.use({prunedProfile:profile});
    test('per-selection totals, license, bound start, automatic download and generation stay exact', async ({page,controller}) => {
      await login(page,controller);
      await page.locator('#scan').click();
      await expect(page.locator('#install-profile')).toHaveValue('h3-gguf-q4-k-m');
      await expect(page.locator('#install-profile option[value="h3-nvfp4"]')).toBeDisabled();
      await expect(page.locator('#unavailable-variants')).toContainText('Pure INT4 diffusion is absent');
      await page.locator('#install-profile').selectOption(profile);
      const total = profile.includes('gguf') ? '31,550,953,118' : '35,889,785,333';
      await expect(page.locator('#selected-profile')).toContainText(total+' bytes');
      await expect(page.locator('#selected-profile')).toContainText('Host RAM: unknown');
      await expect(page.locator('#artifact-details')).toContainText('Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI');
      await expect(page.locator('#artifact-details')).toContainText('Video VAE');
      await expect(page.locator('#artifact-details')).toContainText('Audio VAE');
      await expect(page.locator('#license-disclosures')).toContainText('does not remove upstream derivative obligations');
      const catalog = await page.evaluate(async () => (await (await fetch('/api/v1/controller/profiles')).json()));
      const selected = catalog.profiles.find(p=>p.profile_name===profile);
      const started = page.waitForRequest(r=>r.url().endsWith('/setup/start'));
      await page.locator('#install').click();
      expect((await started).postDataJSON()).toMatchObject({profile,artifact_set_digest:selected.artifact_set_digest});
      await completed(page);
      const status = (await controllerStatus(page)).setup;
      // Low-level exact digest authorization is not bypassed by composite UI.
      const denied = await page.evaluate(async ({status,catalog}) => {
        const csrf = (await (await fetch('/api/v1/controller/status')).json()).csrf_token;
        return (await fetch('/api/v1/controller/setup/download',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
          body:JSON.stringify({plan_digest:status.plan_digest,artifact_set_digest:'0'.repeat(64),license_sha256:catalog.license_sha256,
            license_acknowledged:true,eligibility_confirmed:true,download_approved:true})})).status;
      },{status,catalog});
      expect(denied).toBe(502);
      await page.reload(); await completed(page);
      await expect(page.locator('#install-profile')).toHaveValue(profile);
      await expect(page.locator('#selected-profile')).toContainText(total+' bytes');
      await page.locator('#dashboard-refresh').click();
      await expect(page.locator('#generation-profile')).toHaveValue(profile);
      await expect(page.locator('#generation-profile option')).toHaveCount(1);
      await expect(page.locator('#steps')).toHaveValue('20');
      await expect(page.locator('#lora-field')).toBeHidden();
      await page.locator('#prompt').fill('TEST ONLY pruned fixture output');
      const generation = page.waitForRequest(r=>r.url().endsWith('/remote/jobs') && r.method()==='POST');
      await page.locator('#generate').click();
      expect((await generation).postDataJSON()).toMatchObject({profile,steps:20});
      await expect(page.getByRole('link',{name:'Download MP4'})).toBeVisible();
    });
  });
}

test.describe('ambiguous start and connection cancellation', () => {
  test.use({controlledSetup:'planned'});
  test('lost accepted response restores progress without launching another request', async ({page,controller}) => {
    let starts = 0;
    page.on('request', r=>{if(r.url().endsWith('/setup/start')) starts++;});
    await page.route('**/setup/start', async route => {
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      await route.abort('failed');
    });
    await install(page,controller);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: planned.');
    await expect(page.locator('#install')).toBeDisabled();
    await controller.control({action:'start-setup'}); await controller.control({action:'finish-setup'});
    await completed(page);
    expect(starts).toBe(1);
    expect((await controller.control({action:'calls'})).calls.filter(c=>c==='prepare')).toHaveLength(1);
  });
  test('disconnect cancels busy setup, then reconnect resets visible progress without bypassing host trust', async ({page,controller}) => {
    await install(page,controller);
    await expect(page.locator('#setup-status')).toContainText('Environment checkpoint: planned.');
    await page.locator('#disconnect').click();
    await expect(page.locator('#setup-status')).toContainText('cancelled');
    await expect(page.locator('#setup-progress')).toBeHidden();
    expect((await controllerStatus(page)).recovery.credential_retained).toBe(false);
    await page.locator('#reconnect').click();
    await expect(page.locator('#authenticate')).toBeEnabled();
    await expect(page.locator('#setup-status')).toContainText('have not been verified');
    await expect(page.locator('#fingerprint-panel')).toBeHidden();
    await expect(page.locator('#install')).toBeDisabled();
  });
});

test('retry of an undelivered Install preserves its public id and selection without storing secrets', async ({page,controller}) => {
  const attempts = [];
  let first = true;
  await page.route('**/setup/start', async route => {
    attempts.push(route.request().postDataJSON());
    if (first) { first = false; await route.abort('failed'); }
    else await route.continue();
  });
  await install(page,controller);
  await expect(page.getByRole('button',{name:'Retry Install request',exact:true})).toBeEnabled();
  await expect(page.locator('#install-profile')).toBeDisabled();
  await page.getByRole('button',{name:'Retry Install request',exact:true}).click();
  await completed(page);
  expect(attempts).toHaveLength(2);
  expect(attempts[0]).toEqual(attempts[1]);
  expect(attempts[0].request_id).toMatch(/^[a-f0-9-]{36}$/);
  expect(JSON.stringify(attempts)).not.toContain('TEST-ONLY-secret');
});
