const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const c=vm.createContext({window:{}});vm.runInContext(fs.readFileSync('src/h3server/web/joiner.js','utf8'),c);
const join=c.window.H3Joiner.exportJoined,clips=[{id:'a',width:96,height:64,frame_rate:'12/1'},{id:'b'}];
(async()=>{
 let calls=[],prompts=[];
 const request=async(path,body)=>{calls.push(JSON.parse(JSON.stringify(body)));if(!body.convert)throw {code:'joiner_incompatible',fields:['height','level']};return {converted:true};};
 assert.equal(await join(request,clips,m=>{prompts.push(m);return false;},true),null);
 assert.equal(calls.length,1);assert.match(prompts[0],/reduce quality/);assert.match(prompts[0],/96×64/);
 calls=[];const result=await join(request,clips,()=>true,true);assert.equal(result.converted,true);assert.deepEqual(calls,[{clips:['a','b']},{clips:['a','b'],convert:true}]);
 calls=[];await assert.rejects(join(request,clips,()=>{throw Error('must not ask');},false));assert.equal(calls.length,1);
 let asked=false;await join(async()=>({id:'copy'}),clips,()=>{asked=true;},true);assert.equal(asked,false);
 await assert.rejects(join(async()=>{throw {code:'joiner_timeout'};},clips,()=>{asked=true;},true));assert.equal(asked,false);
 console.log('Joiner conversion requires confirmation, preserves approved order, never retries other errors, and keeps compatible exports lossless');
})().catch(e=>{console.error(e);process.exitCode=1;});
