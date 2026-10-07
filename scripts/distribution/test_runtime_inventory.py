"""CPU fixture collection; no native binary, model, DB, listener or production source access."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import DistributionError
from runtime_inventory import Stager,python_installed,npm_installed,contained,rust_packages,go_modules
from build_budget import snapshot

class InventoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='runtime inventory CPU 空間-');self.root=Path(self.tmp.name).resolve();self.stage=Stager(self.root/'output')
 def tearDown(self):self.tmp.cleanup()
 def test_actual_dist_info_metadata_and_original_license_without_description_or_private_values(self):
  site=self.root/'site';info=site/'fixture_pkg-1.2.dist-info';licenses=info/'licenses';licenses.mkdir(parents=True)
  (info/'METADATA').write_text('Metadata-Version: 2.1\nName: fixture-pkg\nVersion: 1.2\nLicense-Expression: MIT\nLicense-File: LICENSE\nLicense-File: NOTICE\nRequires-Dist: fixture-dependency>=2\n\nDescription with synthetic credential should not stage\n');(licenses/'LICENSE').write_text('original fixture license');(licenses/'NOTICE').write_text('original fixture notice')
  components=python_installed(self.stage,site,'compat');self.assertEqual(components[0]['version'],'1.2');self.assertTrue(components[0]['notice_files']);content=(self.stage.root/'python/compat/fixture_pkg-1.2.dist-info/METADATA').read_text();self.assertNotIn('Description',content);self.assertIn('Requires-Dist',content)
 def test_npm_packages_keep_build_scope_and_do_not_relicense(self):
  package=self.root/'node_modules/fixture';package.mkdir(parents=True);(package/'package.json').write_text(json.dumps({'name':'fixture','version':'1.0','license':'MIT','scripts':{'postinstall':'private fixture no execution'}}));(package/'LICENSE').write_text('original npm notice')
  result=npm_installed(self.stage,self.root/'node_modules','ui');self.assertEqual(result[0]['declared_license'],'MIT');self.assertIn('dev/unlinked',result[0]['input_scope']);self.assertFalse((self.stage.root/'package.json').exists())
 def test_symlink_escape_and_traversal_refused_before_read(self):
  source=self.root/'source';source.mkdir();outside=self.root/'outside';outside.write_text('not copied');link=source/'LICENSE';link.symlink_to(outside)
  with self.assertRaises(DistributionError):self.stage.add_file(source,link,'notice/LICENSE',kind='fixture')
  with self.assertRaises(DistributionError):self.stage.add_bytes('../escape',b'x',kind='fixture')
  with self.assertRaises(DistributionError):contained(source,outside)
 def test_rust_offline_graph_and_foreign_path_rejection(self):
  build=self.root/'build';package=build/'lily';package.mkdir(parents=True);(package/'Cargo.toml').write_text('[package]\nname="fixture"\nversion="1.0.0"\n');(package/'LICENSE').write_text('original Rust fixture notice');cargo_home=self.root/'cargo';cargo_home.mkdir()
  record={'resolve':{'nodes':[]},'packages':[{'name':'fixture','version':'1.0.0','license':'MIT','license_file':None,'manifest_path':str(package/'Cargo.toml')}]}
  with patch('runtime_inventory.metadata_command',return_value=json.dumps(record).encode()) as command:
   result=rust_packages(self.stage,build,Path('/fixture/cargo'),cargo_home,{})
   self.assertTrue(result[0]['notice_files']);self.assertIn('--offline',command.call_args.args[0]);self.assertIn('--locked',command.call_args.args[0])
  record['packages'][0]['manifest_path']=str(self.root/'foreign/Cargo.toml')
  with patch('runtime_inventory.metadata_command',return_value=json.dumps(record).encode()):
   with self.assertRaises(DistributionError):rust_packages(self.stage,build,Path('/fixture/cargo'),cargo_home,{})
 def test_go_binary_module_info_records_actual_module_scope_without_execution(self):
  build=self.root/'go-build';module=build/'go-mod-cache/example.org/module@v1.2.3';module.mkdir(parents=True);(module/'LICENSE').write_text('original Go fixture notice')
  text=b'fixture-bin: go1.27.1\n\tdep\texample.org/module\tv1.2.3\th1:synthetic\n'
  with patch('runtime_inventory.metadata_command',return_value=text) as command:
   result=go_modules(self.stage,build,Path('/fixture/go'),Path('/fixture/manager'),{})
   self.assertEqual(result[0]['version'],'v1.2.3');self.assertTrue(result[0]['notice_files']);self.assertEqual(command.call_args.args[0][1:3],['version','-m'])
 def test_stage_never_overwrites_and_bounds_source_size(self):
  self.stage.add_bytes('metadata.txt',b'first',kind='fixture')
  with self.assertRaises(DistributionError):self.stage.add_bytes('metadata.txt',b'changed',kind='fixture')
  huge=self.root/'too-large';huge.write_bytes(b'12345')
  with self.assertRaises(DistributionError):self.stage.add_file(self.root,huge,'huge',kind='fixture',limit=4)
 def test_budget_separates_runtime_and_immutable_payload_from_transient_roots(self):
  build=self.root/'build';cache=self.root/'cache';runtime=self.root/'runtime';immutable=self.root/'archive';
  for folder in [build,cache,runtime,immutable]:folder.mkdir();(folder/'file').write_bytes(b'x'*4096)
  first=snapshot([build,cache],runtime=runtime,immutable_source=immutable);(runtime/'large').write_bytes(b'x'*32768);second=snapshot([build,cache],runtime=runtime,immutable_source=immutable)
  self.assertEqual(first['transient_build_payload_cache_bytes'],second['transient_build_payload_cache_bytes']);self.assertGreater(second['runtime_bytes_observed_not_evicted'],first['runtime_bytes_observed_not_evicted']);self.assertFalse(second['automatic_deletion'])
  with self.assertRaises(DistributionError):snapshot([build,build/'nested'])
if __name__=='__main__':unittest.main()
