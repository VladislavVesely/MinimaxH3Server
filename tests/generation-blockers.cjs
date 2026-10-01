const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../src/h3server/web/app.js'), 'utf8');
const start = source.indexOf('  function generationBlockReasons()');
assert.ok(start >= 0, 'Queue blocking reasons must have a shared implementation');
const section = source.slice(start, source.indexOf('  function render()', start));
function reasons(overrides = {}, selections = {}) {
  const fields = {'generation-profile':{value:'h3-convrot-int8'},width:{value:'1344'},height:{value:'768'},'sparse-preset': {value: 'off'}, 'use-turbo': {checked: false}, 'use-sage': {checked: false}, ...selections};
  const state = {modelLibrary:null,modelState:{},ready: true, busy: false, submittingGeneration: false, setup: {busy: false}, connection: {state: 'authenticated'}, runtimeReady: true,
    sparseState: {installed: true, supported: true}, turboState: {installed: true, supported: true}, sageState: {installed: true, supported: true}, ...overrides};
  const context = vm.createContext({characters:null,canvasFits:()=>true, conditioningReasons:()=>[], ...state, $: id => fields[id]});
  vm.runInContext(section, context);
  return Array.from(context.generationBlockReasons());
}
assert.deepEqual(reasons(), []);
assert.match(reasons({modelLibrary:{busy:true}}).join(' '),/model installation/);
assert.match(reasons({}, {'generation-profile':{value:'h3-convrot-mixed'},'sparse-preset':{value:'quality'}}).join(' '),/requires the ConvRot INT8/);
assert.match(reasons({canvasFits:()=>false}).join(' '), /Resolution exceeds/);
assert.match(reasons({}, {'sparse-preset': {value: 'quality'}, 'use-turbo': {checked: true}}).join(' '), /Turbo.*sparse.*cannot be combined.*Turn off Turbo/);
assert.match(reasons({connection: {state: 'disconnected'}}).join(' '), /Connect/);
assert.match(reasons({runtimeReady: false}).join(' '), /readiness/i);
assert.match(reasons({busy: true}).join(' '), /operation/);
assert.deepEqual(reasons({busy: true, submittingGeneration: true}), [], 'Own Queue submission must not display a wait warning');
assert.match(reasons({busy: true, submittingGeneration: true, runtimeReady: false}).join(' '), /readiness/, 'Real blockers remain visible');
assert.match(reasons({setup: {busy: true}}).join(' '), /setup/);
for (const [key, selector, selected, label] of [['sparseState','sparse-preset',{value:'quality'},'Sparse'], ['turboState','use-turbo',{checked:true},'Turbo'], ['sageState','use-sage',{checked:true},'SageAttention']]) {
  assert.match(reasons({[key]: {busy:true, installed:true, supported:true}}).join(' '), /Wait/);
  const absent = reasons({[key]: {installed:false, supported:true}}, {[selector]:selected}).join(' ');
  assert.ok(absent.includes(label) && absent.includes('Install'));
  assert.match(reasons({[key]: {installed:true, supported:false}}, {[selector]:selected}).join(' '), /supported/);
}
assert.match(reasons({ready:false}).join(' '), /session/);
console.log('Generation blocker explanations passed');
