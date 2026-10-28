import {test} from 'node:test';
import assert from 'node:assert/strict';
import {backgroundSections,SPRITE_CONFIG} from '../src/defaults.ts';

const guide={family:'minimax-h3'};
const scene={integrated_multimodal_description:'[Shot 1] Camera arcs clockwise45degrees. A faceted crystal rotates while its inner light pulses. Preserve the visible text "A-7".',overall_soundscape:'A quiet chime.',non_diegetic_music:'N/A'};
function paragraph(key:string,policy:any=SPRITE_CONFIG.background){return backgroundSections(scene,key,guide,policy).integrated_multimodal_description.split('\n\n')[0]}

test('blue prompting names the selected colour and fixes it in EVERY intermediate frame',()=>{
 const text=paragraph('#0000ff');
 assert.match(text,/solid BLUE/);assert.match(text,/pure saturated royal blue/);
 assert.match(text,/RGB\(0,0,255\)/);assert.match(text,/#0000ff/);
 assert.match(text,/same.*hue, saturation and brightness/);
 assert.match(text,/EVERY frame/);assert.match(text,/throughout all intermediate movement/);
 assert.match(text,/flat unlit image plane/);assert.match(text,/Foreground lighting acts only on/);
 assert.doesNotMatch(text,/cyan|green|magenta|negative_prompt|guidance_scale|dachshund|pistol/i);
});

test('a selected green or red key never receives hardcoded blue instructions',()=>{
 for(const [key,name,rgb] of [['#00ff00','GREEN','0,255,0'],['#ff0000','RED','255,0,0']]){
  const text=paragraph(key);assert.ok(text.includes('solid '+name));assert.ok(text.includes('RGB('+rgb+')'));assert.ok(text.includes(key));assert.match(text,/EVERY frame/);assert.doesNotMatch(text,/blue|royal/i);
 }
 assert.match(paragraph('#0000FF'),/solid BLUE/);
});

test('arbitrary keys keep exact numeric colour without guessing an English hue',()=>{
 const text=paragraph('#2468ab');assert.match(text,/#2468ab/);assert.match(text,/RGB\(36,104,171\)/);assert.match(text,/EVERY frame/);assert.doesNotMatch(text,/BLUE|GREEN|RED|royal|\{(?:colour|colourDescription|rgb|key)\}/);
});

test('the background paragraph does not override requested camera or foreground animation',()=>{
 const out=backgroundSections(scene,'#0000ff',guide);assert.doesNotMatch(paragraph('#0000ff'),/camera|exposure|framing/i);
 assert.ok(out.integrated_multimodal_description.endsWith(scene.integrated_multimodal_description.slice('[Shot 1]'.length)));
 assert.equal(out.overall_soundscape,scene.overall_soundscape);assert.equal(out.non_diegetic_music,scene.non_diegetic_music);
 assert.deepEqual(backgroundSections(out,'#0000ff',guide),out);assert.deepEqual(scene.integrated_multimodal_description,'[Shot 1] Camera arcs clockwise45degrees. A faceted crystal rotates while its inner light pulses. Preserve the visible text "A-7".');
});

test('legacy pinned wording remains literal and idempotent without a new colour-name policy',()=>{
 const old={wording:'A stationary {key} image plane with RGB({rgb}). Preserve its colour.'};
 const out=backgroundSections(scene,'#0000ff',guide,old as any);
 assert.equal(out.integrated_multimodal_description,'[Shot 1] A stationary #0000ff image plane with RGB(0,0,255). Preserve its colour.\n\n Camera arcs clockwise45degrees. A faceted crystal rotates while its inner light pulses. Preserve the visible text "A-7".');
 assert.deepEqual(backgroundSections(out,'#0000ff',guide,old as any),out);assert.doesNotMatch(out.integrated_multimodal_description,/EVERY|royal/);
});

test('other model-owned sections are not forced into an H3 sprite prompt',()=>{
 const other={description:'The other guide owns this.'};assert.deepEqual(backgroundSections(other,'#0000ff',{family:'other'}),other);
 assert.throws(()=>backgroundSections({...scene,integrated_multimodal_description:'An unlabelled scene'},'#0000ff',guide),/Shot 1/);
});
