const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const c=vm.createContext({window:{}});vm.runInContext(fs.readFileSync('src/h3server/web/joiner.js','utf8'),c);
assert.equal(typeof c.window.H3Joiner.locateTime,'function','Timeline time-to-clip mapping required');
const items=[{id:'a',duration:2},{id:'b',duration:5}];const locate=c.window.H3Joiner.locateTime;
assert.equal(locate(items,0).id,'a');assert.equal(locate(items,2).id,'b');assert.equal(locate(items,4).offset,2);assert.equal(locate(items,99).offset,5);assert.equal(locate([],1),null);
assert.equal(locate(items,-3).offset,0);
console.log('Timeline boundaries, seek offsets, clamping and empty timeline passed');
