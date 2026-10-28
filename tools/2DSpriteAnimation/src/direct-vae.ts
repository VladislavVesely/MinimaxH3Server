import {readFile,writeFile,lstat} from 'node:fs/promises';
import {join} from 'node:path';import {createHash} from 'node:crypto';
import {SPRITE_CONFIG} from './defaults.ts';
import {validateTransparencyKey} from './transparency.ts';
// @ts-expect-error Existing owner-only pinned TLS transport.
import {Client,readConfig} from './h3-client.mjs';
const hash=(b:Buffer)=>createHash('sha256').update(b).digest('hex');
export function validateDirectVae(discovery:any,profile:string,key:string,width:number,height:number,duration:number){
 validateTransparencyKey(key);
 const cap=discovery.capabilities?.direct_vae_transparency;
 if(cap?.version!==1||cap.processor!=='satodive-direct-vae-v1'||cap.frame_source!=='vae-decoded-float32'||cap.fps!==24||cap.frame_selection!=='all'||!cap.profiles?.includes(profile)||!discovery.generation_schema?.properties?.rgba_key)throw Error('Direct VAE transparency is not available for this profile; no saved-video fallback');
 const grid=discovery.capabilities.frames;if(!grid||![grid.min,grid.max,grid.stride,grid.offset].every(Number.isInteger)||grid.min<1||grid.max<grid.min||grid.stride<1)throw Error('Direct VAE frame grid unavailable');
 const frames=Math.max(grid.min,Math.min(grid.max,Math.ceil((duration*24-grid.offset)/grid.stride)*grid.stride+grid.offset));
 if(!Number.isInteger(frames)||!Number.isFinite(cap.max_native_pixels)||width*height*frames>Math.min(cap.max_native_pixels,SPRITE_CONFIG.limits.nativeImportPixels))throw Error('Direct VAE native pixel budget exceeded before generation; do not lower FPS to evade it');
 return {version:1,processor:cap.processor,frames,fps:24,maxNativePixels:cap.max_native_pixels};
}
export async function directVaeArchive(jobId:string,previewSha:string,key:string,folder:string,transport?:any){
 if(!/^[a-f0-9]{32}$/.test(jobId)||!/^[a-f0-9]{64}$/.test(previewSha))throw Error('Invalid direct VAE identity');validateTransparencyKey(key);
 const client=transport??new Client(readConfig());const job=(await client.request('/native/jobs/'+jobId)).data;
 if(job.status!=='completed'||!job.rgba?.requested||!job.rgba.available)throw Error('Direct VAE job has no completed RGBA; no generation retry or saved-video fallback');
 const settings=job.request?.settings,meta=(await client.request('/native/jobs/'+jobId+'/rgba/metadata')).data;
 if(meta.version!==1||meta.processor!=='satodive-direct-vae-v1'||meta.frame_source!=='vae-decoded-float32'||meta.frame_selection!=='all'||meta.job_id!==jobId||meta.preview_video_sha256!==previewSha||meta.key!==key.toLowerCase()||settings?.rgba_key!==meta.key||meta.frames!==settings.num_frames||meta.width!==settings.width||meta.height!==settings.height||meta.fps!==24||meta.source_fps!==24||meta.source_sha256!==meta.raw_frames_sha256||!/^([a-f0-9]{64})$/.test(meta.raw_frames_sha256)||!/^([a-f0-9]{64})$/.test(meta.archive_sha256)||!Number.isInteger(meta.archive_bytes)||meta.archive_bytes<1||meta.archive_bytes>256*1024**2)throw Error('Direct VAE archive provenance mismatch');
 const archive=join(folder,'direct-vae-'+jobId+'.zip');
 try{const st=await lstat(archive);if(!st.isFile()||st.isSymbolicLink()||st.size!==meta.archive_bytes||hash(await readFile(archive))!==meta.archive_sha256)throw Error('Saved direct VAE archive changed');}
 catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;
  const r=await client.request('/native/jobs/'+jobId+'/rgba',{binary:true,limit:256*1024**2,timeout:180000});if(r.contentType!=='application/zip'||r.data.length!==meta.archive_bytes||hash(r.data)!==meta.archive_sha256)throw Error('Direct VAE archive checksum mismatch');await writeFile(archive,r.data,{flag:'wx',mode:0o600});
 }
 return {archive,sha256:meta.archive_sha256,jobId,metadata:meta};
}
