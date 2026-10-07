# SPDX-License-Identifier: Apache-2.0
"""CPU screenshot selection checks for the current product source tree."""
import ast,hashlib,json,shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from package import eligible
from refresh_public_screenshots import refresh
ROOT=Path(__file__).resolve().parents[2]

class ScreenshotPolicyTests(unittest.TestCase):
 def test_source_selection_and_tree_contain_only_current_readme_screenshots(self):
  policy=json.loads((ROOT/'manifests/distribution/source-allowlist.json').read_text());manifest=json.loads((ROOT/'manifests/distribution/public-screenshots.json').read_text())
  current={'assets/screenshots/welcome-dark.jpg','assets/screenshots/model-management.jpg'}
  self.assertEqual({row['path'] for row in manifest['screenshots']},current)
  for row in manifest['screenshots']:
   self.assertTrue(eligible(row['path'],policy));self.assertEqual(hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest(),row['sha256'])
   self.assertEqual(row['width']*9,row['height']*16)
   self.assertEqual(row['locale'],'en');self.assertEqual(row['theme'],'dark')
  self.assertEqual({str(p.relative_to(ROOT)) for p in (ROOT/'assets/screenshots').glob('*.jpg')},current)
  self.assertFalse(eligible('assets/screenshots/private-avatar.jpg',policy))
 def test_generator_is_idempotent_and_does_not_write_image_files(self):
  with tempfile.TemporaryDirectory(prefix='public screenshot CPU-') as folder:
   source=Path(folder).resolve();(source/'manifests/distribution').mkdir(parents=True)
   for name in ('source-allowlist.json','public-screenshots.json'):shutil.copyfile(ROOT/'manifests/distribution'/name,source/'manifests/distribution'/name)
   for name in ('README.md','README.zh-Hant.md','README.zh-Hans.md','README.ja.md'):shutil.copyfile(ROOT/name,source/name)
   manifest=json.loads((source/'manifests/distribution/public-screenshots.json').read_text())
   hashes={}
   for row in manifest['screenshots']:
    p=source/row['path'];p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/row['path'],p);hashes[row['path']]=p.read_bytes()
   first=refresh(source);before=(source/'manifests/distribution/public-screenshots.json').read_bytes();refresh(source)
   self.assertEqual((source/'manifests/distribution/public-screenshots.json').read_bytes(),before);self.assertFalse(first['images_deleted'])
   for name,data in hashes.items():self.assertEqual((source/name).read_bytes(),data)
 def test_guard_uses_bounded10_second_grace_without_changing_compat_bound(self):
  tree=ast.parse((ROOT/'scripts/distribution/agent.py').read_text())
  command_dict=next(node.value for node in ast.walk(tree) if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='commands' for target in node.targets))
  values={key.value:value for key,value in zip(command_dict.keys,command_dict.values)}
  for name,bound in (('guard','10'),('compat','5')):
   args=values[name].elts;index=next(i for i,node in enumerate(args) if isinstance(node,ast.Constant) and node.value=='--timeout-graceful-shutdown');self.assertEqual(args[index+1].value,bound)
if __name__=='__main__':unittest.main()
