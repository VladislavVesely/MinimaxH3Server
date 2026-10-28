import hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from PIL import Image
spec=importlib.util.spec_from_file_location('p',Path(__file__).resolve().parents[1]/'processor.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class BridgeAnalysis(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'frames').mkdir();self.frames=[]
  for i in range(124):
   im=Image.new('RGBA',(32,32));im.paste((200 if i<62 else 250,90,40,180),(4 if i<62 else 16,10,14 if i<62 else 26,24));file=self.root/'frames'/f'{i:04}.png';im.save(file);self.frames.append({'file':f'frames/{i:04}.png','duration_ms':1000/24,'source_time_seconds':i/24})
  (self.root/'animation.json').write_text(json.dumps({'canvas':{'width':32,'height':32},'anchor':[.5,.8],'fps':24,'frames':self.frames}))
  self.a=[self.record(0),self.record(1)];self.b=[self.record(122),self.record(123)]
 def record(self,i):
  f=self.root/self.frames[i]['file'];return {'file':str(f),'duration_ms':1000/24,'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'label':str(i)}
 def test_candidates_are_advisory_and_span_valid_intervals(self):
  r=p.bridge_analyze(str(self.root),self.a,self.b,.45);self.assertTrue(r['review_required']);self.assertEqual(r['source_frames'],124);self.assertTrue(r['candidates'])
  for c in r['candidates']:self.assertLess(c['startFrame'],c['endFrame']);self.assertLessEqual(c['endFrame'],124);self.assertNotIn('accepted',c)
 def test_join_preview_preserves_every_pose_and_duration_without_rekey(self):
  records=[*self.a,self.record(62),self.record(63),*self.b];output=self.root/'join';r=p.bridge_preview(str(output),records,{'width':32,'height':32},[.5,.8]);self.assertEqual(r['frames'],6);meta=json.loads((output/'sequence.json').read_text());self.assertEqual(len(meta['frames']),6);self.assertEqual(meta['frames'][2]['sha256'],records[2]['sha256']);self.assertTrue((output/'sequence.apng').exists())
 def test_changed_context_frame_is_rejected(self):
  self.a[0]['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'changed|hash'):p.bridge_analyze(str(self.root),self.a,self.b,.45)
if __name__=='__main__':unittest.main()
