"""CPU generated-route contract only; no API, DB, listener, model, or existing installation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agent import configs
from profile_registry import load_registry, parse_registry
from common import DistributionError

class RoutingTests(unittest.TestCase):
 def render(self,alias,enabled=1):
  with tempfile.TemporaryDirectory(prefix='routing CPU Unicode 空間-') as folder:
   data=Path(folder).resolve();(data/'run').mkdir(mode=0o700)
   document=json.loads((Path(__file__).resolve().parents[2]/'profiles/distribution/context-registry.json').read_text())
   document['profiles'][0]['public_alias']=alias
   for index,row in enumerate(document['profiles']):row['production_enabled']=index<enabled
   trusted={'data_root':data,'config':{'ports':{'guard':18080,'manager':18081,'litellm':14000}},'registry':parse_registry(document),'secrets':{'MANAGER_BACKEND_TOKEN':'synthetic-not-live'},'source_root':data/'source','binaries':{'compat_python':data/'runtime/compat-python'}}
   manager,lp=configs(trusted);return json.loads(manager.read_text()),json.loads(lp.read_text())
 def test_safe64k_upstream_selects_registered_manager_key_before_lily_rewrite(self):
  alias='qwen3.8-flash-next-lily-q4-64k';manager,lp=self.render(alias);route=lp['model_list'][0];selected=route['litellm_params']['model'].removeprefix('openai/')
  self.assertIn(selected,manager['models']);self.assertEqual(selected,alias);self.assertEqual(route['model_name'],alias);self.assertEqual(manager['models'][selected]['useModelName'],'Qwen3.8-Flash-Next');self.assertNotIn('Qwen3.8-Flash-Next',manager['models'])
 def test_noncanonical_public_alias_is_rejected_by_finite_registry(self):
  with self.assertRaises(DistributionError):self.render('fixture-alternate-public-alias')
 def test_incremental_enabled_routes_have_exact_metadata_and_independent_timeouts(self):
  alias='qwen3.8-flash-next-lily-q4-64k'
  for enabled in (1,2,3):
   manager,lp=self.render(alias,enabled);self.assertEqual(len(lp['model_list']),enabled);self.assertEqual(len(manager['models']),enabled)
   for route,(tokens,seconds) in zip(lp['model_list'],((65536,3672),(131072,4272),(262144,4872))):
    name=route['model_name'];self.assertEqual(manager['models'][name]['capabilities']['context'],tokens);self.assertEqual(route['model_info']['max_tokens'],tokens);self.assertEqual(route['model_info']['default_output_tokens'],65536);self.assertEqual(route['litellm_params']['timeout'],seconds+30);self.assertEqual(route['litellm_params']['stream_timeout'],seconds+30);self.assertIn('--profile ctx',manager['models'][name]['cmd'])
   self.assertNotIn('qwen38-flash-next-q4-safe64k',manager['models'])
   self.assertEqual(lp['router_settings']['timeout'],(3672,4272,4872)[enabled-1]+30)
 def test_caller_guard_endpoint_no_fallback_context_and_auto_boundaries_preserved(self):
  alias='qwen3.8-flash-next-lily-q4-64k';manager,lp=self.render(alias);params=lp['model_list'][0]['litellm_params'];self.assertEqual(params['api_base'],'http://127.0.0.1:18080/v1');self.assertEqual(params['api_key'],'os.environ/MANAGER_BACKEND_TOKEN');self.assertNotIn('LITELLM_MASTER_KEY',params.values());self.assertEqual(lp['router_settings']['num_retries'],0);self.assertEqual(lp['router_settings']['max_fallbacks'],0);self.assertEqual(lp['router_settings']['fallbacks'],[]);self.assertFalse(lp['general_settings']['allow_client_side_credentials']);self.assertTrue(lp['general_settings']['cancel_on_disconnect']);self.assertEqual(manager['models'][alias]['capabilities']['context'],65536);self.assertTrue(manager['models'][alias]['capabilities']['disableAuto']);self.assertEqual(manager['models'][alias]['proxy'],'http://127.0.0.1:${PORT}')

if __name__=='__main__':unittest.main()
