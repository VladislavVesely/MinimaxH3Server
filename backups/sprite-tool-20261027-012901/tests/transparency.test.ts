import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,writeFile,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';
import {serverTransparency} from '../src/transparency.ts';
const sha=(b:any)=>createHash('sha256').update(b).digest('hex');
test('server keying persists receipt before mutation, waits, verifies archive; never generates',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'sprite-server-'));const video=join(folder,'original.mp4');await writeFile(video,'video');const source=sha('video');const archive=Buffer.from('archive');let saved:any;let checkpoint:any;let calls:string[]=[];
 const client={request:async(path:string,opts:any={})=>{calls.push(path);if(path==='/native/assets/video')return {data:{id:source}};if(path==='/native/transparency'){assert.equal(saved.attempted,true);return {data:{state:'queued'}};}if(path.endsWith('/archive'))return {data:archive,contentType:'application/zip'};return {data:{state:'completed',request:{asset_id:source,key:'#0000ff',fps:12},archive_sha256:sha(archive),archive_bytes:archive.length,result:{source_sha256:source,fps:12,key:'#0000ff'}}};}};
 const save=async(s:any)=>{saved=structuredClone(s);checkpoint=s;};
 let r=await serverTransparency(video,source,'#0000ff',12,undefined,save,client);assert.equal(r.pending,true);
 r=await serverTransparency(video,source,'#0000ff',12,checkpoint,save,client);assert.equal(r.pending,false);assert.deepEqual(await readFile(r.archive!),archive);assert.equal(calls.filter(x=>x==='/native/transparency').length,1);assert.ok(!calls.some(x=>x.includes('generate')));
});
test('ambiguous submission is resolved with GET only',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'sprite-server-'));const video=join(folder,'original.mp4');await writeFile(video,'v');const source=sha('v');let saved:any;const calls:string[]=[];
 const client={request:async(path:string)=>{calls.push(path);if(path==='/native/assets/video')return {data:{id:source}};throw new Error('network interrupted');}};
 await assert.rejects(serverTransparency(video,source,'#0000ff',12,undefined,async s=>{saved=structuredClone(s)},client));assert.equal(saved.attempted,true);
 await assert.rejects(serverTransparency(video,source,'#0000ff',12,saved,async()=>{},client));assert.equal(calls.filter(x=>x==='/native/transparency').length,1);
});
