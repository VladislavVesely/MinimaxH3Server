import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
import numpy as np
from PIL import Image, ImageDraw

MODULE = Path(__file__).resolve().parents[1] / 'processor.py'

class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.exists(), 'The sprite processor has not been implemented')
        spec = importlib.util.spec_from_file_location('sprite_processor', MODULE)
        self.p = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.p)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_recorded_mid_stride_green_cyan_border_is_clean_not_just_first_loop_pose(self):
        fixture=Path(__file__).parent/'fixtures/crouch-bleed'
        palette=['#241B29','#713C26','#BB753F','#EDBC78','#AD3045','#344E79','#BCC5D2','#238F9B','#FFE39B']
        for name,key_expected,count in [('walk-mid',[23,251,125],205),('walk-cyan',[32,249,201],114),('walk-last',[40,248,245],22)]:
            with self.subTest(phase=name):
                a=np.asarray(Image.open(fixture/f'{name}-native.png').convert('RGB'))
                base=Image.open(fixture/f'{name}-matte.png').convert('RGBA');old=np.asarray(base)
                alpha=np.asarray(base.getchannel('A').resize((a.shape[1],a.shape[0]),Image.Resampling.NEAREST))
                key=self.p.border_key(a);np.testing.assert_array_equal(key,key_expected)
                clean=np.asarray(Image.fromarray(self.p.despill_matte(a,key,alpha,palette)).resize(base.size,Image.Resampling.NEAREST))
                before=old[:,:,1].astype(int)-np.maximum(old[:,:,0],old[:,:,2]).astype(int)
                after=clean[:,:,1].astype(int)-np.maximum(clean[:,:,0],clean[:,:,2]).astype(int)
                witnesses=(old[:,:,3]>0)&(before>10);self.assertEqual(int(witnesses.sum()),count)
                self.assertLessEqual(int(after[witnesses].max()),8,'Every cycle phase must lose its unrequested green border')
                np.testing.assert_array_equal(clean[:,:,3],old[:,:,3])

    def test_nearest_paint_mode_does_not_invent_green_by_averaging_yellow_and_cyan(self):
        c=np.zeros((3,3,3),dtype=np.float32);known=np.zeros((3,3),dtype=bool)
        c[1,0]=[1,.8,0];c[1,2]=[0,.8,1];known[1,0]=known[1,2]=True
        propagated,found=self.p.nearby_foreground(c,known,1,blend=False)
        self.assertTrue(found[1,1]);self.assertTrue(any(np.array_equal(propagated[1,1],c[1,i]) for i in (0,2)))

    def test_unrelated_teal_palette_cannot_disable_cleanup_on_brown_subject_edges(self):
        a=np.full((128,128,3),[23,251,125],np.uint8);alpha=np.zeros((128,128),np.uint8)
        a[32:96,32:96]=[67,148,105];a[36:92,36:92]=[160,70,25];alpha[32:96,32:96]=255
        result=self.p.despill_matte(a,[23,251,125],alpha,['#a04619','#238f9b'])
        self.assertLessEqual(int(result[80,33,1])-max(int(result[80,33,0]),int(result[80,33,2])),8)
        np.testing.assert_array_equal(result[80,60],[160,70,25,255])
        np.testing.assert_array_equal(result[:,:,3],alpha)

    def test_preserved_export_reprocess_keeps_every_alpha_timing_and_local_repair(self):
        video=self.video();base=self.root/'base';self.p.process_video(str(video),str(base),{'key':'#00ff00','loop':False,'fps':12,'width':64,'height':64,'startFrame':4,'endFrame':20,'cycleSeconds':.4})
        meta=json.loads((base/'animation.json').read_text());f=base/meta['frames'][0]['file']
        old=np.array(Image.open(f));old[20:24,20:24,3]=0;Image.fromarray(old).save(f)
        meta['manual_repair']={'region':[20,20,24,24]};(base/'animation.json').write_text(json.dumps(meta))
        output=self.root/'clean';r=self.p.reprocess_export(str(video),str(base),str(output),{})
        new=json.loads((output/'animation.json').read_text())
        for field in ('frames','loop','canvas','anchor','fps','duration_ms','manual_repair'):self.assertEqual(new[field],meta[field])
        for record in meta['frames']:
            np.testing.assert_array_equal(np.asarray(Image.open(base/record['file']))[:,:,3],np.asarray(Image.open(output/record['file']))[:,:,3])
        self.assertTrue(new['color_repair']['alpha_unchanged']);self.assertTrue((output/'preview.gif').is_file());self.assertTrue((output/'contact-sheet.png').is_file())
        with self.assertRaises(FileExistsError):self.p.reprocess_export(str(video),str(base),str(output),{})
        meta['source_sha256']='0'*64;(base/'animation.json').write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError,'source'):self.p.reprocess_export(str(video),str(base),str(self.root/'bad-source'),{})

    def test_recorded_crouch_recovery_cannot_bypass_despill_or_damage_warm_anatomy(self):
        fixture=Path(__file__).parent/'fixtures/crouch-bleed'
        for name in ('green','warm'):
            a=np.asarray(Image.open(fixture/f'{name}-native.png').convert('RGB'))
            base=Image.open(fixture/f'{name}-matte.png').convert('RGBA');old=np.asarray(base)
            alpha=np.asarray(base.getchannel('A').resize((a.shape[1],a.shape[0]),Image.Resampling.NEAREST))
            clean=np.asarray(Image.fromarray(self.p.despill_matte(a,self.p.border_key(a),alpha,['#241B29','#713C26','#BB753F','#EDBC78','#AD3045','#344E79','#BCC5D2','#238F9B','#FFE39B'])).resize(base.size,Image.Resampling.NEAREST))
            np.testing.assert_array_equal(clean[:,:,3],old[:,:,3])
            if name=='green':
                for x,y in [(80,18),(85,44),(104,46),(57,92),(51,111)]:
                    self.assertGreater(int(old[y,x,1])-max(int(old[y,x,0]),int(old[y,x,2])),60)
                    self.assertLessEqual(int(clean[y,x,1])-max(int(clean[y,x,0]),int(clean[y,x,2])),12)
                for x,y in [(88,28),(94,35),(73,46),(66,63),(74,84)]:np.testing.assert_array_equal(clean[y,x],old[y,x])
            else:
                for x,y in [(101,57),(78,78)]:np.testing.assert_array_equal(clean[y,x],old[y,x])

    def test_reviewed_matte_despill_removes_multihue_edges_without_eroding_coverage(self):
        for background in ([255,0,255],[0,255,0],[0,0,255],[255,150,0],[0,200,255]):
            with self.subTest(background=background):
                a=np.full((128,128,3),background,dtype=np.uint8);alpha=np.zeros((128,128),np.uint8)
                foreground=np.array([160,70,25]);a[32:96,32:96]=np.rint(foreground*.35+np.array(background)*.65)
                a[36:92,36:92]=foreground;alpha[32:96,32:96]=255
                a[48:58,32:48]=[225,155,90] # real tan anatomy, not a key mixture
                a[64:74,32:48]=[210,30,45] # real red cloth touching the edge
                result=self.p.despill_matte(a,background,alpha)
                np.testing.assert_array_equal(result[:,:,3],alpha)
                self.assertLess(np.max(np.abs(result[80,33,:3].astype(int)-foreground)),12)
                np.testing.assert_array_equal(result[52,33,:3],[225,155,90])
                np.testing.assert_array_equal(result[68,33,:3],[210,30,45])
                np.testing.assert_array_equal(result[80,60,:3],foreground)

    def test_retained_neon_key_pocket_cannot_become_a_trusted_interior_colour(self):
        a=np.full((128,128,3),[0,255,0],dtype=np.uint8);alpha=np.zeros((128,128),np.uint8)
        a[32:96,32:96]=[160,70,25];alpha[32:96,32:96]=255;a[50:58,50:58]=[30,150,30]
        result=self.p.despill_matte(a,[0,255,0],alpha,['#a04619','#344e79','#ad3045'])
        self.assertLessEqual(int(result[54,54,1])-max(int(result[54,54,0]),int(result[54,54,2])),12)
        np.testing.assert_array_equal(result[:,:,3],alpha)
        np.testing.assert_array_equal(result[64,64],[160,70,25,255])

    def test_supplied_foreground_palette_protects_legitimate_key_like_edge_paint(self):
        a=np.full((128,128,3),[0,255,255],dtype=np.uint8);alpha=np.zeros((128,128),np.uint8)
        a[32:96,32:96]=[14,167,167];a[36:92,36:92]=[35,35,35];alpha[32:96,32:96]=255
        result=self.p.despill_matte(a,[0,255,255],alpha,['#0ea7a7'])
        np.testing.assert_array_equal(result[80,33],[14,167,167,255])

    def test_reviewed_border_uses_colour_reconstruction_and_refuses_nonuniform_border(self):
        a=np.full((128,128,3),[0,255,0],dtype=np.uint8);a[32:96,32:96]=[56,190,9];a[36:92,36:92]=[160,70,25]
        result=self.p.recovered_key_frame(a,threshold=40)
        self.assertLess(int(result[80,33,1]),100)
        self.assertEqual(int(result[80,33,3]),255)
        self.assertEqual(int(result[0,0,3]),0)
        a[0,:64]=[255,0,255]
        with self.assertRaisesRegex(ValueError,'Nonuniform border'):self.p.recovered_key_frame(a)

    def test_fixed_key_rejects_hue_drift_instead_of_exporting_rainbow_background(self):
        source=self.video();out=self.root/'bad-key'
        with self.assertRaisesRegex(ValueError,'backgroundMode'):
            self.p.process_video(str(source),str(out),{'key':'#ff00ff','loop':False,'pixelArt':True,'width':64,'height':64})

    def test_optional_antialiasing_preserves_interior_and_avoids_transparent_colour_bleed(self):
        a=np.zeros((16,16,4),dtype=np.uint8);a[:]=[0,0,255,0]
        a[3:13,3:13]=[200,80,30,255];a[6:10,6:10]=[20,30,40,255]
        source=Image.fromarray(a)
        crisp=self.p.resize_sprite(source,(8,8),True,False)
        smooth=self.p.resize_sprite(source,(8,8),True,True)
        np.testing.assert_array_equal(np.asarray(crisp),np.asarray(source.resize((8,8),Image.Resampling.NEAREST)))
        b=np.asarray(smooth);partial=(b[:,:,3]>0)&(b[:,:,3]<255)
        self.assertTrue(np.any(partial))
        # Eight-bit premultiplication may round a channel by a few levels.
        self.assertLessEqual(int(np.max(np.abs(b[partial,:3].astype(int)-[200,80,30]))),3)
        np.testing.assert_array_equal(b[3:5,3:5],np.asarray(crisp)[3:5,3:5])
        self.assertEqual(int(b[0,0,3]),0)

    def test_bridge_preparation_aligns_anchors_without_clipping(self):
        a=Image.new('RGBA',(64,64));ImageDraw.Draw(a).rectangle((20,20,40,50),fill='red')
        source=self.root/'endpoint.png';a.save(source);out=self.root/'keyed.png'
        r=self.p.prepare_endpoint(str(source),str(out),128,128,'#00ff00',[.5,.8],[.5,.8],True)
        self.assertEqual(r['offset'],[0,0]);self.assertEqual(r['scale'],2)
        with Image.open(out) as im:self.assertEqual(im.getpixel((60,60)),(255,0,0))
        with self.assertRaisesRegex(ValueError,'clip'):
            self.p.prepare_endpoint(str(source),str(self.root/'bad.png'),128,128,'#00ff00',[.5,.8],[.5,0],True)

    def test_key_selection_avoids_the_actual_palette(self):
        selected = self.p.choose_key(['#00ff00', '#00ffff', '#ffff00'])
        self.assertNotEqual(selected, '#00ff00')
        self.assertNotEqual(selected, '#00ffff')
        self.assertNotEqual(selected, '#ffff00')

    def test_keying_retains_solid_subject_and_removes_background(self):
        a = np.zeros((24, 24, 3), dtype=np.uint8); a[:] = [0,255,0]; a[6:18,6:18] = [200,40,30]
        rgba = self.p.key_frame(a, '#00ff00')
        self.assertEqual(int(rgba[0,0,3]), 0)
        self.assertEqual(int(rgba[10,10,3]), 255)
        np.testing.assert_array_equal(rgba[10,10,:3], a[10,10])

    def test_darkened_reserved_key_hue_does_not_survive_as_an_outline(self):
        a=np.zeros((24,24,3),dtype=np.uint8);a[:]=[4,3,145];a[6:18,6:18]=[180,90,30];a[10,10]=[25,25,25]
        rgba=self.p.key_frame(a,'#0000ff')
        self.assertEqual(int(rgba[0,0,3]),0)
        np.testing.assert_array_equal(rgba[8,8],[180,90,30,255])
        np.testing.assert_array_equal(rgba[10,10],[25,25,25,255])

    def test_mixed_key_edge_is_unmixed_without_shrinking_the_solid_core(self):
        a=np.zeros((32,32,3),dtype=np.uint8);a[:]=[0,0,255]
        a[7:25,7:25]=[100,50,142];a[9:23,9:23]=[200,100,30]
        rgba=self.p.key_frame(a,'#0000ff')
        self.assertLessEqual(int(rgba[8,15,2]),int(rgba[8,15,1])+8)
        self.assertEqual(int(rgba[8,15,3]),255)
        np.testing.assert_array_equal(rgba[15,15],[200,100,30,255])

    def test_despill_changes_colour_not_coverage_for_outline_and_outline_free_edges(self):
        for foreground in ([30,20,10],[220,140,80],[180,180,180]):
            a=np.zeros((32,32,3),dtype=np.uint8);a[:]=[0,0,255]
            a[6:26,6:26]=np.rint(np.array(foreground)*.25+np.array([0,0,255])*.75)
            a[9:23,9:23]=foreground
            clean=self.p.key_frame(a,'#0000ff');raw=self.p.key_frame(a,'#0000ff',despill=False)
            np.testing.assert_array_equal(clean[:,:,3],raw[:,:,3])
            self.assertGreater(int(clean[7,15,3]),128)
            self.assertLess(np.max(np.abs(clean[7,15,:3].astype(int)-foreground)),12)
            self.assertEqual(int(clean[0,0,3]),0)

    def test_neutral_background_still_preserves_dark_and_grey_foreground(self):
        a=np.full((24,24,3),255,dtype=np.uint8);a[6:18,6:18]=[50,50,50]
        rgba=self.p.key_frame(a,'#ffffff')
        self.assertEqual(int(rgba[0,0,3]),0)
        np.testing.assert_array_equal(rgba[12,12],[50,50,50,255])

    def test_magenta_key_does_not_despill_unrelated_blue_or_green_content(self):
        a=np.zeros((24,24,3),dtype=np.uint8);a[:]=[255,0,255];a[6:12,6:18]=[0,0,255];a[12:18,6:18]=[0,255,0]
        rgba=self.p.key_frame(a,'#ff00ff')
        np.testing.assert_array_equal(rgba[8,8],[0,0,255,255]);np.testing.assert_array_equal(rgba[14,8],[0,255,0,255])

    def test_despill_support_scales_with_native_resolution(self):
        for key,background,foreground in [('#ff00ff',[255,0,255],[180,30,10]),('#00ff00',[0,255,0],[180,30,10]),('#0000ff',[0,0,255],[180,90,30])]:
            base=np.full((128,128,3),background,dtype=np.uint8)
            base[40:88,40:88]=np.rint(np.array(foreground)*.4+np.array(background)*.6)
            base[44:84,44:84]=foreground
            for scale in (1,2,4):
                with self.subTest(key=key,scale=scale):
                    a=np.repeat(np.repeat(base,scale,axis=0),scale,axis=1)
                    clean=self.p.key_frame(a,key);raw=self.p.key_frame(a,key,despill=False)
                    np.testing.assert_array_equal(clean[:,:,3],raw[:,:,3])
                    pixel=clean[64*scale,40*scale,:3].astype(int)
                    self.assertLess(int(np.max(np.abs(pixel-foreground))),12)
                    np.testing.assert_array_equal(clean[64*scale,64*scale],[*foreground,255])
                    self.assertEqual(int(clean[0,0,3]),0)

    def test_recorded_scarf_spill_is_removed_without_changing_alpha(self):
        source=Path(__file__).parent/'fixtures/magenta-scarf-source.png'
        a=np.asarray(Image.open(source).convert('RGB'))
        clean=self.p.key_frame(a,'#ff00ff');raw=self.p.key_frame(a,'#ff00ff',despill=False)
        np.testing.assert_array_equal(clean[:,:,3],raw[:,:,3])
        im=np.asarray(self.p.resize_sprite(Image.fromarray(clean),(128,128),True)).astype(int)
        for x,y in [(43,44),(37,46),(38,46),(38,47)]:
            self.assertGreaterEqual(im[y,x,3],128)
            self.assertLessEqual(min(im[y,x,0],im[y,x,2])-im[y,x,1],10)
        # Equipment/trouser colours unrelated to the key hue must stay untouched.
        c=a.astype(int);safe=(np.minimum(c[:,:,0],c[:,:,2])<=c[:,:,1])&(raw[:,:,3]==255)
        np.testing.assert_array_equal(clean[safe],raw[safe])

    def test_soft_matte_unmixes_neutral_translucent_effect(self):
        a = np.array([[[64,192,64]]], dtype=np.uint8) # half grey over green
        rgba = self.p.key_frame(a, '#00ff00', soft=True)
        self.assertTrue(110 < int(rgba[0,0,3]) < 150)
        self.assertLess(abs(int(rgba[0,0,0])-int(rgba[0,0,1])), 8)

    def test_colour_reconstruction_moves_with_subject_and_preserves_real_gaps(self):
        a=np.zeros((48,48,3),dtype=np.uint8);a[:]=[0,0,255]
        a[12:28,12:28]=[55,35,211];a[15:25,15:25]=[220,140,80]
        a[19:21,12:28]=[0,0,255]
        first=self.p.key_frame(a,'#0000ff')
        shifted=self.p.key_frame(np.roll(a,5,axis=1),'#0000ff')
        np.testing.assert_array_equal(np.roll(first,5,axis=1),shifted)
        self.assertTrue(np.all(first[19:21,:,3]==0))
        np.testing.assert_array_equal(first[:,:,3],self.p.key_frame(a,'#0000ff',despill=False)[:,:,3])

    def test_soft_edges_are_not_hardened_by_colour_reconstruction(self):
        a=np.array([[[32,224,32],[64,192,64],[96,160,96]]],dtype=np.uint8)
        clean=self.p.key_frame(a,'#00ff00',soft=True)
        np.testing.assert_array_equal(clean,self.p.key_frame(a,'#00ff00',soft=True,despill=False))
        self.assertTrue(np.all((clean[:,:,3]>0)&(clean[:,:,3]<255)))

    def test_general_loop_finds_repetition_not_whole_minimum_length(self):
        # A changing abstract shape, no character/walking assumptions.
        frames=[]
        for i in range(72):
            image=Image.new('RGBA',(32,32));d=ImageDraw.Draw(image)
            x=12+int(5*math.sin(2*math.pi*i/12)); y=12+int(5*math.cos(2*math.pi*i/12))
            d.rectangle((x,y,x+5,y+5),fill=(220,30,90,255)); frames.append(image)
        result=self.p.select_loop(frames,12)
        self.assertLess(result['end']-result['start'],72)
        self.assertEqual((result['end']-result['start']) % 12,0)
        self.assertTrue(result['accepted'])
        self.assertFalse(result['pixel_perfect_guaranteed'])

    def test_nonperiodic_content_is_not_certified_seamless(self):
        frames=[Image.new('RGBA',(32,32),(i*3,20,200,255)) for i in range(60)]
        result=self.p.select_loop(frames,12)
        self.assertFalse(result['accepted'])

    def video(self, static=False):
        src=self.root/'input';src.mkdir()
        for i in range(60):
            im=Image.new('RGB',(64,64),(0,255,0));d=ImageDraw.Draw(im)
            x=24 if static else 24+int(8*math.sin(2*math.pi*i/12));y=24 if static else 24+int(8*math.cos(2*math.pi*i/12))
            d.ellipse((x,y,x+12,y+12),fill=(240,30,30));im.save(src/f'{i:04}.png')
        video=self.root/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-framerate','12','-i',str(src/'%04d.png'),'-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
        return video

    def test_real_video_exports_full_alpha_frames_sheet_and_looping_gif(self):
        video=self.video();output=self.root/'export'
        result=self.p.process_video(str(video),str(output),{'key':'#00ff00','loop':True,'fps':12,'width':64,'height':64,'anchor':[.5,1]})
        meta=json.loads((output/'animation.json').read_text());pngs=sorted((output/'frames').glob('*.png'))
        self.assertEqual(len(pngs),len(meta['frames']));self.assertLess(len(pngs),60)
        with Image.open(pngs[0]) as im:
            self.assertEqual(im.mode,'RGBA');self.assertEqual(im.size,(64,64));self.assertEqual(im.getpixel((0,0))[3],0)
        self.assertEqual(meta['anchor'],[.5,1]);self.assertTrue((output/'spritesheet.png').exists())
        with Image.open(output/'preview.gif') as gif:self.assertEqual(gif.info.get('loop'),0)
        self.assertTrue((output/'contact-sheet.png').exists());self.assertTrue((output/'quality-report.json').exists())
        self.assertTrue(video.exists());self.assertTrue(result['frames']>1)
        with self.assertRaises((FileExistsError,ValueError)):self.p.process_video(str(video),str(output),{'key':'#00ff00','loop':True})

    def test_reprocessing_a_known_cycle_measures_that_interval_without_false_seam_warning(self):
        video=self.video();out=self.root/'same-cycle'
        result=self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':True,'fps':12,'width':64,'height':64,'startFrame':12,'endFrame':24})
        self.assertEqual(result['frames'],12)
        self.assertTrue(result['quality']['loop']['accepted'])
        self.assertEqual(result['quality']['loop']['start'],12);self.assertEqual(result['quality']['loop']['end'],24)
        self.assertFalse(any('No low-error' in w for w in result['quality']['warnings']))

    def test_pixel_art_keys_native_rgb_before_resize_and_has_binary_alpha(self):
        video=self.video();out=self.root/'pixel'
        result=self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':False,'fps':12,'width':32,'height':32,'pixelArt':True})
        self.assertEqual(result['quality']['keying_resolution'],{'width':64,'height':64})
        self.assertTrue(result['quality']['resize_after_keying'])
        with Image.open(out/'frames/0000.png') as im:
            a=np.asarray(im).astype(int);self.assertEqual(im.size,(32,32))
        self.assertEqual(set(np.unique(a[:,:,3])),{0,255})
        spill=(a[:,:,3]>0)&(a[:,:,1]>a[:,:,0]+30)&(a[:,:,1]>a[:,:,2]+30)
        self.assertLessEqual(int(spill.sum()),2)

    def test_short_retime_resamples_whole_interval_and_preserves_endpoints(self):
        video=self.video();out=self.root/'short'
        self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':False,'fps':24,'width':32,'height':32,'cycleSeconds':.18})
        meta=json.loads((out/'animation.json').read_text())
        self.assertLessEqual(meta['fps'],24)
        self.assertGreaterEqual(len(meta['frames']),2)
        self.assertEqual(meta['frames'][0]['source_time_seconds'],0)
        self.assertAlmostEqual(meta['frames'][-1]['source_time_seconds'],119/24)
        self.assertAlmostEqual(sum(f['duration_ms'] for f in meta['frames']),180)
        self.assertTrue((out/'preview.gif').exists())

    def test_retime_cannot_emit_fps_rejected_by_reference_importer(self):
        video=self.video();out=self.root/'fast'
        self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':False,'fps':24,'width':32,'height':32,'cycleSeconds':1.5})
        meta=json.loads((out/'animation.json').read_text())
        self.assertLessEqual(meta['fps'],24)
        self.assertEqual(len(meta['frames']),36)

    def test_one_shot_keeps_all_frames_and_gif_does_not_loop(self):
        video=self.video();out=self.root/'once'
        self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':False,'fps':12,'width':64,'height':64})
        meta=json.loads((out/'animation.json').read_text());self.assertEqual(len(meta['frames']),60)
        with Image.open(out/'preview.gif') as gif:self.assertNotIn('loop',gif.info)
        self.assertAlmostEqual(sum(x['duration_ms'] for x in meta['frames']),5000,places=3)

    def test_static_clip_is_not_misreported_as_successful_motion(self):
        video=self.video(static=True);out=self.root/'static'
        result=self.p.process_video(str(video),str(out),{'key':'#00ff00','loop':True,'fps':12,'width':64,'height':64})
        self.assertEqual(result['frames'],1)
        self.assertEqual(result['quality']['status'],'needs_review')
        self.assertTrue(any('static' in w for w in result['quality']['warnings']))

    def test_exact_transparent_source_is_contained_without_repainting(self):
        source=self.root/'exact.png';im=Image.new('RGBA',(20,10));im.putpixel((10,5),(255,0,0,255));im.save(source)
        dest=self.root/'prepared.png';r=self.p.prepare_source(str(source),str(dest),64,64,'#00ff00',exact=True)
        self.assertTrue(r['original_preserved'])
        with Image.open(source) as im:self.assertEqual(im.size,(20,10))
        with Image.open(dest) as im:self.assertEqual(im.size,(64,64))

    def test_exact_opaque_source_preserves_solid_ink_and_highlight_rgb(self):
        im=Image.new('RGB',(32,32),'#ff00ff');a=np.array(im)
        a[6:26,6:26]=[180,90,30]
        a[12:15,12:15]=[16,5,28]  # intentional purple-black ink, not key spill
        a[17:20,17:20]=[237,231,255]  # intentional pale painted highlight
        source=self.root/'paint.png';Image.fromarray(a).save(source);dest=self.root/'prepared.png'
        self.p.prepare_source(str(source),str(dest),32,32,'#00ff00',exact=True,input_key='#ff00ff',pixel_art=True)
        b=np.asarray(Image.open(dest))
        np.testing.assert_array_equal(b[6:26,6:26],a[6:26,6:26],err_msg='Exact artwork preparation must not despill/repaint opaque artwork before H3')
        np.testing.assert_array_equal(b[0,0],[0,255,0])

    def test_exact_opaque_source_requires_known_background(self):
        source=self.root/'photo.png';Image.new('RGB',(32,32),'red').save(source)
        with self.assertRaises(ValueError):self.p.prepare_source(str(source),str(self.root/'bad.png'),64,64,'#00ff00',exact=True)

if __name__=='__main__':unittest.main()
