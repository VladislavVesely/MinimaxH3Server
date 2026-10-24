import { mkdir, readFile, writeFile, rename, unlink, open, stat, lstat, copyFile, realpath } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join, resolve, dirname, extname } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import { BRIDGE_MOTION_GUIDANCE } from './bridge-prompt.ts';

export type Call = (name:string,args:any)=>Promise<any>;
export type Worker = (action:string,args:any)=>Promise<any>;
export interface Options {
 name:string; request:string; loop:boolean; loopReason:string;
 camera?:string; motion?:string; palette?:string[]; reference?:string;
 referenceRole?:'inspiration'|'identity'|'exact'; referenceBackground?:string;
 profile?:string; projectId?:string; width?:number; height?:number;
 outputWidth?:number; outputHeight?:number; fps?:number; soft?:boolean;
 pixelArt?:boolean; anchor?:number[]; cycleSeconds?:number; duration?:number;
}
export const STYLE='2DSpriteAnimation.style.md';
export function styleSearchPaths(cwd:string){
 cwd=resolve(cwd);
 const candidates=[join(cwd,STYLE),join(cwd,'2D Animations',STYLE)];
 const packageRoot=resolve(fileURLToPath(new URL('../',import.meta.url)));
 // Only this implementation directory gets the repository workspace fallback.
 // Other projects must supply their own style, not silently inherit ours.
 if(cwd===packageRoot)candidates.push(resolve(packageRoot,'../../2D Animations',STYLE));
 return candidates;
}
const id=()=>randomUUID().replaceAll('-','');
function canonicalId(value:unknown,field:string){
 if(typeof value!=='string'||!/^[a-f0-9]{32}$/.test(value))throw new Error('H3 response missing valid '+field+'; recorded response retained, no downstream operation submitted');
 return value;
}
const hash=(b:Buffer|string)=>createHash('sha256').update(b).digest('hex');
async function atomic(file:string,value:any){const temp=file+'.'+id()+'.tmp';await writeFile(temp,JSON.stringify(value,null,2)+'\n',{mode:0o600,flag:'wx'});await rename(temp,file);}
async function regular(file:string){const s=await lstat(file);if(!s.isFile()||s.isSymbolicLink()||!s.size||s.size>64*1024*1024)throw new Error('Reference must be a regular image file under 64 MiB');}
async function save(m:any){m.updatedAt=new Date().toISOString();await atomic(join(m.folder,'manifest.json'),m);}
export async function getStatus(folder:string){
 folder=resolve(folder);const p=join(folder,'manifest.json');const st=await lstat(p);
 if(!st.isFile()||st.isSymbolicLink()||st.size>2*1024*1024)throw new Error('Invalid sprite manifest');
 const m=JSON.parse(await readFile(p,'utf8'));
 if(m.version!==1||m.kind!=='2d-sprite-animation'||m.folder!==folder)throw new Error('Not an owned sprite animation folder');return m;
}
async function locked<T>(folder:string,fn:(m:any)=>Promise<T>):Promise<T>{
 folder=resolve(folder);await getStatus(folder);const path=join(folder,'.sprite.lock');let handle;
 try{handle=await open(path,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY,0o600);}
 catch(error){
  if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;
  const before=await lstat(path);if(!before.isFile()||before.isSymbolicLink())throw new Error('Invalid sprite lock');
  const lock=JSON.parse(await readFile(path,'utf8'));let dead=false;
  if(Number.isInteger(lock.pid)&&lock.pid>0){try{process.kill(lock.pid,0);}catch(e){dead=(e as NodeJS.ErrnoException).code==='ESRCH';}}
  if(!dead)throw new Error('Sprite folder is busy in another operation; do not duplicate generation');
  const now=await lstat(path);if(now.ino!==before.ino)throw new Error('Sprite lock changed');
  await unlink(path);handle=await open(path,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY,0o600);
 }
 try{
  await handle.writeFile(JSON.stringify({pid:process.pid,nonce:id()}));
  // Read authoritative state after acquiring the inter-process lock, not before.
  return await fn(await getStatus(folder));
 }finally{await handle.close();await unlink(path);}
}
function message(error:unknown){return error instanceof Error?error.message:String(error);}
function validateOptions(o:Options){
 if(!o.name?.trim()||!o.request?.trim()||typeof o.loop!=='boolean'||!o.loopReason?.trim())throw new Error('Name, request, resolved loop boolean and loopReason are required');
 if(o.request.length>4000||o.name.length>200)throw new Error('Request/name too long');
 if(o.referenceRole&&!o.reference)throw new Error('Reference role needs an image');
 if(o.referenceRole&&!['inspiration','identity','exact'].includes(o.referenceRole))throw new Error('Invalid reference role');
 if(o.reference&&!o.referenceRole)throw new Error('Specify inspiration, identity or exact for the reference image');
 for(const c of [...(o.palette??[]),...(o.referenceBackground?[o.referenceBackground]:[])])if(!/^#[a-fA-F0-9]{6}$/.test(c))throw new Error('Palette colours must be #RRGGBB');
 for(const n of ['width','height'] as const)if(o[n]!==undefined&&(!Number.isInteger(o[n])||o[n]!%32!==0||o[n]!<32||o[n]!>1920))throw new Error('Source canvas must use supported multiples of 32');
 for(const n of ['outputWidth','outputHeight'] as const)if(o[n]!==undefined&&(!Number.isInteger(o[n])||o[n]!<16||o[n]!>1024))throw new Error('Output canvas must be 16–1024 pixels per axis');
 if(o.fps!==undefined&&(!Number.isFinite(o.fps)||o.fps<1||o.fps>60))throw new Error('Invalid output FPS');
 if(o.duration!==undefined&&(!Number.isFinite(o.duration)||o.duration<5||o.duration>15))throw new Error('H3 generation duration must be 5–15 seconds; final duration is independent');
 if(o.cycleSeconds!==undefined&&(!Number.isFinite(o.cycleSeconds)||o.cycleSeconds<=0||o.cycleSeconds>120))throw new Error('Invalid final animation duration');
 if(o.anchor&&(!Array.isArray(o.anchor)||o.anchor.length!==2||o.anchor.some(x=>!Number.isFinite(x)||x<0||x>1)))throw new Error('Invalid normalized anchor');
 for(const flag of ['soft','pixelArt'] as const)if(o[flag]!==undefined&&typeof o[flag]!=='boolean')throw new Error('Style flags must be booleans');
}
async function bridgeEndpoint(folder:string,selection:string|undefined,last:boolean){
 folder=resolve(folder);const m=await getStatus(folder);
 const output=selection?join(folder,selection):m.exports?.at(-1)?.output;
 if(selection&&!/^export_\d{3,}$/.test(selection))throw new Error('Invalid export selection');
 if(typeof output!=='string'||dirname(output)!==folder||!/^export_\d{3,}$/.test(output.slice(folder.length+1)))throw new Error('Folder has no valid completed sprite export');
 const canonicalOutput=join(await realpath(folder),output.slice(folder.length+1));
 if(await realpath(output)!==canonicalOutput)throw new Error('Export path must not contain symlinks');
 const metaFile=join(output,'animation.json');await regular(metaFile);const bytes=await readFile(metaFile);if(bytes.length>2*1024*1024)throw new Error('Oversized animation metadata');
 const meta=JSON.parse(bytes.toString());
 if(meta.version!==1||!Array.isArray(meta.frames)||!meta.frames.length||meta.frames.length>900)throw new Error('Invalid exported frames');
 if(![meta.canvas?.width,meta.canvas?.height].every(x=>Number.isInteger(x)&&x>=16&&x<=1024)||!Array.isArray(meta.anchor)||meta.anchor.length!==2||!meta.anchor.every((x:any)=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=1)||!Number.isFinite(meta.fps)||meta.fps<1||meta.fps>60)throw new Error('Invalid export canvas, anchor or FPS');
 const frameIndex=last?meta.frames.length-1:0;const frame=meta.frames[frameIndex].file;
 if(typeof frame!=='string'||!/^frames\/[\w-]+\.png$/.test(frame))throw new Error('Invalid endpoint frame path');
 const file=join(output,frame);await regular(file);if(await realpath(file)!==join(canonicalOutput,frame))throw new Error('Endpoint frame path must not contain symlinks');
 const motionContext=[];
 const start=last?Math.max(0,meta.frames.length-4):0,end=last?meta.frames.length:Math.min(4,meta.frames.length);
 for(let index=start;index<end;index++){
  const item=meta.frames[index];if(typeof item.file!=='string'||!/^frames\/[\w-]+\.png$/.test(item.file))throw new Error('Invalid motion context frame path');
  const path=join(output,item.file);await regular(path);if(await realpath(path)!==join(canonicalOutput,item.file))throw new Error('Motion context frame path must not contain symlinks');
  motionContext.push({frameIndex:index,file:path,sha256:hash(await readFile(path)),duration_ms:item.duration_ms??1000/meta.fps});
 }
 return {folder,export:output,frame,frameIndex,file,sha256:hash(await readFile(file)),metadataSha256:hash(bytes),canvas:meta.canvas,anchor:meta.anchor,fps:meta.fps,motionContext,options:m.options};
}
export async function beginBridge(fromFolder:string,toFolder:string,options:Options,cwd:string,call:Call,worker:Worker,fromExport?:string,toExport?:string){
 const from=await bridgeEndpoint(resolve(cwd,fromFolder),fromExport,true),to=await bridgeEndpoint(resolve(cwd,toFolder),toExport,false);
 const inherited={width:from.options?.width??512,height:from.options?.height??512,outputWidth:from.canvas.width,outputHeight:from.canvas.height,fps:from.fps,anchor:from.anchor,pixelArt:from.options?.pixelArt??false,soft:from.options?.soft??false};
 const chosen={...inherited,...options,loop:false,loopReason:'One-shot motion bridge from A last frame to B first frame',reference:from.file,referenceRole:'exact' as const};
 return begin(chosen,cwd,call,worker,{from,to,endpointPolicy:'A last frame to B first frame',motionGuidance:BRIDGE_MOTION_GUIDANCE,transforms:{}});
}
export async function begin(options:Options,cwd:string,call:Call,worker:Worker,bridge?:any){
 validateOptions(options);cwd=resolve(cwd);
 let styleFile='',style='';
 for(const candidate of styleSearchPaths(cwd)){
  try{style=await readFile(candidate,'utf8');styleFile=candidate;break;}
  catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 }
 if(!styleFile)throw new Error('Create '+STYLE+' in the working folder (or project 2D Animations folder) before generating');
 if(!style.trim()||style.length>50000)throw new Error('Style file must be nonempty and under 50,000 characters');
 const settings=style.match(/```sprite-settings\s*\n([\s\S]*?)\n```/);
 if(settings){
  let defaults:any;try{defaults=JSON.parse(settings[1]);}catch{throw new Error('Invalid JSON in sprite-settings style block');}
  const allowed=['camera','palette','profile','width','height','outputWidth','outputHeight','fps','soft','pixelArt','anchor','cycleSeconds','duration'];
  if(!defaults||typeof defaults!=='object'||Array.isArray(defaults)||Object.keys(defaults).some(k=>!allowed.includes(k)))throw new Error('Unknown style setting (loop choice belongs to each request)');
  options={...defaults,...options};validateOptions(options);
 }
 await worker('check',{});
 let reference:string|undefined;if(options.reference){reference=resolve(cwd,options.reference);await regular(reference);}
 const discovery=await call('h3_discover',{});const caps=discovery.capabilities;
 const profile=options.profile??(caps.profiles?.includes(caps.readiness_profile)?caps.readiness_profile:caps.profiles?.[0]);
 if(!profile||!caps.profiles?.includes(profile))throw new Error('Requested profile is not currently installed');
 if(!discovery.model_prompt_modes?.[profile]?.includes('frames')||!discovery.generation_schema?.properties?.first_frame)throw new Error('Installed model does not expose supported frame conditioning; no silent fallback');
 if(bridge&&!discovery.generation_schema?.properties?.last_frame)throw new Error('Installed model does not expose last-frame conditioning');
 const guide=await call('h3_guide',{profile,mode:'frames'});
 if(!Array.isArray(guide.sections)||!guide.sections.length||typeof guide.digest!=='string')throw new Error('Missing model-owned prompt contract');
 const width=options.width??512,height=options.height??512;const limits=caps.service_limits;
 if(limits&&(width>limits.axis_max||height>limits.axis_max||width*height>limits.canvas_pixels||width%limits.spatial_multiple||height%limits.spatial_multiple))throw new Error('Canvas exceeds discovered model limits');
 const palette=[...(options.palette??[]),...(style.match(/#[a-fA-F0-9]{6}\b/g)??[])];
 const plan=await worker('plan',{palette,...(reference?{reference}:{}),...(bridge?{references:[bridge.to.file]}:{})});
 const safe=options.name.normalize('NFKC').replace(/[^\p{L}\p{N}_-]+/gu,'_').replace(/^[_-]+|[_-]+$/g,'').slice(0,80)||'Animation';
 const base=dirname(styleFile);let folder='';
 for(let i=1;i<=10000;i++){folder=join(base,'Sprite_'+safe+(i===1?'':'_'+i));try{await mkdir(folder,{mode:0o700});break;}catch(e){if((e as NodeJS.ErrnoException).code!=='EEXIST')throw e;if(i===10000)throw e;}}
 const m:any={version:1,kind:'2d-sprite-animation',folder,createdAt:new Date().toISOString(),stage:'prepared',options:{...options,width,height},style,styleSha256:hash(style),profile,mode:'frames',guide,key:plan.key,steps:limits?.steps_default??20,warnings:[],exports:[],budget:{image_requests:options.referenceRole==='exact'?0:1,h3_jobs:1}};
 if(reference){m.referenceFile='reference'+(extname(reference).toLowerCase()||'.png');await copyFile(reference,join(folder,m.referenceFile));await (await import('node:fs/promises')).chmod(join(folder,m.referenceFile),0o600);m.referenceSha256=hash(await readFile(reference));}
 if(bridge){
  m.bridge=bridge;m.targetReferenceFile='target.reference.png';await copyFile(bridge.to.file,join(folder,m.targetReferenceFile));await (await import('node:fs/promises')).chmod(join(folder,m.targetReferenceFile),0o600);
  if(m.referenceSha256!==bridge.from.sha256||hash(await readFile(join(folder,m.targetReferenceFile)))!==bridge.to.sha256)throw new Error('Endpoint changed during preparation; no generation submitted');
  m.warnings.push('Bridge endpoint conditioning is not a pixel lock or velocity match. Inspect both joins. Sources are unchanged; canvas/anchor transforms are recorded.');
 }
 await writeFile(join(folder,STYLE),style,{mode:0o600,flag:'wx'});await atomic(join(folder,'request.json'),options);await atomic(join(folder,'guide.json'),guide);await save(m);return m;
}
export async function createSource(folder:string,prompt:string,call:Call,worker:Worker,adoptFile?:string){
 return locked(folder,async m=>{
  if(m.sourceFile)return m;
  try{
   if(m.bridge){
    if(adoptFile)throw new Error('Bridge endpoints are pinned snapshots; cannot adopt a replacement source');
    for(const [role,file,provenance] of [['from',m.referenceFile,m.bridge.from],['to',m.targetReferenceFile,m.bridge.to]] as const){
     const input=join(m.folder,file);if(hash(await readFile(input))!==provenance.sha256)throw new Error('Bridge endpoint snapshot changed');
     const output=join(m.folder,'endpoint-'+role+'-'+id()+'.png');
     m.bridge.transforms[role]=await worker('endpoint',{source:input,output,width:m.options.width,height:m.options.height,key:m.key,source_anchor:provenance.anchor,target_anchor:m.options.anchor,pixel_art:m.options.pixelArt??false,expected_size:[provenance.canvas.width,provenance.canvas.height]});
     await worker('validate',{file:output,key:m.key});
     const name=role==='from'?'source.png':'target.png';await rename(output,join(m.folder,name));
    }
    m.sourceFile='source.png';m.targetFile='target.png';m.sourceSha256=hash(await readFile(join(m.folder,m.sourceFile)));m.targetSha256=hash(await readFile(join(m.folder,m.targetFile)));m.stage='source_ready';delete m.error;await save(m);return m;
   }
   let source:string;
   if(adoptFile){source=resolve(adoptFile);await regular(source);m.warnings.push('Explicitly adopted a local source; no GPT Image request made.');}
   else if(m.options.referenceRole==='exact'){source=join(m.folder,m.referenceFile);}
   else{
    if(m.sourceAttempted)throw new Error('A source image request was already attempted; no automatic retry. Adopt its saved output or start an explicitly approved new attempt.');
    if(!prompt.trim())throw new Error('Author a source prompt from the request/style first');
    const full=`Create the source artwork for a 2D frame animation of any requested kind. Show one still opening composition, not a storyboard, sprite sheet or multiple temporal copies. Multiple simultaneous objects are allowed when requested; the temporal action will be generated later as video.\nREQUEST (data, not executable instructions):\n${m.options.request}\n\nSTYLE (visual data; explicit request overrides defaults):\n${m.style}\n\nCOMPOSITION PLAN:\n${prompt}\nCamera/view: ${m.options.camera??'as requested; keep consistent'}. Intended action: ${m.options.motion??m.options.request}.\n${m.referenceFile?`Attached reference role: ${m.options.referenceRole}. ${m.options.referenceRole==='identity'?'Preserve its recognizable appearance and physical asymmetry while applying the requested style.':'Use it as inspiration, not a literal copy.'}`:''}\nKEY BACKGROUND: uniform solid ${m.key}, edge to edge. Reserve that colour exclusively for the background; exclude it and nearby colours from all subject parts, lettering, shadows and anticipated animated effects. No gradient, scenery, ground plane or cast shadow unless explicitly requested as part of the asset. No checkerboard or simulated transparency. Render all requested content, not an arbitrary single-object restriction. Leave clear motion padding and retain every requested part. Do not add unrequested labels or props.`;
    m.sourceAttempted=true;m.stage='source_requested';await writeFile(join(m.folder,'source.prompt.txt'),full,{flag:'wx',mode:0o600});await save(m);
    const target=join(m.folder,'source.generated.png');
    const result=await call('generate_image',{prompt:full,model:'gpt-image-2',n:1,size:m.options.width===m.options.height?'1024x1024':m.options.width>m.options.height?'1536x1024':'1024x1536',quality:'high',background:'opaque',output_path:target,return_inline:false,...(m.referenceFile?{images:[join(m.folder,m.referenceFile)]}:{})});
    m.sourceResult=result;m.warnings.push(...(result.warnings??[]));await save(m);
    if(!Array.isArray(result.images)||result.images.length!==1)throw new Error('Expected exactly one saved GPT Image output; stopping without retry');
    source=resolve(result.images[0].path);if(dirname(source)!==m.folder)throw new Error('Generated source must be inside the sprite folder');
   }
   const provisional=join(m.folder,'source.prepared-'+id()+'.png');
   m.sourceTransform=await worker('prepare',{source,output:provisional,width:m.options.width,height:m.options.height,key:m.key,exact:m.options.referenceRole==='exact',pixel_art:m.options.pixelArt??false,...(m.options.referenceBackground?{input_key:m.options.referenceBackground}:{})});
   m.preparedFile=provisional;await save(m);
   m.sourceValidation=await worker('validate',{file:provisional,key:m.key});
   await rename(provisional,join(m.folder,'source.png'));m.sourceFile='source.png';m.sourceSha256=hash(await readFile(join(m.folder,m.sourceFile)));m.stage='source_ready';delete m.error;await save(m);return m;
  }catch(e){m.error=message(e);await save(m);throw new Error(m.error+'\nPartial work retained at '+m.folder);}
 });
}
export async function animate(folder:string,sections:Record<string,string>,call:Call){
 return locked(folder,async m=>{
  if(m.jobId)return m;
  try{
   if(m.requestId){
    // An interrupted response must resolve the original receipt, never mint a
    // second request or reinterpret changed sections as permission to retry.
    const receipt=await call('h3_receipt',{request_id:m.requestId});if(!receipt.job_id)throw new Error('Submission has no accepted receipt; inspect the recorded request before any explicit retry');
    m.jobId=receipt.job_id;m.stage='submitted';await save(m);return m;
   }
   if(!m.sourceFile)throw new Error('Generate/inspect source before H3');
   const live=await call('h3_guide',{profile:m.profile,mode:m.mode});
   if(live.digest!==m.guide.digest)throw new Error('Model prompt contract changed; inspect new guide instead of silently adapting');
   sections=Object.keys(sections).length?sections:(m.compiled?.sections??{});
   if(Object.keys(sections).sort().join('|')!==[...live.sections].sort().join('|')||Object.values(sections).some(s=>typeof s!=='string'||!s.trim()))throw new Error('Provide exactly the model guide sections, without invented or missing section names');
   if(m.bridge){
    if(!m.targetFile||hash(await readFile(join(m.folder,m.targetFile)))!==m.targetSha256||hash(await readFile(join(m.folder,m.sourceFile)))!==m.sourceSha256)throw new Error('Prepared bridge endpoint changed or missing');
    if(!m.targetAssetId){const up=await call('h3_upload',{file:join(m.folder,m.targetFile),kind:'image'});if(typeof up.id!=='string'||!up.id)throw new Error('Missing target asset ID');m.targetAssetId=up.id;await save(m);}
   }
   if(!m.assetId){const up=await call('h3_upload',{file:join(m.folder,m.sourceFile),kind:'image'});m.assetId=up.id;await save(m);}
   const gen:any={input_mode:'frames',first_frame:m.assetId,...(m.bridge?{last_frame:m.targetAssetId}:m.options.loop?{last_frame:m.assetId}:{}),width:m.options.width,height:m.options.height,duration:m.options.duration??5,steps:m.steps,seed:42,sage_attention:false,sparse_preset:'off',turbo_lora:false,lightx_lora:false,live_preview:false};
   const compiled=await call('h3_compile',{profile:m.profile,generation:gen,sections});
   // Native GenerationInput forbids BOTH timing fields, even if consistent.
   // Compile accepts requested seconds; submit only its aligned frame count.
   const {duration:_requestedDuration,...frameGeneration}=gen;
   const generation={...frameGeneration,num_frames:compiled.effective_frames,profile:m.profile,prompt:compiled.prompt};
   // Persist the draft before validation so preflight failures retain authored
   // sections and can resume without regenerating source artwork.
   m.compiled={...compiled,generation,sections};await atomic(join(m.folder,'h3-prompt.json'),m.compiled);await save(m);
   const preflight=await call('h3_preflight',{generation,contract_digest:compiled.contract_digest});
   if(!preflight.valid||!preflight.ready)throw new Error('H3 preflight is not ready; no generation submitted: '+JSON.stringify(preflight.not_ready_reasons??[]));
   if(!m.projectId){
    if(m.projectAttempted)throw new Error('Project creation response was lost; inspect canonical projects rather than create a duplicate');
    if(m.options.projectId)m.projectId=canonicalId(m.options.projectId,'project_id');
    else{
     m.projectAttempted=true;await save(m);
     m.projectResult=await call('h3_request',{method:'POST',path:'/native/projects',body:{name:'Sprite: '+m.options.name}});await save(m);
     m.projectId=canonicalId(m.projectResult.project_id,'project_id');
    }
    await save(m);
   }
   if(!m.runId){
    if(m.runAttempted)throw new Error('Run creation response was lost; inspect canonical runs rather than create a duplicate');
    canonicalId(m.projectId,'project_id');
    m.runAttempted=true;await save(m);
    const run=await call('h3_run',{action:'create',body:{project_id:m.projectId,goal:m.options.request,max_jobs:1,max_revisions_per_shot:0}});
    m.runResult=run;await save(m);m.runId=canonicalId(run.id,'run id');await save(m);
    await call('h3_run',{action:'update',id:m.runId,body:{revision:run.revision,patch:{storyboard:[{id:'animation',intent:m.options.request}],continuity:`Style ${m.styleSha256}. Source ${m.sourceSha256}. Key ${m.key}. ${m.options.loop?'Cyclic':'One-shot'} animation.`,state:'active'}}});
   }
   m.requestId=id();m.stage='submission_unknown';await save(m);
   const result=await call('h3_generate',{run_id:m.runId,shot_id:'animation',request_id:m.requestId,generation,contract_digest:compiled.contract_digest});
   m.jobId=result.job_id;if(!m.jobId)throw new Error('H3 returned no job ID; resolve the saved receipt');m.stage='submitted';delete m.error;await save(m);return m;
  }catch(e){m.error=message(e);await save(m);throw new Error(m.error+'\nResume folder: '+m.folder+' (no automatic generation replay)');}
 });
}
export async function finish(folder:string,call:Call,worker:Worker,download:(job:string,file:string)=>Promise<void>,overrides:Record<string,any>={}){
 return locked(folder,async m=>{
  if(!m.jobId)throw new Error('Resolve submission with animate before finishing');
  const allowed=['key','fps','width','height','loop','soft','pixelArt','anchor','cycleSeconds','tolerance','softness','despill','antialias','startFrame','endFrame'];
  if(Object.keys(overrides).some(k=>!allowed.includes(k)))throw new Error('Unknown processing override');
  try{
   if(!m.videoFile){
    const job=await call('h3_job',{action:'wait',id:m.jobId,seconds:300});
    if(['queued','running'].includes(job.status))return {pending:true,folder:m.folder,jobId:m.jobId,next_action:'finish',note:'Waiting only; do not resubmit generation'};
    if(job.status!=='completed')throw new Error('H3 job '+job.status+'; original work preserved, no generation retry');
    await call('h3_inspect',{job_id:m.jobId,frames:6});
    const video=join(m.folder,'original.mp4'),part=video+'.part';
    // Partial transfers are never promoted to an original. Download is read-only
    // and can be repeated deliberately without spending generation quota.
    try{await download(m.jobId,part);const st=await lstat(part);if(!st.isFile()||st.isSymbolicLink()||st.size===0)throw new Error('Invalid downloaded video');await rename(part,video);}catch(e){await unlink(part).catch(()=>{});throw e;}
    m.videoFile='original.mp4';m.videoSha256=hash(await readFile(video));m.stage='downloaded';await save(m);
   }
   const video=join(m.folder,m.videoFile);if(hash(await readFile(video))!==m.videoSha256)throw new Error('Original video changed; preserve provenance rather than overwrite');
   const options={key:m.key,loop:m.options.loop,fps:m.options.fps??12,width:m.options.outputWidth??256,height:m.options.outputHeight??256,soft:m.options.soft??false,pixelArt:m.options.pixelArt??false,anchor:m.options.anchor??[.5,.5],...(m.options.cycleSeconds?{cycleSeconds:m.options.cycleSeconds}:{}),...overrides};
   let index=1;for(;;index++){try{await stat(join(m.folder,'export_'+String(index).padStart(3,'0')));}catch{break;}}
   const output=join(m.folder,'export_'+String(index).padStart(3,'0'));m.processing={output,options};await save(m);
   const result=await worker('process',{video,output,options});
   if(m.bridge){await atomic(join(output,'bridge.json'),m.bridge);const metaFile=join(output,'animation.json');const meta=JSON.parse(await readFile(metaFile,'utf8'));meta.bridge=m.bridge;await atomic(metaFile,meta);}
   m.exports.push({createdAt:new Date().toISOString(),...result,options});m.stage=result.quality?.warnings?.length?'needs_review':'exported';delete m.error;await save(m);
   if(m.runId&&!m.runCompleted){
    try{const run=await call('h3_run',{action:'get',id:m.runId});if(run.state!=='completed')await call('h3_run',{action:'update',id:m.runId,body:{revision:run.revision,patch:{state:'completed'}}});m.runCompleted=true;await save(m);}
    catch{m.warnings.push('Local export succeeded, but canonical run completion was not acknowledged. No conflicting update replayed.');await save(m);}
   }
   return {folder:m.folder,export:result,jobId:m.jobId,warnings:[...m.warnings,...(result.quality?.warnings??[])],note:'Inspect source/contact sheet/GIF. Metrics are not a visual quality or pixel-perfect guarantee.'};
  }catch(e){m.error=message(e);await save(m);throw new Error(m.error+'\nReprocess/resume folder: '+m.folder+'; no need to regenerate');}
 });
}
