// TEST ONLY: actual controller authentication; no real SSH or generation jobs.
const {test:base,expect}=require('@playwright/test');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const test=base.extend({controller:async({},use)=>{
  const child=spawn(path.join(root,'.venv/bin/python'),[path.join(__dirname,'generation_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},stdio:['pipe','pipe','pipe']});
  let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});
  try{
    const launch=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr)),15000);lines.once('line',l=>{clearTimeout(timer);resolve(JSON.parse(l).launch);});child.once('exit',c=>{clearTimeout(timer);reject(Error('Fixture exit '+c+stderr));});});
    await use({launch});
  }finally{lines.close();if(child.exitCode===null){const done=once(child,'exit');child.kill('SIGTERM');await done;}}
}});

test('trusted browser restores a dropped cookie without re-entering the key',async({page,context,controller})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(controller.launch);
  await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
  const grant=await page.evaluate(()=>localStorage.getItem('h3-remembered-access'));
  expect(grant).toBeTruthy();
  await context.clearCookies();
  const restored=page.waitForResponse(r=>r.url().endsWith('/session/restore')&&r.status()===200);
  await page.reload();await restored;
  await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
  expect((await context.cookies()).some(c=>c.name==='h3_session'&&c.httpOnly)).toBe(true);
  expect(await page.evaluate(()=>localStorage.getItem('h3-remembered-access'))).toBe(grant);
  // A stale one-use launch bookmark must not override valid browser trust.
  await page.goto('about:blank');
  const stale=page.waitForResponse(r=>r.url().endsWith('/bootstrap')&&r.status()===401);
  await page.goto(controller.launch);await stale;
  await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
  await expect(page.locator('#error')).toBeHidden();
  // Without either form of browser trust, there is no automatic authentication.
  await page.evaluate(()=>localStorage.clear());await context.clearCookies();
  await page.reload();await expect(page.locator('#session')).toContainText('Local session unavailable');
  expect(errors).toEqual([]);
});

test('a rejected mutation is not replayed and invalid remembered grants are forgotten',async({page,controller})=>{
  await page.goto(controller.launch);await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
  await page.getByLabel('SSH host').fill('fixture.example');await page.getByLabel('SSH username').fill('test');
  await page.getByRole('button',{name:'Connect to server',exact:true}).click();
  await expect(page.locator('#disconnect')).toBeEnabled();
  let disconnects=0,restores=0;
  await page.route('**/api/v1/controller/disconnect',route=>{disconnects++;return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:{code:'session_required'}})});});
  page.on('request',r=>{if(r.url().endsWith('/session/restore'))restores++;});
  const rejected=page.waitForResponse(r=>r.url().endsWith('/disconnect')&&r.status()===401);
  await page.locator('#disconnect').click();await rejected;
  await expect.poll(()=>disconnects).toBe(1);expect(restores).toBe(0);
  // Stop old in-flight refreshes before removing cookies, then seed a bad grant
  // before the next app loads (a successful old response must not overwrite it).
  await page.goto('about:blank');await page.context().clearCookies();
  await page.addInitScript(()=>localStorage.setItem('h3-remembered-access','wrong-grant'));
  await page.goto(controller.launch.split('#')[0]);
  await expect(page.locator('#session')).toContainText('Local session unavailable');
  await expect.poll(()=>page.evaluate(()=>localStorage.getItem('h3-remembered-access'))).toBe(null);
});
