"""Trusted runtime copy counterexamples only; no compiler/download/model or service."""
import json,hashlib,os,sys,tempfile,unittest,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.append(os.environ.get('LILIUXFLOW_TOOL_TEST_SUPPORT',str(HERE)))
import build_native as build
from common import DistributionError
from ui_recipe import git_environment
class NodeCopyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='node-tool-cpu-');self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name).resolve();self.origin=self.base/'origin';self.data=self.base/'data';self.data.mkdir(mode=0o700)
  for rel,body in [('bin/node','synthetic node'),('lib/node_modules/npm/bin/npm-cli.js','synthetic npm'),('lib/node_modules/npm/bin/npx-cli.js','synthetic npx'),('lib/node_modules/corepack/dist/corepack.js','synthetic corepack')]:
   p=self.origin/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
  for rel,target in [('bin/nodejs','node'),('bin/npm','../lib/node_modules/npm/bin/npm-cli.js'),('bin/npx','../lib/node_modules/npm/bin/npx-cli.js'),('bin/corepack','../lib/node_modules/corepack/dist/corepack.js')]:(self.origin/rel).symlink_to(target)
 def run_copy(self):return build.install_project_node(self.data,self.origin/'bin/node')
 def test_vendor_internal_four_aliases_regular_and_npm_cli_unchanged(self):
  before=(self.origin/'lib/node_modules/npm/bin/npm-cli.js').read_bytes();record=self.run_copy();target=self.data/'runtime/toolchains/node';self.assertEqual((target/'bin/node').read_text(),'synthetic node');self.assertEqual((target/'bin/nodejs').read_text(),'synthetic node');self.assertEqual((target/'lib/node_modules/npm/bin/npm-cli.js').read_bytes(),before);self.assertIn('exec', (target/'bin/npm').read_text());self.assertFalse(any(p.is_symlink()for p in target.rglob('*')));self.assertTrue((self.origin/'bin/npm').is_symlink());self.assertEqual(set(record),{'node','npm','npm_cli'})
 def test_absolute_bin_link_refused(self):
  p=self.origin/'bin/npm';p.unlink();p.symlink_to(self.origin/'lib/node_modules/npm/bin/npm-cli.js')
  with self.assertRaises(DistributionError):self.run_copy()
 def test_relative_outside_origin_refused(self):
  p=self.origin/'bin/npm';p.unlink();p.symlink_to('../../outside')
  with self.assertRaises(DistributionError):self.run_copy()
 def test_directory_link_refused(self):
  (self.origin/'linked-dir').symlink_to(self.origin/'lib',target_is_directory=True)
  with self.assertRaises(DistributionError):self.run_copy()
 def test_link_cycle_refused(self):
  p=self.origin/'bin/npm';p.unlink();p.symlink_to('npx');q=self.origin/'bin/npx';q.unlink();q.symlink_to('npm')
  with self.assertRaises(DistributionError):self.run_copy()
 def test_unknown_bin_link_refused(self):
  (self.origin/'bin/unknown').symlink_to('node')
  with self.assertRaises(DistributionError):self.run_copy()
 def test_wrong_internal_canonical_target_refused(self):
  p=self.origin/'bin/npm';p.unlink();p.symlink_to('node')
  with self.assertRaises(DistributionError):self.run_copy()
 def test_existing_target_never_overwritten(self):
  self.run_copy()
  with self.assertRaises(DistributionError):self.run_copy()
 def test_git_environment_restores_patch_under_ancestor_repo(self):
  subprocess.run(['git','init','-q',str(self.base)],check=True);(self.base/'root.txt').write_text('tracked root fixture');subprocess.run(['git','add','root.txt'],cwd=self.base,check=True);child=self.base/'child';child.mkdir();(child/'file.txt').write_text('before\n');patch=self.base/'small.patch';patch.write_text('diff --git a/file.txt b/file.txt\n--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-before\n+after\n');self.assertEqual(subprocess.run(['git','rev-parse','--show-prefix'],cwd=child,capture_output=True,text=True,check=True).stdout.strip(),'child/');subprocess.run(['git','apply',str(patch)],cwd=child,check=True);self.assertEqual((child/'file.txt').read_text(),'before\n');subprocess.run(['git','apply',str(patch)],cwd=child,env=git_environment(child),check=True);self.assertEqual((child/'file.txt').read_text(),'after\n')
if __name__=='__main__':unittest.main(verbosity=2)
