const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const start=source.indexOf('  function scheduleEstimate()'),end=source.indexOf("  $('input-mode').addEventListener",start);
const elements=new Map(),timers=new Map(),requests=[];let next=0;
const c=vm.createContext({
  AbortController,estimateContext:'initial',estimateAbort:null,estimateTimer:null,estimateEpoch:0,estimateKey:null,performanceAvailable:true,connection:{state:'authenticated'},pageActive:true,
  body:{prompt:'private preview',steps:20},
  $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
  setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),
  request:async(path,body)=>{requests.push({path,body});return {samples:0};},
  estimateText:()=> 'No matching measurements',timeLabel:String
});
vm.runInContext('function generationBody(){return {...body};}\n'+source.slice(start,end),c);
(async()=>{
 c.scheduleEstimate();const first=c.estimateTimer;
 c.scheduleEstimate();assert.equal(c.estimateTimer,first,'dashboard polling must not restart the debounce');
 await timers.get(first)();timers.delete(first);
 assert.equal(requests.length,1);assert.notEqual(requests[0].body.prompt,'private preview');
 for(let i=0;i<5;i++)c.scheduleEstimate();
 assert.equal(timers.size,0,'unchanged settings must not issue periodic lookups');
 assert.equal(elements.get('generation-estimate').textContent,'No matching measurements','background polling must not flash loading text');
 c.body.prompt='another prompt in the same length bucket';c.scheduleEstimate();assert.equal(timers.size,0,'prompt wording alone must retain the measurement');
 c.estimateContext='new successful run';c.scheduleEstimate();assert.equal(timers.size,1,'new measurements must refresh unchanged settings');await timers.get(c.estimateTimer)();timers.delete(c.estimateTimer);requests.length=1;
 c.body.steps=6;c.scheduleEstimate();await timers.get(c.estimateTimer)();timers.delete(c.estimateTimer);
 assert.equal(requests.length,2,'changed input must refresh');
 c.scheduleEstimate();assert.equal(timers.size,0,'input/change events must deduplicate');
 c.estimateKey=null;c.scheduleEstimate();assert.equal(timers.size,1,'explicit reset permits reconnect/unload refresh');
 c.request=(_path,_body,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted'))));
 const pending=timers.get(c.estimateTimer)();timers.delete(c.estimateTimer);
 assert.equal(timers.size,1,'pending lookup has a deadline');timers.values().next().value();await pending;
 assert.match(elements.get('generation-estimate').textContent,/timed out/);assert.equal(timers.size,0);
 console.log('Estimate refresh preserves results, refreshes new measurements and bounds pending lookups');
})().catch(e=>{console.error(e);process.exitCode=1;});
