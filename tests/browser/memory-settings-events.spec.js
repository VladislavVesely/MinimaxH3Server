const {test,expect}=require('@playwright/test');
const path=require('node:path');
test('settings stay focused and issue no timer refresh; idle transitions refresh without blocking edits',async({page})=>{
 await page.clock.install();
 await page.setContent('<form id="memory-form"><fieldset id="memory-fields"><input id="memory-auto" type="checkbox"><input id="memory-reserve" type="number"><select id="memory-offload"><option value="automatic">Auto</option><option value="low_vram">Low</option></select><input id="memory-expandable" type="checkbox"><button id="memory-apply">Apply</button></fieldset></form><button id="memory-refresh">Reload</button><button id="memory-defaults">Defaults</button><p id="memory-status"></p>');
 await page.addScriptTag({path:path.resolve(__dirname,'../../src/h3server/web/memory-settings.js')});
 await page.evaluate(()=>{
  window.calls=0;window.next=null;
  window.unit=H3MemorySettings.create({request:()=>{calls++;return next||Promise.resolve({settings:{reserve_vram_gib:4,offload_mode:'automatic',expandable_segments:false},revision:'a'.repeat(64),target_key:'b'.repeat(64),supported:true,busy:false});}});
  unit.context({connected:true,target:'A',busy:false,runtimeBusy:false});unit.visible(true);
 });
 await expect(page.locator('#memory-reserve')).toBeEnabled();await page.locator('#memory-reserve').fill('5');await page.locator('#memory-reserve').focus();
 await page.clock.fastForward(10000);
 expect(await page.evaluate(()=>calls)).toBe(1);await expect(page.locator('#memory-reserve')).toBeFocused();await expect(page.locator('#memory-reserve')).toHaveValue('5');
 await page.evaluate(()=>{next=new Promise(r=>window.finish=r);unit.context({connected:true,target:'A',busy:true,runtimeBusy:true});unit.context({connected:true,target:'A',busy:false,runtimeBusy:false});});
 expect(await page.evaluate(()=>calls)).toBe(2);await expect(page.locator('#memory-reserve')).toBeEnabled();await expect(page.locator('#memory-reserve')).toBeFocused();
 await page.evaluate(()=>finish({settings:{reserve_vram_gib:6,offload_mode:'automatic',expandable_segments:false},revision:'c'.repeat(64),target_key:'b'.repeat(64),supported:true,busy:false}));
 await expect(page.locator('#memory-status')).toContainText('changed');await expect(page.locator('#memory-reserve')).toHaveValue('5');await expect(page.locator('#memory-apply')).toBeDisabled();
});
