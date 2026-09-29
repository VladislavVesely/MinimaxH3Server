// Exercise the real controller; no route interception or mocked application logic.
const { test: base, expect } = require('@playwright/test');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const path = require('node:path');

const test = base.extend({
  controller: async ({}, use) => {
    const root = path.resolve(__dirname, '../..');
    const child = spawn(path.join(root, '.venv/bin/python'), [path.join(__dirname, 'controller_fixture.py')], {
      cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', data => { stderr += data; });
    const lines = readline.createInterface({ input: child.stdout });
    try {
      const launch = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Fixture startup timed out: ' + stderr)), 10000);
        lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line).launch); });
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${stderr}`)); });
      });
      const origin = new URL(launch).origin;
      await expect.poll(async () => {
        try { return (await fetch(origin)).status; } catch { return 0; }
      }).toBe(200);
      await use({ launch, origin });
    } finally {
      lines.close();
      if (child.exitCode === null) { const exit = once(child, 'exit'); child.kill('SIGTERM'); await exit; }
    }
  },
});

async function open(page, controller) {
  await page.goto(controller.launch);
  await expect(page.getByText('Local session secured', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(controller.origin + '/');
}
async function connect(page, host = 'fixture.example') {
  await page.getByLabel('SSH host').fill(host);
  await page.getByLabel('SSH port').fill('22');
  await page.getByLabel('SSH username').fill('test');
  await page.getByRole('button', { name: 'Connect to server', exact: true }).click();
}
async function approve(page) {
  await expect(page.getByRole('heading', { name: 'Verify server identity' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Trust this fingerprint' })).toBeDisabled();
  await page.getByLabel('I compared this fingerprint with a trusted source').check();
  await page.getByRole('button', { name: 'Trust this fingerprint' }).click();
}
async function authenticate(page, password = 'browser-secret') {
  await page.getByLabel('SSH password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Authenticate', exact: true }).click();
  await expect(page.getByLabel('SSH password', { exact: true })).toHaveValue('');
}

test('bootstrap is single-use, cookie is HttpOnly, writes require CSRF and reload restores session', async ({ page, controller }) => {
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await open(page, controller);
  expect(requests.every(url => !url.includes('token='))).toBeTruthy();
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === 'h3_session')).toMatchObject({ httpOnly: true, sameSite: 'Strict' });
  const rejected = await page.evaluate(async () => (await fetch('/api/v1/controller/connect', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ host: 'fixture.example', username: 'test' }),
  })).status);
  expect(rejected).toBe(403);
  await page.reload();
  await expect(page.getByText('Local session secured', { exact: true })).toBeVisible();
  await page.goto('about:blank');
  await page.goto(controller.launch);
  await expect(page.getByRole('alert')).toContainText('invalid or expired');
});

test('trust before authentication, read-only scan, unknowns, safe text and explicit installation staging', async ({ page, controller }) => {
  const posts = [];
  const logs = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
  page.on('console', message => logs.push(message.text()));
  await open(page, controller);
  await connect(page);
  await expect(page.getByText(/^SHA256:/)).toBeVisible();
  expect(posts.some(url => url.endsWith('/authenticate'))).toBeFalsy();
  await approve(page);
  await authenticate(page);
  await expect(page.getByText('SSH authenticated', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Scan hardware', exact: true }).click();
  await expect(page.getByRole('progressbar')).toBeVisible();
  await expect(page.getByRole('progressbar')).not.toHaveAttribute('value', /.+/);
  await expect(page.getByRole('heading', { name: 'Hardware observations' })).toBeVisible();
  await expect(page.locator('#hardware')).toContainText('Unknown');
  await expect(page.locator('#hardware')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#hardware img')).toHaveCount(0);
  await expect(page.locator('#profiles article')).not.toHaveCount(0);
  await expect(page.locator('#profiles')).toContainText('Required free disk');
  await expect(page.locator('#install')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Stage release & review plan' })).toBeEnabled();
  await expect(page.locator('#installation')).toContainText('No system mutation occurs until approval');
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(logs.join(' ')).not.toContain('browser-secret');
  await expect(page.locator('body')).not.toContainText('browser-secret');
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByText('SSH disconnected', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Authenticate', exact: true })).toBeEnabled();
  await expect(page.getByLabel('SSH password', { exact: true })).toHaveValue('');
  expect(posts.some(url => /install|generation/.test(url))).toBeFalsy();
});

test('connection and authentication failures are actionable and clear the password', async ({ page, controller }) => {
  await open(page, controller);
  await connect(page, 'unreachable.example');
  await expect(page.getByRole('alert')).toContainText('Could not reach');
  await connect(page);
  await approve(page);
  await authenticate(page, 'wrong-password');
  await expect(page.getByRole('alert')).toContainText('Authentication failed');
  await expect(page.getByRole('button', { name: 'Reconnect', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await authenticate(page);
  await expect(page.getByText('SSH authenticated', { exact: true })).toBeVisible();
});

test('missing launch session is unavailable rather than pretending to connect', async ({ page, controller }) => {
  await page.goto(controller.origin);
  await expect(page.getByRole('alert')).toContainText('launch URL');
  await expect(page.getByRole('button', { name: 'Connect to server', exact: true })).toBeDisabled();
});

for (const width of [360, 390, 768, 1280]) {
  test(`setup, fingerprint and results fit ${width}px without horizontal scrolling`, async ({ page, controller }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page, controller);
    const fits = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await fits();
    await connect(page);
    await fits();
    await approve(page);
    await authenticate(page);
    await page.getByRole('button', { name: 'Scan hardware', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Hardware observations' })).toBeVisible();
    await fits();
  });
}

test('scan failure is explicit, stops progress and never shows readiness', async ({ page, controller }) => {
  await open(page, controller);
  await connect(page, 'scan-failure.example');
  await approve(page);
  await authenticate(page);
  await page.getByRole('button', { name: 'Scan hardware', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('home directory could not be read');
  await expect(page.getByRole('progressbar')).toBeHidden();
  await expect(page.locator('#scan-results')).toBeHidden();
  await expect(page.locator('#install')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Scan hardware', exact: true })).toBeEnabled();
});

test('changed host keys block reconnect without bypass or authentication', async ({ page, controller }) => {
  await open(page, controller);
  await connect(page, 'changed-key.example');
  await approve(page);
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('host key has changed');
  await expect(page.locator('#fingerprint-panel')).toBeHidden();
  await expect(page.locator('#auth-form')).toBeHidden();
});

test('reload preserves the real pending fingerprint challenge and requires fresh explicit consent', async ({ page, controller }) => {
  await open(page, controller);
  await connect(page);
  await expect(page.locator('#fingerprint')).toHaveText(/^SHA256:/);
  const fingerprint = await page.locator('#fingerprint').textContent();
  await page.getByLabel('I compared this fingerprint with a trusted source').check();
  await page.reload();
  await expect(page.locator('#fingerprint')).toHaveText(fingerprint);
  await expect(page.getByRole('button', { name: 'Trust this fingerprint' })).toBeDisabled();
  await approve(page);
  await authenticate(page);
  await expect(page.getByText('SSH authenticated', { exact: true })).toBeVisible();
});
