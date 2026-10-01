const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');
const block=source.slice(source.indexOf('  function audioDownloadButton('),source.indexOf('  function lastFrameButton('));
function fixture(){let work;const downloads=[],requests=[],blob=new Blob(['ID3fixture'],{type:'audio/mpeg'});
 const c=vm.createContext({API:'/api',assetEpoch:0,
 node:(tag,text)=>({textContent:text,addEventListener(_,fn){this.click=fn;},click(){downloads.push(this.download);},remove(){}}),
 perform:(_,fn)=>{work=fn();return work;},fetch:async(url,options)=>{requests.push({url,options});return {ok:true,headers:{get:()=> 'audio/mpeg'},blob:async()=>blob};},
 URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},document:{body:{append(){}}},setTimeout(){}});
 vm.runInContext(block,c);const button=c.audioDownloadButton('a'.repeat(32));return {c,button,downloads,requests,run:async()=>{button.click();await work;}};
}
(async()=>{
 let f=fixture();await f.run();assert.equal(f.downloads[0],'a'.repeat(32)+'.mp3');assert.equal(f.button.disabled,false);assert.ok(f.requests.every(r=>!r.options.method));
 f=fixture();f.c.fetch=async()=>({ok:false,json:async()=>({detail:'audio_track_missing'})});await assert.rejects(f.run(),e=>e.code==='audio_track_missing');assert.equal(f.downloads.length,0);assert.equal(f.button.disabled,false);
 f=fixture();const old=f.c.fetch;f.c.fetch=async(...a)=>{f.c.assetEpoch++;return old(...a);};await assert.rejects(f.run(),e=>e.code==='asset_target_changed');assert.equal(f.downloads.length,0);
 f=fixture();f.c.fetch=async()=>({ok:true,headers:{get:()=> 'video/mp4'}});await assert.rejects(f.run(),e=>e.code==='invalid_response');assert.equal(f.downloads.length,0);
 assert.ok(source.includes("node('a', 'Download MP4', 'button-link secondary')"));
 console.log('MP3 download is explicit, bounded and target-fenced; no-audio errors do not download; MP4 shares button styling');
})().catch(e=>{console.error(e);process.exitCode=1;});
