import hashlib,importlib.util,io,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from PIL import Image
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('processor',Path(__file__).resolve().parents[1]/'processor.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class DirectVaeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.video=self.root/'preview.mp4'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=32x32:r=24','-frames:v','4','-an','-c:v','libx264',str(self.video)],check=True,capture_output=True)
  self.sha=hashlib.sha256(self.video.read_bytes()).hexdigest();self.job='a'*32
  self.meta={'version':1,'processor':'satodive-direct-vae-v1','frame_source':'vae-decoded-float32','frame_selection':'all','job_id':self.job,'source_sha256':'b'*64,'raw_frames_sha256':'b'*64,'preview_video_sha256':self.sha,'generation_settings_sha256':'c'*64,'source_fps':24,'fps':24,'duration':4/24,'frames':4,'width':32,'height':32,'key':'#0000ff','alpha':'straight RGBA'}
 def archive(self,change=None):
  f=self.root/'native.zip'
  with zipfile.ZipFile(f,'w') as z:
   z.writestr('manifest.json',json.dumps({**self.meta,**(change or {})}))
   for i in range(4):
    im=Image.new('RGBA',(32,32));im.putpixel((16,16),(218,241,243,148+i));b=io.BytesIO();im.save(b,format='PNG');z.writestr(f'frames/{i:04}.png',b.getvalue())
  return f,hashlib.sha256(f.read_bytes()).hexdigest()
 def test_native_float_source_is_not_confused_with_encoded_preview_and_no_rematte(self):
  f,h=self.archive()
  with patch.object(p,'key_frame',side_effect=AssertionError('No local keyer')),patch.object(p,'despill_matte',side_effect=AssertionError('No repaint')):
   out=self.root/'export';r=p.process_video(str(self.video),str(out),{'key':'#0000ff','fps':12,'width':32,'height':32,'loop':False,'pixelArt':True},rgba_archive=str(f),rgba_sha256=h,rgba_job_id=self.job)
  self.assertEqual(r['frames'],2);m=json.loads((out/'animation.json').read_text());self.assertEqual(m['source_interval']['sample_fps'],24);self.assertEqual(m['frames'][1]['source_time_seconds'],3/24)
  self.assertEqual(Image.open(out/'frames/0001.png').getpixel((16,16)),(218,241,243,151));self.assertEqual(r['quality']['despill'],'satodive_direct_vae_v1')
 def test_native_archive_requires_exact_job_preview_and_raw_provenance(self):
  for change in [{'job_id':'d'*32},{'preview_video_sha256':'d'*64},{'raw_frames_sha256':'d'*64},{'frame_selection':'sampled'},{'fps':12}]:
   f,h=self.archive(change)
   with self.assertRaisesRegex(ValueError,'provenance'):p.unpack_server_rgba(f,h,self.sha,24,'#0000ff',self.root,expected_job=self.job)
 def test_no_silent_native_import_as_legacy(self):
  f,h=self.archive()
  with self.assertRaisesRegex(ValueError,'provenance'):p.unpack_server_rgba(f,h,self.sha,24,'#0000ff',self.root)
if __name__=='__main__':unittest.main()
