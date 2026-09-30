/* Presentation only. Stable controls, forms, media and original handlers are retained.
 * This module makes no API requests and never clicks Install/Generate/Delete. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const make = (tag, className, text) => {
    const el = document.createElement(tag); if (className) el.className = className;
    if (text !== undefined) el.textContent = text; return el;
  };
  const text = (el, value) => { if (el.textContent !== value) el.textContent = value; };
  const pages = {
    install: ['Set up your studio', 'Connect your machine, discover its hardware and build your model library.'],
    generate: ['Generation studio', 'Bring your ideas to life. Your current generations stay right here.'],
    history: ['Your generation history', 'Revisit results, reuse their inputs or bring a clip into the joiner.'],
    joiner: ['Your next sequence', 'Arrange whole clips on the timeline. Keep their original encoded quality.']
  };
  const host = document.querySelector('.setup-panels');
  const views = {};
  for (const key of Object.keys(pages)) {
    const view = make('div', 'studio-view studio-' + key);view.id='workspace-'+key;view.hidden=true;
    host.append(view);views[key]=view;
  }
  const steps = document.querySelector('.steps-wrap');
  views.install.append(steps);
  const setupTop = make('div','setup-overview');views.install.append(setupTop);
  setupTop.append($('connection'),$('scan-panel'));views.install.append($('installation'));
  const generationLayout=make('div','generation-layout'),queueHost=make('div','current-host');
  views.generate.append(generationLayout);generationLayout.append($('generation'),queueHost);
  views.joiner.append($('joiner'));
  const jobPanel=$('current-queue');queueHost.append(jobPanel);
  const sidebarStatus=make('div','sidebar-connection');sidebarStatus.setAttribute('role','status');
  sidebarStatus.append(make('span','connection-light'),make('strong',null,'Not connected'),make('small',null,'Choose Local or SSH in Install.'));
  document.querySelector('.sidebar-note').replaceWith(sidebarStatus);
  const activity=document.querySelector('.activity'),activityDrawer=make('details','activity-drawer');
  activityDrawer.append(make('summary',null,'Session activity & diagnostics'),activity);
  document.querySelector('.work-grid').after(activityDrawer);
  const operation=document.querySelector('.operation-status');operation.classList.add('studio-operation');
  document.querySelector('.work-grid').before(operation);

  // Collapse explanatory details without discarding a single input or disclosure.
  function disclosure(first,last,title,id) {
    const details=make('details','studio-disclosure');if(id)details.id=id;
    details.append(make('summary',null,title));first.before(details);
    let current=first;
    while(current){const next=current.nextSibling;details.append(current);if(current===last)break;current=next;}
    return details;
  }
  const advanced=disclosure($('lora-field'),$('generate').previousElementSibling,'Acceleration & advanced settings','generation-advanced');
  const residency=disclosure($('models-status'),$('unload-models-reason'),'Model memory & residency');
  $('generation-form').after(residency);
  $('generation-fields').querySelector('legend').classList.add('sr-only');
  const custom=disclosure($('width').closest('.form-row'),$('resolution-warning'),'Custom size & frame count','generation-custom');
  custom.append($('frames').closest('.field'));
  const lengths=$('duration').closest('.form-row');lengths.after(custom);lengths.after($('duration-note'));
  custom.after($('resolution-warning')); // Higher-memory and invalid-canvas warnings stay visible.
  disclosure($('export-timings'),$('export-timings').nextElementSibling,'Export performance measurements');
  const selectedDetails=disclosure($('selected-profile'),$('selected-profile'),'Selected model · download & memory details');
  selectedDetails.open=false;
  const optional=disclosure($('install-sage').closest('label'),$('sparse-install-output'),'Optional accelerators · SageAttention, Turbo & sparse');
  optional.id='installation-extras';
  // A clear review surface: choices are never an implicit batch or uninstall request.
  const library=make('section','model-library');library.setAttribute('aria-label','Model variants');
  library.append(make('p','field-hint','Check variants to plan your library. Installed variants stay checked. Review and install one variant at a time below; checking boxes never downloads or uninstalls anything.'));
  const cards=make('div','model-cards');library.append(cards);$('install-profile').previousElementSibling.before(library);
  $('install-profile').previousElementSibling.textContent='Variant to review and install now';
  const reviewNote=make('p','review-note');$('install').before(reviewNote);
  const planned=new Set(),cardNodes=new Map();let currentPage=null,initialViewResolved=false;

  function revealAncestors(el){ for(let p=el?.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true; }
  function show(key,{tab=true,focus=false,initial=false}={}) {
    if(!views[key])return;
    if(!initial)initialViewResolved=true;
    const active=document.activeElement;
    const changed=currentPage!==key;currentPage=key;
    for(const [name,view] of Object.entries(views))view.hidden=name!==key;
    if(key==='generate'||key==='history') {
      const parent=key==='generate'?queueHost:views.history;
      if(jobPanel.parentElement!==parent){
        parent.append(jobPanel);
        if(jobPanel.contains(active))active.focus({preventScroll:true});
      }
      const button=$('jobs-tab-'+(key==='generate'?'current':'history'));
      if(tab&&button.getAttribute('aria-selected')!=='true')button.click();
    }
    for(const link of document.querySelectorAll('[data-workspace]')){
      const active=link.dataset.workspace===key;link.classList.toggle('selected',active);
      if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
    text($('page-title'),pages[key][0]);text($('page-description'),pages[key][1]);
    text(document.querySelector('.topbar strong'),key==='generate'?'Generate':key==='joiner'?'Video joiner':key==='history'?'History':'Install');
    document.title='H3 Studio · '+(key==='generate'?'Generate':key==='joiner'?'Video joiner':key==='history'?'History':'Install');
    document.body.dataset.studioView=key;
    text(jobPanel.querySelector('.queue-heading h3'),key==='history'?'Generation history':'Current generations');
    if(changed&&focus){$('page-title').tabIndex=-1;$('page-title').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});}
  }
  for(const link of document.querySelectorAll('[data-workspace]'))link.addEventListener('click',e=>{e.preventDefault();show(link.dataset.workspace,{focus:true});});
  $('jobs-tab-current').addEventListener('click',()=>show('generate',{tab:false}));
  $('jobs-tab-history').addEventListener('click',()=>show('history',{tab:false}));
  // Existing keyboard tab selection is owned by app.js; observe its resulting state.
  for(const id of ['jobs-tab-current','jobs-tab-history'])$(id).addEventListener('keydown',e=>{
    if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key))show($('jobs-tab-current').getAttribute('aria-selected')==='true'?'generate':'history',{tab:false});
  });
  document.addEventListener('focusin',e=>{
    const view=e.target.closest('.studio-view');if(view?.hidden)show(Object.keys(views).find(k=>views[k]===view));
    if(e.target.tagName!=='SUMMARY')revealAncestors(e.target);
  });
  // Native form validation must never leave a required invalid control in a closed disclosure.
  document.addEventListener('invalid',e=>{revealAncestors(e.target);if(e.target.closest('#generation'))show('generate');},true);
  document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();show('install',{focus:true});});
  $('installation').addEventListener('click',e=>{if(e.isTrusted)initialViewResolved=true;},true);

  function update({profiles=[],library:state,connection={},setup={},runtimeReady=false}={}) {
    const select=$('install-profile');const found=new Set();
    for(const profile of profiles){
      const id=profile.profile_name;found.add(id);
      let card=cardNodes.get(id);
      if(!card){
        const el=make('article','model-card');el.dataset.model=id;
        const label=make('label','model-choice'),input=make('input');input.type='checkbox';input.setAttribute('aria-label',profile.label);
        const copy=make('span','model-copy'),name=make('strong'),status=make('span','model-status'),meta=make('p','model-meta');
        copy.append(name,status);label.append(input,copy);const review=make('button','secondary','Review');review.type='button';
        el.append(label,meta,review);cards.append(el);card={el,input,name,status,meta,review};cardNodes.set(id,card);
        input.addEventListener('change',()=>{if(input.checked)planned.add(id);else planned.delete(id);el.classList.toggle('planned',input.checked);});
        review.addEventListener('click',()=>{
          if(select.disabled||[...select.options].find(o=>o.value===id)?.disabled)return;
          select.value=id;select.dispatchEvent(new Event('change',{bubbles:true}));
          text(reviewNote,'Reviewing '+profile.label+'. Install applies only to this variant and the optional components you enable.');
          select.focus({preventScroll:true});select.scrollIntoView({block:'center',behavior:'smooth'});
        });
      }
      const entry=state?.profiles?.find(p=>p.profile===id),installed=entry?.installed===true;
      const installing=state?.busy&&state.profile===id;
      const ineligible=[...select.options].find(o=>o.value===id)?.disabled===true;
      if(installed)planned.delete(id);
      card.input.checked=installed||planned.has(id);card.input.disabled=installed||select.disabled||ineligible;
      card.review.disabled=select.disabled||ineligible;
      text(card.name,profile.label);
      text(card.status,(installing?'Installing…':installed?'Installed':entry?'Not installed':'Installation status unknown')+(entry?.compatible===false?' · Requires different runtime':'')+(ineligible?' · GPU eligibility not confirmed':''));
      text(card.meta,(profile.weight_bytes? (profile.weight_bytes/1024**3).toFixed(1)+' GiB model weights · ':'')+(profile.publisher_vram_guidance||'Experimental profile · review exact requirements below.'));
      card.el.classList.toggle('installed',installed);card.el.classList.toggle('planned',card.input.checked);
      card.el.classList.toggle('reviewing',select.value===id);
    }
    for(const [id,card] of cardNodes)if(!found.has(id)){card.el.remove();cardNodes.delete(id);planned.delete(id);}
    library.hidden=!profiles.length;
    const selected=profiles.find(p=>p.profile_name===select.value);
    text(reviewNote,selected?'Reviewing '+selected.label+'. Install applies only to this variant and the optional components you enable.':'Scan hardware to review model variants.');
    const connected=connection.state==='authenticated';sidebarStatus.dataset.connected=String(connected);
    text(sidebarStatus.querySelector('strong'),connected?(connection.mode==='local'?'Connected · Local':'Connected · SSH'):'Not connected');
    text(sidebarStatus.querySelector('small'),connected?'Your machine. Your models.': 'Choose Local or SSH in Install.');
    text(custom.querySelector('summary'),'Custom size & frame count · '+$('width').value+' × '+$('height').value+' · '+$('frames').value+' frames');
    const options=[];if($('use-sage').checked)options.push('Sage on');if($('use-turbo').checked)options.push('Turbo on');if($('sparse-preset').value!=='off')options.push('Sparse '+$('sparse-preset').value);
    text(advanced.querySelector('summary'),'Acceleration & advanced'+(options.length?' · '+options.join(' · '):' settings'));
    text(optional.querySelector('summary'),'Optional accelerators'+(['install-sage','install-turbo','install-sparse'].some(id=>$(id).checked)?' · selected':' · SageAttention, Turbo & sparse'));
    if(!initialViewResolved && connected && runtimeReady === true && !setup.busy)show('generate');
  }
  window.H3Studio={show,update};show('install',{tab:false,initial:true});
})();
