const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const elements=new Map(),states=[];
const c=vm.createContext({busy:false,submittingGeneration:false,setup:{busy:false},
 $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},log:()=>{},
 refresh:async()=>{},showError:()=>{},setConnection:()=>{}
});
c.render=()=>states.push([c.busy,c.submittingGeneration]);
const start=source.indexOf('  async function perform('),end=source.indexOf('  function dataText(',start);
vm.runInContext(source.slice(start,end),c);
(async()=>{
 let release,calls=0;const wait=new Promise(r=>release=r);
 const first=c.perform('Submitting',async()=>{calls++;await wait;},{generationSubmission:true});
 assert.deepEqual(states.at(-1),[true,true]);
 await c.perform('Duplicate',async()=>{calls++;},{generationSubmission:true});assert.equal(calls,1);
 release();await first;assert.deepEqual(states.at(-1),[false,false]);
 await c.perform('Rejected',async()=>{throw Error('fixture');},{generationSubmission:true});
 assert.deepEqual(states.at(-1),[false,false],'Failed submissions must clear their state');
 await c.perform('Other operation',async()=>{assert.equal(c.submittingGeneration,false);});
 assert.match(source,/\$\('generate'\)\.disabled = busy \|\| generationReasons\.length > 0/);
 console.log('Queue submission state blocks duplicates and clears on success/failure');
})().catch(e=>{console.error(e);process.exitCode=1;});
