import { test } from 'node:test';
import assert from 'node:assert/strict';
import extension from '../index.ts';
import { access, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createJiti } from 'jiti';

test('starting Pi in the implementation folder discovers its local loader',async()=>{
 const loader=new URL('../.pi/extensions/2DSpriteAnimation.ts',import.meta.url);
 await access(loader);
 const module=await createJiti(import.meta.url).import(fileURLToPath(loader)) as {default:typeof extension};
 const commands:string[]=[];
 module.default({registerTool:()=>{},registerCommand:(name:string)=>commands.push(name)} as any);
 assert.deepEqual(commands,['sprite']);
});

test('registration is pure and exposes resumable tools/command without inference',async()=>{
 const tools:any[]=[];const commands:any[]=[];const messages:string[]=[];
 extension({registerTool:(x:any)=>tools.push(x),registerCommand:(n:string,x:any)=>commands.push({name:n,...x}),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 assert.equal(tools.length,1);assert.equal(tools[0].name,'sprite_animation');
 const cmd=commands.find(c=>c.name==='sprite');assert.ok(cmd);
 await cmd.handler('A looping isometric crystal changing into liquid');
 assert.match(messages[0],/crystal/);assert.match(messages[0],/finish/);assert.match(messages[0],/guide/i);
 await cmd.handler('resume /tmp/My Sprite');assert.match(messages[1],/\/tmp\/My Sprite/);assert.match(messages[1],/receipt/i);
});

test('status treats an old preflight 422 as history and gives an actionable initial submission step',async t=>{
 const folder=await mkdtemp(join(tmpdir(),'sprite-resume-status-'));t.after(()=>rm(folder,{recursive:true,force:true}));
 await writeFile(join(folder,'manifest.json'),JSON.stringify({version:1,kind:'2d-sprite-animation',folder,stage:'source_ready',sourceFile:'source.png',sourceAttempted:true,error:'H3 422: invalid_generation. No automatic mutation replay.'}));
 const tools:any[]=[];extension({registerTool:(x:any)=>tools.push(x),registerCommand:()=>{}} as any);
 const result=await tools[0].execute('status',{action:'status',folder},undefined,undefined,{cwd:folder,executeTool:async()=>{throw new Error('Status must not call H3');}});
 assert.equal(result.structuredContent.resume.next_action,'animate');assert.equal(result.structuredContent.resume.blocked,false);
 assert.equal(result.structuredContent.resume.generation_submitted,false);assert.equal(result.structuredContent.error,undefined);
 assert.match(result.structuredContent.previous_error,/422/);
});

test('ambiguous submission status requires receipt resolution, not another generation',async t=>{
 const folder=await mkdtemp(join(tmpdir(),'sprite-receipt-status-'));t.after(()=>rm(folder,{recursive:true,force:true}));
 await writeFile(join(folder,'manifest.json'),JSON.stringify({version:1,kind:'2d-sprite-animation',folder,stage:'submission_unknown',sourceFile:'source.png',requestId:'a'.repeat(32),error:'connection lost'}));
 const tools:any[]=[];extension({registerTool:(x:any)=>tools.push(x),registerCommand:()=>{}} as any);
 const result=await tools[0].execute('status',{action:'status',folder},undefined,undefined,{cwd:folder,executeTool:async()=>{throw new Error('No remote status requests here');}});
 assert.equal(result.structuredContent.resume.next_action,'animate');assert.equal(result.structuredContent.resume.mode,'resolve_receipt_only');assert.equal(result.structuredContent.resume.generation_submitted,'unknown');
});

test('natural-language two-folder request routes through bridge without requiring JSON',async()=>{
 const commands:any[]=[];const messages:string[]=[];const tools:any[]=[];
 extension({registerTool:(x:any)=>tools.push(x),registerCommand:(_name:string,c:any)=>commands.push(c),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 await commands[0].handler('Create a bridge from /tmp/Sprite_Walking to /tmp/Sprite_Jumping');
 assert.match(messages[0],/call bridge with fromFolder\/toFolder/);assert.match(messages[0],/without GPT/);assert.match(messages[0],/inspect both/);
 assert.ok(tools[0].parameters.properties.action.anyOf.some((x:any)=>x.const==='bridge'));
 assert.match(tools[0].promptGuidelines.join(' '),/Antialiasing is off by default/);
});

test('one-folder new action is routed through contextual inspection and explicit pose selection',async()=>{
 const commands:any[]=[];const messages:string[]=[];const tools:any[]=[];
 extension({registerTool:(x:any)=>tools.push(x),registerCommand:(_name:string,c:any)=>commands.push(c),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 await commands[0].handler('Use /tmp/Sprite_Existing as reference and animate a new action');
 assert.match(messages[0],/first inspect_reference/);assert.match(messages[0],/not automatically frame zero/);assert.match(messages[0],/reason\/scope/);
 assert.ok(tools[0].parameters.properties.action.anyOf.some((x:any)=>x.const==='extend'));
 assert.match(tools[0].promptGuidelines.join(' '),/what appearance, props, grip/);
});

test('explicit reprocessing JSON is dispatched without preparation instructions',async()=>{
 const commands:any[]=[];const messages:string[]=[];
 extension({registerTool:()=>{},registerCommand:(name:string,c:any)=>commands.push({name,...c}),sendUserMessage:(s:string)=>messages.push(s),sendMessage:()=>{}} as any);
 await commands[0].handler('{"action":"finish","folder":"/tmp/Existing"}');
 assert.match(messages[0],/once|only/);assert.doesNotMatch(messages[0],/Use prepare, then source/);
});
