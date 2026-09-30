const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
assert.ok(fs.existsSync('src/h3server/web/job-selection.js'),'Bulk job selection module missing');
const elements=new Map();function element(){return {disabled:false,checked:false,textContent:'',handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}};}
for(const id of ['jobs-select-all','jobs-clear-selection','jobs-delete-selected','jobs-selection-status'])elements.set(id,element());
let confirmed=true,calls=[],fail=true;
const c=vm.createContext({window:{confirm:()=>confirmed},document:{getElementById:id=>elements.get(id)}});
vm.runInContext(fs.readFileSync('src/h3server/web/job-selection.js','utf8'),c);
const ui=c.window.H3JobSelection.create({run:fn=>fn(),remove:async id=>{calls.push(id);if(id==='b'&&fail){const error=Error('failed');if(fail==='network')error.code='network_unavailable';throw error;}},refresh:async()=>{}});
const a=element(),b=element(),running=element();ui.register('a',a);ui.register('b',b);ui.register('r',running);
ui.update([{job_id:'a',status:'completed'},{job_id:'b',status:'failed'},{job_id:'r',status:'running'}],true);
elements.get('jobs-select-all').handlers.click();assert.equal(a.checked,true);assert.equal(b.checked,true);assert.equal(running.checked,false);assert.equal(running.disabled,true);
(async()=>{confirmed=false;await elements.get('jobs-delete-selected').handlers.click();assert.deepEqual(calls,[]);
confirmed=true;await elements.get('jobs-delete-selected').handlers.click();assert.deepEqual(calls,['a','b']);assert.equal(a.checked,false);assert.equal(b.checked,true);assert.match(elements.get('jobs-selection-status').textContent,/1 deleted.*1 failed/);
fail=false;await elements.get('jobs-delete-selected').handlers.click();assert.deepEqual(calls,['a','b','b']);assert.equal(b.checked,false);
elements.get('jobs-select-all').handlers.click();ui.reset();assert.equal(a.checked,false);assert.equal(b.checked,false);
ui.update([],true);assert.equal(elements.get('jobs-delete-selected').disabled,true);
fail='network';calls=[];ui.update([{job_id:'b',status:'failed'},{job_id:'a',status:'completed'}],true);elements.get('jobs-select-all').handlers.click();await elements.get('jobs-delete-selected').handlers.click();assert.deepEqual(calls,['b']);assert.equal(a.checked,true);assert.equal(b.checked,true);assert.match(elements.get('jobs-selection-status').textContent,/1 not attempted/);
console.log('Bulk selection scopes to visible stopped jobs, confirms once, preserves failures and clears on tab/target changes');})();
