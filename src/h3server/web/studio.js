/* Stable workspace layout and explicit desired-state component selection.
 * Editing selections and opening information never starts an operation. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const make=(tag,cls,value)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(value!==undefined)el.textContent=value;return el;};
  const text=(el,value)=>{if(el.textContent!==value)el.textContent=value;};
  const pages={install:['Set up your studio','Connect your machine and choose the components you want to keep.'],generate:['Generation studio','Bring your ideas to life. Your current generations stay right here.'],history:['Your generation history','Revisit results, reuse their inputs or bring a clip into the joiner.'],joiner:['Your next sequence','Arrange whole clips on the timeline. Keep their original encoded quality.']};
  const host=document.querySelector('.setup-panels'),views={};
  for(const key of Object.keys(pages)){const view=make('div','studio-view studio-'+key);view.id='workspace-'+key;view.hidden=true;host.append(view);views[key]=view;}
  views.install.append(document.querySelector('.steps-wrap'));
  const setupTop=make('div','setup-overview');views.install.append(setupTop);setupTop.append($('connection'),$('scan-panel'));views.install.append($('installation'));
  const generationLayout=make('div','generation-layout'),queueHost=make('div','current-host');views.generate.append(generationLayout);generationLayout.append($('generation'),queueHost);views.joiner.append($('joiner'));
  const jobPanel=$('current-queue');queueHost.append(jobPanel);
  const sidebarStatus=make('div','sidebar-connection');sidebarStatus.setAttribute('role','status');sidebarStatus.append(make('span','connection-light'),make('strong',null,'Not connected'),make('small',null,'Choose Local or SSH in Install.'));document.querySelector('.sidebar-note').replaceWith(sidebarStatus);
  const activity=document.querySelector('.activity'),activityDrawer=make('details','activity-drawer');activityDrawer.append(make('summary',null,'Session activity & diagnostics'),activity);document.querySelector('.work-grid').after(activityDrawer);
  const operation=document.querySelector('.operation-status');operation.classList.add('studio-operation');document.querySelector('.work-grid').before(operation);
  function disclosure(first,last,title,id){const details=make('details','studio-disclosure');if(id)details.id=id;details.append(make('summary',null,title));first.before(details);let current=first;while(current){const next=current.nextSibling;details.append(current);if(current===last)break;current=next;}return details;}
  const advanced=disclosure($('lora-field'),$('generate').previousElementSibling,'Acceleration & advanced settings','generation-advanced');
  const residency=disclosure($('models-status'),$('unload-models-reason'),'Model memory & residency');$('generation-form').after(residency);$('generation-fields').querySelector('legend').classList.add('sr-only');
  const custom=disclosure($('width').closest('.form-row'),$('resolution-warning'),'Custom size & frame count','generation-custom');custom.append($('frames').closest('.field'));const lengths=$('duration').closest('.form-row');lengths.after(custom);lengths.after($('duration-note'));custom.after($('resolution-warning'));
  disclosure($('export-timings'),$('export-timings').nextElementSibling,'Export performance measurements');

  // Original setup handlers keep their private controls; there is only one visible selection UI.
  const legacy=make('div');legacy.id='legacy-install-controls';legacy.hidden=true;$('install-note').before(legacy);
  let current=$('install-note');while(current){const next=current.nextSibling;legacy.append(current);if(current.id==='install-disabled-reason')break;current=next;}
  const diagnostics=disclosure($('retry-setup'),$('setup-technical'),'Setup recovery & diagnostics','installation-diagnostics');
  const library=make('section','component-library');library.setAttribute('aria-label','Installed components');legacy.before(library);
  text($('installation-heading'),'Choose what to keep');
  library.append(make('p','field-hint','Checked = keep installed. Check to add, uncheck to remove, then apply all changes together. Videos, history and shared runtime dependencies are preserved.'));
  const cards=make('div','model-cards'),optionalCards=make('div','model-cards');
  library.append(make('h3','component-group-title','Models'),cards,make('h3','component-group-title','Optional components'),optionalCards);
  const changes=make('p','component-changes');changes.id='component-changes';changes.setAttribute('aria-live','polite');library.append(changes);
  const consent=make('p','field-hint','By applying, you approve the selected downloads, accept their pinned terms and confirm license eligibility. Open ⓘ beside each component for its license and restrictions.');library.append(consent);
  const actions=make('div','component-actions'),apply=make('button','primary','Apply changes'),reset=make('button','secondary','Reset selection'),stop=make('button','secondary','Stop changes');
  apply.id='component-apply';apply.disabled=true;reset.id='component-reset';stop.id='component-stop';for(const b of [apply,reset,stop])b.type='button';stop.hidden=true;actions.append(apply,reset,stop);library.append(actions);
  const reason=make('p','field-hint');reason.id='component-reason';reason.setAttribute('role','status');library.append(reason);apply.setAttribute('aria-describedby',reason.id);
  const progress=make('progress');progress.id='component-progress';progress.setAttribute('aria-label','Overall component changes');progress.hidden=true;library.append(progress);
  const status=make('p','field-hint');status.id='component-status';status.setAttribute('role','status');library.append(status);
  const dialog=make('dialog','component-dialog');dialog.id='component-info';dialog.setAttribute('aria-labelledby','component-info-title');
  const dialogTitle=make('h3');dialogTitle.id='component-info-title';const dialogBody=make('div'),dialogClose=make('button','secondary','Close');dialogClose.type='button';dialogClose.addEventListener('click',()=>dialog.close());dialog.append(dialogTitle,dialogBody,dialogClose);document.body.append(dialog);
  const optional={sageattention:{label:'SageAttention 1.0.6',description:'Optional Triton attention acceleration. No Torch upgrade. Requires a pruned model. A package required by the frozen runtime cannot be removed separately.',url:'https://pypi.org/project/sageattention/1.0.6/'},turbo:{label:'Turbo LoRA',description:'Pinned v4-600 EMA LoRA, custom node and time-conditioning grid. Apache-2.0 add-on; base-model terms still apply. Requires ConvRot INT8 or mixed. Not compatible with sparse attention during generation.',url:'https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora'},sparse:{label:'Sparse attention',description:'Pinned native sparse kernels, GPU-verified. No additional model weights. Requires ConvRot INT8. Not verified with Turbo or conditioning during generation. See upstream source and license notices.',url:'https://github.com/Zironic/H3-Optimizations'},reference:{label:'Ref2VA · reference inputs',description:'Separate pinned INT8 checkpoint for reference images, videos and audio. Requires ConvRot INT8. MiniMax H3 Community License and Exhibit A apply.',url:'https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot'}};
  const cardNodes=new Map();let context={},desired=new Set(),baseline=null,dirty=false,target=null,submitting=false,ownOperation=null;
  let currentPage=null,initialViewResolved=false;
  function revealAncestors(el){for(let p=el?.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;}
  function show(key,{tab=true,focus=false,initial=false}={}){
    if(!views[key])return;if(!initial)initialViewResolved=true;const active=document.activeElement,changed=currentPage!==key;currentPage=key;
    for(const [name,view] of Object.entries(views))view.hidden=name!==key;
    if(key==='generate'||key==='history'){const parent=key==='generate'?queueHost:views.history;if(jobPanel.parentElement!==parent){parent.append(jobPanel);if(jobPanel.contains(active))active.focus({preventScroll:true});}const button=$('jobs-tab-'+(key==='generate'?'current':'history'));if(tab&&button.getAttribute('aria-selected')!=='true')button.click();}
    for(const link of document.querySelectorAll('[data-workspace]')){const active=link.dataset.workspace===key;link.classList.toggle('selected',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
    text($('page-title'),pages[key][0]);text($('page-description'),pages[key][1]);const label=key==='generate'?'Generate':key==='joiner'?'Video joiner':key==='history'?'History':'Install';text(document.querySelector('.topbar strong'),label);document.title='H3 Studio · '+label;document.body.dataset.studioView=key;text(jobPanel.querySelector('.queue-heading h3'),key==='history'?'Generation history':'Current generations');
    if(changed&&focus){$('page-title').tabIndex=-1;$('page-title').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});}
  }
  for(const link of document.querySelectorAll('[data-workspace]'))link.addEventListener('click',e=>{e.preventDefault();show(link.dataset.workspace,{focus:true});});
  $('jobs-tab-current').addEventListener('click',()=>show('generate',{tab:false}));$('jobs-tab-history').addEventListener('click',()=>show('history',{tab:false}));
  for(const id of ['jobs-tab-current','jobs-tab-history'])$(id).addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key))show($('jobs-tab-current').getAttribute('aria-selected')==='true'?'generate':'history',{tab:false});});
  document.addEventListener('focusin',e=>{const view=e.target.closest('.studio-view');if(view?.hidden)show(Object.keys(views).find(k=>views[k]===view));if(e.target.tagName!=='SUMMARY')revealAncestors(e.target);});
  document.addEventListener('invalid',e=>{revealAncestors(e.target);if(e.target.closest('#generation'))show('generate');},true);
  document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();show('install',{focus:true});});$('installation').addEventListener('click',e=>{if(e.isTrusted)initialViewResolved=true;},true);
  function label(id){return optional[id]?.label||context.profiles?.find(p=>p.profile_name===id)?.label||id;}
  function info(id){
    text(dialogTitle,label(id));dialogBody.replaceChildren();const profile=context.profiles?.find(p=>p.profile_name===id),entry=context.library?.components?.items.find(p=>p.id===id);
    dialogBody.append(make('p',null,optional[id]?.description||profile?.publisher_vram_guidance||'Pinned model weights. Installation does not guarantee a fit in GPU memory.'));
    if(entry?.reason)dialogBody.append(make('p',null,entry.reason));
    const bytes=entry?.total_bytes||profile?.weight_bytes;if(bytes)dialogBody.append(make('p',null,(bytes/1024**3).toFixed(2)+' GiB · additional runtime, staging and output space may be needed.'));
    for(const detail of profile?.license_disclosures||[])dialogBody.append(make('p',null,typeof detail==='string'?detail:JSON.stringify(detail)));
    if(optional[id]){const a=make('a',null,'Publisher, source and license notices');a.href=optional[id].url;a.target='_blank';a.rel='noopener noreferrer';dialogBody.append(a);}
    dialogBody.append(make('p','field-hint','Artifact pin: '+(entry?.artifact_digest||profile?.artifact_set_digest||'Not yet loaded')));
    if(!optional[id]||id==='reference'||id==='turbo'){dialogBody.append(make('h4',null,'MiniMax H3 Community License · Exhibit A'),make('pre',null,$('license-document').textContent||'License text has not loaded yet. Reconnect before applying changes.'));}
    dialog.showModal();
  }
  function dependency(name){
    const models=[...desired].filter(n=>!optional[n]&&(!context.library?.components?.supported||context.library.components.items.find(i=>i.id===n)?.can_install));
    if(name==='reference'||name==='sparse')return 'h3-convrot-int8';
    if(name==='turbo')return models.find(n=>['h3-convrot-int8','h3-convrot-mixed'].includes(n))||'h3-convrot-int8';
    return models[0]||context.library?.components?.items.find(i=>i.kind==='model'&&i.can_install)?.id;
  }
  function toggle(id,checked){
    dirty=true;if(checked){desired.add(id);if(optional[id]&&!context.library?.components?.items.find(i=>i.id===id)?.installed){const dep=dependency(id);if(dep)desired.add(dep);}}else{
      desired.delete(id);for(const name of Object.keys(optional))if(desired.has(name)&&!desired.has(dependency(name))&&!context.library?.components?.items.find(i=>i.id===name)?.installed)desired.delete(name);
    }
    if(!context.library?.components?.supported){
      if(!optional[id]&&checked)for(const n of [...desired])if(!optional[n]&&n!==id)desired.delete(n);
      const selected=[...desired].find(n=>!optional[n]);
      if(!context.profiles?.find(p=>p.profile_name===selected)?.runtime_id?.startsWith('comfyui-h3-'))desired.delete('sageattention');
      if(!['h3-convrot-int8','h3-convrot-mixed'].includes(selected))desired.delete('turbo');
      if(selected!=='h3-convrot-int8')desired.delete('sparse');
      for(const [name,input] of [['sageattention','install-sage'],['turbo','install-turbo'],['sparse','install-sparse']])$(input).checked=desired.has(name);
      if(selected){$('install-profile').value=selected;$('install-profile').dispatchEvent(new Event('change',{bubbles:true}));}
    }
    paintComponents();
  }
  function hydrate(inventory){desired=new Set(inventory.items.filter(i=>i.installed).map(i=>i.id));baseline=inventory.digest;dirty=false;}
  function paintComponents(){
    const {profiles=[],library:state,connection={},setup={},busy=false}=context,inventory=state?.components,sync=state?.synchronization||{},batch=inventory?.supported===true;
    const connected=connection.state==='authenticated',loading=submitting||busy||setup.busy||sync.busy||state?.busy;
    if(batch&&(!dirty||baseline===null)){hydrate(inventory);if(sync.busy&&Array.isArray(sync.desired))desired=new Set(sync.desired);}
    if(batch&&ownOperation&&ownOperation===sync.operation&&!sync.busy){if(sync.state==='completed')hydrate(inventory);else baseline=inventory.digest;ownOperation=null;}
    const rows=[...profiles.map(p=>({id:p.profile_name,kind:'model'})),...Object.keys(optional).map(id=>({id,kind:'optional'}))],found=new Set();
    for(const row of rows){
      const id=row.id;found.add(id);let card=cardNodes.get(id);
      if(!card){const el=make('article','component-card model-card');el.dataset.component=id;if(row.kind==='model')el.dataset.model=id;const choice=make('label','model-choice'),input=make('input');input.type='checkbox';const copy=make('span','model-copy'),name=make('strong'),status=make('span','model-status'),meta=make('p','model-meta'),about=make('button','component-info','ⓘ');about.type='button';copy.append(name,status);choice.append(input,copy);el.append(choice,about,meta);(row.kind==='model'?cards:optionalCards).append(el);card={el,input,name,status,meta,about};cardNodes.set(id,card);input.addEventListener('change',()=>toggle(id,input.checked));about.addEventListener('click',()=>info(id));}
      const item=inventory?.items.find(i=>i.id===id),profile=profiles.find(p=>p.profile_name===id),installed=item?.installed===true;
      let unsupported='';
      if(batch){
        if(!item)unsupported='Not supported by this runtime.';
        else if(installed&&!item.can_remove)unsupported=item.reason||'Required by the runtime.';
        else if(!installed&&!item.can_install)unsupported=item.reason||'Not supported by this runtime.';
        else if(!installed&&profile?.required_gpu_family&&profile.gpu_eligible!==true)unsupported='Requires positively identified Blackwell hardware.';
        else if(!installed&&profile?.artifact_set_digest&&item.artifact_digest!==profile.artifact_set_digest)unsupported='Controller catalog differs from the daemon. Update the controller before installing.';
        if(optional[id]&&!installed){const dep=dependency(id),entry=inventory.items.find(i=>i.id===dep);if(!dep||!entry?.can_install&&!entry?.installed)unsupported='Requires a compatible model/runtime.';}
      }else{
        const option=[...$('install-profile').options].find(o=>o.value===id);
        if(id==='reference')unsupported='Set up a ConvRot INT8 runtime first.';
        else if(row.kind==='model'&&(!option||option.disabled))unsupported='Scan compatible hardware first.';
        else if(!connected||$('install-profile').disabled)unsupported='Connect and scan hardware first.';
        else if(optional[id]){const selected=[...desired].find(n=>!optional[n]);if(id==='sageattention'&&!profiles.find(p=>p.profile_name===selected)?.runtime_id?.startsWith('comfyui-h3-'))unsupported='Select a pruned model first.';if(['turbo','sparse'].includes(id)&&!(['h3-convrot-int8',...(id==='turbo'?['h3-convrot-mixed']:[])].includes(selected)))unsupported='Select a compatible ConvRot model first.';}
      }
      card.input.checked=desired.has(id);card.input.disabled=!!(loading||unsupported||!connected);card.input.setAttribute('aria-label',label(id));card.about.setAttribute('aria-label','About '+label(id));text(card.name,label(id));
      text(card.status,batch?(installed?(desired.has(id)?'Installed':'Will remove'):desired.has(id)?'Will install':item?.present?'Incomplete · will clean up':'Not installed'):'Not selected'+(desired.has(id)?' · will install':''));
      const size=item?.total_bytes||profile?.weight_bytes;const pending=desired.has(id)&&!installed||!desired.has(id)&&item?.present;
      text(card.meta,unsupported||((size?(size/1024**3).toFixed(2)+' GiB · ':'')+(optional[id]?'Optional component':'Model weights')));card.el.classList.toggle('installed',installed);card.el.classList.toggle('planned',!!pending);
    }
    for(const [id,card]of cardNodes)if(!found.has(id)){card.el.remove();cardNodes.delete(id);}
    const adds=batch?[...desired].filter(n=>!inventory.items.find(i=>i.id===n)?.installed):[...desired],removes=batch?inventory.items.filter(i=>i.present&&!desired.has(i.id)).map(i=>i.id):[];
    text(changes,[adds.length?'Install: '+adds.map(label).join(', '):'',removes.length?'Remove: '+removes.map(label).join(', '):''].filter(Boolean).join(' · ')||(batch?'Your selection matches the installed components.':'Select one base model to set up its runtime. You can manage multiple compatible models afterward.'));
    const stale=batch&&dirty&&baseline!==inventory.digest&&!ownOperation;
    let blocked=!connected?'Connect your target first.':loading?'Changes are in progress.':stale?'Installed components changed. Reset the selection to review the latest state.':context.licenseReady===false?'Loading pinned license information…':!adds.length&&!removes.length?'No pending changes.':!batch&&$('install').disabled?$('install-disabled-reason').textContent:'';
    if(batch&&!blocked){const invalid=adds.find(n=>cardNodes.get(n)?.input.disabled);if(invalid)blocked='Cannot install '+label(invalid)+': '+cardNodes.get(invalid).meta.textContent;}
    apply.disabled=!!blocked;reset.disabled=!!loading;stop.hidden=!sync.busy;stop.disabled=submitting||sync.stop_requested===true;text(reason,blocked||'Ready. Changes run only while the generation queue is idle.');text(apply,submitting?'Submitting…':batch?'Apply changes':'Set up selected components');
    progress.hidden=!(sync.busy||sync.state==='completed'||setup.busy);progress.max=Math.max(1,sync.total_actions||1);
    const fraction=sync.current_total>0?Math.min(1,(sync.current_completed||0)/sync.current_total):0;
    if(setup.busy||sync.busy&&!sync.total_actions)progress.removeAttribute('value');else progress.value=(sync.completed_actions||0)+(sync.busy?fraction:0);
    const action=sync.current;let message=sync.busy?`${sync.completed_actions||0} / ${sync.total_actions||0} changes finished · ${action?(action.action==='install'?'Installing ':'Removing ')+label(action.id):'Preparing…'}`:sync.state==='completed'?`All ${sync.total_actions||0} changes applied.`:sync.error||'';
    if(sync.busy&&sync.current_phase)message+=' · '+sync.current_phase;
    if(sync.busy&&sync.current_total>0)message+=` · ${((sync.current_completed||0)/1024**3).toFixed(2)} / ${(sync.current_total/1024**3).toFixed(2)} GiB`;
    if(setup.busy)message=$('setup-status').textContent;text(status,message);status.classList.toggle('error-box',['failed','interrupted'].includes(sync.state));
    if(setup.busy||setup.state==='failed')diagnostics.open=true;
  }
  apply.addEventListener('click',async()=>{
    if(apply.disabled)return;submitting=true;paintComponents();
    try{if(context.library?.components?.supported){await context.applyComponents?.({desired:[...desired],inventory_digest:baseline});}else $('install').click();}
    finally{submitting=false;paintComponents();}
  });
  reset.addEventListener('click',()=>{if(context.library?.components?.supported)hydrate(context.library.components);else{desired.clear();dirty=false;}paintComponents();});
  stop.addEventListener('click',()=>context.cancelComponents?.(context.library?.synchronization?.operation));
  function update(value={}){
    context=value;const {connection={},setup={},runtimeReady=false}=value,key=JSON.stringify([connection.state,connection.mode,connection.host,connection.port,connection.username]);
    if(target!==key){target=key;desired.clear();dirty=false;baseline=null;ownOperation=null;}
    paintComponents();const connected=connection.state==='authenticated';sidebarStatus.dataset.connected=String(connected);text(sidebarStatus.querySelector('strong'),connected?(connection.mode==='local'?'Connected · Local':'Connected · SSH'):'Not connected');text(sidebarStatus.querySelector('small'),connected?'Your machine. Your models.':'Choose Local or SSH in Install.');
    text(custom.querySelector('summary'),'Custom size & frame count · '+$('width').value+' × '+$('height').value+' · '+$('frames').value+' frames');const options=[];if($('use-sage').checked)options.push('Sage on');if($('use-turbo').checked)options.push('Turbo on');if($('sparse-preset').value!=='off')options.push('Sparse '+$('sparse-preset').value);text(advanced.querySelector('summary'),'Acceleration & advanced'+(options.length?' · '+options.join(' · '):' settings'));
    if(!initialViewResolved&&connected&&runtimeReady===true&&!setup.busy)show('generate');
  }
  window.H3Studio={show,update,syncAccepted(operation){ownOperation=operation;}};show('install',{tab:false,initial:true});
})();
