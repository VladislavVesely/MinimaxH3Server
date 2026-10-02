/* Server-wide startup policy. Draft edits never start jobs or apply automatically. */
(() => {
  'use strict';
  const defaults={reserve_vram_gib:null,offload_mode:'automatic',expandable_segments:false};
  function create({request,run=fn=>fn(),onApplied=async()=>{}}) {
    const $=id=>document.getElementById(id);
    let connected=false,operating=false,target=null,data=null,dirty=false,stale=false;
    let working=false,loading=false,epoch=0,serial=0,shown=false,timer=null,notice=null;
    const messages={memory_settings_busy:'Wait for queued/running jobs and maintenance to finish, then reload saved settings.',
      memory_settings_changed:'Settings changed on the server. Reload saved settings to review them; your draft has not been applied.',
      memory_settings_target_changed:'The connected target changed. Reload saved settings for the current server.',
      memory_settings_unsupported:'This backend does not support these controls. Use a pruned H3/ComfyUI runtime.',
      memory_settings_unavailable:'Server memory settings are unavailable. Reload saved settings and retry.'};
    function fill(settings) {
      $('memory-auto').checked=settings.reserve_vram_gib===null;
      $('memory-reserve').value=settings.reserve_vram_gib??4;
      $('memory-offload').value=settings.offload_mode;
      $('memory-expandable').checked=settings.expandable_segments;
    }
    function desired() {
      return {reserve_vram_gib:$('memory-auto').checked?null:$('memory-reserve').valueAsNumber,
        offload_mode:$('memory-offload').value,expandable_segments:$('memory-expandable').checked};
    }
    function paint() {
      const usable=connected&&!!data&&data.supported&&!working&&!loading;
      $('memory-fields').disabled=!usable;
      $('memory-reserve').disabled=!usable||$('memory-auto').checked;
      $('memory-reserve').required=!$('memory-auto').checked;
      $('memory-refresh').disabled=!connected||working||loading;
      $('memory-defaults').disabled=!usable;
      $('memory-apply').disabled=!usable||!dirty||stale||operating||data?.busy;
      $('memory-status').textContent=!connected?'Connect a generation server in Install to load its settings.':working?'Applying memory settings…':loading?'Reading saved server settings…':
        stale?'Settings changed or could not be refreshed. Reload saved settings to review; your draft is preserved.':notice||(!data?'Load saved settings from the server.':!data.supported?messages.memory_settings_unsupported:
        data.busy||operating?'Queue or maintenance is busy. You may edit a draft; Apply requires an idle server.':dirty?'Draft only. Apply explicitly to save server-wide settings and release resident models.':'Saved settings loaded. No runtime changes were made.');
    }
    function schedule() {
      if(shown&&connected&&!document.hidden&&!timer)timer=setTimeout(()=>{timer=null;void load({keepDraft:true});},3000);
    }
    async function load({keepDraft=false}={}) {
      if(!connected||working||loading)return;
      const token=epoch,id=++serial;loading=true;paint();
      try {
        const next=await request('/remote/settings/memory');
        if(token!==epoch||id!==serial)return;
        if(!next?.settings||typeof next.revision!=='string'||typeof next.target_key!=='string')throw {code:'invalid_response'};
        if(keepDraft&&dirty&&data) {
          if(next.revision!==data.revision)stale=true;
          data={...data,busy:next.busy,supported:next.supported,loaded:next.loaded};
        } else {data=next;fill(next.settings);dirty=false;stale=false;notice=null;}
      } catch(error) {
        if(token!==epoch||id!==serial)return;
        notice=messages[error.code]||'Unable to load server memory settings. Check the connection and matching server release.';
        if(data)stale=true;
      } finally {if(token===epoch&&id===serial){loading=false;paint();schedule();}}
    }
    function context(value) {
      const key=JSON.stringify(value.target??null);
      if(key!==target||(!value.connected&&connected)) {
        epoch++;serial++;data=null;dirty=false;stale=false;loading=false;working=false;notice=null;target=key;
        clearTimeout(timer);timer=null;fill(defaults);
      }
      connected=!!value.connected;operating=!!value.busy;paint();
      if(shown&&connected&&!data&&!loading&&!working)void load();else schedule();
    }
    function visible(value) {
      const changed=shown!==value;shown=value;
      if(!shown){clearTimeout(timer);timer=null;return;}
      if(changed)void load({keepDraft:true});else schedule();
    }
    function edited() {dirty=!data||Object.entries(desired()).some(([key,value])=>value!==data.settings[key]);notice=null;paint();}
    $('memory-form').addEventListener('input',edited);
    $('memory-form').addEventListener('change',edited);
    $('memory-refresh').addEventListener('click',()=>void load());
    $('memory-defaults').addEventListener('click',()=>{fill(defaults);edited();});
    $('memory-form').addEventListener('submit',event=>{
      event.preventDefault();if($('memory-apply').disabled||!$('memory-form').reportValidity())return;
      const token=epoch,body={settings:desired(),revision:data.revision,target_key:data.target_key};working=true;paint();
      void run(async()=>{
        try {
          const next=await request('/remote/settings/memory',body);
          if(token!==epoch)return;
          data={...next,target_key:body.target_key};fill(next.settings);dirty=false;stale=false;
          notice='Saved. These settings apply when the next GPU worker starts. '+(next.loaded?'Resident runtime unchanged.':'Resident models are unloaded.')+' No job was cancelled.';
          await onApplied();
        } catch(error) {
          if(token!==epoch)return;
          notice=messages[error.code]||'Unable to save memory settings. Reload saved settings and retry.';
          if(['memory_settings_changed','memory_settings_target_changed'].includes(error.code))stale=true;
          if(error.code==='memory_settings_busy'&&data)data.busy=true;
        } finally {if(token===epoch){working=false;paint();schedule();}}
      });
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);timer=null;}else schedule();});
    window.addEventListener('pagehide',()=>{visible(false);epoch++;serial++;loading=false;working=false;});
    fill(defaults);paint();
    return {context,visible,load};
  }
  window.H3MemorySettings={create};
})();
