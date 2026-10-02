// TEST ONLY mocked runtime/SSH; favorite persistence uses the real remote SQLite store.
const {test:base,expect}=require('@playwright/test');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const test=base.extend({controller:async({},use)=>{const child=spawn(path.join(root,'.venv/bin/python'),[path.join(__dirname,'generation_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},stdio:['pipe','pipe','pipe']});let errors='';child.stderr.on('data',v=>errors+=v);const lines=readline.createInterface({input:child.stdout});try{const launch=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(errors)),15000);lines.once('line',v=>{clearTimeout(timer);resolve(JSON.parse(v).launch)});child.once('exit',c=>{clearTimeout(timer);reject(Error('Fixture '+c+errors))})});await use({launch});}finally{lines.close();if(child.exitCode===null){const done=once(child,'exit');child.kill('SIGTERM');await done;}}}});

test('star in Current persists into History/reload; unfavoriting changes its visual and server state',async({page,controller})=>{
 await page.goto(controller.launch);await expect(page.locator('#session')).toHaveText('Local session secured');
 await page.getByLabel('SSH host').fill('fixture.example');await page.getByLabel('SSH username').fill('test');await page.getByRole('button',{name:'Connect to server',exact:true}).click();
 await page.getByLabel('I compared this fingerprint with a trusted source').check();await page.getByRole('button',{name:'Trust this fingerprint',exact:true}).click();await page.getByLabel('SSH password',{exact:true}).fill('TEST-ONLY-secret');await page.getByRole('button',{name:'Authenticate',exact:true}).click();
 await page.locator('[data-workspace=generate]').click();await page.getByRole('button',{name:'Refresh dashboard',exact:true}).click();await expect(page.locator('#readiness')).toContainText('Runtime ready: true');
 await page.locator('#prompt').fill('TEST ONLY favorite fixture');await page.getByRole('button',{name:'Queue generation',exact:true}).click();
 const card=page.locator('.job-card').first();const star=card.locator('.favorite-star');
 await expect(star).toBeVisible();await expect(star).toHaveAttribute('aria-pressed','false');
 await star.click();await expect(star).toHaveAttribute('aria-pressed','true');await expect(card).toHaveAttribute('data-favorite','true');await expect(star).toHaveText('★');
 await expect(card.locator('video')).toBeVisible({timeout:15000});const id=await card.getAttribute('data-job-id');
 await page.reload();await page.locator('[data-workspace=generate]').click();await page.getByRole('button',{name:'Refresh dashboard',exact:true}).click();await page.locator('[data-workspace=history]').click();const history=page.locator(`.job-card[data-job-id="${id}"]`);
 await expect(history.locator('.favorite-star')).toHaveAttribute('aria-pressed','true');await expect(history).toHaveAttribute('data-favorite','true');
 await history.locator('.favorite-star').click();await expect(history.locator('.favorite-star')).toHaveAttribute('aria-pressed','false');await expect(history).toHaveAttribute('data-favorite','false');await expect(history.locator('.favorite-star')).toHaveText('☆');
 const jobs=await page.evaluate(async()=>await(await fetch('/api/v1/controller/remote/jobs')).json());expect(jobs.jobs.find(j=>j.job_id===id).favorite).toBe(false);
 await history.locator('.favorite-star').click();await expect(history).toHaveAttribute('data-favorite','true');await expect(history.locator('.favorite-star')).toBeEnabled();await page.screenshot({path:'/tmp/h3-job-favorites.png',fullPage:true});
 await page.route('**/remote/jobs/*/favorite',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'TEST ONLY save failure'})}));
 await history.locator('.favorite-star').click();await expect(page.locator('#error')).toBeVisible();await expect(history).toHaveAttribute('data-favorite','true');
 const unchanged=await page.evaluate(async()=>await(await fetch('/api/v1/controller/remote/jobs')).json());expect(unchanged.jobs.find(j=>j.job_id===id).favorite).toBe(true);
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
