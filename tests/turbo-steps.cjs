const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../src/h3server/web/app.js'), 'utf8');
const start = source.indexOf("  for (const id of ['install-sage', 'use-sage', 'install-turbo', 'use-turbo'])");
const end = source.indexOf("  $('duration').addEventListener", start);
assert.ok(start >= 0 && end > start);
const fields = Object.fromEntries(['install-sage', 'use-sage', 'install-turbo', 'use-turbo', 'steps', 'install-sparse', 'sparse-preset'].map(id => [id, {
  checked: false, value: '20', addEventListener(event, callback) { this.change = callback; }
}]));
vm.runInNewContext(source.slice(start, end), {
  $: id => fields[id], render() {}, savePreferences: () => Promise.resolve(), showError(error) { throw error; }
});
fields['use-turbo'].checked = true;
fields['use-turbo'].change();
assert.equal(Number(fields.steps.value), 6);
fields['use-turbo'].checked = false;
fields['use-turbo'].change();
assert.equal(Number(fields.steps.value), 20, 'Disabling Turbo must restore 20 steps');
for (const steps of [4, 6, 8]) {
  fields.steps.value = String(steps);
  fields['use-turbo'].checked = true;
  fields['use-turbo'].change();
  assert.equal(Number(fields.steps.value), steps);
  fields['use-turbo'].checked = false;
  fields['use-turbo'].change();
  assert.equal(Number(fields.steps.value), 20);
}
fields.steps.value = '30';
fields['use-sage'].change();
assert.equal(fields.steps.value, '30', 'Other preferences must not reset steps');
fields['sparse-preset'].value = 'quality';
fields['sparse-preset'].change();
assert.equal(fields.steps.value, '30', 'Sparse selection must not change steps');
console.log('Turbo toggle step regression checks passed');
