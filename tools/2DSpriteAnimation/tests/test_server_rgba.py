import hashlib,importlib.util,io,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from PIL import Image
import numpy as np
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('processor',Path(__file__).resolve().parents[1]/'processor.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class ServerRgbaTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.video=self.root/'original.mp4'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=32x32:r=4:d=1','-an','-c:v','libx264',str(self.video)],check=True,capture_output=True)
  self.meta={'version':1,'processor':'satodive-cpu-v1','source_sha256':hashlib.sha256(self.video.read_bytes()).hexdigest(),'fps':4,'key':'#0000ff','frames':4,'width':32,'height':32,'alpha':'straight RGBA','warnings':['server estimate']}
 def archive(self,changes=None,extra=None,image=None):
  meta={**self.meta,**(changes or {})};path=self.root/'rgba.zip'
  with zipfile.ZipFile(path,'w') as z:
   z.writestr('manifest.json',json.dumps(meta))
   for i in range(4):
    im=(image[i] if isinstance(image,list) else image).copy() if image is not None else Image.new('RGBA',(32,32))
    if image is None:im.putpixel((16,16),(0,255,0,255))
    b=io.BytesIO();im.save(b,format='PNG');z.writestr(f'frames/{i:04}.png',b.getvalue())
   if extra:z.writestr(extra,b'bad')
  return path,hashlib.sha256(path.read_bytes()).hexdigest()
 def test_server_rgba_is_used_directly_not_keyed_again(self):
  archive,digest=self.archive();out=self.root/'export'
  result=p.process_video(str(self.video),str(out),{'key':'#0000ff','fps':4,'width':32,'height':32,'loop':False},rgba_archive=str(archive),rgba_sha256=digest)
  self.assertEqual(result['frames'],4);self.assertEqual(Image.open(out/'frames/0000.png').getpixel((16,16)),(0,255,0,255));self.assertEqual(Image.open(out/'frames/0000.png').getpixel((0,0))[3],0)
  self.assertIsNone(result['quality']['despill_radius_pixels']);self.assertEqual(result['quality']['despill'],'satodive_cpu_v1');self.assertEqual(json.loads((out/'animation.json').read_text())['server_transparency']['processor'],'satodive-cpu-v1')
 def test_server_pixel_art_preserves_supplied_rgba_instead_of_hardening_alpha(self):
  im=Image.new('RGBA',(32,32));alphas=[0,1,64,127,128,148,200,254,255]
  for y in range(32):
   for x in range(32):im.putpixel((x,y),(218,241,243,alphas[(x//2)%len(alphas)]))
  archive,digest=self.archive(image=im)
  for size in [32,16]:
   with self.subTest(size=size),patch.object(p,'key_frame',side_effect=AssertionError('RGBA must not be keyed')),patch.object(p,'despill_matte',side_effect=AssertionError('RGBA must not be despilled')),patch.object(p,'recovered_key_frame',side_effect=AssertionError('RGBA must not be rematted')):
    out=self.root/f'export_{size}';result=p.process_video(str(self.video),str(out),{'key':'#0000ff','fps':4,'width':size,'height':size,'loop':False,'pixelArt':True,'soft':False},rgba_archive=str(archive),rgba_sha256=digest)
    expected=np.asarray(im.resize((size,size),Image.Resampling.NEAREST));meta=json.loads((out/'animation.json').read_text());atlas=Image.open(out/'spritesheet.png')
    for f in meta['frames']:
     np.testing.assert_array_equal(np.asarray(Image.open(out/f['file'])),expected,err_msg='Already-keyed server RGBA must retain RGB and partial alpha')
     r=f['rect'];np.testing.assert_array_equal(np.asarray(atlas.crop((r['x'],r['y'],r['x']+size,r['y']+size))),expected)
    self.assertEqual(result['quality']['pixel_art_alpha'],'server-provided');self.assertEqual(result['quality']['antialias'],'off')
 def test_server_rgba_has_lossless_transparent_animation_not_only_checker_gif(self):
  images=[Image.new('RGBA',(32,32),(218-i,241,243,148+i)) for i in range(4)]
  for im in images:im.putpixel((0,0),(123,124,125,0))
  archive,digest=self.archive(image=images);out=self.root/'animated'
  p.process_video(str(self.video),str(out),{'key':'#0000ff','fps':4,'width':32,'height':32,'loop':False,'pixelArt':True},rgba_archive=str(archive),rgba_sha256=digest)
  meta=json.loads((out/'animation.json').read_text());self.assertEqual(meta.get('transparent_animation'),'animation.apng')
  self.assertTrue((out/'animation.apng').is_file(),'RGBA export needs a real transparent animation, not just a checkerboard preview')
  animation=Image.open(out/'animation.apng');self.addCleanup(animation.close);self.assertEqual(animation.n_frames,4);total=0
  for i in range(getattr(animation,'n_frames',1)):
   animation.seek(i);np.testing.assert_array_equal(np.array(animation.convert('RGBA')),np.array(Image.open(out/f'frames/{i:04}.png')));total+=animation.info.get('duration',0)
  self.assertAlmostEqual(total,1000,delta=1);self.assertEqual(animation.info.get('loop'),1)
 def test_explicit_window_retiming_retains_every_selected_rgba_pose(self):
  images=[Image.new('RGBA',(32,32),(200+i,150,80,100+i)) for i in range(4)]
  archive,digest=self.archive(image=images);out=self.root/'all_poses'
  p.process_video(str(self.video),str(out),{'key':'#0000ff','fps':4,'width':32,'height':32,'loop':False,'cycleSeconds':.75,'retainSelectedFrames':True},rgba_archive=str(archive),rgba_sha256=digest)
  meta=json.loads((out/'animation.json').read_text());self.assertEqual(len(meta['frames']),4);self.assertEqual(meta['duration_ms'],750)
  for i,f in enumerate(meta['frames']):np.testing.assert_array_equal(np.array(Image.open(out/f['file'])),np.array(images[i]));self.assertEqual(f['source_time_seconds'],i/4)
 def test_archive_source_and_members_are_checked_before_extraction(self):
  archive,digest=self.archive({'source_sha256':'f'*64})
  with self.assertRaisesRegex(ValueError,'provenance'):p.unpack_server_rgba(archive,digest,self.meta['source_sha256'],4,'#0000ff',self.root)
  archive,digest=self.archive(extra='../escape.png')
  with self.assertRaisesRegex(ValueError,'members'):p.unpack_server_rgba(archive,digest,self.meta['source_sha256'],4,'#0000ff',self.root)
 def test_checksum_mismatch_rejected(self):
  archive,digest=self.archive()
  with self.assertRaisesRegex(ValueError,'checksum'):p.unpack_server_rgba(archive,'0'*64,self.meta['source_sha256'],4,'#0000ff',self.root)

if __name__=='__main__':unittest.main()
