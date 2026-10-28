import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BRIDGE_MOTION_GUIDANCE} from '../src/bridge-prompt.ts';
import extension from '../index.ts';
test('bridge base guidance requires motion context and game-ready continuity rather than pose interpolation',()=>{
 const text=BRIDGE_MOTION_GUIDANCE;
 for(const pattern of [/last.*frames/i,/first.*frames/i,/velocity/i,/contact/i,/no.*hold/i,/not.*morph/i,/duration/i,/in.place/i,/explicit/i])assert.match(text,pattern);
 const tools:any[]=[];extension({registerTool:(t:any)=>tools.push(t),registerCommand:()=>{}} as any);
 assert.ok(tools[0].promptGuidelines.includes(text));
});
