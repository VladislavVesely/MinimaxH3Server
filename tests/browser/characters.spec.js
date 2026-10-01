// TEST ONLY: real server library/API and media validation; fixture runtime is not H3.
const {test:base,expect}=require('@playwright/test');
const {spawn,execFileSync}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const python=process.env.H3_TEST_PYTHON||path.join(root,'.venv/bin/python');
const pythonpath=[path.join(root,'src'),process.env.H3_TEST_MEDIA_PATH].filter(Boolean).join(path.delimiter);
let codecs=false;
try {execFileSync(python,['-c','import PIL, av'],{env:{...process.env,PYTHONPATH:pythonpath},stdio:'pipe'});execFileSync('ffmpeg',['-version'],{stdio:'pipe'});codecs=true;}catch{}
const test=base.extend({controller:async({},use)=>{
  const child=spawn(python,[path.join(__dirname,'generation_fixture.py')],{cwd:root,env:{...process.env,PYTHONPATH:pythonpath,H3_TEST_PRUNED_PROFILE:'h3-convrot-int8'},stdio:['pipe','pipe','pipe']});
  let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});
  try {
    const launch=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr)),15000);lines.once('line',l=>{clearTimeout(timer);resolve(JSON.parse(l).launch);});child.once('exit',c=>{clearTimeout(timer);reject(Error('Fixture exit '+c+stderr));});});
    await use({launch});
  }finally{lines.close();if(child.exitCode===null){const done=once(child,'exit');child.kill('SIGTERM');await done;}}
}});
async function login(page,controller){
  await page.goto(controller.launch);await expect(page.getByText('Local session secured',{exact:true})).toBeVisible();
  await page.getByLabel('SSH host').fill('fixture.example');await page.getByLabel('SSH username').fill('test');
  await page.getByRole('button',{name:'Connect to server',exact:true}).click();
  await page.getByLabel('I compared this fingerprint with a trusted source').check();await page.getByRole('button',{name:'Trust this fingerprint',exact:true}).click();
  await page.getByLabel('SSH password',{exact:true}).fill('TEST-ONLY-secret');await page.getByRole('button',{name:'Authenticate',exact:true}).click();
  await expect(page.locator('#ssh-state')).toHaveText('SSH authenticated');await page.locator('[data-workspace="generate"]').click();
  await page.locator('#characters-library > summary').click();await expect(page.locator('#characters-status')).toContainText('global characters');
  await page.getByRole('button',{name:'Refresh dashboard',exact:true}).click();await expect(page.locator('#readiness')).toContainText('Runtime ready: true');
}
test('historical reference draft detaches library selections without a false review blocker',async({page})=>{
  await page.setContent('<textarea id="prompt" maxlength="15000">Scene uses &lt;Picture 1&gt;.</textarea><select id="input-mode"><option value="references">References</option></select>'+['characters-status','character-prompt-status','character-review','character-cards','characters-refresh','character-new','character-selected','character-prompt-preview','character-completions','character-scaffold'].map(id=>'<div id="'+id+'"></div>').join(''));
  await page.addScriptTag({path:path.join(root,'src/h3server/web/characters.js')});
  const reasons=await page.evaluate(()=>{
    const draft={reference_images:[{id:'a'.repeat(64),kind:'image'}],reference_videos:[],reference_audios:[]};
    const unit=window.H3Characters.create({api:'',request:async()=>({characters:[]}),upload:async()=>{},getDraft:()=>draft,applyDraft(){},changed(){},isConnected:()=>true,isBusy:()=>false});
    unit.detach();return unit.reasons();
  });
  expect(reasons).toEqual([]);
});

test('character editor opens and adds variants without secure-context randomUUID',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<textarea id="prompt" maxlength="15000"></textarea><select id="input-mode"><option value="references">References</option></select><button id="character-new">New character</button>'+['characters-status','character-prompt-status','character-review','character-cards','characters-refresh','character-selected','character-prompt-preview','character-completions','character-scaffold'].map(id=>'<div id="'+id+'"></div>').join(''));
  await page.evaluate(()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
  await page.addScriptTag({path:path.join(root,'src/h3server/web/characters.js')});
  await page.evaluate(async()=>{
    const draft={reference_images:[],reference_videos:[],reference_audios:[]};
    const unit=window.H3Characters.create({api:'',request:async()=>({characters:[]}),upload:async()=>{},getDraft:()=>draft,applyDraft(){},changed(){},isConnected:()=>true,isBusy:()=>false});
    await unit.load();
  });
  await page.getByRole('button',{name:'New character',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();await expect(page.getByLabel('Character name',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Add variant',exact:true}).click();await expect(page.getByLabel('Variant name',{exact:true})).toHaveCount(2);
  const ids=await page.locator('dialog input,dialog textarea').evaluateAll(nodes=>nodes.map(n=>n.id).filter(Boolean));
  expect(new Set(ids).size).toBe(ids.length);expect(errors).toEqual([]);
});

test('server library variants, MP3, picker, autocomplete and reference review',async({page,controller})=>{
  test.skip(!codecs,'Native Pillow/PyAV and ffmpeg required for media upload acceptance');
  // LAN HTTP omits randomUUID but still exposes cryptographically secure getRandomValues.
  await page.addInitScript(()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
  const errors=[];page.on('pageerror',e=>errors.push(e.message));await login(page,controller);
  const png=execFileSync(python,['-c','from PIL import Image; import sys; Image.new("RGB",(32,32),(20,100,180)).save(sys.stdout.buffer,"PNG")'],{env:{...process.env,PYTHONPATH:pythonpath}});
  const mp3=execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','1','-f','mp3','pipe:1']);
  await page.getByRole('button',{name:'New character',exact:true}).click();
  const dialog=page.getByRole('dialog');await dialog.getByLabel('Character name',{exact:true}).fill('Ada');await dialog.getByLabel('Identity description',{exact:true}).fill('Woman with dark hair.');
  await dialog.getByLabel('Variant name',{exact:true}).fill('Blue coat');await dialog.getByLabel('Variant description',{exact:true}).fill('Wearing a blue coat.');
  await dialog.getByLabel('Variant image',{exact:true}).setInputFiles({name:'blue.png',mimeType:'image/png',buffer:png});
  await dialog.getByRole('button',{name:'Add variant',exact:true}).click();
  await dialog.getByLabel('Variant name',{exact:true}).nth(1).fill('Travel outfit');
  const png2=execFileSync(python,['-c','from PIL import Image; import sys; Image.new("RGB",(32,32),(180,100,20)).save(sys.stdout.buffer,"PNG")'],{env:{...process.env,PYTHONPATH:pythonpath}});
  await dialog.getByLabel('Variant image',{exact:true}).nth(1).setInputFiles({name:'travel.png',mimeType:'image/png',buffer:png2});
  await dialog.getByLabel('Voice MP3', {exact:false}).setInputFiles({name:'voice.mp3',mimeType:'audio/mpeg',buffer:mp3});
  await dialog.getByRole('button',{name:'Save character',exact:true}).click();await expect(dialog).toHaveCount(0);
  await page.getByRole('button',{name:'Use Ada',exact:true}).click();
  await expect(page.locator('#input-mode')).toHaveValue('references');await expect(page.locator('#assets-reference_images img')).toHaveCount(1);await expect(page.locator('#assets-reference_audios audio')).toHaveCount(1);
  await expect(page.locator('#prompt')).toHaveValue(/subject_definitions:[\s\S]*<Subject 1> is Ada[\s\S]*<Audio 1> is the voice-timbre/);
  await page.getByLabel('Prompt',{exact:true}).fill('Scene: <sub');await expect(page.getByRole('listbox',{name:'Reference suggestions'})).toBeVisible();
  await page.getByLabel('Prompt',{exact:true}).press('Enter');await expect(page.locator('#prompt')).toHaveValue('Scene: <Subject 1>');
  await page.getByText('Clickable prompt preview',{exact:true}).click();await page.locator('#character-prompt-preview').getByRole('button',{name:'<Subject 1>',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('Ada · Blue coat');await expect(page.getByRole('dialog').locator('audio')).toHaveCount(1);await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  await page.getByLabel('Variant for Ada',{exact:true}).selectOption({label:'Travel outfit'});
  await expect(page.locator('#character-selected')).toContainText('Travel outfit');await expect(page.locator('#prompt')).toHaveValue('Scene: <Subject 1>');
  await expect(page.locator('#character-prompt-status')).toContainText('Reference assignments changed');
  await page.getByRole('button',{name:'I reviewed the reference labels',exact:true}).click();
  await page.getByRole('button',{name:'Remove Ada',exact:true}).click();await expect(page.locator('#prompt')).toHaveValue('Scene: <Subject 1>');await expect(page.locator('#character-prompt-status')).toContainText('Reference assignments changed');
  await page.getByRole('button',{name:'I reviewed the reference labels',exact:true}).click();
  await page.reload();await page.locator('[data-workspace="generate"]').click();await page.locator('#characters-library > summary').click();await expect(page.getByRole('button',{name:'Use Ada',exact:true})).toBeVisible();
  await expect(page.locator('#character-cards img')).toHaveJSProperty('naturalWidth',32);
  await page.getByRole('button',{name:'Refresh dashboard',exact:true}).click();await page.getByRole('button',{name:'Use Ada',exact:true}).click();
  await expect(page.locator('#character-selected')).toContainText('Ada');
  await expect(page.locator('#assets-reference_images img')).toHaveJSProperty('naturalWidth',32);
  await page.locator('#assets-reference_audios audio').evaluate(n=>{n.preload='metadata';n.load();});
  await expect.poll(()=>page.locator('#assets-reference_audios audio').evaluate(n=>Number.isFinite(n.duration)?n.duration:0)).toBeGreaterThan(0);
  await page.screenshot({path:'/tmp/h3-character-library.png',fullPage:true});
  await page.getByLabel('Prompt',{exact:true}).fill('Scene with <Subject 1>');
  await page.locator('[data-workspace="install"]').click();await page.locator('#disconnect').click();await page.locator('[data-workspace="generate"]').click();
  await expect(page.locator('#character-selected')).toHaveText('');await expect(page.locator('#character-prompt-status')).toContainText('Reference assignments changed');
  expect(errors).toEqual([]);
});
