import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,mkdir,rm,cp,symlink} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';import {execFileSync} from 'node:child_process';
import {beginBridge,createSource,animate,getStatus,finish,inspectReference,beginExtension} from '../src/workflow.ts';
import {runWorker} from '../src/worker.ts';
async function fixture(t:any){
 const root=await mkdtemp(join(tmpdir(),'sprite-bridge-'));t.after(()=>rm(root,{recursive:true,force:true}));await writeFile(join(root,'2DSpriteAnimation.style.md'),'Generic artwork.');
 const folders=[join(root,'Sprite_A'),join(root,'Sprite_B')];
 for(const [index,folder] of folders.entries()){
  const output=join(folder,'export_001');await mkdir(join(output,'frames'),{recursive:true});
  execFileSync('python3',['-c',`from PIL import Image,ImageDraw
import sys
for n in range(2):
 im=Image.new('RGBA',(64,64));d=ImageDraw.Draw(im);d.rectangle((16+n*2,16,40,48),fill=('red' if sys.argv[2]=='0' else 'yellow'));im.save(sys.argv[1]+'/frames/'+str(n)+'.png')`,output,String(index)]);
  await writeFile(join(output,'animation.json'),JSON.stringify({version:1,canvas:{width:64,height:64},anchor:[.5,.8],fps:12,frames:[{file:'frames/0.png'},{file:'frames/1.png'}]}));
  await writeFile(join(folder,'manifest.json'),JSON.stringify({version:1,kind:'2d-sprite-animation',folder,options:{width:128,height:128,pixelArt:true,soft:false},exports:[{output}]}));
 }
 const calls:any[]=[];let uploads=0;
 const call=async(name:string,args:any):Promise<any>=>{
  calls.push({name,args});switch(name){
   case 'h3_discover':return {capabilities:{profiles:['test'],readiness_profile:'test'},model_prompt_modes:{test:['frames']},generation_schema:{properties:{first_frame:{},last_frame:{}}}};
   case 'h3_guide':return {sections:['action'],digest:'a'.repeat(64)};
   case 'h3_upload':return {id:String(++uploads).repeat(64)};
   case 'h3_compile':return {prompt:'bridge',effective_frames:124,contract_digest:'a'.repeat(64)};
   case 'h3_preflight':return {valid:true,ready:true};
   case 'h3_request':return {project_id:'b'.repeat(32)};
   case 'h3_run':return {id:'c'.repeat(32),revision:1};
   case 'h3_generate':case 'h3_receipt':return {job_id:'d'.repeat(32)};
   case 'h3_job':return {status:'completed'};
   case 'h3_inspect':return {};
   default:throw Error('Unexpected tool '+name);
  }
 };
 return {root,folders,call,calls,options:{name:'A to B',request:'Transition naturally between these poses',loop:false,loopReason:'one-shot bridge'}};
}
test('bridge snapshots last-to-first endpoints and submits distinct conditioning without GPT',async t=>{
 const f=await fixture(t);const before=await readFile(join(f.folders[0],'manifest.json'));
 const m=await beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker);
 assert.deepEqual(m.bridge.from.motionContext.map((x:any)=>x.frameIndex),[0,1]);
 assert.deepEqual(m.bridge.to.motionContext.map((x:any)=>x.frameIndex),[0,1]);
 assert.match(m.bridge.motionGuidance,/velocity/);assert.match(m.bridge.from.motionContext[0].sha256,/^[a-f0-9]{64}$/);
 assert.equal(m.budget.image_requests,0);assert.equal(m.options.loop,false);assert.equal(m.bridge.from.frameIndex,1);assert.equal(m.bridge.to.frameIndex,0);
 await createSource(m.folder,'',f.call,runWorker);await animate(m.folder,{action:'Move from first pose into the second'},f.call);
 const g=f.calls.find(x=>x.name==='h3_generate').args.generation;
 assert.notEqual(g.first_frame,g.last_frame);assert.equal(g.num_frames,124);assert.equal(g.duration,undefined);
 assert.deepEqual(await readFile(join(f.folders[0],'manifest.json')),before);
 assert.equal(f.calls.filter(x=>x.name==='h3_generate').length,1);assert.ok(!f.calls.some(x=>x.name==='generate_image'));
 const saved=await getStatus(m.folder);assert.ok(saved.targetSha256);assert.ok(saved.bridge.transforms.to);
 const r=await finish(m.folder,f.call,runWorker,async(_job,file)=>{
  execFileSync('ffmpeg',['-v','error','-loop','1','-i',join(m.folder,'source.png'),'-t','1','-r','12','-c:v','libx264','-pix_fmt','yuv420p','-f','mp4',file]);
 });
 const meta=JSON.parse(await readFile(join(r.export.output,'animation.json'),'utf8'));
 assert.equal(meta.loop,false);assert.equal(meta.bridge.from.frameIndex,1);assert.equal(meta.bridge.to.frameIndex,0);assert.equal(meta.frames.length,12);
 assert.equal(meta.canvas.width,64);assert.deepEqual(meta.anchor,[.5,.8]);assert.equal(r.export.quality.antialias,'off');
 assert.ok((await readFile(join(r.export.output,'preview.gif'))).subarray(0,3).equals(Buffer.from('GIF')));
});
test('bridge rejects path escape before remote calls',async t=>{
 const f=await fixture(t);const p=join(f.folders[1],'export_001/animation.json');const meta=JSON.parse(await readFile(p,'utf8'));meta.frames[0].file='../../secret.png';await writeFile(p,JSON.stringify(meta));
 await assert.rejects(beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker),/frame|path/i);assert.equal(f.calls.length,0);
});
test('bridge fails closed when last-frame support is missing',async t=>{
 const f=await fixture(t);const call=async(n:string,a:any)=>{const r=await f.call(n,a);if(n==='h3_discover')delete r.generation_schema.properties.last_frame;return r;};
 await assert.rejects(beginBridge(...f.folders as [string,string],f.options,f.root,call,runWorker),/last.frame/i);assert.ok(!f.calls.some(x=>x.name==='h3_generate'));
});
test('latest export is default and explicit revisions remain selectable',async t=>{
 const f=await fixture(t);const folder=f.folders[0];const output=join(folder,'export_002');await cp(join(folder,'export_001'),output,{recursive:true});
 const path=join(folder,'manifest.json');const m=JSON.parse(await readFile(path,'utf8'));m.exports.push({output});await writeFile(path,JSON.stringify(m));
 const latest=await beginBridge(folder,f.folders[1],f.options,f.root,f.call,runWorker);assert.equal(latest.bridge.from.export,output);
 const pinned=await beginBridge(folder,f.folders[1],f.options,f.root,f.call,runWorker,'export_001');assert.equal(pinned.bridge.from.export,join(folder,'export_001'));
});
test('bridge rejects symlinked endpoint and detects altered pinned snapshots',async t=>{
 const f=await fixture(t);const m=await beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker);
 await writeFile(join(m.folder,m.targetReferenceFile),'modified');await assert.rejects(createSource(m.folder,'',f.call,runWorker),/snapshot changed/);
 const file=join(f.folders[1],'export_001/frames/0.png');await rm(file);await symlink(join(f.folders[0],'export_001/frames/0.png'),file);
 await assert.rejects(beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker),/regular|symlink/i);
 assert.ok(!f.calls.some(x=>x.name==='h3_generate'||x.name==='generate_image'));
});
test('bridge fails on inconsistent PNG dimensions rather than trusting metadata',async t=>{
 const f=await fixture(t);const p=join(f.folders[1],'export_001/animation.json');const meta=JSON.parse(await readFile(p,'utf8'));meta.canvas.width=32;await writeFile(p,JSON.stringify(meta));
 const m=await beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker);await assert.rejects(createSource(m.folder,'',f.call,runWorker),/dimensions/);
 assert.ok(!f.calls.some(x=>x.name==='h3_generate'));
});
test('single-folder extension requires contextual frame choice and preserves provenance without GPT',async t=>{
 const f=await fixture(t);const before=await readFile(join(f.folders[0],'manifest.json'));
 const info=await inspectReference(f.folders[0],runWorker);
 assert.equal(info.frameCount,2);assert.equal(info.selectionRequired,true);assert.equal(info.preview.mimeType,'image/png');assert.equal(f.calls.length,0);
 const selected=info.sampledFrames[1];
 const choice={export:info.exportName,frameIndex:selected.frameIndex,metadataSha256:info.metadataSha256,frameSha256:selected.sha256,reason:'This is the stable holding pose, not recoil',scope:'Preserve identity and prop grip; change action to upright locomotion'};
 const m=await beginExtension(f.folders[0],{...f.options,loop:true,loopReason:'Repeated new action'},choice,f.root,f.call,runWorker);
 assert.equal(m.extension.reference.frameIndex,1);assert.equal(m.budget.image_requests,0);assert.equal(m.options.loop,true);
 await createSource(m.folder,'',f.call,runWorker);await animate(m.folder,{action:'Perform the requested new action with preserved appearance'},f.call);
 const gen=f.calls.find(x=>x.name==='h3_generate').args.generation;assert.equal(gen.first_frame,gen.last_frame);
 assert.deepEqual(await readFile(join(f.folders[0],'manifest.json')),before);
 assert.ok(!f.calls.some(x=>x.name==='generate_image'));
 const r=await finish(m.folder,f.call,runWorker,async(_job,file)=>execFileSync('ffmpeg',['-v','error','-loop','1','-i',join(m.folder,'source.png'),'-t','1','-r','12','-c:v','libx264','-pix_fmt','yuv420p','-f','mp4',file]) as any);
 const meta=JSON.parse(await readFile(join(r.export.output,'animation.json'),'utf8'));assert.equal(meta.reference.selection.scope,choice.scope);
});
test('single-folder extension refuses implicit first-frame selection and stale inspected metadata',async t=>{
 const f=await fixture(t);await assert.rejects(beginExtension(f.folders[0],f.options,{} as any,f.root,f.call,runWorker),/selection|choice/i);
 const info=await inspectReference(f.folders[0],runWorker);const s=info.sampledFrames[0];
 const choice={export:info.exportName,frameIndex:0,metadataSha256:'0'.repeat(64),frameSha256:s.sha256,reason:'selected after inspection',scope:'new motion'};
 await assert.rejects(beginExtension(f.folders[0],f.options,choice,f.root,f.call,runWorker),/changed|inspect/i);assert.equal(f.calls.length,0);
});
test('ambiguous bridge submit resumes via original receipt only',async t=>{
 const f=await fixture(t);const m=await beginBridge(f.folders[0],f.folders[1],f.options,f.root,f.call,runWorker);await createSource(m.folder,'',f.call,runWorker);
 let attempts=0;const call=async(n:string,a:any)=>{if(n==='h3_generate'){attempts++;throw Error('lost response');}return f.call(n,a);};
 await assert.rejects(animate(m.folder,{action:'bridge'},call),/lost response/);await animate(m.folder,{},call);assert.equal(attempts,1);
});
