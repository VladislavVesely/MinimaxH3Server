const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../src/h3server/web/app.js'), 'utf8');
const section = source.slice(source.indexOf('  function inputValue('), source.indexOf('  function updateJobTimes('));
function node(tag, text, className) {
  return { tag, text, className, children: [], append(...items) { this.children.push(...items); },
    set innerHTML(value) { throw Error('Inputs must never become HTML'); } };
}
const context = vm.createContext({node});
vm.runInContext(section, context);
const prompt = '<img src=x onerror=alert(1)>\nFull prompt with line breaks';
const request = {prompt,settings:{seed:42,profile:'h3-convrot-int8',width:1344,height:768,num_frames:124,steps:20,sage_attention:true},loras:[],sparse_attention:null};
const result = context.jobInputs(request);
assert.equal(result.tag, 'details');
assert.equal(result.children.find(n=>n.className==='job-prompt').text, prompt);
const fields = result.children.find(n=>n.tag==='dl').children;
for (const [label, value] of [['Seed','42'],['Model','h3-convrot-int8'],['SageAttention','On'],['LoRAs','None']]) {
  const i=fields.findIndex(n=>n.tag==='dt'&&n.text===label);assert.ok(i>=0);assert.equal(fields[i+1].text,value);
}
assert.match(context.inputValue(2**63), /exact browser integer range/);
assert.ok(context.jobInputs({prompt:'old',settings:null}).children.some(n=>n.text?.includes('not recorded')));
assert.equal(request.prompt, prompt);
console.log('Saved-input rendering checks passed');
