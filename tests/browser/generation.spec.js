// TEST ONLY: real controller/daemon/store/worker, mocked SSH and runtime IO.
// Fixture output is a labelled byte sequence, NEVER claimed as generated video.
const { test: base, expect } = require('@playwright/test');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const path = require('node:path');
const test = base.extend({
  controller: async ({}, use) => {
    const root = path.resolve(__dirname, '../..');
    const child = spawn(path.join(root, '.venv/bin/python'), [path.join(__dirname, 'generation_fixture.py')], {
      cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = ''; child.stderr.on('data', data => { stderr += data; });
    const lines = readline.createInterface({ input: child.stdout });
    try {
      const launch = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('TEST fixture timeout: ' + stderr)), 10000);
        lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line).launch); });
        child.once('exit', code => { clearTimeout(timer); reject(new Error(`TEST fixture exited ${code}: ${stderr}`)); });
      });
      await use({ launch, origin: new URL(launch).origin });
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
