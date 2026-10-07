"""CPU git replay only. Optional exact baseline verifies the reviewed production patch."""
import copy,hashlib,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.append(str(Path(os.environ.get('LILIUXFLOW_RECIPE_SUPPORT',str(HERE))).resolve()))
from native_recipe import apply_manager_source_patches,MANAGER_FILES
from common import DistributionError

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class ReplayTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='manager recipe 日本語 ');self.base=Path(self.tmp.name).resolve();self.source=self.base/'source';self.source.mkdir();self.target=self.base/'upstream';self.target.mkdir();self.patch=self.source/'metrics.patch'
  hunks=[];before=[];after=[]
  for f in sorted(MANAGER_FILES):
   p=self.target/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('before\n');before.append({'path':f,'sha256':digest(p)});after.append({'path':f,'sha256':hashlib.sha256(b'after\n').hexdigest()});hunks.append(f'--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-before\n+after\n')
  self.patch.write_text(''.join(hunks));self.pin={'patches':[{'path':'metrics.patch','sha256':digest(self.patch),'strip':1}],'source_files_before_patch':before,'patched_source_files':after}
 def tearDown(self):self.tmp.cleanup()
 def run_recipe(self):return apply_manager_source_patches(self.source,self.target,self.pin)
 def unchanged(self):
  for f in MANAGER_FILES:self.assertEqual((self.target/f).read_text(),'before\n')
 def reject(self):
  with self.assertRaises(DistributionError):self.run_recipe()
  self.unchanged()
 def test_three_files_exact_replay(self):
  self.assertEqual(self.run_recipe()['replay'],'PASS')
  for f in MANAGER_FILES:self.assertEqual((self.target/f).read_text(),'after\n')
 def test_old_manifest_no_patch_is_unchanged(self):
  self.assertEqual(apply_manager_source_patches(self.source,self.target,{})['replay'],'NOT_REQUESTED');self.unchanged()
 def test_hash_rejected_before_mutation(self):
  self.pin['patches'][0]['sha256']='0'*64;self.reject()
 def test_wrong_strip(self):self.pin['patches'][0]['strip']=0;self.reject()
 def test_outside_observer_file(self):
  self.patch.write_text(self.patch.read_text().replace('internal/server/metrics.go','internal/server/auth.go'));self.pin['patches'][0]['sha256']=digest(self.patch);self.reject()
 def test_missing_after_binding(self):self.pin['patched_source_files'].pop();self.reject()
 def test_duplicate_binding(self):self.pin['patched_source_files'].append(self.pin['patched_source_files'][0]);self.reject()
 def test_malformed_after_hash(self):self.pin['patched_source_files'][0]['sha256']='not-a-sha';self.reject()
 def test_before_hash_mismatch(self):self.pin['source_files_before_patch'][0]['sha256']='0'*64;self.reject()
 def test_absolute_and_traversal_patch_targets(self):
  original=self.patch.read_text()
  for path in ['/absolute.go','internal/server/../../outside.go']:
   self.patch.write_text(original.replace('internal/server/metrics.go',path));self.pin['patches'][0]['sha256']=digest(self.patch);self.reject()
 def test_link_mode(self):
  self.patch.write_text('new file mode 120000\n'+self.patch.read_text());self.pin['patches'][0]['sha256']=digest(self.patch);self.reject()
 def test_target_symlink(self):
  f=next(iter(MANAGER_FILES));p=self.target/f;other=self.base/'original';shutil.copyfile(p,other);p.unlink();p.symlink_to(other);self.reject()
 def test_bad_second_patch_is_prevalidated(self):
  self.pin['patches'].append({'path':'missing.patch','sha256':'0'*64,'strip':1});self.reject()
 def test_after_hash_mismatch_stops_acceptance(self):
  self.pin['patched_source_files'][0]['sha256']='0'*64
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_ancestor_git_and_inherited_git_env(self):
  subprocess.run(['git','init','-q',str(self.base)],check=True)
  with patch.dict(os.environ,{'GIT_DIR':str(self.base/'.git'),'GIT_WORK_TREE':str(self.base),'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.ignorecase','GIT_CONFIG_VALUE_0':'true'}):self.assertEqual(self.run_recipe()['replay'],'PASS')
 def test_exact_reviewed_production_patch(self):
  baseline=os.environ.get('LILIUXFLOW_MANAGER_BASELINE')
  if not baseline:self.skipTest('exact baseline not supplied; synthetic scope only')
  source=HERE.parents[1];pin=json.loads((source/'manifests/distribution/native-sources.json').read_text())['llama_swap'];target=self.base/'exact original';target.mkdir()
  for f in MANAGER_FILES:
   p=target/f;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(baseline)/f,p)
  self.assertEqual(apply_manager_source_patches(source,target,pin)['replay'],'PASS')
  for f in pin['patched_source_files']:self.assertEqual(digest(target/f['path']),f['sha256'])

if __name__=='__main__':unittest.main(verbosity=2)
