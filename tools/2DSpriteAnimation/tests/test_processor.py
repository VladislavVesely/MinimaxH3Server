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
        self.assertTrue(80<int(rgba[8,15,3])<220)
        np.testing.assert_array_equal(rgba[15,15],[200,100,30,255])

    def test_neutral_background_still_preserves_dark_and_grey_foreground(self):
        a=np.full((24,24,3),255,dtype=np.uint8);a[6:18,6:18]=[50,50,50]
        rgba=self.p.key_frame(a,'#ffffff')
        self.assertEqual(int(rgba[0,0,3]),0)
        np.testing.assert_array_equal(rgba[12,12],[50,50,50,255])

    def test_magenta_key_does_not_despill_unrelated_blue_or_green_content(self):
        a=np.zeros((24,24,3),dtype=np.uint8);a[:]=[255,0,255];a[6:12,6:18]=[0,0,255];a[12:18,6:18]=[0,255,0]
        rgba=self.p.key_frame(a,'#ff00ff')
        np.testing.assert_array_equal(rgba[8,8],[0,0,255,255]);np.testing.assert_array_equal(rgba[14,8],[0,255,0,255])

    def test_soft_matte_unmixes_neutral_translucent_effect(self):
        a = np.array([[[64,192,64]]], dtype=np.uint8) # half grey over green
        rgba = self.p.key_frame(a, '#00ff00', soft=True)
        self.assertTrue(110 < int(rgba[0,0,3]) < 150)
        self.assertLess(abs(int(rgba[0,0,0])-int(rgba[0,0,1])), 8)

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

    def test_exact_opaque_source_requires_known_background(self):
        source=self.root/'photo.png';Image.new('RGB',(32,32),'red').save(source)
        with self.assertRaises(ValueError):self.p.prepare_source(str(source),str(self.root/'bad.png'),64,64,'#00ff00',exact=True)

if __name__=='__main__':unittest.main()
