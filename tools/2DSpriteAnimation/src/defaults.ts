import {readFileSync} from 'node:fs';
export const SPRITE_CONFIG=JSON.parse(readFileSync(new URL('../sprite-defaults.json',import.meta.url),'utf8')) as {version:number;defaults:{quality:string;seed:number;steps:number;sageAttention:boolean;turboLora:boolean;transparency:string;duration:number;fps:number;backgroundRetries:number};presets:{id:string;resolution:number;aliases:string[]}[];limits:{resolutionMin:number;resolutionMax:number;spatialMultiple:number;stepsMin:number;stepsMax:number;maxBackgroundRetries:number;nativeImportPixels:number;archiveBytes:number};background:{wordingVersion?:number;colourNames?:Record<string,{name:string;description:string}>;maxChannelError:number;maxChromaError:number;maxEdgeDisagreement:number;minimumAgreeingEdges:number;retrySeedIncrement:number;wording:string}};
if(SPRITE_CONFIG.version!==1||!SPRITE_CONFIG.presets.length)throw Error('Invalid sprite-defaults.json');
const norm=(s:string)=>s.normalize('NFD').replace(/\p{M}/gu,'').toLowerCase();
function sourceSize(n:number){const l=SPRITE_CONFIG.limits;if(!Number.isInteger(n)||n<l.resolutionMin||n>l.resolutionMax)throw Error('Sprite source resolution must be512–1024');if(n%l.spatialMultiple)throw Error('Sprite source resolution must use multiples of32');return n}
const escape=(s:string)=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
export function parseSpriteText(request:string):Record<string,any>{
 const text=norm(request),out:Record<string,any>={};const dims=[...text.matchAll(/(?:(video|source|zdroj\w*|rozliseni|sprite|sprit\w*|export\w*|vystup\w*)\s*(?:v\s+rozliseni\s*)?)?(\d{2,4})\s*[x×]\s*(\d{2,4})\s*(?:px)?/g)];
 const sources=dims.filter(m=>!m[1]||!/^(sprite|sprit|export|vystup)/.test(m[1])),outputs=dims.filter(m=>m[1]&&/^(sprite|sprit|export|vystup)/.test(m[1]));
 if(new Set(sources.map(m=>m[2]+'x'+m[3])).size>1||new Set(outputs.map(m=>m[2]+'x'+m[3])).size>1)throw Error('Ambiguous source/output resolution; specify one');
 if(sources.length){out.width=sourceSize(+sources[0][2]);out.height=sourceSize(+sources[0][3])}
 if(outputs.length){out.outputWidth=+outputs[0][2];out.outputHeight=+outputs[0][3]}
 if(!sources.length){const nums=[...text.matchAll(/(?:rozliseni|resolution|video|source)\s*(?:v\s+rozliseni\s*)?(\d{3,4})(?!\d|\s*[x×])/g)];if(new Set(nums.map(m=>m[1])).size>1)throw Error('Ambiguous source resolution');if(nums.length)out.resolution=sourceSize(+nums[0][1])}
 const aliases=SPRITE_CONFIG.presets.flatMap(p=>p.aliases.map(a=>({p,a}))).sort((a,b)=>b.a.length-a.a.length);
 for(const {p,a} of aliases){const word=new RegExp('(?<![\\p{L}\\p{N}])'+escape(a)+'(?![\\p{L}\\p{N}])','u');if(word.test(text)&&(/kvalit|quality|detail/.test(text)||['ultra','maximum','draft','nahled','rychly nahled'].includes(a))){out.quality=p.id;break}}
 if(/-\d+\s*(?:kroku|kroky|krok|steps?)\b/.test(text))throw Error('Invalid sprite steps');
 const leading=text.match(/^\s*(\d{3,4})(?:px)?(?:\s|$)/);if(leading&&!sources.length&&!out.resolution)out.resolution=sourceSize(+leading[1]);
 const seed=text.match(/\bseed\s*(?:=|:)?\s*(\d{1,16})\b/);if(seed)out.seed=+seed[1];
 const steps=text.match(/(\d{1,4})\s*(?:kroku|kroky|krok|steps?)\b/);if(steps)out.steps=+steps[1];
 if(/(?:bez|no|without)\s+sage|sage\s*(?:off|vypn)/.test(text))out.sageAttention=false;else if(/\b(?:s|with)\s+sage|sage\s*(?:on|zapn)/.test(text))out.sageAttention=true;
 if(/(?:bez|no|without)\s+turbo|turbo\s*(?:off|vypn)/.test(text))out.turboLora=false;else if(/\b(?:s|with)\s+turbo|turbo\s*(?:on|zapn)/.test(text))out.turboLora=true;
 if(/lokalni\s+klicovani|local\s+key(?:er|ing)|bez\s+satodive/.test(text))out.transparency='local';
 if(/bez\s+opakovani|no\s+retr(?:y|ies)|without\s+retr/.test(text))out.backgroundRetries=0;
 return out;
}
export function resolveSpriteOptions<T extends {request:string}>(explicit:T,style:Record<string,any>={}):T&Record<string,any>{
 const text=parseSpriteText(explicit.request),e:Record<string,any>=Object.fromEntries(Object.entries(explicit).filter(([,v])=>v!==undefined));const o:any={...SPRITE_CONFIG.defaults,...style,...text,...e};
 const chosen=SPRITE_CONFIG.presets.find(p=>p.id===o.quality);if(!chosen)throw Error('Unknown sprite quality preset');
 // A source preset/numeric request outranks style dimensions, never output dimensions.
 const square=e.resolution??(typeof e.quality==='number'?e.quality:undefined)??text.resolution;
 if(square!==undefined)sourceSize(square);
 const explicitQuality=e.quality!==undefined||e.resolution!==undefined,textQuality=text.quality!==undefined||text.resolution!==undefined;
 const size=square??chosen.resolution;o.width=e.width??(explicitQuality?size:text.width??(textQuality?size:style.width??size));o.height=e.height??(explicitQuality?size:text.height??(textQuality?size:style.height??size));
 for(const n of [o.width,o.height])if(!Number.isInteger(n)||n<32||n>1920||n%SPRITE_CONFIG.limits.spatialMultiple)throw Error('Source canvas must use supported multiples of32');
 if(!Number.isSafeInteger(o.seed)||o.seed<0||o.seed>Number.MAX_SAFE_INTEGER-SPRITE_CONFIG.limits.maxBackgroundRetries*SPRITE_CONFIG.background.retrySeedIncrement)throw Error('Invalid sprite seed');
 if(!Number.isInteger(o.steps)||o.steps<SPRITE_CONFIG.limits.stepsMin||o.steps>SPRITE_CONFIG.limits.stepsMax)throw Error('Invalid sprite steps');
 if(!Number.isInteger(o.backgroundRetries)||o.backgroundRetries<0||o.backgroundRetries>SPRITE_CONFIG.limits.maxBackgroundRetries)throw Error('Invalid background retry allowance');
 if(typeof o.sageAttention!=='boolean'||typeof o.turboLora!=='boolean')throw Error('Invalid Sage/Turbo boolean');if(!['direct-vae','local','experimental-server'].includes(o.transparency))throw Error('Invalid transparency mode');
 for(const key of ['outputWidth','outputHeight'])if(o[key]!==undefined&&(!Number.isInteger(o[key])||o[key]<16||o[key]>1024))throw Error('Invalid output size');return o;
}
export function backgroundSections(sections:Record<string,string>,key:string,guide:any,policy=SPRITE_CONFIG.background){
 if(guide.family!=='minimax-h3'||typeof sections.integrated_multimodal_description!=='string')return sections;
 const rgb=[1,3,5].map(i=>parseInt(key.slice(i,i+2),16)).join(',');
 // Exact-key names are configuration data, not a guessed hue or a blue-only rule.
 // Legacy pinned policies have neither names nor placeholders and remain literal.
 const colour=policy.colourNames?.[key.toLowerCase()];
 const words=policy.wording.replaceAll('{key}',key).replaceAll('{rgb}',rgb).replaceAll('{colour}',colour?.name??'reserved key colour').replaceAll('{colourDescription}',colour?.description??'the supplied reserved key colour');const s=sections.integrated_multimodal_description;
 if(s.includes(words))return sections;
 if(!s.startsWith('[Shot 1]'))throw Error('The live H3 guide requires [Shot 1] before background/action description');
 return {...sections,integrated_multimodal_description:s.replace('[Shot 1]','[Shot 1] '+words+'\n\n')};
}
