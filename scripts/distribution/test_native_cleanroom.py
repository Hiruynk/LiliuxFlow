"""CPU counterexamples only. No port bind, launchd, PG, build, checksum or real inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import DistributionError
import native_cleanroom as runner
import native_smoke as smoke
import agent
ROOT=Path(__file__).resolve().parents[2]

class NativeCleanroomTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='native CPU fixture Unicode 測試 space-');self.base=Path(self.tmp.name).resolve();self.source=self.base/'source';self.source.mkdir();self.data=self.base/'data';self.data.mkdir(mode=0o700);(self.data/'secrets').mkdir(mode=0o700)
  target=self.source/'manifests/distribution';target.mkdir(parents=True)
  for name in ['native-sources.json','source-allowlist.json']:(target/name).write_bytes((ROOT/'manifests/distribution'/name).read_bytes())
  pins=json.loads((target/'native-sources.json').read_text());self.recipe={'schema_version':1,'state':'FROZEN_FOR_REVIEW','coverage_state':'SOURCE_COMPLETE_BROWSER_PENDING','supported_locales':['en','zh-Hant','zh-Hans','ja'],'browser_locales':['en','zh-Hant','zh-Hans','ja'],'four_locale_browser_accepted':True,'apps':{}}
  for name,key,prefix in [('litellm','litellm','ui/litellm-dashboard'),('llama-swap','llama_swap','ui')]:
   file=self.source/('patches/ui/'+name+'.txt');file.parent.mkdir(parents=True,exist_ok=True);file.write_text('synthetic fixture display text')
   self.recipe['apps'][name]={'upstream_commit':pins[key]['commit'],'archive_sha256':pins[key]['archive_sha256'],'patches':[],'extra_files':[{'source':str(file.relative_to(self.source)),'target':prefix+'/fixture.txt','sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'before_sha256':None}],'lockfiles':[{'path':prefix+'/package-lock.json','sha256':'a'*64}]}
  self.recipe_path='manifests/distribution/ui-recipe-context-profiles.json';self.commit='b'*40;self.recipe_digest=self.write_recipe()
  self.trusted={'checkpoint':{'payload_verified':True},'trust':{'source_commit':self.commit,'ui_recipe_sha256':self.recipe_digest},'config':{'ports':dict(runner.PORTS),'installation_id':'ee50290b-e7a1-4938-a9ee-bb3c358920c8'},'data_root':self.data,'secrets':{'LITELLM_MASTER_KEY':'synthetic-master-never-used'}}
 def tearDown(self):self.tmp.cleanup()
 def write_recipe(self):
  file=self.source/self.recipe_path;file.write_text(json.dumps(self.recipe));return hashlib.sha256(file.read_bytes()).hexdigest()
 def test_complete_hash_bound_source_recipe_accepts_only_source_not_browser(self):
  self.assertEqual(runner.reviewed_recipe(self.source,self.recipe_path,self.recipe_digest),self.source/self.recipe_path)
 def test_missing_recipe_and_wrong_hash_refused(self):
  for relative,digest in [('manifests/distribution/missing.json',self.recipe_digest),(self.recipe_path,'0'*64)]:
   with self.assertRaises((DistributionError,OSError)):runner.reviewed_recipe(self.source,relative,digest)
 def test_unfrozen_or_incomplete_recipe_refused(self):
  for field,value in [('state','IN_PROGRESS'),('coverage_state','NOT_RUN'),('coverage_state','SOURCE_CANDIDATE_BROWSER_PENDING')]:
   before=self.recipe[field];self.recipe[field]=value;digest=self.write_recipe()
   with self.assertRaises(DistributionError):runner.reviewed_recipe(self.source,self.recipe_path,digest)
   self.recipe[field]=before
 def test_stock_UI_cannot_substitute_for_localized_frozen_recipe(self):
  self.recipe['apps']['litellm']['extra_files']=[];digest=self.write_recipe()
  with self.assertRaises(DistributionError):runner.reviewed_recipe(self.source,self.recipe_path,digest)
 def test_recipe_asset_outside_allowlist_refused(self):
  self.recipe['apps']['litellm']['extra_files'][0]['source']='var/private/extra.txt';digest=self.write_recipe()
  with self.assertRaises(DistributionError):runner.reviewed_recipe(self.source,self.recipe_path,digest)
 def test_old_three_locale_or_source_only_approval_cannot_grant_final_native_scope(self):
  for field,value in [('supported_locales',['zh-Hant','zh-Hans','ja']),('browser_locales',['zh-Hant','zh-Hans','ja']),('four_locale_browser_accepted',False),('four_locale_browser_accepted','true')]:
   old=self.recipe[field];self.recipe[field]=value;digest=self.write_recipe()
   with self.assertRaises(DistributionError):runner.reviewed_recipe(self.source,self.recipe_path,digest)
   self.recipe[field]=old
 def test_original_or_duplicate_ports_refused(self):
  for ports in [dict(runner.PORTS,litellm=4000),dict(runner.PORTS,manager=runner.PORTS['guard'])]:
   with self.assertRaises(DistributionError):runner.isolated_ports(ports)
 def test_old_archive_source_recipe_or_run_receipt_refused(self):
  args=argparse.Namespace(expected_source_commit=self.commit,expected_archive_sha256='c'*64,expected_ui_recipe_sha256=self.recipe_digest,run_id='612f6576-35c9-4137-89e3-fb79d9c32cd0')
  record={'schema_version':1,'source_commit':self.commit,'archive_sha256':'c'*64,'ui_recipe_sha256':self.recipe_digest,'run_id':args.run_id,'ports':dict(runner.PORTS)};runner.match_review(record,args)
  for key,value in [('source_commit','d'*40),('archive_sha256','d'*64),('ui_recipe_sha256','d'*64),('run_id','e'*36)]:
   with self.assertRaises(DistributionError):runner.match_review(dict(record,**{key:value}),args)
 def test_metadata_false_or_flag_cannot_count_real_Q4(self):
  for checkpoint in [{'payload_verified':False},{'payload_verified':True,'validation':'metadata_only'}]:
   with patch.object(smoke.subprocess,'run') as command:
    with self.assertRaises(DistributionError):smoke.preflight(dict(self.trusted,checkpoint=checkpoint),self.commit,self.recipe_digest)
    command.assert_not_called()
 def test_duplicate_model_lease_refused_before_listener_or_initialize(self):
  lease=self.base/'gpu.lease';lease.mkdir()
  with patch.object(smoke,'model_lease_path',return_value=lease),patch.object(smoke.subprocess,'run') as command:
   with self.assertRaises(DistributionError):smoke.preflight(self.trusted,self.commit,self.recipe_digest)
   command.assert_not_called()
 def test_occupied_or_unknown_listener_refused_without_signaling(self):
  for code in (0,2):
   with patch.object(smoke,'model_lease_path',return_value=self.base/'absent'),patch.object(smoke.subprocess,'run',return_value=argparse.Namespace(returncode=code)),patch.object(agent,'initialize') as initialize:
    with self.assertRaises(DistributionError):smoke.preflight(self.trusted,self.commit,self.recipe_digest)
    initialize.assert_not_called()
 def test_caller_identity_or_master_key_inference_refused(self):
  file=self.data/'secrets/caller.json';file.write_text(json.dumps({'user_id':'different-caller'}));file.chmod(0o600)
  with patch.object(smoke,'request_parts') as request:
   with self.assertRaises(DistributionError):smoke.inference_parts(self.trusted,'openai',False)
   request.assert_not_called()
  file.write_text(json.dumps({'user_id':'local-'+self.trusted['config']['installation_id']}))
  with patch.object(smoke,'request_parts',return_value=('http://127.0.0.1:14000/v1/chat/completions',{},self.trusted['secrets']['LITELLM_MASTER_KEY'])):
   with self.assertRaises(DistributionError):smoke.inference_parts(self.trusted,'openai',False)
 def test_legacy_terminal_counterexamples(self):
  valid=b'data: {"done":false,"message":{"thinking":"synthetic","content":"OK"}}\n\ndata: {"done":true,"done_reason":"length"}\n\n';self.assertEqual(smoke.parse_legacy_sse(valid)['terminal_count'],1)
  for raw in [b'',valid+valid,valid.replace(b'"length"',b'"other"'),valid.replace(b'"done":false',b'"done":"false"'),valid.replace(b'"done":true',b'"done":true,"error":"fixture"')]:
   with self.assertRaises(DistributionError):smoke.parse_legacy_sse(raw)
 def test_reasoning_only_or_malformed_OpenAI_cannot_pass_usable_smoke(self):
  base={'usage':{'completion_tokens':4096},'choices':[{'finish_reason':'length','message':{'reasoning_content':'synthetic','content':None}}]}
  for response in [base,dict(base,choices=['leaked-choice']),dict(base,choices=[{'finish_reason':'stop','message':{'content':'  '}}])]:
   with patch.object(smoke,'inference_parts',return_value=('http://127.0.0.1:14000/v1/chat/completions',{},'synthetic-caller')),patch.object(agent,'http_json',return_value=(200,response)) as request:
    with self.assertRaises(DistributionError):smoke.openai(self.trusted)
    self.assertEqual(request.call_args.kwargs['payload']['max_tokens'],4096)
 def test_legacy_reasoning_only_choices_or_OpenAI_done_cannot_pass(self):
  for raw in [b'data: {"done":false,"message":{"thinking":"synthetic","content":""}}\n\ndata: {"done":true,"done_reason":"length"}\n\n',b'data: [DONE]\n\n',b'data: {"choices":[{"delta":{"content":"synthetic"}}]}\n\n']:
   with self.assertRaises(DistributionError):smoke.parse_legacy_sse(raw)
 def test_legacy_nonstream_JSON_HTML_empty_cannot_pass_plain_text_contract(self):
  self.assertEqual(smoke.nonstream_contract('text/plain',b'OK')['visible_content_characters'],2)
  for content_type,raw in [('application/json',b'{"choices":[]}'),('text/html',b'<h1>fixture</h1>'),('text/plain',b''),('text/plain',b'  '),('text/plain',b'\xff')]:
   with self.assertRaises(DistributionError):smoke.nonstream_contract(content_type,raw)
 def test_start_readiness_bound_refuses_invalid_before_trust_or_mutation(self):
  for bound in [True,0,181,'30']:
   with patch.object(agent,'validate') as trusted:
    with self.assertRaises(DistributionError):agent.start(self.data,readiness_seconds=bound)
    trusted.assert_not_called()
 def test_failed_initialize_still_attempts_owned_stop_and_keeps_failure_evidence(self):
  with patch.object(smoke,'validate',return_value=self.trusted),patch.object(smoke,'preflight'),patch.object(agent,'initialize',side_effect=DistributionError('synthetic firstpoint')),patch.object(agent,'stop',return_value={'state':'already_stopped'}) as stop,patch.object(smoke.subprocess,'run',return_value=argparse.Namespace(returncode=1)),patch.object(smoke,'openai') as inference:
   result=smoke.check(self.data,source_commit=self.commit,recipe_sha256=self.recipe_digest,evidence=self.base/'CPU-failure-only.json')
   self.assertEqual(result['status'],'FAIL');self.assertEqual(result['first_failure_stage'],'initialize');stop.assert_called_once_with(self.data);inference.assert_not_called()
 def test_default_plan_performs_no_model_or_host_actions(self):
  with patch.object(runner,'verify_archive') as archive:
   result=runner.stage(argparse.Namespace(execute=False,action='smoke'));archive.assert_not_called();self.assertFalse(result['full_Q4_acceptance'])
class ClientCatalogTests(unittest.TestCase):
 def setUp(self):
  import client_example
  self.client=client_example
  self.temp=tempfile.TemporaryDirectory(prefix='client CPU fixture ');self.addCleanup(self.temp.cleanup)
  self.data=Path(self.temp.name).resolve();(self.data/'secrets').mkdir(mode=0o700)
  self.config={'ports':{'litellm':14000,'compat':18001}}
  self.key='sk-synthetic-client-fixture'
  self.mtp2=['qwen3.8-flash-next-lily-q4-mtp2-128k','qwen3.8-flash-next-lily-q4-mtp2-262k']
  self.caller={'api_base':'http://127.0.0.1:14000/v1','compat_base':'http://127.0.0.1:18001','alias':self.client.ALIAS,'api_key':self.key,'models':[self.client.ALIAS]+self.mtp2}
  self.write_caller()
 def write_caller(self):
  file=self.data/'secrets/caller.json';file.write_text(json.dumps(self.caller));file.chmod(0o600)
 def test_true_client_default_and_both_shared_aliases_reach_only_configured_loopback(self):
  import contextlib,io,types
  for model in (None,*self.mtp2):
   for api in ('openai','legacy'):
    with self.subTest(model=model,api=api):
     argv=['client_example','--data-root',str(self.data),'--api',api,'--stream']
     if model:argv+=['--model',model]
     response=types.SimpleNamespace(read1=unittest.mock.Mock(side_effect=[b'CPU response',b'']))
     output=io.StringIO()
     with patch.object(self.client,'installation',return_value=self.config),patch.object(sys,'argv',argv),patch.object(self.client,'build_opener') as opener,contextlib.redirect_stdout(output):
      opener.return_value.open.return_value.__enter__.return_value=response
      self.assertEqual(self.client.main(),0)
     request=opener.return_value.open.call_args.args[0]
     expected='http://127.0.0.1:14000/v1/chat/completions' if api=='openai' else 'http://127.0.0.1:18001/api/chat'
     self.assertEqual(request.full_url,expected);self.assertEqual(request.get_header('Authorization'),'Bearer '+self.key)
     body=json.loads(request.data);self.assertEqual(body['model'],model or self.client.ALIAS);self.assertTrue(body['stream'])
     self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],self.client.catalog_profile(body['model']).total_deadline_seconds+50)
     self.assertIn('CPU response',output.getvalue());self.assertNotIn(self.key,output.getvalue())
 def test_unknown_alias_and_missing_or_wildcard_grants_refuse_before_HTTP(self):
  import contextlib,io
  cases=[('unknown-model',self.caller['models']),(self.mtp2[0],[self.client.ALIAS]),(self.mtp2[0],['*']),(self.mtp2[0],[])]
  for model,grants in cases:
   with self.subTest(model=model,grants=grants):
    self.caller['models']=grants;self.write_caller()
    with patch.object(self.client,'installation',return_value=self.config),patch.object(sys,'argv',['client_example','--data-root',str(self.data),'--api','openai','--model',model]),patch.object(self.client,'build_opener') as opener,contextlib.redirect_stdout(io.StringIO()):
     self.assertEqual(self.client.main(),2)
    opener.assert_not_called()
 def test_server_rejection_is_reported_without_fallback_or_estimated_success(self):
  import contextlib,io
  from urllib.error import HTTPError
  output=io.StringIO()
  argv=['client_example','--data-root',str(self.data),'--api','openai','--model',self.mtp2[0]]
  with patch.object(self.client,'installation',return_value=self.config),patch.object(sys,'argv',argv),patch.object(self.client,'build_opener') as opener,contextlib.redirect_stdout(output):
   opener.return_value.open.side_effect=HTTPError('http://127.0.0.1:14000/v1/chat/completions',403,'CPU refused',{},None)
   self.assertEqual(self.client.main(),2)
  opener.return_value.open.assert_called_once();self.assertEqual(json.loads(output.getvalue()),{'state':'api_error','status':403})
  self.assertNotIn(self.key,output.getvalue())
 def test_endpoint_default_alias_key_and_private_file_checks_preserved(self):
  for field,value in (('api_base','https://foreign.invalid/v1'),('compat_base','http://127.0.0.1:8001'),('alias','unknown-model'),('api_key','not-a-virtual-key')):
   with self.subTest(field=field):
    original=self.caller[field];self.caller[field]=value;self.write_caller()
    with patch.object(self.client,'installation',return_value=self.config),self.assertRaises(DistributionError):self.client.request_parts(self.data,'openai',False,'CPU prompt',self.mtp2[0])
    self.caller[field]=original
  self.write_caller();(self.data/'secrets/caller.json').chmod(0o644)
  with patch.object(self.client,'installation',return_value=self.config),self.assertRaises(DistributionError):self.client.request_parts(self.data,'openai',False,'CPU prompt',self.mtp2[0])

class SharedInstallationCLIParserTests(unittest.TestCase):
 def test_documented_sequence_dispatches_actual_parser_without_mutations(self):
  import liliuxflow as cli
  # Parse the whole documented setup-to-grant sequence; only mutation handlers
  # are replaced. No listener, build, DB initialization or live model is used.
  calls=[('setup',['setup','--model-dir','/CPU/checkpoint','--port','litellm=14000']),
         ('build_runtime',['build','--optin-engine','latest13f-defer-pc123-mtp2-shared-context-v1','--ui-manifest','manifests/distribution/ui-recipe-mtp2-shared.json','--execute']),
         ('verify_model',['verify-model']),('initialize_runtime',['initialize','--execute']),
         ('doctor',['doctor']),('require_runtime',['start','--mtp2-shared-context']),('status',['status']),
         ('profiles',['profiles','list','--mtp2-shared-context']),
         ('profiles',['profiles','info','ctx128k-mtp2','--mtp2-shared-context']),
         ('profiles',['profiles','grant-owner','ctx128k-mtp2','--mtp2-shared-context','--dry-run']),
         ('profiles',['profiles','grant-owner','ctx128k-mtp2','--mtp2-shared-context','--execute']),
         ('profiles',['profiles','grant-owner','ctx262k-mtp2','--mtp2-shared-context','--execute']),
         ('profiles',['profiles','grant-ui-user','my-ui-user','ctx128k-mtp2','ctx262k-mtp2','--mtp2-shared-context','--execute'])]
  for handler,arguments in calls:
   with self.subTest(arguments=arguments),patch.object(sys,'argv',['liliuxflow','--data-root','/CPU/shared',*arguments]),patch.object(cli,handler,return_value=0) as dispatch,patch.object(cli.os,'umask'):
    self.assertEqual(cli.main(),0)
    parsed=dispatch.call_args.args[0];self.assertEqual(parsed.data_root,Path('/CPU/shared'))
    self.assertEqual(parsed.command,arguments[0])
    if '--mtp2-shared-context' in arguments:self.assertTrue(parsed.mtp2_shared_context)
    if '--execute' in arguments:self.assertTrue(parsed.execute)
    if '--dry-run' in arguments:self.assertTrue(parsed.dry_run)
    if parsed.command=='build':self.assertEqual(parsed.optin_engine,'latest13f-defer-pc123-mtp2-shared-context-v1');self.assertEqual(parsed.ui_manifest,Path('manifests/distribution/ui-recipe-mtp2-shared.json'))

if __name__=='__main__':unittest.main()
