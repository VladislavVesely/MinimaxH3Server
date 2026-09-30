const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');
const start=source.indexOf('  function prepareJobReuse(');assert.ok(start>=0,'Reuse normalization must exist');
const c=vm.createContext({});vm.runInContext(source.slice(start,source.indexOf('  async function reuseJobInputs(',start)),c);
const settings={profile:'model',seed:123,width:512,height:288,num_frames:124,steps:6,sage_attention:true,turbo_lora:true,turbo_strength:.8,turbo_low_vram:true,turbo_artifact_digest:'turbo',input_mode:'references',reference_artifact_digest:'ref',reference_images:['a'.repeat(64)]};
const request={prompt:'Exact prompt\nsecond line',settings,loras:[]};const options={profiles:['model'],turbo:'turbo',sparse:'sparse',reference:'ref',lora:true};
const result=c.prepareJobReuse(request,options);assert.equal(result.prompt,request.prompt);assert.equal(result.settings.seed,123);assert.equal(result.settings.turbo_strength,.8);assert.equal(result.settings.reference_images[0],'a'.repeat(64));assert.equal(result.settings.sparse_preset,'off');
assert.throws(()=>c.prepareJobReuse({...request,settings:null},options),/recorded/);
assert.throws(()=>c.prepareJobReuse({...request,settings:{...settings,seed:2**63}},options),/exact/);
assert.throws(()=>c.prepareJobReuse(request,{...options,profiles:[]}),/model/);
assert.throws(()=>c.prepareJobReuse(request,{...options,reference:'changed'}),/pin/i);
assert.throws(()=>c.prepareJobReuse({...request,loras:['one','two']},options),/LoRA/);
assert.throws(()=>c.prepareJobReuse({...request,settings:{...settings,reference_images:['../file']}},options),/asset/i);
assert.equal(c.prepareJobReuse({prompt:'text',settings:{profile:'model',seed:0,width:512,height:288,num_frames:124,steps:50}},options).settings.input_mode,'text');
assert.throws(()=>c.prepareJobReuse({...request,settings:{...settings,future_sampler:'other'}},options),/cannot reproduce/);
assert.equal(c.prepareJobReuse({...request,settings:{...settings,input_mode:'text',reference_images:[],turbo_lora:false,sparse_preset:'quality',sparse_artifact_digest:'sparse'}},options).settings.sparse_preset,'quality');
(async()=>{
  for(const scenario of ['success','missing','target-change']){
    const nodes=new Map();const fields={reference_images:[],reference_videos:[],reference_audios:[],first_frame:[],last_frame:[]};const reads=[];
    let ctx;
    const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'original',options:[{value:'model'}],scrollIntoView(){},focus(){}});return nodes.get(id);};
    ctx=vm.createContext({$:get,assetEpoch:1,assetDraft:fields,connection:{state:'authenticated'},capabilities:{turbo_artifact_digest:'turbo',sparse_artifact_digest:'sparse'},referenceState:{artifact_digest:'ref'},backendCapabilities:{lora:{status:'supported'}},request:async path=>{reads.push(path);if(scenario==='missing')throw Error('missing');if(scenario==='target-change')ctx.assetEpoch++;return {id:'a'.repeat(64),kind:'image'};},syncDuration(){},syncResolutionPresets(){},showInputAssets(){},render(){},scheduleEstimate(){}});
    vm.runInContext(source.slice(start,source.indexOf('  function jobInputs(',start)),ctx);
    await ctx.reuseJobInputs({job_id:'1'.repeat(32),request});assert.equal(reads.length,1);assert.ok(reads.every(path=>path.startsWith('/remote/assets/')));
    assert.equal(get('prompt').value,scenario==='success'?request.prompt:'original');
    assert.equal(fields.reference_images.length,scenario==='success'?1:0);
    if(scenario==='success'){assert.equal(get('seed').value,'123');assert.equal(get('use-turbo').checked,true);assert.equal(get('steps').value,'6');assert.match(get('reuse-inputs-status').textContent,/Nothing was queued/);}
    else assert.match(get('reuse-inputs-status').textContent,/unchanged/);
  }
  console.log('Reuse preserves exact settings/assets; rejects incompatible inputs; metadata failures and target changes leave the draft unchanged without generation');
})();
