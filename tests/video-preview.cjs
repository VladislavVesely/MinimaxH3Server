const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');const start=source.indexOf('  function prepareVideoPreview(');assert.ok(start>=0,'Visible video previews must decode a frame without preloading hidden history');
let observer;const c=vm.createContext({videoPreviews:new WeakMap(),previewObserver:null,IntersectionObserver:class{constructor(callback){this.callback=callback;this.watched=new Set();observer=this;}observe(v){this.watched.add(v);}unobserve(v){this.watched.delete(v);}}});
vm.runInContext(source.slice(start,source.indexOf('  async function fetchLastFrame(',start)),c);
function fixture(){const handlers={};return {handlers,preload:'',playsInline:false,src:'',loads:0,paused:false,addEventListener(type,fn){handlers[type]=fn;},load(){this.loads++;},pause(){this.paused=true;},removeAttribute(k){if(k==='src')this.src='';}};}
const v=fixture(),hidden=fixture(),notice={};c.prepareVideoPreview(v,'/video',notice);c.prepareVideoPreview(hidden,'/hidden',{});
assert.equal(v.src,'');assert.equal(v.preload,'none');assert.equal(v.playsInline,true);
observer.callback([{target:v,isIntersecting:false}]);assert.equal(v.loads,0);
observer.callback([{target:v,isIntersecting:true}]);assert.equal(v.src,'/video');assert.equal(v.preload,'auto');assert.equal(v.loads,1);assert.match(notice.textContent,/Loading/);assert.equal(hidden.src,'');
observer.callback([{target:v,isIntersecting:true}]);assert.equal(v.loads,1,'Never reset a playing video on repeated visibility or dashboard updates');
v.handlers.loadeddata();assert.equal(notice.hidden,true);
v.error={code:3};v.handlers.error();assert.equal(notice.hidden,false);assert.match(notice.textContent,/decode|browser/);
c.releaseVideoPreviews({querySelectorAll:()=>[v,hidden]});assert.equal(v.src,'');assert.equal(v.paused,true);assert.equal(observer.watched.size,0);assert.equal(c.videoPreviews.has(v),false);
c.IntersectionObserver=undefined;const fallback=fixture();c.prepareVideoPreview(fallback,'/fallback',{});assert.equal(fallback.src,'/fallback');assert.equal(fallback.loads,1);
console.log('Video previews load only when visible, decode with auto preload, preserve playback and clean up safely');
