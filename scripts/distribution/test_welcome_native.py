# SPDX-License-Identifier: Apache-2.0
"""CPU-only isolated Welcome routing, finite assets and trust counterexamples."""
import copy,hashlib,importlib.util,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parent))
import welcome_native as native
import patch_litellm_profiles as profiles
import trust,test_runtime
from common import DistributionError
from profile_registry import REGISTRY_PATH,parse_registry,legacy_registry
from welcome_profiles import render_html
ROOT=Path(__file__).resolve().parents[2]
SOURCE=b'from fastapi import FastAPI\ndef _get_docs_url(): return "/"\napp = FastAPI(\n    docs_url=_get_docs_url(),\n)\n# original API/auth/UI remains verbatim\n'
spec=importlib.util.spec_from_file_location('reviewed_welcome_runtime',ROOT/'patches/litellm-welcome/runtime.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
class WelcomeTests(unittest.TestCase):
 def test_exact_app_patch_and_finite_reviewed_assets(self):
  result=native.patch_source(SOURCE);self.assertIn(b'# original API/auth/UI remains verbatim',result)
  self.assertIn(b'docs_url=_liliuxflow_welcome.docs_url(_get_docs_url()),',result)
  with self.assertRaises(ValueError):native.patch_source(result)
  self.assertEqual({r['path'] for r in native.assets_inventory(ROOT/'assets/welcome')},native.ASSETS)
  self.assertEqual(native.ASSETS,runtime.ASSETS)
 def test_routes_preserve_auth_ui_and_csp(self):
  with tempfile.TemporaryDirectory(prefix='welcome CPU-') as name:
   base=Path(name).resolve();proxy=base/'proxy_server.py';proxy.write_bytes(SOURCE);shutil.copytree(ROOT/'assets/welcome',base/native.PACKAGE)
   welcome=runtime.Welcome(proxy);app=FastAPI(docs_url=welcome.docs_url('/'));welcome.install(app)
   @app.get('/v1/models')
   def models(authorization:str|None=Header(default=None)):
    if authorization!='Bearer synthetic-caller':raise HTTPException(401,'Unauthorized')
    return {'data':[]}
   @app.get('/ui/login')
   def login():return {'fixture':True}
   client=TestClient(app)
   self.assertEqual(client.get('/').status_code,200);self.assertEqual(client.get('/api-docs').status_code,200)
   self.assertEqual(client.get('/v1/models').status_code,401);self.assertEqual(client.get('/v1/models',headers={'Authorization':'Bearer synthetic-caller'}).status_code,200)
   self.assertEqual(client.get('/ui/login').json(),{'fixture':True})
   for asset in native.ASSETS:
    response=client.get('/liliuxflow-welcome/'+asset);self.assertEqual(response.status_code,200);self.assertIn("connect-src 'none'",response.headers['content-security-policy'])
   self.assertEqual(client.get('/liliuxflow-welcome/fonts/secret.json').status_code,404)
 def test_highest_enabled_render_keeps_api_default(self):
  document=json.loads((ROOT/REGISTRY_PATH).read_text())
  for enabled,limit in ((1,65536),(2,131072),(3,262144)):
   current=copy.deepcopy(document)
   for index,row in enumerate(current['profiles']):row['production_enabled']=index<enabled
   result,receipt=render_html((ROOT/'assets/welcome/index.html').read_text(),parse_registry(current))
   self.assertIn(f'data-context-tokens="{limit}"',result);self.assertEqual(receipt['api_default_model'],'qwen3.8-flash-next-lily-q4-64k')
 def test_new_runtime_install_only_reviewed_assets(self):
  with tempfile.TemporaryDirectory(prefix='welcome install CPU-') as name:
   data=Path(name).resolve();proxy=data/'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/proxy_server.py';proxy.parent.mkdir(parents=True);proxy.write_bytes(SOURCE)
   after=native.patch_source(SOURCE);real_sha=native.sha
   def fixture_sha(value):
    if value==SOURCE:return native.BEFORE_SHA
    if value==after:return '0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8'
    return real_sha(value)
   with patch.object(native,'sha',fixture_sha):record=native.install_welcome(ROOT,data)
   self.assertEqual(len(record['assets']),9)
   for row in record['assets']:self.assertEqual(hashlib.sha256((data/row['path']).read_bytes()).hexdigest(),row['sha256'])
   with self.assertRaises(DistributionError):native.install_welcome(ROOT,data)
class WelcomeTrustTests(unittest.TestCase):
 def setUp(self):
  self.fixture=test_runtime.RuntimeTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.data=self.fixture.data;self.source=self.fixture.source;self.record=self.fixture.record
  for name in ('welcome_native.py','welcome_profiles.py'):
   shutil.copyfile(ROOT/'scripts/distribution'/name,self.source/'scripts/distribution'/name)
  helper=self.source/'patches/litellm-welcome/runtime.py';helper.parent.mkdir(parents=True);shutil.copyfile(ROOT/'patches/litellm-welcome/runtime.py',helper)
  shutil.copytree(ROOT/'assets/welcome',self.source/'assets/welcome');shutil.copyfile(ROOT/'manifests/distribution/welcome-assets.json',self.source/'manifests/distribution/welcome-assets.json')
  paths=['scripts/distribution/welcome_native.py','scripts/distribution/welcome_profiles.py','patches/litellm-welcome/runtime.py','manifests/distribution/welcome-assets.json']+[p.relative_to(self.source).as_posix() for p in (self.source/'assets/welcome').rglob('*') if p.is_file()]
  for name in paths:self.record['source_files'].append({'path':name,'sha256':trust.sha256(self.source/name)})
  prefix='runtime/litellm/lib/python3.12/site-packages/litellm/proxy/';runtime_root=self.data/prefix;runtime_root.mkdir(parents=True)
  shutil.copyfile(helper,runtime_root/native.HELPER);shutil.copytree(self.source/'assets/welcome',runtime_root/native.PACKAGE)
  index=runtime_root/native.PACKAGE/'index.html';index.write_text(render_html(index.read_text(),legacy_registry(trust.PROFILE))[0])
  module=runtime_root/'_liliuxflow_context_profiles.py';shutil.copyfile(ROOT/'scripts/distribution/litellm_profile_policy.py',module)
  source_module=self.source/'scripts/distribution/litellm_profile_policy.py';shutil.copyfile(ROOT/'scripts/distribution/litellm_profile_policy.py',source_module);self.record['source_files'].append({'path':'scripts/distribution/litellm_profile_policy.py','sha256':trust.sha256(source_module)})
  proxy=runtime_root/'proxy_server.py';proxy.write_text((profiles.MARKER+'\n')*3)
  self.record['litellm_profile_policy']={'module':{'path':prefix+module.name,'sha256':trust.sha256(module)},'proxy':{'path':prefix+'proxy_server.py','sha256':trust.sha256(proxy),'before_sha256':'0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8'},'catalog_filters':3}
  self.record['welcome']={'helper':{'path':prefix+native.HELPER,'sha256':trust.sha256(runtime_root/native.HELPER)},'assets':[{'path':prefix+native.PACKAGE+'/'+p.relative_to(runtime_root/native.PACKAGE).as_posix(),'sha256':trust.sha256(p)} for p in (runtime_root/native.PACKAGE).rglob('*') if p.is_file()],'proxy_before_sha256':native.BEFORE_SHA,'proxy_after_sha256':'0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8'};self.save()
 def save(self):trust.atomic_private_json(self.data/'runtime/release-trust.json',self.record)
 def test_matches_finite_sources_and_profile(self):self.assertTrue(trust.validate(self.data)['registry'].default.production_enabled)
 def test_unknown_asset_path_is_refused(self):
  self.record['welcome']['assets'][0]['path']='runtime/private/secret.json';self.save()
  with self.assertRaises(DistributionError):trust.validate(self.data)
if __name__=='__main__':unittest.main()
