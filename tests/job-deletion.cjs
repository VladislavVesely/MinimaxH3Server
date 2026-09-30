const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');
const start=source.indexOf("        const remove = node('button', 'Delete job'");
assert.ok(start>=0);const block=source.slice(start,source.indexOf('        heading.append',start));
function fixture({accept=true,fail=false,busy=false}={}){
 const button={disabled:false,addEventListener(_,fn){this.click=fn;}},calls=[];let pending;
 const c=vm.createContext({busy,node:()=>button,job:{job_id:'job'},confirm:()=>{calls.push('confirm');return accept;},
  perform:(_,fn)=>{pending=fn();return pending;},request:async(path,body)=>{calls.push([path,body]);if(fail)throw Error('fixture conflict');return {removed_assets:['gone']};},
  assetDraft:{reference_images:[{id:'gone'},{id:'shared'}],first_frame:[{id:'gone'}]},showInputAssets:()=>calls.push('assets'),
  currentPageJobs:new Set(['job']),seenPageJobs:new Set(['job']),selectedJob:'job',estimateKey:'cached',dashboard:async()=>calls.push('dashboard')});
 vm.runInContext(block,c);return {button,c,calls,run:async()=>{button.click();if(pending)await pending;}};
}
(async()=>{
 for(const opts of [{accept:false},{busy:true}]){const f=fixture(opts);await f.run();assert.equal(f.calls.some(Array.isArray),false);assert.equal(f.c.selectedJob,'job');}
 const disabled=fixture();disabled.button.disabled=true;await disabled.run();assert.deepEqual(disabled.calls,[]);
 const success=fixture();await success.run();assert.equal(success.calls.find(Array.isArray)[0],'/remote/jobs/job/delete');
 assert.equal(success.c.assetDraft.reference_images[0].id,'shared');assert.equal(success.c.assetDraft.reference_images.length,1);assert.equal(success.c.assetDraft.first_frame.length,0);
 assert.equal(success.c.currentPageJobs.has('job'),false);assert.equal(success.c.selectedJob,null);assert.equal(success.c.estimateKey,null);assert.ok(success.calls.includes('dashboard'));
 const failed=fixture({fail:true});await assert.rejects(failed.run(),/fixture conflict/);assert.equal(failed.c.assetDraft.reference_images.length,2);assert.equal(failed.c.selectedJob,'job');assert.equal(failed.calls.includes('dashboard'),false);
 assert.match(source,/card\.remove\.disabled = busy \|\| \['queued', 'running'\]\.includes\(job\.status\)/);
 console.log('Delete confirmation, cancel-first guard, successful draft cleanup and failed-delete preservation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
