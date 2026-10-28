import {SPRITE_CONFIG} from './defaults.ts';
export type BackgroundReview={verdict:'pass'|'drift'|'unknown';source:string;badFrames:number[];unknownFrames:number[];maxError?:number;reason:string};
export function reviewBackground(metadata:any,key:string,policy=SPRITE_CONFIG.background):BackgroundReview{
 const p=policy;const expected=[1,3,5].map(i=>parseInt(key.slice(i,i+2),16)/255);
 const valid=(c:any):c is number[]=>Array.isArray(c)&&c.length===3&&c.every(n=>typeof n==='number'&&Number.isFinite(n)&&n>=0&&n<=1);
 if(!/^#[a-fA-F0-9]{6}$/.test(key))throw Error('Invalid background-review key');
 const dist=(a:number[],b:number[])=>Math.max(...a.map((v,i)=>Math.abs(v-b[i])));const chroma=(c:number[])=>{const m=Math.max(...c);return m>.05?c.map(v=>v/m):c};
 const error=(c:number[])=>Math.max(dist(c,expected)/p.maxChannelError,dist(chroma(c),chroma(expected))/p.maxChromaError);
 const rows=metadata.analysis?.background_frames;let maxError=0;const badFrames:number[]=[],unknownFrames:number[]=[];
 if(metadata.analysis?.background_review_version===1&&Array.isArray(rows)&&rows.length){
  if(metadata.frames!==undefined&&rows.length!==metadata.frames)return {verdict:'unknown',source:'float-borders',badFrames:[],unknownFrames:[],reason:'Frame diagnostics count mismatch'};
  rows.forEach((edges:any,index:number)=>{if(!Array.isArray(edges)||edges.length!==4||!edges.every(valid)){unknownFrames.push(index);return}
   const med=[0,1,2].map(i=>[...edges.map((e:number[])=>e[i])].sort((a,b)=>a-b).slice(1,3).reduce((a,b)=>a+b)/2);const agreeing=edges.filter((e:number[])=>dist(e,med)<=p.maxEdgeDisagreement).length;
   if(agreeing<p.minimumAgreeingEdges){unknownFrames.push(index);return}const e=error(med);maxError=Math.max(maxError,e);if(e>1)badFrames.push(index);
  });return {verdict:badFrames.length?'drift':unknownFrames.length?'unknown':'pass',source:'vae-float-per-frame-borders',badFrames,unknownFrames,maxError,reason:badFrames.length?'Coherent background colour drift in native float borders':unknownFrames.length?'Border occlusion/nonuniformity needs review':'Native background within configured tolerance'};
 }
 // Backward compatible diagnostics for retained native archives. Absence is NOT
 // evidence of failure and never licenses regeneration. Aggregate drift is only
 // used when the actual custom backdrop measurement exists.
 const color=metadata.analysis?.backdrop?.color;if(valid(color)){const e=error(color);return {verdict:e>1?'drift':'pass',source:'legacy-native-aggregate',badFrames:[],unknownFrames:[],maxError:e,reason:e>1?'Native measured backdrop differs from requested key':'Legacy aggregate agrees; per-frame inspection remains required'}};
 return {verdict:'unknown',source:'unavailable',badFrames:[],unknownFrames:[],reason:'No trustworthy native background measurements; review without automatic inference'};
}
