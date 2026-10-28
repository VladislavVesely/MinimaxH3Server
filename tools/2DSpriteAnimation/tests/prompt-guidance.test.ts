import {test} from 'node:test';import assert from 'node:assert/strict';import {fileURLToPath} from 'node:url';
import {loadExtensions} from '../node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js';

test('the actual registered sprite tool exposes the controlled colour and action-review lessons',async()=>{
 const file=fileURLToPath(new URL('../index.ts',import.meta.url));const loaded=await loadExtensions([file],process.cwd());assert.deepEqual(loaded.errors,[]);assert.equal(loaded.extensions.length,1);
 const tool=loaded.extensions[0].tools.get('sprite_animation')!.definition;const rules=tool.promptGuidelines!.join('\n');
 assert.match(rules,/EVERY frame/);assert.match(rules,/plain-language colour name/);
 assert.match(rules,/negative_prompt/);assert.match(rules,/inactive/);
 assert.match(rules,/background pass is not action acceptance/);
 assert.match(rules,/unrequested shots/);assert.match(rules,/one controlled/);
 assert.ok(loaded.extensions[0].commands.has('sprite'));
});
