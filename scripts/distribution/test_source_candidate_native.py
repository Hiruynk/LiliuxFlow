"""Source-mode counterexamples; CPU fixtures only, no native/model/service action."""
import argparse,hashlib,json,os,sys,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.append(os.environ.get('LILIUXFLOW_SOURCE_TEST_SUPPORT',str(HERE)))
import native_cleanroom as runner
import test_native_cleanroom as baseline_tests
from common import DistributionError
from unittest.mock import patch
import build_native,trust

class SourceCandidateTests(unittest.TestCase):
 def setUp(self):
  self.fixture=baseline_tests.NativeCleanroomTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.fixture.recipe.update(four_locale_browser_accepted=False,ui_accepted=False,full_GUI_functional_acceptance=False)
  self.fixture.recipe_digest=self.fixture.write_recipe()
 def check(self,source_candidate=True):return runner.reviewed_recipe(self.fixture.source,self.fixture.recipe_path,self.fixture.recipe_digest,source_candidate=source_candidate)
 def test_pending_browser_explicit_source_mode_returns_recipe_without_acceptance_mutation(self):
  before=(self.fixture.source/self.fixture.recipe_path).read_bytes();self.assertEqual(self.check(),self.fixture.source/self.fixture.recipe_path);self.assertEqual((self.fixture.source/self.fixture.recipe_path).read_bytes(),before)
  parsed=json.loads(before);self.assertFalse(parsed['four_locale_browser_accepted']);self.assertFalse(parsed['ui_accepted']);self.assertFalse(parsed['full_GUI_functional_acceptance'])
 def test_pending_browser_default_strict_still_refuses(self):
  with self.assertRaises(DistributionError):self.check(False)
 def test_four_source_locales_still_required(self):
  self.fixture.recipe['supported_locales']=['zh-Hant','zh-Hans','ja'];self.fixture.recipe_digest=self.fixture.write_recipe()
  with self.assertRaises(DistributionError):self.check()
 def test_wrong_recipe_hash_still_refused(self):
  self.fixture.recipe_digest='0'*64
  with self.assertRaises(DistributionError):self.check()
 def test_unfrozen_and_incomplete_source_still_refused(self):
  for field,value in [('state','IN_PROGRESS'),('coverage_state','NOT_RUN')]:
   old=self.fixture.recipe[field];self.fixture.recipe[field]=value;self.fixture.recipe_digest=self.fixture.write_recipe()
   with self.assertRaises(DistributionError):self.check()
   self.fixture.recipe[field]=old
 def test_stock_UI_or_changed_source_asset_still_refused(self):
  self.fixture.recipe['apps']['litellm']['extra_files'][0]['sha256']='0'*64;self.fixture.recipe_digest=self.fixture.write_recipe()
  with self.assertRaises(DistributionError):self.check()
  self.fixture.recipe['apps']['litellm']['extra_files']=[];self.fixture.recipe_digest=self.fixture.write_recipe()
  with self.assertRaises(DistributionError):self.check()
 def test_modes_cannot_change_during_build_verify_smoke(self):
  record={'schema_version':1,'source_commit':self.fixture.commit,'archive_sha256':'c'*64,'ui_recipe_sha256':self.fixture.recipe_digest,'run_id':'612f6576-35c9-4137-89e3-fb79d9c32cd0','ports':dict(runner.PORTS),'source_candidate':True}
  args=argparse.Namespace(expected_source_commit=self.fixture.commit,expected_archive_sha256='c'*64,expected_ui_recipe_sha256=self.fixture.recipe_digest,run_id=record['run_id'],source_candidate=True);runner.match_review(record,args)
  for mode in [False,'true',1,None]:
   with self.assertRaises(DistributionError):runner.match_review(dict(record,source_candidate=mode),args)
  args.source_candidate=False
  with self.assertRaises(DistributionError):runner.match_review(record,args)
 def test_old_strict_receipt_compatible_without_new_option(self):
  record={'schema_version':1,'source_commit':self.fixture.commit,'archive_sha256':'c'*64,'ui_recipe_sha256':self.fixture.recipe_digest,'run_id':'612f6576-35c9-4137-89e3-fb79d9c32cd0','ports':dict(runner.PORTS)}
  args=argparse.Namespace(expected_source_commit=self.fixture.commit,expected_archive_sha256='c'*64,expected_ui_recipe_sha256=self.fixture.recipe_digest,run_id=record['run_id']);runner.match_review(record,args)
 def test_plan_explicit_source_mode_never_grants_acceptance_or_executes(self):
  for action in ['build','verify','smoke']:
   result=runner.stage(argparse.Namespace(execute=False,action=action,source_candidate=True));self.assertTrue(result['source_candidate']);self.assertFalse(result['native_or_model_actions']);self.assertFalse(result['final_UI_browser_acceptance']);self.assertFalse(result['full_GUI_functional_acceptance']);self.assertFalse(result['full_Q4_acceptance'])
 def test_nonboolean_direct_recipe_mode_fails_closed(self):
  for mode in ['true',1,None]:
   with self.assertRaises(DistributionError):self.check(mode)
 def fake_smoke_result(self,mode):
  task=self.fixture.base/('source-mode-smoke'if mode else'strict-mode-smoke');task.mkdir(mode=0o700);data=task/'own data 資料';data.mkdir(mode=0o700)
  source=self.fixture.source;identity='ee50290b-e7a1-4938-a9ee-bb3c358920c8';record={'data_root':str(data),'installation_id':identity,'source_commit':self.fixture.commit,'ui_recipe_sha256':self.fixture.recipe_digest,'source_candidate':mode,'ports':dict(runner.PORTS),'state':'PAYLOAD_VERIFIED'}
  config={'installation_id':identity,'ports':dict(runner.PORTS),'package_source_root':str(source)}
  args=argparse.Namespace(execute=True,action='smoke',source_candidate=mode,expected_source_commit=self.fixture.commit,expected_archive_sha256='c'*64,expected_ui_recipe_sha256=self.fixture.recipe_digest,run_id='612f6576-35c9-4137-89e3-fb79d9c32cd0',task_root=task)
  original={'status':'PASS_NATIVE_Q4_SMOKE','counterexample_fixture':True,'final_UI_browser_acceptance':True,'full_GUI_functional_acceptance':True,'full_Q4_acceptance':True}
  def fake_command(argv,**kwargs):
   evidence=Path(argv[argv.index('--evidence')+1]);evidence.write_text(json.dumps(original));evidence.chmod(0o600);return {'exit_code':0}
  with patch.object(runner,'verify_staged',return_value=(record,source,source/self.fixture.recipe_path)),patch.object(trust,'installation',return_value=config),patch.object(trust,'validate',return_value=self.fixture.trusted),patch.object(trust,'atomic_private_json'),patch.object(build_native,'command',side_effect=fake_command)as command,patch.object(runner,'read_object',return_value=original):
   result=runner.stage(args);self.assertEqual(command.call_count,1)
  return result,original
 def test_strict_success_return_contract_unchanged_with_fake_fixture_no_live_run(self):
  result,original=self.fake_smoke_result(False);self.assertIs(result,original);self.assertTrue(result['full_Q4_acceptance']);self.assertNotIn('source_candidate',result)
 def test_source_success_only_overrides_pending_acceptance_with_fake_fixture_no_live_run(self):
  result,original=self.fake_smoke_result(True);self.assertTrue(result['source_candidate']);self.assertFalse(result['full_Q4_acceptance']);self.assertFalse(result['final_UI_browser_acceptance']);self.assertFalse(result['full_GUI_functional_acceptance']);self.assertTrue(original['full_Q4_acceptance'])
 def test_source_mode_still_refuses_original_and_duplicate_ports(self):
  for ports in [dict(runner.PORTS,litellm=4000),dict(runner.PORTS,manager=runner.PORTS['guard'])]:
   with self.assertRaises(DistributionError):runner.isolated_ports(ports)

if __name__=='__main__':unittest.main(verbosity=2)
