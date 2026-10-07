"""CPU replay checks on tiny synthetic UI snapshots; no UI build or acceptance claim."""
import hashlib,json,os,subprocess,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import DistributionError
from ui_recipe import replay,source_tree_digest
ROOT=Path(__file__).resolve().parents[2]

class RecipeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='liliuxflow-ui-recipe-');self.base=Path(self.tmp.name).resolve();self.src=self.base/'source';self.src.mkdir();self.roots={}
  target=self.src/'manifests/distribution';target.mkdir(parents=True)
  for name in ('native-sources.json','source-allowlist.json'):(target/name).write_bytes((ROOT/'manifests/distribution'/name).read_bytes())
  pins=json.loads((target/'native-sources.json').read_text());self.recipe={'schema_version':1,'state':'PROPOSAL','coverage_state':'NOT_RUN','apps':{}}
  for app,prefix,key in [('litellm','ui/litellm-dashboard','litellm'),('llama-swap','ui','llama_swap')]:
   root=self.base/app;self.roots[app]=root;(root/prefix).mkdir(parents=True)
   (root/prefix/'title.txt').write_text('Stock\n');(root/prefix/'package-lock.json').write_text('{}\n')
   patch=self.src/'patches/ui'/ (app+'.patch');patch.parent.mkdir(parents=True,exist_ok=True)
   patch.write_text('diff --git a/'+prefix+'/title.txt b/'+prefix+'/title.txt\n--- a/'+prefix+'/title.txt\n+++ b/'+prefix+'/title.txt\n@@ -1 +1 @@\n-Stock\n+Localized\n')
   self.recipe['apps'][app]={'upstream_commit':pins[key]['commit'],'archive_sha256':pins[key]['archive_sha256'],'patches':[{'path':str(patch.relative_to(self.src)),'sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'strip':1}],'extra_files':[],'lockfiles':[{'path':prefix+'/package-lock.json','sha256':hashlib.sha256(b'{}\n').hexdigest()}]}
  self.manifest=self.src/'recipe.json'
 def tearDown(self):self.tmp.cleanup()
 def run_recipe(self):
  self.manifest.write_text(json.dumps(self.recipe));return replay(self.src,self.roots,self.manifest)
 def test_full_upstream_paths_replay_in_arbitrary_data_root(self):
  result=self.run_recipe();self.assertEqual(result['replay'],'PASS');self.assertFalse(result['ui_locale_acceptance']);self.assertEqual((self.roots['litellm']/'ui/litellm-dashboard/title.txt').read_text(),'Localized\n')
 def test_bad_patch_hash_refused_before_any_replay(self):
  self.recipe['apps']['llama-swap']['patches'][0]['sha256']='0'*64
  with self.assertRaises(DistributionError):self.run_recipe()
  self.assertEqual((self.roots['litellm']/'ui/litellm-dashboard/title.txt').read_text(),'Stock\n')
 def test_patch_outside_ui_root_refused(self):
  patch=self.src/'patches/ui/litellm.patch';patch.write_text(patch.read_text().replace('ui/litellm-dashboard/title.txt','services/auth.py'));self.recipe['apps']['litellm']['patches'][0]['sha256']=hashlib.sha256(patch.read_bytes()).hexdigest()
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_secret_extra_source_refused(self):
  file=self.src/'secrets/key.env';file.parent.mkdir();file.write_text('synthetic')
  self.recipe['apps']['litellm']['extra_files']=[{'source':'secrets/key.env','target':'ui/litellm-dashboard/public/extra.txt','sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_extra_target_absolute_and_traversal_refused(self):
  file=self.src/'patches/ui/extra.txt';file.write_text('safe synthetic')
  for target in ('/outside','ui/litellm-dashboard/../../auth.py'):
   self.recipe['apps']['litellm']['extra_files']=[{'source':str(file.relative_to(self.src)),'target':target,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]
   with self.assertRaises(DistributionError):self.run_recipe()
 def test_uppercase_secret_source_directory_refused(self):
  file=self.src/'patches/ui/Secrets/extra.json';file.parent.mkdir();file.write_text('{}')
  self.recipe['apps']['litellm']['extra_files']=[{'source':str(file.relative_to(self.src)),'target':'ui/litellm-dashboard/public/extra.json','sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_lock_identity_checked_after_replay(self):
  self.recipe['apps']['litellm']['lockfiles'][0]['sha256']='0'*64
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_link_patch_rejected(self):
  file=self.src/'patches/ui/litellm.patch';file.write_text(file.read_text().replace('--- a/','new file mode 120000\n--- a/'));self.recipe['apps']['litellm']['patches'][0]['sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_nested_ancestor_git_and_inherited_git_environment_cannot_skip_patch(self):
  subprocess.run(['git','init','-q',str(self.base)],check=True)
  with patch.dict(os.environ,{'GIT_DIR':str(self.base/'.git'),'GIT_WORK_TREE':str(self.base)}):result=self.run_recipe()
  self.assertEqual(result['replay'],'PASS')
  for name,prefix in [('litellm','ui/litellm-dashboard'),('llama-swap','ui')]:self.assertEqual((self.roots[name]/prefix/'title.txt').read_text(),'Localized\n')
 def test_inventory_inside_ancestor_git_with_unicode_spaces_and_inherited_env(self):
  from ui_recipe import patch_paths
  inventory_patch=self.src/'patches/ui/space 日本語.patch';inventory_patch.write_bytes((self.src/'patches/ui/litellm.patch').read_bytes())
  ancestor=self.base/'unrelated Git 日本語';ancestor.mkdir();subdir=ancestor/'child directory 空間';subdir.mkdir()
  subprocess.run(['git','init','-q',str(ancestor)],check=True)
  prior=Path.cwd()
  try:
   os.chdir(subdir)
   with patch.dict(os.environ,{'GIT_DIR':str(ancestor/'.git'),'GIT_WORK_TREE':str(ancestor),'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.ignorecase','GIT_CONFIG_VALUE_0':'true'}):
    self.assertEqual(patch_paths(inventory_patch,1),['ui/litellm-dashboard/title.txt'])
  finally:os.chdir(prior)
 def test_full_source_tree_hash_rejects_skipped_or_changed_source_with_same_lock(self):
  self.recipe['apps']['litellm']['source_tree_sha256']=source_tree_digest(self.roots['litellm'],'litellm')
  with self.assertRaises(DistributionError):self.run_recipe()
 def test_extra_cannot_silently_overwrite_source(self):
  file=self.src/'patches/ui/extra.txt';file.write_text('replacement')
  self.recipe['apps']['litellm']['extra_files']=[{'source':str(file.relative_to(self.src)),'target':'ui/litellm-dashboard/title.txt','sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]
  with self.assertRaises(DistributionError):self.run_recipe()

if __name__=='__main__':unittest.main(verbosity=2)
