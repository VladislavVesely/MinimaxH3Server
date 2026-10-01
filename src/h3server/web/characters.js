'use strict';
(() => {
  const types={reference_images:'Picture',reference_videos:'Video',reference_audios:'Audio'};
  function bindings(selected,draft) {
    const result=selected.map((s,i)=>{
      const variant=s.character.variants.find(v=>v.id===s.variant_ids[0]);
      const image=draft.reference_images.findIndex(a=>a.id===variant.image_asset_id);
      const voice=draft.reference_audios.findIndex(a=>a.id===s.character.voice_asset_id);
      return {label:`<Subject ${i+1}>`,character:s.character,variant,picture:image<0?null:`<Picture ${image+1}>`,audio:voice<0?null:`<Audio ${voice+1}>`,asset_id:variant.image_asset_id};
    });
    for(const [field,type] of Object.entries(types)) draft[field].forEach((asset,i)=>result.push({label:`<${type} ${i+1}>`,asset_id:asset.id,asset,kind:asset.kind}));
    return result;
  }
  function header(rows) {
    const subjects=rows.filter(r=>r.character), voices=new Map();
    const definitions=subjects.map(r=>`${r.label} is ${r.character.name}, shown in ${r.picture || '[missing image]'}. ${r.character.description} ${r.variant.description}`.trim());
    for(const r of subjects) if(r.audio) {
      const line=`${r.label}${r.character.voice_description?' ('+r.character.voice_description+')':''}`;
      voices.set(r.audio,[...(voices.get(r.audio)||[]),line]);
    }
    for(const [audio,names] of voices) definitions.push(`${audio} is the voice-timbre and delivery reference for ${names.join(' and ')}; do not copy the source dialogue. Assign each speaker's (Sx) by actual vocal order in the target scene.`);
    return `subject_definitions:\n${definitions.join('\n')}\n\nsummary:\n[reference generation${voices.size?' + audio reference':''}] The target video features ${subjects.map(r=>r.label).join(', ') || '[describe subjects]'}.\n\nretention_analysis:\n${subjects.map(r=>`${r.label} (appears in [Shot 1]): fully_preserved - retain identity and the appearance of the selected ${r.variant.name} variant.`).join('\n')}\n${[...voices.keys()].map(a=>`${a}: reference - follow vocal timbre and delivery without copying the original signal.`).join('\n')}\ndetailed_description:\n`;
  }
  function prefill(text,previousHeader,rows,max=15000) {
    const h=header(rows),marker='detailed_description:\n',at=text.indexOf(marker);
    const tail=!text.trim()?'[Shot 1] Describe the visual style, composition, action and camera movement. Add dialogue as <Subject N> (Sx) says: <d>[Language] Your dialogue.</d>\n\noverall_soundscape:\nDescribe ambience and physical sounds.\n\nnon_diegetic_music:\nN/A':
      previousHeader&&text.startsWith(previousHeader)?text.slice(previousHeader.length):at<0?text:text.slice(at+marker.length);
    const result=h+tail;
    if(result.length>max)throw Error('Prompt limit exceeded. Shorten character descriptions or the scene before prefilling definitions.');
    return result;
  }
  function complete(text,caret,rows) {
    const match=text.slice(0,caret).match(/<([^<>\n]*)$/);
    return match?rows.filter(r=>r.label.toLowerCase().startsWith('<'+match[1].toLowerCase())):[];
  }
  function insert(text,caret,label) {
    const start=text.lastIndexOf('<',caret-1), next=text.slice(0,start)+label+text.slice(caret);
    return {text:next,caret:start+label.length};
  }
  function signature(rows) {return JSON.stringify(rows.map(r=>[r.label,r.asset_id,r.picture,r.audio,r.character?.id,r.character?.revision,r.variant?.id]));}
  function needsReview(text,before,after) {return /<(Subject|Picture|Video|Audio) \d+>/i.test(text)&&signature(before)!==signature(after);}
  function prepareDraft(draft,owned,images,audios) {
    const next={...draft};
    for(const [field,add,max] of [['reference_images',images,9],['reference_audios',audios,3]]) {
      const values=draft[field].filter(a=>!owned.has(a.id));
      for(const asset of add) if(!values.some(a=>a.id===asset.id)) values.push(asset);
      if(values.length>max) throw Error(`Selection exceeds ${max} ${types[field]} references. Remove references first.`);
      next[field]=values;
    }
    return next;
  }
  function create({api,request,upload,getDraft,applyDraft,changed,isConnected,isBusy}) {
    const $=id=>document.getElementById(id), prompt=$('prompt');
    let records=[],selected=[],owned=new Set(),rows=[],previousHeader='',stale=false,epoch=0,loaded=false,loading=false,working=false,choices=[],choice=0,editor=null;
    const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
    const button=(text,fn)=>{const n=el('button',text);n.type='button';n.addEventListener('click',fn);return n;};
    const error=e=>{$('characters-status').textContent=e.message||e.code||'Character operation failed. Try refreshing the library.';};
    const content=id=>api+'/remote/assets/'+id+'/content';
    function media(id,kind='image',name='Character image') {
      const n=el(kind==='image'?'img':kind==='audio'?'audio':'video');n.src=content(id);
      if(kind==='image'){n.alt=name;n.loading='lazy';}else{n.controls=true;n.preload='none';}return n;
    }
    function notify(){sync();changed();}
    async function run(fn) {
      if(working||isBusy())return;
      working=true;render();changed();
      try{await fn();}catch(e){error(e);}finally{working=false;render();changed();}
    }
    async function load(force=false) {
      if(!isConnected()||loading||(loaded&&!force))return;
      const token=epoch;loading=true;
      try {
        const result=await request('/remote/characters');if(token!==epoch)return;
        records=result.characters;loaded=true;render();$('characters-status').textContent=`${records.length} global characters on this server.`;
      }catch(e){if(token===epoch){loaded=true;error({message:'Character library unavailable. Update the server if needed, or click Refresh characters to retry.'});}}
      finally{if(token===epoch){loading=false;render();}}
    }
    function sync() {
      const next=bindings(selected,getDraft());
      if(needsReview(prompt.value,rows,next)) {
        const tail=previousHeader&&prompt.value.startsWith(previousHeader)?prompt.value.slice(previousHeader.length):prompt.value;
        if(!needsReview(tail,rows,next)&&previousHeader&&prompt.value.startsWith(previousHeader)) {
          const h=header(next);
          if((h+tail).length>prompt.maxLength)stale=true;
          else{prompt.value=h+tail;previousHeader=h;}
        }else stale=true;
      }
      rows=next;
      $('character-prompt-status').textContent=stale?'Reference assignments changed. Review the prompt labels against the previews, then confirm below. Your text has not been rewritten.':'';
      $('character-review').hidden=!stale;
      preview();
    }
    function scaffold() {
      if(prompt.value.trim()&&!(previousHeader&&prompt.value.startsWith(previousHeader))&&!window.confirm('Replace reference definitions with the current selection? Scene text from detailed_description onward will be preserved. For a freeform prompt, all your text is preserved.'))return;
      try{prompt.value=prefill(prompt.value,previousHeader,rows,prompt.maxLength);previousHeader=header(rows);notify();}
      catch(e){error(e);}
    }
    async function choose(character,variantId) {
      const token=epoch;
      const selections=selected.filter(s=>s.character.id!==character.id);
      if(variantId)selections.push({character,variant_ids:[variantId]});
      // Preserve subject position when changing a variant.
      const old=selected.findIndex(s=>s.character.id===character.id);
      if(variantId&&old>=0){const added=selections.pop();selections.splice(old,0,added);}
      const images=[],audios=[];
      for(const s of selections) {
        const v=s.character.variants.find(v=>v.id===s.variant_ids[0]);
        images.push({...await request('/remote/assets/'+v.image_asset_id),name:s.character.name+' · '+v.name});
        if(s.character.voice_asset_id) audios.push({...await request('/remote/assets/'+s.character.voice_asset_id),name:s.character.name+' · Voice'});
      }
      if(token!==epoch)throw Error('The server connection changed.');
      const draft=getDraft(),next=prepareDraft(draft,owned,images,audios),nextOwned=new Set();
      for(const field of ['reference_images','reference_audios'])for(const a of next[field]) {
        if(owned.has(a.id)||!draft[field].some(old=>old.id===a.id))nextOwned.add(a.id);
      }
      selected=selections;owned=nextOwned;applyDraft(next,selected.length>0);notify();
      if(!prompt.value.trim()&&selected.length)scaffold();render();
    }
    function render() {
      const root=$('character-cards');root.replaceChildren();
      $('characters-refresh').disabled=!isConnected()||working||isBusy();$('character-new').disabled=!isConnected()||!loaded||working||isBusy();
      for(const c of records) {
        const card=el('article');card.className='character-card';
        card.append(el('strong',c.name),el('p',c.description));
        const active=selected.find(s=>s.character.id===c.id),v=c.variants.find(v=>v.id===active?.variant_ids[0])||c.variants[0];
        card.append(media(v.image_asset_id,'image',c.name+' · '+v.name));
        const select=el('select');select.setAttribute('aria-label','Variant for '+c.name);
        for(const v of c.variants){const o=el('option',v.name);o.value=v.id;select.append(o);}select.value=v.id;
        select.disabled=working||isBusy();select.addEventListener('change',()=>{if(active)void run(()=>choose(c,select.value));else card.querySelector('img').src=content(c.variants.find(v=>v.id===select.value).image_asset_id);});
        const pick=button(active?'Remove '+c.name:'Use '+c.name,()=>run(()=>choose(c,active?null:select.value)));pick.disabled=working||isBusy();
        const edit=button('Edit '+c.name,()=>openEditor(c));edit.disabled=working||isBusy();
        card.append(select,pick,edit);if(c.voice_asset_id)card.append(media(c.voice_asset_id,'audio'));
        root.append(card);
      }
      if(!records.length)root.append(el('p',loaded?'No characters yet. Add a character with its image variants and optional MP3 voice.':'Connect to load server characters.'));
      $('character-selected').textContent=selected.map((s,i)=>`<Subject ${i+1}> · ${s.character.name} · ${s.character.variants.find(v=>v.id===s.variant_ids[0]).name}`).join(' | ');
    }
    function preview() {
      const root=$('character-prompt-preview');root.replaceChildren();
      const regex=/<(Subject|Picture|Video|Audio) \d+>/gi;let at=0,match;
      while((match=regex.exec(prompt.value))) {
        root.append(document.createTextNode(prompt.value.slice(at,match.index)));
        const row=rows.find(r=>r.label.toLowerCase()===match[0].toLowerCase());
        if(row)root.append(button(match[0],()=>inspect(row)));else root.append(document.createTextNode(match[0]));
        at=regex.lastIndex;
      }root.append(document.createTextNode(prompt.value.slice(at)));
    }
    function inspect(row) {
      const d=el('dialog');d.className='character-dialog';d.append(el('h3',row.label));
      if(row.character){d.append(el('p',row.character.name+' · '+row.variant.name),el('p',row.character.description+' '+row.variant.description),media(row.asset_id));if(row.character.voice_asset_id)d.append(el('p',row.character.voice_description),media(row.character.voice_asset_id,'audio'));}
      else d.append(media(row.asset_id,row.kind));
      d.append(button('Close',()=>d.close()));d.addEventListener('close',()=>d.remove());document.body.append(d);d.showModal();
    }
    function input(parent,label,value='',multiline=false) {
      const id='character-field-'+crypto.randomUUID(),l=el('label',label),n=el(multiline?'textarea':'input');l.htmlFor=id;n.id=id;n.value=value;n.maxLength=multiline?500:100;parent.append(l,n);return n;
    }
    function openEditor(record) {
      const token=epoch,d=el('dialog');editor=d;d.className='character-dialog';
      const form=el('form');d.append(form);form.append(el('h3',record?'Edit character':'New character'));
      const name=input(form,'Character name',record?.name);name.required=true;
      const description=input(form,'Identity description',record?.description,true);description.maxLength=1000;
      const voiceDescription=input(form,'Voice description',record?.voice_description,true);
      const voiceLabel=el('label','Voice MP3 (0.2–15 seconds, up to 64 MiB)'),voice=el('input');voice.type='file';voice.accept='audio/mpeg';voiceLabel.append(voice);form.append(voiceLabel);
      let voiceId=record?.voice_asset_id||null;
      const voicePreview=el('div');if(voiceId)voicePreview.append(media(voiceId,'audio'));form.append(voicePreview);
      form.append(button('Remove voice',()=>{voiceId=null;voice.value='';voicePreview.replaceChildren();}));
      const variants=el('div'),items=[];form.append(variants);
      function addVariant(value={}) {
        if(items.length>=16)return;
        const root=el('section');root.className='character-variant-editor';const item={id:value.id||crypto.randomUUID().replaceAll('-',''),image_asset_id:value.image_asset_id||null};
        item.name=input(root,'Variant name',value.name||'Variant '+(items.length+1));item.name.required=true;
        item.description=input(root,'Variant description',value.description,true);
        const fileLabel=el('label','Variant image'),file=el('input');file.type='file';file.accept='image/png,image/jpeg,image/webp';fileLabel.append(file);root.append(fileLabel);item.file=file;
        if(value.image_asset_id)root.append(media(value.image_asset_id,'image',value.name));
        root.append(button('Remove variant',()=>{items.splice(items.indexOf(item),1);root.remove();}));items.push(item);variants.append(root);
      }
      for(const v of record?.variants||[{}])addVariant(v);
      form.append(button('Add variant',()=>addVariant()));
      const status=el('p');status.setAttribute('role','status');form.append(status);
      const save=el('button','Save character');save.type='submit';form.append(save,button('Cancel',()=>d.close()));
      if(record)form.append(button('Delete character',()=>{
        if(!window.confirm('Delete this character from the server library? Existing jobs and media will remain.'))return;
        void saveAction(async()=>{await request('/remote/characters/'+record.id+'/delete',{revision:record.revision});if(token!==epoch)return;await choose(record,null);d.close();await load(true);});
      }));
      async function saveAction(fn) {
        if(working||isBusy())return;working=true;for(const n of form.querySelectorAll('button,input,textarea'))n.disabled=true;render();changed();
        try{await fn();}catch(e){status.textContent=(e.code==='character_revision_conflict'?'This character was edited elsewhere. Cancel and refresh before editing again.':e.message||e.code||'Save failed.');}
        finally{working=false;if(d.isConnected)for(const n of form.querySelectorAll('button,input,textarea'))n.disabled=false;render();changed();}
      }
      form.addEventListener('submit',event=>{
        event.preventDefault();
        void saveAction(async()=>{
          if(!items.length)throw Error('Add at least one image variant.');
          const check=()=>{if(token!==epoch)throw Error('The server changed; no character was saved.');};check();
          const data={name:name.value,description:description.value,voice_description:voiceDescription.value,voice_asset_id:voiceId,project_id:null,variants:[]};
          for(const item of items){let id=item.image_asset_id;if(item.file.files[0])id=(await upload('image',item.file.files[0])).id;check();if(!id)throw Error('Every variant needs an image.');item.image_asset_id=id;data.variants.push({id:item.id,name:item.name.value,description:item.description.value,image_asset_id:id});}
          if(voice.files[0]){const asset=await upload('audio',voice.files[0]);check();if(asset.mime!=='audio/mpeg')throw Error('Voice reference must be MP3.');data.voice_asset_id=asset.id;voiceId=asset.id;}
          const result=await request(record?'/remote/characters/'+record.id+'/update':'/remote/characters',record?{...data,revision:record.revision}:data);check();
          d.close();await load(true);$('characters-status').textContent=`Saved ${result.name} on this server. Selected generation references are snapshots; reselect to use edits.`;
        });
      });
      d.addEventListener('close',()=>{d.remove();if(editor===d)editor=null;});document.body.append(d);d.showModal();name.focus();
    }
    function suggestions() {
      choices=complete(prompt.value,prompt.selectionStart,rows);choice=0;drawChoices();preview();
    }
    function drawChoices() {
      const list=$('character-completions');list.replaceChildren();list.hidden=!choices.length;prompt.setAttribute('aria-expanded',String(!!choices.length));
      choices.forEach((row,i)=>{const n=el('li');n.id='character-option-'+i;n.setAttribute('role','option');n.setAttribute('aria-selected',String(i===choice));
        const b=button(row.label+' · '+(row.character?row.character.name+' / '+row.variant.name:row.asset.name||row.asset.id.slice(0,12)),()=>accept(i));
        if(row.character||row.kind==='image')b.prepend(media(row.asset_id,'image',row.label));n.append(b);list.append(n);});
      if(choices.length)prompt.setAttribute('aria-activedescendant','character-option-'+choice);else prompt.removeAttribute('aria-activedescendant');
    }
    function accept(i) {
      const result=insert(prompt.value,prompt.selectionStart,choices[i].label);if(result.text.length>prompt.maxLength)return;
      prompt.value=result.text;prompt.focus();prompt.setSelectionRange(result.caret,result.caret);choices=[];drawChoices();notify();
    }
    prompt.addEventListener('input',()=>{suggestions();});prompt.addEventListener('click',suggestions);
    prompt.addEventListener('keydown',event=>{
      if(!choices.length)return;
      if(event.key==='Escape'){event.preventDefault();choices=[];drawChoices();}
      else if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();choice=(choice+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length;drawChoices();}
      else if(event.key==='Enter'){event.preventDefault();accept(choice);}
    });
    $('characters-refresh').addEventListener('click',()=>load(true));$('character-new').addEventListener('click',()=>openEditor());
    $('character-scaffold').addEventListener('click',scaffold);
    $('character-review').addEventListener('click',()=>{stale=false;notify();});
    function reset() {stale=stale||needsReview(prompt.value,rows,[]);epoch++;records=[];selected=[];owned.clear();rows=[];loaded=false;loading=false;previousHeader='';choices=[];drawChoices();for(const dialog of document.querySelectorAll('.character-dialog'))dialog.close();render();sync();}
    function detach() {selected=[];owned.clear();rows=bindings([],getDraft());previousHeader='';stale=false;choices=[];drawChoices();render();sync();}
    function reasons() {
      const r=[];if(working)r.push('Wait for the character operation to finish.');if(stale)r.push('Review changed character reference labels and confirm the prompt.');
      if(selected.length&&$('input-mode').value!=='references')r.push('Selected characters require Reference input mode.');
      if(selected.some(s=>{const v=s.character.variants.find(v=>v.id===s.variant_ids[0]);return !getDraft().reference_images.some(a=>a.id===v.image_asset_id)||(s.character.voice_asset_id&&!getDraft().reference_audios.some(a=>a.id===s.character.voice_asset_id));}))r.push('A selected character reference was removed. Remove and reselect the character to restore it.');return r;
    }
    function update() {
      const disabled=!isConnected()||working||isBusy();
      $('characters-refresh').disabled=disabled;
      $('character-new').disabled=disabled||!loaded;
      for(const n of $('character-cards').querySelectorAll('button,select'))n.disabled=disabled;
    }
    render();return {load,reset,detach,sync,reasons,update};
  }
  const exported={bindings,header,prefill,complete,insert,prepareDraft,needsReview,create};
  if(typeof module!=='undefined')module.exports=exported;
  if(typeof window!=='undefined')window.H3Characters=exported;
})();
