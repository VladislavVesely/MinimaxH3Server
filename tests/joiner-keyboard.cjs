const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const c=vm.createContext({window:{}});vm.runInContext(fs.readFileSync('src/h3server/web/joiner.js','utf8'),c);
const {timelineShortcut,scrollMetrics}=c.window.H3Joiner;
assert.equal(typeof timelineShortcut,'function','Scoped timeline shortcuts are missing');
assert.equal(timelineShortcut({key:' '}),'play');assert.equal(timelineShortcut({key:'Delete'}),'remove');assert.equal(timelineShortcut({key:'Backspace'}),'remove');
assert.equal(timelineShortcut({key:'Delete',repeat:true}),'ignore');assert.equal(timelineShortcut({key:' ',repeat:true}),'ignore');
assert.equal(timelineShortcut({key:'Delete',metaKey:true}),null);assert.equal(timelineShortcut({key:' ',ctrlKey:true}),null);assert.equal(timelineShortcut({key:'ArrowRight'}),null);
assert.equal(scrollMetrics(1000,400,900).position,600);assert.equal(scrollMetrics(1000,400,-20).position,0);assert.equal(scrollMetrics(1000,400,0).visible,true);assert.equal(scrollMetrics(400,400,0).visible,false);assert.equal(scrollMetrics(200,400,0).maximum,0);
console.log('Timeline shortcuts handle space/delete without repeats/modifiers; overflow scrollbar is bounded and conditional');
