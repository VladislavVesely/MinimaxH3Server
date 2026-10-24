import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { begin, createSource, animate, finish, getStatus, styleSearchPaths } from '../src/workflow.ts';

const project='a'.repeat(32),run='b'.repeat(32),job='c'.repeat(32),asset='d'.repeat(64),digest='e'.repeat(64);
const discovery={capabilities:{profiles:['installed-profile'],runtime_ready:true,readiness_profile:'installed-profile',service_limits:{steps_default:20,spatial_multiple:32,axis_min:32,axis_max:1920,canvas_pixels:2088960}},model_prompt_modes:{'installed-profile':['frames']},generation_schema:{properties:{first_frame:{},duration:{anyOf:[{ge:5,le:15}]}}}};
const guide={profile:'installed-profile',input_mode:'frames',sections:['dynamic_section','sound'],digest,instructions:'Real model contract'};
async function fixture(t:any){
 const cwd=await mkdtemp(join(tmpdir(),'sprite-test-'));t.after(()=>rm(cwd,{recursive:true,force:true}));await writeFile(join(cwd,'2DSpriteAnimation.style.md'),'Our shared style; palette #00ff00.');
 const calls:Array<{name:string,args:any}>=[];
 const worker=async(action:string,args:any):Promise<any>=>{
  if(action==='check')return {ready:true};if(action==='plan')return {key:'#ff00ff'};
  if(action==='prepare'){await writeFile(args.output,'prepared image',{flag:'wx'});return {original_preserved:true};}
  if(action==='validate')return {border_key_fraction:1};
  if(action==='process'){await mkdir(args.output,{recursive:true});return {output:args.output,frames:12,quality:{warnings:[]}};}
  throw new Error(action);
 };
 const call=async(name:string,args:any):Promise<any>=>{
  calls.push({name,args});
  if(name==='h3_discover')return discovery;if(name==='h3_guide')return guide;
  if(name==='generate_image'){await writeFile(args.output_path,'original image');return {images:[{path:args.output_path}],warnings:['backend warning']};}
  if(name==='h3_upload')return {id:asset};if(name==='h3_compile')return {prompt:'dynamic_section: real\nsound: silent',contract_digest:digest,effective_frames:124,effective_seconds:124/24,warnings:[]};
  if(name==='h3_preflight'||name==='h3_generate'){
   assert.notEqual(Object.hasOwn(args.generation,'duration'),Object.hasOwn(args.generation,'num_frames'),'Native H3 requires frames or duration, not both');
   if(name==='h3_preflight')return {valid:true,ready:true};
  }
  if(name==='h3_request')return {project_id:project,name:args.body?.name,revision:1};
  if(name==='h3_run'){
   if(args.action==='create')assert.match(args.body.project_id,/^[a-f0-9]{32}$/,'Native RunCreate requires project_id');
   return {id:run,revision:1};
  }
  if(name==='h3_generate'||name==='h3_receipt'||name==='h3_job')return {job_id:job,status:'completed'};
  if(name==='h3_inspect')return {};throw new Error(name);
 };
 return {cwd,call,calls,worker,options:{name:'Abstract morph',request:'An isometric geometric lattice transforming into a spiral, repeating.',loop:true,loopReason:'User explicitly requested repetition',camera:'fixed dimetric',motion:'A continuous geometric transformation'}};
}

test('implementation-folder startup finds the shared workspace style without falling back for unrelated projects',()=>{
 const pkg=fileURLToPath(new URL('../',import.meta.url));
 assert.equal(styleSearchPaths(pkg).at(-1),resolve(pkg,'../../2D Animations/2DSpriteAnimation.style.md'));
 assert.deepEqual(styleSearchPaths('/tmp/unrelated-project'),['/tmp/unrelated-project/2DSpriteAnimation.style.md','/tmp/unrelated-project/2D Animations/2DSpriteAnimation.style.md']);
});

test('prepare supports arbitrary intent, style, adaptive key and actual model guide without spending',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);
 assert.equal(m.options.loop,true);assert.equal(m.key,'#ff00ff');assert.equal(m.guide.instructions,'Real model contract');assert.match(m.style,/shared style/);
 assert.deepEqual(f.calls.map(c=>c.name),['h3_discover','h3_guide']);assert.equal((await getStatus(m.folder)).stage,'prepared');
});
test('missing style stops before any inference',async t=>{
 const f=await fixture(t);await rm(join(f.cwd,'2DSpriteAnimation.style.md'));
 await assert.rejects(begin(f.options,f.cwd,f.call,f.worker),/style/i);assert.equal(f.calls.length,0);
});
test('source uses exactly one GPT request with style/key/reference and preserves backend warnings',async t=>{
 const f=await fixture(t);const ref=join(f.cwd,'ref.png');await writeFile(ref,'reference');
 const m=await begin({...f.options,reference:ref,referenceRole:'inspiration'},f.cwd,f.call,f.worker);
 await createSource(m.folder,'Entire requested composition with movement margin',f.call,f.worker);
 await createSource(m.folder,'Do not spend again',f.call,f.worker);
 const g=f.calls.filter(c=>c.name==='generate_image');assert.equal(g.length,1);assert.match(g[0].args.prompt,/#ff00ff/);assert.match(g[0].args.prompt,/shared style/);assert.equal(g[0].args.images.length,1);
 assert.match((await getStatus(m.folder)).warnings.join(' '),/backend warning/);
});
test('failed source cannot accidentally spend another image request on resume',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);let count=0;
 const fail=async(n:string,a:any)=>{if(n==='generate_image'){count++;throw new Error('quota exhausted');}return f.call(n,a);};
 await assert.rejects(createSource(m.folder,'source',fail,f.worker),/quota/);
 await assert.rejects(createSource(m.folder,'source',fail,f.worker),/attempt|uncertain|retry/i);assert.equal(count,1);
});
test('dynamic sections compile/preflight and original source conditions bounded generation',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);
 await assert.rejects(animate(m.folder,{invented:'no'},f.call),/section/i);
 await animate(m.folder,{dynamic_section:'[Shot 1] The requested action',sound:'silence'},f.call);
 assert.equal(f.calls.filter(c=>c.name==='h3_generate').length,1);assert.ok(f.calls.findIndex(c=>c.name==='h3_preflight')<f.calls.findIndex(c=>c.name==='h3_generate'));
 const submission=f.calls.find(c=>c.name==='h3_generate')!.args;assert.equal(submission.generation.first_frame,asset);assert.equal(Object.hasOwn(submission.generation,'duration'),false);assert.equal(submission.generation.num_frames,124);assert.equal(submission.generation.profile,'installed-profile');assert.match(submission.request_id,/^[a-f0-9]{32}$/);
});
test('a malformed project response is retained and cannot reach run creation',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);
 const wrong=async(n:string,a:any)=>n==='h3_request'?{id:project}:f.call(n,a);
 await assert.rejects(animate(m.folder,{dynamic_section:'action',sound:'silence'},wrong),/project_id/);
 assert.equal(f.calls.filter(c=>c.name==='h3_run').length,0);
 assert.equal((await getStatus(m.folder)).projectResult.id,project);
});

test('a malformed run acknowledgement is retained without submitting a generation',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);
 const wrong=async(n:string,a:any)=>n==='h3_run'&&a.action==='create'?{revision:1}:f.call(n,a);
 await assert.rejects(animate(m.folder,{dynamic_section:'action',sound:'silence'},wrong),/run id/);
 const saved=await getStatus(m.folder);assert.equal(saved.projectId,project);assert.equal(saved.runResult.revision,1);assert.equal(saved.requestId,undefined);
 assert.equal(f.calls.filter(c=>c.name==='h3_generate').length,0);
});

test('ambiguous submission persists its ID and resumes via receipt, never submits again',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);let submits=0;
 const ambiguous=async(n:string,a:any)=>{if(n==='h3_generate'){submits++;throw new Error('connection lost');}return f.call(n,a);};
 await assert.rejects(animate(m.folder,{dynamic_section:'action',sound:'silence'},ambiguous),/connection lost/);
 const saved=await getStatus(m.folder);assert.match(saved.requestId,/^[a-f0-9]{32}$/);
 await animate(m.folder,{},ambiguous);assert.equal(submits,1);assert.equal((await getStatus(m.folder)).jobId,job);assert.equal(f.calls.find(c=>c.name==='h3_receipt')!.args.request_id,saved.requestId);
});
test('finish/reprocess uses original download, creates separate exports and never re-generates',async t=>{
 const f=await fixture(t);const m=await begin({...f.options,loop:false,loopReason:'one-shot'},f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);await animate(m.folder,{dynamic_section:'action',sound:'silence'},f.call);
 let downloads=0;const download=async(id:string,p:string)=>{assert.equal(id,job);downloads++;await writeFile(p,'video');};
 const first=await finish(m.folder,f.call,f.worker,download,{});const second=await finish(m.folder,f.call,f.worker,download,{fps:8});
 assert.notEqual(first.export.output,second.export.output);assert.equal(downloads,1);assert.equal(f.calls.filter(c=>c.name==='h3_generate').length,1);
});
test('style settings set output defaults but explicit request overrides them',async t=>{
 const f=await fixture(t);await writeFile(join(f.cwd,'2DSpriteAnimation.style.md'),'Style\n```sprite-settings\n{"fps":8,"outputWidth":128,"outputHeight":128,"pixelArt":true}\n```');
 const m=await begin({...f.options,fps:10},f.cwd,f.call,f.worker);assert.equal(m.options.fps,10);assert.equal(m.options.outputWidth,128);assert.equal(m.options.pixelArt,true);
});
test('exact artwork skips GPT Image and one-shot does not force a last frame',async t=>{
 const f=await fixture(t);const ref=join(f.cwd,'exact.png');await writeFile(ref,'exact');
 const m=await begin({...f.options,reference:ref,referenceRole:'exact',loop:false},f.cwd,f.call,f.worker);await createSource(m.folder,'',f.call,f.worker);await animate(m.folder,{dynamic_section:'one shot',sound:'silence'},f.call);
 assert.equal(f.calls.filter(c=>c.name==='generate_image').length,0);assert.equal(f.calls.find(c=>c.name==='h3_generate')!.args.generation.last_frame,undefined);
});
test('preflight failure cannot queue or create a run',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);
 const blocked=async(n:string,a:any)=>n==='h3_preflight'?{valid:true,ready:false,not_ready_reasons:['busy']}:f.call(n,a);
 await assert.rejects(animate(m.folder,{dynamic_section:'action',sound:'silence'},blocked),/not ready/);assert.equal(f.calls.filter(c=>c.name==='h3_generate'||c.name==='h3_run').length,0);
 const persisted=JSON.parse(await readFile(join(m.folder,'h3-prompt.json'),'utf8'));assert.equal(persisted.generation.num_frames,124);assert.equal(persisted.sections.dynamic_section,'action');
 await animate(m.folder,{},f.call);assert.equal(f.calls.filter(c=>c.name==='generate_image').length,1);assert.equal(f.calls.filter(c=>c.name==='h3_generate').length,1);
});
test('a rejected prepared source can be explicitly adopted again without paying',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);let fails=true;
 const check=async(a:string,b:any)=>{if(a==='validate'&&fails)throw new Error('Wrong background');return f.worker(a,b);};
 await assert.rejects(createSource(m.folder,'source',f.call,check),/Wrong background/);fails=false;
 await createSource(m.folder,'',f.call,check,join(m.folder,'source.generated.png'));
 assert.equal(f.calls.filter(c=>c.name==='generate_image').length,1);assert.equal((await getStatus(m.folder)).stage,'source_ready');
});
test('concurrent source operations cannot spend twice',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);let release!:()=>void;
 const wait=new Promise<void>(r=>{release=r});let entered!:()=>void;const started=new Promise<void>(r=>{entered=r});
 const blocked=async(n:string,a:any)=>{if(n==='generate_image'){entered();await wait;}return f.call(n,a);};
 const first=createSource(m.folder,'source',blocked,f.worker);await started;
 await assert.rejects(createSource(m.folder,'source',blocked,f.worker),/busy/);release();await first;
 assert.equal(f.calls.filter(c=>c.name==='generate_image').length,1);
});
test('pending video waits without downloading or generating and can finish later',async t=>{
 const f=await fixture(t);const m=await begin(f.options,f.cwd,f.call,f.worker);await createSource(m.folder,'source',f.call,f.worker);await animate(m.folder,{dynamic_section:'action',sound:'silence'},f.call);
 const waiting=async(n:string,a:any)=>n==='h3_job'?{job_id:job,status:'running'}:f.call(n,a);
 const r=await finish(m.folder,waiting,f.worker,async()=>{throw new Error('Should not download running job')});assert.equal(r.pending,true);assert.equal(f.calls.filter(c=>c.name==='h3_generate').length,1);
});
test('unknown profile or unsupported conditioning fails closed',async t=>{
 const f=await fixture(t);await assert.rejects(begin({...f.options,profile:'not-installed'},f.cwd,f.call,f.worker),/profile/i);
});
