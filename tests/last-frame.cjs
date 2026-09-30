const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');const start=source.indexOf('  async function fetchLastFrame(');assert.ok(start>=0);
const block=source.slice(start,source.indexOf('  function reconcileJobs(',start));
function fixture(){
 const calls=[],blob=new Blob(['unmodified fixture bytes'],{type:'image/png'}),fields={'input-mode':{value:'references'},'frame-assets':{scrollIntoView(){calls.push('scroll');}}};let work;
 const c=vm.createContext({API:'/api',busy:false,assetEpoch:0,inputModes:['text','frames'],Blob,File,
  $:id=>fields[id],node:(tag,text)=>({textContent:text,dataset:{},addEventListener(_,fn){this.click=fn;},click(){calls.push('download');},remove(){}}),
  perform:(_,fn)=>{work=fn();return work;},fetch:async()=>({ok:true,headers:{get:()=> 'image/png'},blob:async()=>blob}),
  uploadInputAssets:async(field,files)=>{calls.push(['upload',field,files[0].name,await files[0].text()]);},
  showInputAssets(){calls.push('assets');},render(){},scheduleEstimate(){calls.push('estimate');},
  URL:{createObjectURL(b){assert.equal(b,blob);return 'blob:fixture';},revokeObjectURL(){}},document:{body:{append(){}}},setTimeout(){}});
 vm.runInContext(block,c);return {c,calls,fields,run:async reuse=>{const b=c.lastFrameButton('a'.repeat(32),reuse);b.click();await work;}};
}
(async()=>{
 let f=fixture();await f.run(false);assert.ok(f.calls.includes('download'));assert.equal(f.fields['input-mode'].value,'references');assert.equal(f.calls.some(Array.isArray),false);
 f=fixture();await f.run(true);assert.equal(f.fields['input-mode'].value,'frames');assert.deepEqual(f.calls.find(Array.isArray),['upload','first_frame','a'.repeat(32)+'-last-frame.png','unmodified fixture bytes']);assert.ok(f.calls.includes('estimate'));assert.equal(f.calls.includes('download'),false);
 f=fixture();const old=f.c.fetch;f.c.fetch=async()=>{f.c.assetEpoch++;return old();};await assert.rejects(f.run(true),e=>e.code==='asset_target_changed');assert.equal(f.calls.length,0);
 f=fixture();f.c.inputModes=['text'];assert.equal(f.c.lastFrameButton('a'.repeat(32),true).disabled,true);await assert.rejects(f.run(true),e=>e.code==='frame_inputs_unsupported');
 f=fixture();f.c.fetch=async()=>({ok:false,json:async()=>({detail:'last_frame_unavailable'})});await assert.rejects(f.run(false),e=>e.code==='last_frame_unavailable');assert.equal(f.calls.length,0);
 console.log('Last-frame download/reuse keeps original PNG bytes, replaces only first-frame slot, checks target/profile and reports errors');
})().catch(e=>{console.error(e);process.exitCode=1;});
