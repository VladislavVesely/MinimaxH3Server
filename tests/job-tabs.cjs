const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const start=source.indexOf('  function observePageJobs(');assert.ok(start>=0,'Current jobs need page-local tracking');
const end=source.indexOf('  function reconcileJobs(',start);
function page(){
 const elements=new Map();const c=vm.createContext({jobsTab:'current',currentJobs:[],jobCards:new Map(),selectedJob:null,showJobTerminal:()=>{},
  currentPageJobs:new Set(),seenPageJobs:new Set(),pageJobsInitialized:false,
  $:id=>{if(!elements.has(id))elements.set(id,{setAttribute(k,v){this[k]=v;}});return elements.get(id);}});
 vm.runInContext(source.slice(start,end),c);
 function update(jobs){c.currentJobs=jobs;c.observePageJobs(jobs);for(const j of jobs){if(!c.jobCards.has(j.job_id))c.jobCards.set(j.job_id,{item:{dataset:{}}});c.jobCards.get(j.job_id).item.dataset.status=j.status;}c.renderJobTabs();}
 return {c,elements,update};
}
const p=page(),{c,elements,update}=p;
update([{job_id:'a',status:'running'},{job_id:'b',status:'queued'},{job_id:'c',status:'completed'}]);
assert.equal(c.jobCards.get('c').item.hidden,true);assert.equal(c.selectedJob,'a');
assert.equal(elements.get('jobs-tab-current').textContent,'Current (2)');
const videoCard=c.jobCards.get('a');
update([{job_id:'a',status:'completed'},{job_id:'b',status:'failed'},{job_id:'c',status:'completed'}]);
assert.equal(c.jobCards.get('a').item.hidden,false,'Finished current job must stay visible');
assert.equal(c.jobCards.get('b').item.hidden,false,'Failures in this view remain current');
assert.equal(c.selectedJob,'a');assert.equal(c.jobCards.get('a'),videoCard,'Keep video/card identity');
c.jobsTab='history';c.renderJobTabs();assert.equal(c.jobCards.get('a').item.hidden,true);assert.equal(c.jobCards.get('c').item.hidden,false);
// A fast job can finish between dashboard polls without ever appearing running.
update([...c.currentJobs,{job_id:'d',status:'completed'}]);assert.equal(c.jobMatchesTab({job_id:'d',status:'completed'},'current'),true);
// Disconnect/reconnect of the same page does not clear membership.
update([]);update([{job_id:'a',status:'completed'}]);assert.equal(c.jobMatchesTab(c.currentJobs[0],'current'),true);
const fresh=page();fresh.update([{job_id:'a',status:'completed'},{job_id:'d',status:'completed'},{job_id:'e',status:'running'}]);
assert.equal(fresh.c.jobCards.get('a').item.hidden,true,'Reload moves finished work to history');
assert.equal(fresh.c.jobCards.get('e').item.hidden,false,'Reload retains unfinished work in Current');
fresh.update([{job_id:'e',status:'cancelled'}]);assert.equal(fresh.c.jobCards.get('e').item.hidden,false);
const empty=page();empty.update([{job_id:'old',status:'interrupted'}]);assert.equal(empty.elements.get('jobs-empty').hidden,false);
assert.match(source,/if \(selectedJob\) currentPageJobs\.add\(selectedJob\)/,'Track accepted jobs before the next poll');
console.log('Current tracks page-view jobs through completion; reload archives finished jobs without changing history');
