const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const start=source.indexOf('  function jobMatchesTab(');assert.ok(start>=0,'Queue tabs must filter job states');
const end=source.indexOf('  function reconcileJobs(',start);
const elements=new Map();const c=vm.createContext({jobsTab:'active',currentJobs:[],jobCards:new Map(),selectedJob:null,showJobTerminal:()=>{},
 $:id=>{if(!elements.has(id))elements.set(id,{setAttribute(k,v){this[k]=v;}});return elements.get(id);}});
vm.runInContext(source.slice(start,end),c);
for(const status of ['queued','running'])assert.equal(c.jobMatchesTab({status},'active'),true);
for(const status of ['completed','failed','cancelled','interrupted'])assert.equal(c.jobMatchesTab({status},'history'),true);
c.currentJobs=[{job_id:'a',status:'running'},{job_id:'b',status:'queued'},{job_id:'c',status:'completed'}];
for(const j of c.currentJobs)c.jobCards.set(j.job_id,{item:{dataset:{status:j.status}}});
c.renderJobTabs();assert.equal(c.jobCards.get('c').item.hidden,true);assert.equal(c.selectedJob,'a');
assert.equal(elements.get('jobs-tab-active').textContent,'Active (2)');
c.jobsTab='history';c.renderJobTabs();assert.equal(c.jobCards.get('a').item.hidden,true);assert.equal(c.jobCards.get('c').item.hidden,false);assert.equal(c.selectedJob,'c');
const card=c.jobCards.get('c');c.renderJobTabs();assert.equal(c.jobCards.get('c'),card,'Tab updates must not recreate cards/videos');
c.jobsTab='active';c.currentJobs=c.currentJobs.map(j=>({...j,status:'completed'}));
for(const j of c.currentJobs)c.jobCards.get(j.job_id).item.dataset.status=j.status;
c.renderJobTabs();assert.equal(elements.get('jobs-empty').hidden,false);assert.equal(c.selectedJob,null);
console.log('Active/history tabs classify jobs, retain card identity, and show empty states');
