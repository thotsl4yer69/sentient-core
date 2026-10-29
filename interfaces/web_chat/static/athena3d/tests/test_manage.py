import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('manage', Path(__file__).resolve().parents[1] / 'tools/manage.py')
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)

# These intentionally minimal GLB headers are unit fixtures, never shipped as avatars.
def fixture(path, compatible=True):
    data={'asset':{'version':'2.0'},'nodes':[{'name':n} for n in ['Hips','Spine','Neck','Head']],
          'skins':[{'joints':[0,1,2,3]}], 'meshes':[{'extras':{'targetNames':['eyeBlinkLeft','eyeBlinkRight']+['viseme_'+n for n in ['PP','aa','FF','TH','DD','kk','O','U']]}}]}
    if not compatible:data['skins']=[]
    chunk=json.dumps(data).encode();chunk+=b' '*((-len(chunk))%4)
    path.write_bytes(struct.pack('<4sIIII',b'glTF',2,len(chunk)+20,len(chunk),0x4E4F534A)+chunk)

class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()
    def release(self):
        release=self.root/'release';release.mkdir();(release/'models').mkdir();
        for name in ['index.html','ui.js','athena.bundle.mjs']:(release/name).write_text('test fixture')
        (release/'boot.js').write_text("const baseURL=new URL('./',scriptURL);")
        (release/'config.json').write_text(json.dumps({'defaultProfile':'mpfb','profiles':[{'id':'mpfb'}]}))
        fixture(release/'models/athena.glb');return release
    def test_invalid_binary_rejected(self):
        p=self.root/'bad.glb';p.write_bytes(b'not a glb file');self.assertRaises(ValueError,manage.audit,p)
    def test_bones_and_shapes_are_measured(self):
        p=self.root/'valid.glb';fixture(p);result=manage.audit(p);self.assertEqual(result['bones'],4);self.assertEqual(result['facialShapes'],10)
    def test_recovery_leaves_original_unchanged(self):
        release=self.release();p=self.root/'original.glb';fixture(p);before=p.read_bytes();result=manage.recover(release,[p]);self.assertEqual(p.read_bytes(),before);self.assertEqual(Path(result[0]['copy']).read_bytes(),before)
    def test_recovery_does_not_change_default(self):
        release=self.release();p=self.root/'original.glb';fixture(p);manage.recover(release,[p]);self.assertEqual(json.loads((release/'config.json').read_text())['defaultProfile'],'mpfb')
    def test_recovery_deduplicates(self):
        release=self.release();p=self.root/'original.glb';fixture(p);manage.recover(release,[p]);manage.recover(release,[p]);self.assertEqual(len(json.loads((release/'config.json').read_text())['profiles']),2)
    def test_unrigged_asset_preserved_not_activated(self):
        release=self.release();p=self.root/'original.glb';fixture(p,False);r=manage.recover(release,[p]);self.assertTrue(Path(r[0]['copy']).exists());self.assertEqual(len(json.loads((release/'config.json').read_text())['profiles']),1)
    def test_missing_asset_is_reported(self):
        release=self.release();r=manage.recover(release,[self.root/'missing.glb']);self.assertEqual(r[0]['verdict'],'NOT_FOUND')
    def test_install_keeps_unrelated_files(self):
        release=self.release();static=self.root/'static';static.mkdir();(static/'existing.txt').write_text('keep');manage.install(str(static),root=release);self.assertEqual((static/'existing.txt').read_text(),'keep')
    def test_install_rollback_restores_entrypoint(self):
        release=self.release();static=self.root/'static';static.mkdir();(static/'athena.html').write_text('original');r=manage.install(str(static),root=release);manage.rollback(r['rollbackReceipt']);self.assertEqual((static/'athena.html').read_text(),'original')
    def test_rollback_does_not_overwrite_concurrent_edit(self):
        release=self.release();static=self.root/'static';static.mkdir();r=manage.install(str(static),root=release);(static/'athena.html').write_text('Dot changed this');self.assertRaises(ValueError,manage.rollback,r['rollbackReceipt']);self.assertEqual((static/'athena.html').read_text(),'Dot changed this')
    def test_incomplete_release_refused(self):
        self.assertRaises(ValueError,manage.install,str(self.root),False,self.root)

if __name__=='__main__':unittest.main()
