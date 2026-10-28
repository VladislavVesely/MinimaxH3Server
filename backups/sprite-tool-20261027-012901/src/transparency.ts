import {readFile,writeFile,lstat} from 'node:fs/promises';
import {dirname,join} from 'node:path';import {createHash,randomUUID} from 'node:crypto';
// @ts-expect-error Existing private owner-only pinned-TLS transport.
import {Client,readConfig} from './h3-client.mjs';
const hash=(b:Buffer)=>createHash('sha256').update(b).digest('hex');
export async function serverTransparency(video:string,source:string,key:string,fps:number,prior:any,save:(state:any)=>Promise<void>,transport?:any):Promise<{pending:boolean;archive?:string;sha256?:string;requestId:string}>{
 const client=transport??new Client(readConfig());key=key.toLowerCase();
 let state=prior;
 if(!state||state.source!==source||state.key!==key||state.fps!==fps){state={requestId:randomUUID().replaceAll('-',''),source,key,fps,attempted:false};await save(state);}
 if(!/^[a-f0-9]{32}$/.test(state.requestId)||!/^#[a-f0-9]{6}$/.test(key)||!Number.isFinite(fps)||fps<1||fps>60)throw new Error('Invalid experimental transparency checkpoint');
 const path='/native/transparency/'+state.requestId;let record:any;
 if(!state.attempted){
  const st=await lstat(video);if(!st.isFile()||st.isSymbolicLink()||st.size>64*1024*1024)throw new Error('Experimental server transparency needs a regular original video at most 64 MiB');
  const bytes=await readFile(video);if(hash(bytes)!==source)throw new Error('Original video changed');
  // Canonical asset upload is content-addressed and does not submit generation.
  const uploaded=await client.request('/native/assets/video',{method:'POST',body:bytes,timeout:120000});
  if(uploaded.data.id!==source)throw new Error('Server source identity mismatch');
  state.attempted=true;await save(state);
  record=(await client.request('/native/transparency',{method:'POST',body:{request_id:state.requestId,asset_id:source,key,fps}})).data;
 }else record=(await client.request(path)).data;
 if(['queued','running'].includes(record.state))return {pending:true,requestId:state.requestId};
 if(record.state!=='completed')throw new Error('Experimental transparency '+record.state+'; no automatic retry or fallback. Original and exports preserved.');
 if(record.request?.asset_id!==source||record.request?.key!==key||record.request?.fps!==fps||!/^([a-f0-9]{64})$/.test(record.archive_sha256)||record.result?.source_sha256!==source||record.result?.fps!==fps)throw new Error('Server transparency provenance mismatch');
 const archive=join(dirname(video),'transparency-'+state.requestId+'.zip');
 try{const st=await lstat(archive);if(!st.isFile()||st.isSymbolicLink()||st.size>128*1024*1024||hash(await readFile(archive))!==record.archive_sha256)throw new Error('Saved transparency archive changed');}
 catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;
  const r=await client.request(path+'/archive',{binary:true,limit:128*1024*1024,timeout:120000});
  if(r.contentType!=='application/zip'||r.data.length!==record.archive_bytes||hash(r.data)!==record.archive_sha256)throw new Error('Server transparency archive checksum mismatch');
  await writeFile(archive,r.data,{flag:'wx',mode:0o600});
 }
 state.archive=archive;state.archiveSha256=record.archive_sha256;state.result=record.result;await save(state);
 return {pending:false,archive,sha256:record.archive_sha256,requestId:state.requestId};
}
