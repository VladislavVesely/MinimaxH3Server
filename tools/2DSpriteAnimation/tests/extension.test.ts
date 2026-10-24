import { test } from 'node:test';
import assert from 'node:assert/strict';
import extension from '../index.ts';

test('registration is pure and exposes resumable tools/command without inference',async()=>{
 const tools:any[]=[];const commands:any[]=[];const messages:string[]=[];
 extension({registerTool:(x:any)=>tools.push(x),registerCommand:(n:string,x:any)=>commands.push({name:n,...x}),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 assert.equal(tools.length,1);assert.equal(tools[0].name,'sprite_animation');
 const cmd=commands.find(c=>c.name==='sprite');assert.ok(cmd);
 await cmd.handler('A looping isometric crystal changing into liquid');
 assert.match(messages[0],/crystal/);assert.match(messages[0],/finish/);assert.match(messages[0],/guide/i);
 await cmd.handler('resume /tmp/My Sprite');assert.match(messages[1],/\/tmp\/My Sprite/);assert.match(messages[1],/receipt/i);
});

test('explicit reprocessing JSON is dispatched without preparation instructions',async()=>{
 const commands:any[]=[];const messages:string[]=[];
 extension({registerTool:()=>{},registerCommand:(name:string,c:any)=>commands.push({name,...c}),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 await commands[0].handler('{"action":"finish","folder":"/tmp/Existing"}');
 assert.match(messages[0],/once|only/);assert.doesNotMatch(messages[0],/Use prepare, then source/);
});
