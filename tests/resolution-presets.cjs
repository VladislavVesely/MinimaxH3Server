const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require.resolve('../src/h3server/web/app.js'),'utf8');
const start=source.indexOf('  function resolutionPresets()');
assert.ok(start>=0,'Resolution presets must be defined');
const end=source.indexOf('  function generationBody()',start);
const fields=Object.fromEntries(['width','height','aspect-ratio','resolution-preset'].map(id=>[id,{value:'',children:[],replaceChildren(){this.children=[];},append(o){this.children.push(o);}}]));
const c=vm.createContext({$:id=>fields[id],node:(tag,text)=>({textContent:text})});
vm.runInContext(source.slice(start,end),c);
const presets=c.resolutionPresets();
assert.equal(presets.length,5);
for(const p of presets)for(const [tier,[w,h]] of Object.entries(p.sizes)){
 assert.ok(w%32===0&&h%32===0&&w<=1344&&h<=1344&&w*h<=1344*768,`${p.id}/${tier} outside server limits`);
 fields.width.value=String(w);fields.height.value=String(h);c.syncResolutionPresets();
 assert.equal(fields['aspect-ratio'].value,p.id);assert.equal(fields['resolution-preset'].value,tier);
}
fields.width.value='1344';fields.height.value='768';c.syncResolutionPresets();
assert.equal(fields['aspect-ratio'].value,'landscape');assert.equal(fields['resolution-preset'].value,'large');
c.applyResolutionPreset('portrait','large');assert.equal(fields.width.value,768);assert.equal(fields.height.value,1344);
fields.width.value='640';fields.height.value='480';c.syncResolutionPresets();
assert.equal(fields['aspect-ratio'].value,'custom');assert.equal(fields['resolution-preset'].value,'custom');
assert.equal(fields.width.value,'640');assert.equal(fields.height.value,'480');
c.applyResolutionPreset('square','medium');assert.equal(fields.width.value,768);assert.equal(fields.height.value,768);
console.log('All resolution presets are valid, selections synchronize, and custom dimensions are preserved');
