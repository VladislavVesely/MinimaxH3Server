const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/h3server/web/app.js','utf8');
const start=source.indexOf('  function assetDimensions(');assert.ok(start>=0,'Asset dimension cloning is required');
const fields={width:{value:'512',max:'1920',dataset:{canvasPixels:String(1920*1088)}},height:{value:'288'}};
const calls=[];const c=vm.createContext({$:id=>fields[id],syncResolutionPresets:()=>calls.push('presets'),render:()=>calls.push('render'),scheduleEstimate:()=>calls.push('estimate'),busy:false});
vm.runInContext(source.slice(source.indexOf('  function canvasFits('),source.indexOf('  function setResolutionLimits(')),c);
vm.runInContext(source.slice(start,source.indexOf('  function conditioningBody(',start)),c);
const image=(w,h)=>({kind:'image',width:w,height:h});
for(const [w,h,ew,eh] of [[1920,1080,1920,1088],[641,479,640,480],[1080,1920,1088,1920],[48,80,64,96],[1,15,32,32],[512,288,512,288]]){
 const size=c.assetDimensions(image(w,h));assert.equal(size.width,ew);assert.equal(size.height,eh);assert.equal(size.exact,w===ew&&h===eh);
 assert.match(size.message,w===ew&&h===eh?/Exact/:/not an exact match/);
}
assert.equal(c.assetDimensions({kind:'audio',width:0,height:0}),null);
for(const w of [0,-1,NaN,Infinity,1.5])assert.equal(c.assetDimensions(image(w,32)),null);
const oversized=c.assetDimensions(image(2048,2048));assert.equal(oversized.fits,false);assert.match(oversized.message,/exceeds/);
assert.equal(c.applyAssetDimensions(image(2048,2048)),false);assert.equal(fields.width.value,'512');assert.equal(calls.length,0);
assert.equal(c.applyAssetDimensions({kind:'video',width:1920,height:1080}),true);
assert.equal(Number(fields.width.value),1920);assert.equal(Number(fields.height.value),1088);assert.deepEqual(calls,['presets','render','estimate']);
fields.width.max='1344';fields.width.dataset.canvasPixels=String(1344*768);assert.equal(c.assetDimensions(image(1920,1080)).fits,false);
const tooMuchArea=c.assetDimensions(image(1344,1344));assert.equal(tooMuchArea.fits,false);
c.busy=true;assert.equal(c.applyAssetDimensions(image(640,480)),false);
console.log('Asset dimensions: nearest-32 rounding, exact/mismatch notices, image/video support, profile limits and estimate/preset updates passed');
