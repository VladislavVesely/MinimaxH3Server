import { writeFile } from 'node:fs/promises';
// Vendored unchanged from MinimaxH3Server/packages/pi-h3: private owner-only
// config, pinned TLS/hostname verification, no redirects or credential logging.
// @ts-expect-error The existing H3 transport is a JavaScript module.
import { Client, readConfig } from './h3-client.mjs';

export async function downloadVideo(job:string,file:string,signal?:AbortSignal){
 if(!/^[a-f0-9]{32}$/.test(job))throw new Error('Invalid H3 job ID');
 const client=new Client(readConfig());
 const response=await client.request('/native/jobs/'+job+'/video',{binary:true,signal,limit:128*1024*1024,timeout:120000});
 const bytes:Buffer=response.data;
 if(response.contentType!=='video/mp4'||bytes.length<12||bytes.toString('ascii',4,8)!=='ftyp')throw new Error('H3 did not return an original MP4; nothing was accepted as a video');
 await writeFile(file,bytes,{mode:0o600,flag:'wx'});
}
