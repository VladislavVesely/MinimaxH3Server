// TEST ONLY: real controller/remote policy storage with mocked SSH/runtime; no GPU jobs.
const {test:base,expect}=require('@playwright/test');
const {spawn}=require('node:child_process');const {once}=require('node:events');
const readline=require('node:readline'),path=require('node:path');const root=path.resolve(__dirname,'../..');
const test=base.extend({controller:async({},use)=>{
 const child=spawn(path.join(root,'.venv/bin/python'),[path.join(__dirname,'generation_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src'),H3_TEST_PRUNED_PROFILE:'h3-convrot-int8',H3_TEST_MEMORY_SETTINGS:'1'},stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});
 try{const launch=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr)),15000);lines.once('line',l=>{clearTimeout(timer);resolve(JSON.parse(l).launch)});child.once('exit',c=>{clearTimeout(timer);reject(Error('Fixture exit '+c+stderr))})});await use({launch});}
 finally{lines.close();if(child.exitCode===null){const done=once(child,'exit');child.kill('SIGTERM');await done;}}
}});
async function connect(page,controller){
 await page.goto(controller.launch);await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
 await page.getByLabel('SSH host').fill('fixture.example');await page.getByLabel('SSH username').fill('test');
 await page.getByRole('button',{name:'Connect to server',exact:true}).click();
 await page.getByLabel('I compared this fingerprint with a trusted source').check();await page.getByRole('button',{name:'Trust this fingerprint',exact:true}).click();
 await page.getByLabel('SSH password',{exact:true}).fill('TEST-ONLY-secret');await page.getByRole('button',{name:'Authenticate',exact:true}).click();
 await expect(page.locator('#ssh-state')).toHaveText('SSH authenticated');
 await page.locator('[data-workspace=generate]').click();await page.getByRole('button',{name:'Refresh dashboard',exact:true}).click();
 await expect(page.locator('#readiness')).toContainText('Runtime ready: true');
}
test('server-wide settings apply explicitly, persist, and protect drafts from another client',async({page,controller})=>{
 const posts=[];page.on('request',r=>{if(r.method()==='POST')posts.push(r.url())});await connect(page,controller);
 await expect(page.locator('[data-workspace=settings]')).toBeVisible();await page.locator('[data-workspace=settings]').click();
 const live=await page.request.get(new URL('/api/v1/controller/remote/settings/memory',controller.launch).href);expect(live.status(),await live.text()).toBe(200);
 await expect(page.locator('#memory-auto')).toBeEnabled();await expect(page.locator('#memory-auto')).toBeChecked();await expect(page.locator('#memory-apply')).toBeDisabled();
 const initialPosts=posts.length;
 await page.locator('#memory-auto').uncheck();await page.locator('#memory-reserve').fill('4');await page.locator('#memory-offload').selectOption('low_vram');await page.locator('#memory-expandable').check();
 expect(posts.length).toBe(initialPosts);
 await page.locator('#memory-apply').click();await expect(page.locator('#memory-status')).toContainText('Saved');
 await page.reload();await page.locator('[data-workspace=settings]').click();
 await expect(page.locator('#memory-reserve')).toHaveValue('4');await expect(page.locator('#memory-offload')).toHaveValue('low_vram');await expect(page.locator('#memory-expandable')).toBeChecked();
 await page.locator('#memory-reserve').fill('5');
 await page.evaluate(async()=>{const api='/api/v1/controller',s=await(await fetch(api+'/status')).json(),m=await(await fetch(api+'/remote/settings/memory')).json();const r=await fetch(api+'/remote/settings/memory',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:JSON.stringify({revision:m.revision,target_key:m.target_key,settings:{reserve_vram_gib:6,offload_mode:'automatic',expandable_segments:false}})});if(!r.ok)throw Error('TEST ONLY cross-client update failed')});
 await page.locator('#memory-apply').click();
 await expect(page.locator('#memory-status')).toContainText('changed');await expect(page.locator('#memory-reserve')).toHaveValue('5');await expect(page.locator('#memory-apply')).toBeDisabled();
 await page.locator('#memory-refresh').click();await expect(page.locator('#memory-reserve')).toHaveValue('6');
 await page.screenshot({path:'/tmp/h3-memory-settings.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.screenshot({path:'/tmp/h3-memory-settings-mobile.png',fullPage:true});
 expect(posts.filter(p=>/\/remote\/(jobs|models\/install|features\/)/.test(p))).toEqual([]);
});

test('memory settings ignore late responses from the previous target',async({page})=>{
 await page.setContent('<form id="memory-form"><fieldset id="memory-fields"><input id="memory-auto" type="checkbox"><input id="memory-reserve" type="number"><select id="memory-offload"><option value="automatic">Auto</option><option value="low_vram">Low</option></select><input id="memory-expandable" type="checkbox"><button id="memory-apply">Apply</button></fieldset></form><button id="memory-refresh">Reload</button><button id="memory-defaults">Defaults</button><p id="memory-status"></p>');
 await page.addScriptTag({path:path.join(root,'src/h3server/web/memory-settings.js')});
 const value=await page.evaluate(async()=>{
  let finish;let calls=0;const data=n=>({settings:{reserve_vram_gib:n,offload_mode:'automatic',expandable_segments:false},revision:'a'.repeat(64),target_key:'b'.repeat(64),supported:true,busy:false});
  const unit=H3MemorySettings.create({request:()=>++calls===1?new Promise(r=>finish=r):Promise.resolve(data(6)),run:fn=>fn()});
  unit.context({connected:true,target:'A',busy:false});const old=unit.load();unit.context({connected:true,target:'B',busy:false});await unit.load();finish(data(2));await old;
  return document.getElementById('memory-reserve').value;
 });expect(value).toBe('6');
});
