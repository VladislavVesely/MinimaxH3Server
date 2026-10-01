const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const elements=new Map(),timers=new Map(),requests=[];let next=0,fail=true;
const c=vm.createContext({pageActive:true,ready:true,connection:{state:'authenticated'},jobTimer:null,dashboardEpoch:0,dashboardRequest:0,dashboardFailures:0,runtimeReady:true,
 activeProfileKey:JSON.stringify(['fixture',['A']]),sageState:{},turboState:{},modelLibrary:null,performance:{now:()=>0},
 $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
 setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id),
 request:async(path,body)=>{requests.push({path,body});if(fail)throw {code:'remote_unavailable'};return path==='/remote/jobs'?{jobs:[]}:{backend:'fixture',profiles:['A'],runtime_ready:true,diagnostic:{},service_limits:{}};},
 showError:error=>{const el=c.$('error');el.hidden=false;el.dashboardPoll=error.dashboardPoll===true;},
});
for(const name of ['setResolutionLimits','showModelLibrary','showSage','showTurbo','showSparse','showInputAssets','reconcileJobs','scheduleEstimate','render','jobEvents'])c[name]=()=>{};
vm.runInContext(source.slice(source.indexOf('  async function dashboard()'),source.indexOf("  $('dashboard-refresh').addEventListener")),c);
(async()=>{
 await assert.rejects(c.dashboard());assert.equal(timers.size,1,'a failed read must schedule automatic recovery');assert.equal(c.runtimeReady,false);
 const delays=[];
 for(let i=0;i<6;i++){const t=timers.get(c.jobTimer);delays.push(t.ms);timers.delete(c.jobTimer);await t.fn();assert.equal(timers.size,1);}
 assert.ok(delays[0]>=2000&&delays.at(-1)<=30000);assert.ok(delays.at(-1)>delays[0]);
 fail=false;let t=timers.get(c.jobTimer);timers.delete(c.jobTimer);await t.fn();assert.equal(c.runtimeReady,true);assert.equal(c.$('error').hidden,true);assert.equal(timers.size,1);
 c.$('error').dashboardPoll=false;c.$('error').hidden=false;await c.dashboard();assert.equal(c.$('error').hidden,false,'recovery must preserve unrelated action errors');
 c.jobEvents=async()=>{throw {code:'network_unavailable'};};await assert.rejects(c.dashboard());assert.equal(timers.size,1,'event-read failures must also keep polling alive');c.jobEvents=()=>{};
 t=timers.get(c.jobTimer);timers.delete(c.jobTimer);await t.fn();assert.equal(c.runtimeReady,true);
 fail=true;await assert.rejects(c.dashboard());t=timers.get(c.jobTimer);timers.delete(c.jobTimer);c.dashboardEpoch++;const count=requests.length;await t.fn();assert.equal(requests.length,count,'old-target retries must be inert');
 assert.ok(requests.every(r=>r.body===undefined),'recovery must never resubmit generation or any mutation');
 c.request=async()=>{throw {code:'session_required'};};await assert.rejects(c.dashboard());assert.equal(timers.size,0,'expired authentication is not retried');
 console.log('Dashboard read recovery: bounded backoff, restored readiness, scoped error clearing, target fencing and no mutation retries passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
