const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {constructor(tag){this.tag=tag;this.children=[];this.textContent='';}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}}
const nodes={'software-status':new Element('p'),'software-content':new Element('div')};
const c=vm.createContext({window:{},document:{getElementById:id=>nodes[id],createElement:tag=>new Element(tag)}});
vm.runInContext(fs.readFileSync('src/h3server/web/software.js','utf8'),c);
const api=c.window.H3Software;
(async()=>{
 assert.equal(api.core('nvidia-cudnn-cu13'),true);assert.equal(api.core('torch'),true);assert.equal(api.core('nvidia-cuda-runtime'),true);assert.equal(api.core('cuda-toolkit'),true);
 let finish;const pending=api.load(()=>new Promise(r=>finish=r));api.clear();finish({environments:[{id:'old',profile:'old target',packages:[]}]});await pending;assert.equal(nodes['software-content'].children.length,0);
 await api.load(async()=>({observed_at:'now',tools:[{name:'<img onerror=evil>',version:'1'}],environments:[{id:'abcdef123456789',profile:'runtime',python:'3.14.4',python_recorded:'3.14.3',cuda_built:'13.0',packages:[{name:'torch',version:'2.14.0'}]}],system_packages:[],components:[],issues:[]}));
 const text=JSON.stringify(nodes['software-content']);assert.match(text,/All installed Python packages/);assert.match(text,/2.14.0/);assert.match(text,/ownership unknown/);assert.match(text,/<img onerror=evil>/);assert.ok(!text.includes('"tag":"img"'));
 await api.load(async()=>{throw Error('secret internal details');});assert.match(nodes['software-status'].textContent,/no versions assumed/);assert.ok(!nodes['software-status'].textContent.includes('secret'));
 console.log('Software inventory: full versions, text-only rendering, stale-target fencing, bounded refresh and honest unavailable states');
})().catch(e=>{console.error(e);process.exitCode=1;});
