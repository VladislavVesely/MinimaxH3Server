import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,copyFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {begin,createSource,animate,finish} from '../src/workflow.ts';
import {runWorker} from '../src/worker.ts';

test('real worker through complete orchestration produces a keyed loop and GIF without paid inference',async t=>{
 const root=await mkdtemp(join(tmpdir(),'sprite-integration-'));t.after(()=>rm(root,{recursive:true,force:true}));
 await writeFile(join(root,'2DSpriteAnimation.style.md'),'Clean abstract 2D illustration.\n```sprite-settings\n{"width":128,"height":128,"outputWidth":64,"outputHeight":64,"fps":12}\n```');
 const digest='a'.repeat(64),profile='fixture-profile';let generated=0;let submitted=0;let original='';let source='';
 const call=async(name:string,args:any):Promise<any>=>{
  switch(name){
   case 'h3_discover':return {capabilities:{profiles:[profile],readiness_profile:profile,service_limits:{steps_default:20,spatial_multiple:32,axis_max:1920,canvas_pixels:2088960}},model_prompt_modes:{[profile]:['frames']},generation_schema:{properties:{first_frame:{}}}};
   case 'h3_guide':return {sections:['action','sound'],digest,instructions:'Synthetic transport only; actual pixels use real processing.'};
   case 'generate_image':generated++;await copyFile(source,args.output_path);return {images:[{path:args.output_path}],warnings:[]};
   case 'h3_upload':return {id:'b'.repeat(64)};
   case 'h3_compile':return {prompt:'action: abstract cycle\nsound: silence',effective_frames:124,effective_seconds:124/24,contract_digest:digest};
   case 'h3_preflight':return {valid:true,ready:true};
   case 'h3_request':return {project_id:'c'.repeat(32),name:args.body.name,revision:1};
   case 'h3_run':if(args.action==='create')assert.match(args.body.project_id,/^[a-f0-9]{32}$/);return {id:'d'.repeat(32),revision:1,state:'active'};
   case 'h3_generate':submitted++;return {job_id:'e'.repeat(32),status:'queued'};
   case 'h3_job':return {job_id:'e'.repeat(32),status:'completed'};
   case 'h3_inspect':return {};
   default:throw new Error(name);
  }
 };
 const m=await begin({transparency:'local',name:'AbstractCycle',request:'A rotating abstract shape; loop',loop:true,loopReason:'Explicit loop'},root,call,runWorker);
 execFileSync(process.env.SPRITE_PYTHON||'python3',['-c',`import sys,json,math,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(sys.argv[1]);key=sys.argv[2];folder=root/'fixture';folder.mkdir()
for i in range(60):
 im=Image.new('RGB',(64,64),key);d=ImageDraw.Draw(im);x=24+int(8*math.sin(i*math.pi/6));y=24+int(8*math.cos(i*math.pi/6));d.rectangle((x,y,x+10,y+10),fill='white');im.save(folder/f'{i:04d}.png')
subprocess.run(['ffmpeg','-v','error','-framerate','12','-i',str(folder/'%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(root/'fixture.mp4')],check=True)
`,root,m.key]);
 source=join(root,'fixture','0000.png');original=join(root,'fixture.mp4');
 await createSource(m.folder,'A complete abstract shape with motion margin',call,runWorker);
 await animate(m.folder,{action:'continuous cyclic transformation',sound:'silence'},call);
 const r=await finish(m.folder,call,runWorker,async(_job,file)=>copyFile(original,file));
 assert.equal(generated,1);assert.equal(submitted,1);assert.ok(r.export.frames<60);assert.ok(r.export.frames>1);
 const meta=JSON.parse(await readFile(join(r.export.output,'animation.json'),'utf8'));
 assert.equal(meta.loop,true);assert.equal(meta.canvas.width,64);assert.ok((await readFile(join(r.export.output,'preview.gif'))).subarray(0,3).equals(Buffer.from('GIF')));
 assert.ok((await readFile(join(m.folder,'original.mp4'))).equals(await readFile(original)));
});
