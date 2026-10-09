"""Actual portable builder/trust/runner/probe wiring on bounded synthetic CPU inputs."""
from pathlib import Path
import copy,hashlib,json,os,shutil,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT
PRODUCT=ROOT
sys.path[:0]=[str(ROOT/'scripts/distribution'),str(PRODUCT/'scripts/distribution')]
from common import DistributionError
import optin_engine as e,native_recipe as recipe,build_native,profile_registry as profiles,trust,model_runner,runtime_proof,native_metadata

class ActualWiringTests(unittest.TestCase):
 def document(self):return json.loads((ROOT/profiles.REGISTRY_PATH).read_text())
 def record(self):return e.generated_record({'source_commit':e.LATEST_COMMIT,'patch_sha256':list(e.PATCHES),'source_inventory_sha256':e.SOURCE_SHA},'a'*64)
 def selected(self):return profiles.parse_registry(self.document(),enabled_optin_profiles=['ctx64k-mtp2'])
 def trusted(self,data):
  record=self.record();engine=e.from_generated_trust(record)
  return {'registry':self.selected(),'engines':{e.OPT64:engine},'data_root':data,
          'config':{'installation_id':'cpu-own-install','model_dir':'/synthetic/readonly-Q4','ports':{'guard':18080}},
          'binaries':{'lily':Path('/synthetic/old-lily'),'lily_opt64':Path('/synthetic/opt-lily')},
          'trust':{'binaries':{'lily':{'sha256':'b'*64},'lily_opt64':{'sha256':'a'*64}}}}
 def state(self,root):
  t=self.trusted(root);profile=t['registry'].by_id('ctx64k-mtp2');engine=e.engine_for(t,profile)
  owner={'schema_version':1,'installation_id':'cpu-own-install','profile_id':profile.profile_id,
         'public_alias':profile.public_alias,'context_tokens':65536,'binary_sha256':'a'*64,'backend_port':19111,
         'runner':{'pid':123,'uid':os.getuid()},'child':{'pid':124,'ppid':123,'uid':os.getuid()},
         'startup_id':'a'*32,'argv_sha256':'c'*64,**e.record_fields(engine)}
  state=model_runner.LaneState(root/'state.json',owner)
  state.consume('LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=bf16 mtp_drafts=2 max_batch=1')
  state.consume('LILY_QSA_ROUTE_EFFECTIVE requested=split route=split')
  state.consume('LILY_QSA_DISPATCH_METADATA sparse_prefill_rows=4096 route=split split_dispatch_count=1 query_dispatch_count=0')
  return t,profile,state
 def cancel(self,state,request='req-123-2',drop=True):
  state.consume(f'[lily-meta] request_id={request} phase=queue_enter')
  state.consume(f'[lily-meta] request_id={request} phase=lane_acquired queue_wait_ms=0')
  state.consume(f'[lily-meta] request_id={request} phase=prefill_progress completed_chunks=1 prefilled_tokens=4096')
  if drop:state.consume(f'[lily-meta] request_id={request} phase=prefill_cancelled stage=prefill_chunk completed_chunks=2 prefilled_tokens=8192 session_disposition=dropped')
  state.consume(f'[lily-meta] request_id={request} phase=lane_released cancelled=true lane_ms=1')
 def probe(self,root,t,before,after):
  probe=runtime_proof.RunnerResourceProbe(t,lease_root=root/'lease-root');probe.baseline=before
  # Mock kernel-owner syscall results only. Keep all scalar/native/queue checks in the actual proof.
  return probe,patch.object(probe,'_state',return_value=after),patch.object(probe,'_owned',return_value=True)
 def test_normal_builder_finite_optin_plan_keeps_default_and_never_executes(self):
  lock=json.loads((ROOT/'manifests/distribution/native-sources.json').read_text())
  self.assertEqual(recipe.select_lily_recipe(lock)[0],lock['lily'])
  with tempfile.TemporaryDirectory() as tmp,patch.object(build_native,'installation',return_value={'source_root':str(ROOT)}),patch.object(build_native,'resolve_source_commit',return_value='d'*40),patch.object(build_native,'download') as fetch,patch.object(build_native,'command') as compile:
   old=build_native.build(Path(tmp).resolve());new=build_native.build(Path(tmp).resolve(),optin_engine=e.OPT64)
   self.assertIsNone(old['optin_engine']);self.assertEqual(old['source_commits'],new['source_commits'])
   self.assertEqual(new['optin_source_commit'],e.LATEST_COMMIT);self.assertEqual(new['existing_default_engine'],recipe.LEGACY_ENGINE)
   with self.assertRaises(DistributionError):build_native.build(Path(tmp).resolve(),optin_engine='arbitrary')
   fetch.assert_not_called();compile.assert_not_called()
 def test_fresh_normal_candidate_plan_has_no_fabricated_installed_runtime(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(build_native,'installation',return_value={'source_root':str(ROOT),'runtime_state':'NOT_BUILT'}),patch.object(build_native,'resolve_source_commit',return_value='d'*40),patch.object(build_native,'command') as compile,patch.object(build_native,'download') as fetch:
   plan=build_native.build_optin_source_candidate(Path(tmp).resolve())
   self.assertFalse(plan['installed_runtime_ready']);self.assertFalse(plan['production_enabled']);self.assertFalse(plan['frontend_build'])
   self.assertEqual(plan['native_source_commit'],e.LATEST_COMMIT);compile.assert_not_called();fetch.assert_not_called()
 def test_normal_recipe_baseline_binds_all235_and_actual_four_patch_files(self):
  lock=json.loads((ROOT/'manifests/distribution/native-sources.json').read_text())
  pin,baseline=recipe.select_lily_recipe(lock,e.OPT64)
  rows=json.loads((ROOT/baseline).read_text())['files']
  self.assertEqual(len(rows),235)
  values={row['path']:row['sha256'] for row in rows}
  self.assertEqual(len(values),235)
  self.assertEqual(hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest(),e.SOURCE_SHA)
  for row in pin['patches']:self.assertEqual(trust.sha256(ROOT/row['path']),row['sha256'])
 def test_normal_patch_replay_uses_hash_bound_small_CPU_fixture(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp).resolve();source=base/'source';target=base/'upstream';target.mkdir();source.mkdir()
   file=target/'main.rs';file.write_text('fn before() {}\n')
   patchfile=source/'tiny.patch';patchfile.write_text('--- a/main.rs\n+++ b/main.rs\n@@ -1 +1 @@\n-fn before() {}\n+fn after() {}\n')
   baseline=source/'manifests/distribution/lily-source-baseline.json';baseline.parent.mkdir(parents=True)
   baseline.write_text(json.dumps({'files':[{'path':'main.rs','sha256':hashlib.sha256(b'fn after() {}\n').hexdigest()}]}))
   lock={'lily':{'commit':e.LEGACY_COMMIT,'patches':[{'path':'tiny.patch','sha256':trust.sha256(patchfile),'unidiff_zero':False}]}}
   self.assertEqual(recipe.apply_lily_source_patches(source,target,lock)['replay'],'PASS')
   self.assertEqual(file.read_text(),'fn after() {}\n')
 def test_generated_engine_trust_rejects_wrong_or_missing_bindings(self):
  lock=json.loads((ROOT/'manifests/distribution/native-sources.json').read_text());record={'optin_engines':{e.OPT64:self.record()},'enabled_optin_profiles':['ctx64k-mtp2']}
  self.assertEqual(e.validate_engine_records(record,lock)[e.OPT64].source_commit,e.LATEST_COMMIT)
  for mutation in [lambda x:x['optin_engines'][e.OPT64].update(default=True),lambda x:x['optin_engines'][e.OPT64].update(source_inventory_sha256='b'*64),lambda x:x.update(enabled_optin_profiles=[]),lambda x:x.pop('enabled_optin_profiles'),lambda x:x.update(optin_engines={}),lambda x:x.pop('optin_engines'),lambda x:x.update(enabled_optin_profiles=['ctx262k']),lambda x:x.update(enabled_optin_profiles=['ctx64k-mtp2','ctx64k-mtp2']),lambda x:x.update(enabled_optin_profiles=[{}]),lambda x:x['optin_engines'][e.OPT64].update(extra_args=['--memory-limit-gb','8'])]:
   value=copy.deepcopy(record);mutation(value)
   with self.assertRaises(DistributionError):e.validate_engine_records(value,lock)
 def test_generated_engine_trust_accepts_only_ordered_finite_nonempty_profile_selection(self):
  lock=json.loads((ROOT/'manifests/distribution/native-sources.json').read_text())
  for selected in [['ctx64k-mtp2'],['ctx128k-mtp2'],['ctx64k-mtp2','ctx128k-mtp2'],list(e.OPT_CONTEXTS)]:
   value={'optin_engines':{e.OPT64:self.record()},'enabled_optin_profiles':selected}
   self.assertEqual(e.validate_engine_records(value,lock)[e.OPT64].binary_sha256,'a'*64)
  self.assertEqual(e.validate_engine_records({},lock),{})
  for selected in [['ctx128k-mtp2','ctx64k-mtp2'],['ctx128k-mtp2','ctx128k-mtp2'],['ctx128k'],['*']]:
   value={'optin_engines':{e.OPT64:self.record()},'enabled_optin_profiles':selected}
   with self.assertRaises(DistributionError):e.validate_engine_records(value,lock)
 def test_source_registry_cannot_enable_candidate_without_generated_trust(self):
  document=self.document();registry=profiles.parse_registry(document)
  self.assertEqual([r.profile_id for r in registry.enabled_profiles],['ctx64k','ctx128k','ctx262k'])
  with self.assertRaises(DistributionError):registry.by_id('ctx64k-mtp2')
  enabled=self.selected();self.assertEqual(enabled.default.profile_id,'ctx64k');self.assertEqual(enabled.default.as_dict()['mtp_drafts'],0)
  candidate=enabled.resolve(profiles.OPT64_ALIAS);self.assertEqual(candidate.as_dict()['mtp_drafts'],2)
  for key,value in [('context_tokens',131072),('max_sessions',4),('cache_bytes',12*1024**3),('engine_id','arbitrary'),('production_enabled',True)]:
   bad=copy.deepcopy(document);bad['optin_profiles'][0][key]=value
   with self.assertRaises(DistributionError):profiles.parse_registry(bad,enabled_optin_profiles=['ctx64k-mtp2'])
 def test_actual_runner_selects_two_binary_versions_without_flag_or_context_drift(self):
  with tempfile.TemporaryDirectory() as tmp:
   t=self.trusted(Path(tmp).resolve());old=model_runner.lily_argv(t,19000);new=model_runner.lily_argv(t,19000,profile_id='ctx64k-mtp2')
   self.assertEqual(old[0],'/synthetic/old-lily');self.assertEqual(old[old.index('--mtp-drafts')+1],'0');self.assertIn('--max-images',old);self.assertNotIn('--thinking',old)
   self.assertEqual(new[0],'/synthetic/opt-lily');self.assertNotIn('--max-images',new)
   for flag,value in {'--max-seq':'65536','--mtp-drafts':'2','--max-batch':'1','--kv-cache':'bf16','--thinking-budget':'off','--ngram-preload':'true','--cache-bytes':str(8*1024**3),'--disk-cache-bytes':'0'}.items():self.assertEqual(new[new.index(flag)+1],value)
   self.assertEqual(t['registry'].maximum_images,64)
   t['engines']={}
   with self.assertRaises(DistributionError):model_runner.lily_argv(t,19000,profile_id='ctx64k-mtp2')
 def test_actual_parser_and_proof_positive_cancel_require_sameid_drop_balanced_queue(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();t,profile,state=self.state(root);before=copy.deepcopy(state.record);self.cancel(state)
   probe,sp,op=self.probe(root,t,before,state.record)
   with sp,op:self.assertTrue(probe._probe(profile,'generation_cancelled'))
   for change in [{'cancelled_session_dropped':False},{'last_cancelled_request_id':'req-123-1'},{'proof_valid':False},{'queue_terminal_sequence':1},{'queued_request_ids':['req-123-99']},{'released_sequence':0},{'startup_id':'b'*32}]:
    after=copy.deepcopy(state.record);after.update(change);probe,sp,op=self.probe(root,t,before,after)
    with sp,op:self.assertFalse(probe._probe(profile,'generation_cancelled'))
 def test_actual_parser_rejects_capped0_context_kv_and_invalid_private_env_text(self):
  for line in ['LILY_ENGINE_EFFECTIVE context_tokens=131072 kv_cache=bf16 mtp_drafts=2 max_batch=1','LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=bf16 mtp_drafts=0 max_batch=1','LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=q8 mtp_drafts=2 max_batch=1','LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=bf16 mtp_drafts=2 max_batch=4','LILY_QSA_ROUTE_EFFECTIVE requested=PRIVATE_PROMPT route=split']:
   with self.subTest(line=line),tempfile.TemporaryDirectory() as tmp:
    state=model_runner.LaneState(Path(tmp).resolve()/'state.json',{'engine_id':e.OPT64,'profile_id':'ctx64k-mtp2','context_tokens':65536})
    self.assertFalse(state.consume(line));self.assertFalse(state.record['proof_valid']);self.assertNotIn('PRIVATE_PROMPT',state.path.read_text())
 def test_exact_queue_rejection_terminal_has_no_live_lane_and_no_false_release(self):
  with tempfile.TemporaryDirectory() as tmp:
   t,profile,state=self.state(Path(tmp).resolve())
   state.consume('[lily-meta] request_id=req-123-2 phase=queue_enter')
   state.consume('[lily-meta] request_id=req-123-2 phase=queue_rejected status=503 queue_slot_released=true')
   self.assertEqual(state.record['queued_request_ids'],[]);self.assertEqual(state.record['active_lane_ids'],[])
   self.assertEqual(state.record['acquired_sequence'],0);self.assertEqual(state.record['released_sequence'],0)
   probe=runtime_proof.RunnerResourceProbe(t);self.assertTrue(probe._runtime_matches(state.record))

class ActualTrustBoundaryTests(unittest.TestCase):
 def setUp(self):
  # Reuse the product's synthetic installation fixture; its credentials are invented CPU strings.
  import test_runtime
  self.fixture=test_runtime.RuntimeTests('test_complete_synthetic_trust_validates_without_live_resources');self.fixture.setUp()
  selected=['scripts/distribution/'+name for name in ('build_native.py','native_recipe.py','optin_engine.py','native_metadata.py','profile_registry.py','model_runner.py','runtime_proof.py','trust.py')]
  selected+=['manifests/distribution/native-sources.json','manifests/distribution/lily-opt64-source-baseline.json',profiles.REGISTRY_PATH,'services/compat/src/compat_api/llama_guard.py']
  selected+=[row['path'] for row in json.loads((ROOT/'manifests/distribution/native-sources.json').read_text())['lily_opt64']['patches']]
  for name in selected:
   f=ROOT/name;rel=f.relative_to(ROOT);target=self.fixture.source/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,target)
   self.fixture.record['source_files'].append({'path':str(rel),'sha256':trust.sha256(target)})
  # Copy only the ordinary unchanged policy source needed by a hash-bound registry.
  for name in ('litellm_profile_policy.py','patch_litellm_profiles.py'):
   target=self.fixture.source/'scripts/distribution'/name;shutil.copyfile(PRODUCT/'scripts/distribution'/name,target)
   self.fixture.record['source_files'].append({'path':str(target.relative_to(self.fixture.source)),'sha256':trust.sha256(target)})
  registry=self.fixture.source/profiles.REGISTRY_PATH
  self.fixture.record['profile_registry']={'path':profiles.REGISTRY_PATH,'sha256':trust.sha256(registry)}
  prefix='runtime/litellm/lib/python3.12/site-packages/litellm/proxy/'
  module=self.fixture.data/prefix/'_liliuxflow_context_profiles.py';module.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(PRODUCT/'scripts/distribution/litellm_profile_policy.py',module)
  proxy=module.with_name('proxy_server.py');proxy.write_text('# LILIUXFLOW_CONTEXT_PROFILE_CATALOG_V1\n'*3)
  self.fixture.record['litellm_profile_policy']={'module':{'path':str(module.relative_to(self.fixture.data)),'sha256':trust.sha256(module)},'proxy':{'path':str(proxy.relative_to(self.fixture.data)),'sha256':trust.sha256(proxy),'before_sha256':'8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7'},'catalog_filters':3}
  binary=self.fixture.data/'runtime/bin/lily-opt64';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o700)
  self.fixture.record['binaries']['lily_opt64']={'path':'runtime/bin/lily-opt64','sha256':trust.sha256(binary)}
  self.fixture.record['optin_engines']={e.OPT64:e.generated_record({'source_commit':e.LATEST_COMMIT,'patch_sha256':list(e.PATCHES),'source_inventory_sha256':e.SOURCE_SHA},trust.sha256(binary))}
  self.fixture.record['source_files']=[{'path':rel,'sha256':trust.sha256(self.fixture.source/rel)} for rel in sorted({v['path'] for v in self.fixture.record['source_files']})]
  self.fixture.record['engine_contract_support']={'schema_version':1}
  self.fixture.record['enabled_optin_profiles']=['ctx64k-mtp2'];self.save()
 def save(self):trust.atomic_private_json(self.fixture.data/'runtime/release-trust.json',self.fixture.record)
 def tearDown(self):self.fixture.tearDown()
 def test_actual_trust_validate_enables_only_generated_extra_binary_and_alias(self):
  result=trust.validate(self.fixture.data,require_checkpoint=False)
  self.assertEqual(result['registry'].default.profile_id,'ctx64k');self.assertEqual(len(result['registry'].enabled_profiles),4)
  profile=result['registry'].resolve(profiles.OPT64_ALIAS);self.assertEqual(profile.engine_id,e.OPT64)
  self.assertEqual(result['engines'][e.OPT64].binary_sha256,trust.sha256(result['binaries']['lily_opt64']))
 def test_actual_trust_refuses_binary_source_missing_registry_or_private_arg_drift(self):
  original=copy.deepcopy(self.fixture.record)
  mutations=[lambda r:r['optin_engines'][e.OPT64].update(binary_sha256='a'*64),lambda r:r['optin_engines'][e.OPT64].update(extra_args=['--memory-limit-gb','8']),lambda r:r.pop('profile_registry'),lambda r:r.update(source_files=[v for v in r['source_files'] if v['path']!='scripts/distribution/native_metadata.py']),lambda r:r['binaries']['lily_opt64'].update(path='runtime/bin/lily')]
  for mutate in mutations:
   self.fixture.record=copy.deepcopy(original);mutate(self.fixture.record);self.save()
   with self.assertRaises(DistributionError):trust.validate(self.fixture.data,require_checkpoint=False)
 def test_versioned_legacy_receipt_must_hash_every_imported_security_helper(self):
  original=copy.deepcopy(self.fixture.record)
  for path in ['scripts/distribution/optin_engine.py','scripts/distribution/native_metadata.py','scripts/distribution/native_recipe.py']:
   self.fixture.record=copy.deepcopy(original);self.fixture.record.pop('optin_engines');self.fixture.record.pop('enabled_optin_profiles');self.fixture.record['binaries'].pop('lily_opt64')
   self.fixture.record['source_files']=[row for row in self.fixture.record['source_files'] if row['path']!=path];self.save()
   with self.subTest(path=path),self.assertRaises(DistributionError):trust.validate(self.fixture.data,require_checkpoint=False)
  self.fixture.record=copy.deepcopy(original);self.fixture.record.pop('engine_contract_support');self.save()
  with self.assertRaises(DistributionError):trust.validate(self.fixture.data,require_checkpoint=False)
 def test_incremental_native_only_plan_preserves_old_binary_and_never_calls_UI_or_model(self):
  self.fixture.record.pop('optin_engines');self.fixture.record.pop('enabled_optin_profiles');self.fixture.record['binaries'].pop('lily_opt64');(self.fixture.data/'runtime/bin/lily-opt64').unlink();self.save()
  with patch.object(build_native,'command') as compile,patch.object(build_native,'download') as fetch:
   plan=build_native.build_optin_candidate(self.fixture.data)
   self.assertEqual(plan['enabled_optin_profiles'],['ctx64k-mtp2']);self.assertFalse(plan['frontend_build']);self.assertFalse(plan['model_loaded'])
   compile.assert_not_called();fetch.assert_not_called()
  result=trust.validate(self.fixture.data,require_checkpoint=False)
  self.assertEqual(result['registry'].default.profile_id,'ctx64k');self.assertEqual(len(result['registry'].enabled_profiles),3)
 def test_actual_legacy_trust_keeps_three_profiles_and_cannot_admit_opt64(self):
  self.fixture.record.pop('optin_engines');self.fixture.record.pop('enabled_optin_profiles');self.fixture.record['binaries'].pop('lily_opt64');self.save()
  result=trust.validate(self.fixture.data,require_checkpoint=False)
  self.assertEqual(len(result['registry'].enabled_profiles),3)
  with self.assertRaises(DistributionError):result['registry'].resolve(profiles.OPT64_ALIAS)




import asyncio,importlib.util,types,httpx
import runtime_proof as proof
from common import private_directory
from trust import atomic_private_json
import optin_engine as engine_module

class CpuContract:
    SHADOW_ID='cpu-own-cancel-install'
    PC3_SHA='a'*64
    @staticmethod
    def shadow_trusted():
        registry=profiles.parse_registry(json.loads((ROOT/profiles.REGISTRY_PATH).read_text()),enabled_optin_profiles=['ctx64k-mtp2'])
        engine=engine_module.from_generated_trust({'engine_id':engine_module.OPT64,'source_commit':engine_module.LATEST_COMMIT,
          'patch_sha256':list(engine_module.PATCHES),'source_inventory_sha256':engine_module.SOURCE_SHA,'binary_sha256':'a'*64,'default':False})
        return {'registry':registry,'engines':{engine_module.OPT64:engine},'data_root':OUT,
                'config':{'installation_id':CpuContract.SHADOW_ID},
                'trust':{'binaries':{'lily':{'sha256':'b'*64},'lily_opt64':{'sha256':'a'*64}}}}
    @staticmethod
    def contract_for(trusted):
        return types.SimpleNamespace(record_fields=lambda:engine_module.record_fields(trusted['engines'][engine_module.OPT64]))
c=CpuContract
import runtime_proof as proof
from common import private_directory
from trust import atomic_private_json
path=ROOT/'services/compat/src/compat_api/llama_guard.py'
spec=importlib.util.spec_from_file_location('private_cancel_guard_cpu',path)
guard_module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=guard_module;spec.loader.exec_module(guard_module)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=OUT);self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name);self.trusted=c.shadow_trusted();self.profile=self.trusted['registry'].by_id('ctx64k-mtp2')
        self.probe=proof.RunnerResourceProbe(self.trusted,run_root=self.data,lease_root=self.data/'leases')
        identity=lambda pid,parent:{'pid':pid,'ppid':parent,'pgid':101,'uid':os.getuid(),'started':'CPU fake birth','command_sha256':'a'*64}
        self.before={'schema_version':1,'installation_id':c.SHADOW_ID,'runner':identity(101,100),'child':identity(102,101),
          'profile_id':'ctx64k-mtp2','public_alias':self.profile.public_alias,'context_tokens':65536,'backend_port':5810,
          'binary_sha256':c.PC3_SHA,'startup_id':'a'*32,'argv_sha256':'b'*64,**c.contract_for(self.trusted).record_fields(),
          'proof_valid':True,'stderr_closed':False,'child_exited':False,'active_lane_ids':[],'queued_request_ids':[],
          'acquired_sequence':8,'released_sequence':8,'event_sequence':16,'queue_entered_sequence':8,
          'queue_acquired_sequence':8,'queue_terminal_sequence':0,'queue_event_sequence':16,
          'last_acquired_request_id':'req-100-8','last_released_request_id':'req-100-8',
          'last_cancelled_request_id':None,'last_release_cancelled':False,'cancelled_session_dropped':False,
          'native_context_tokens':65536,'native_engine_effective':{'context_tokens':65536,'kv_cache':'bf16','mtp_drafts':2,'max_batch':1},
          'qsa_route_effective':{'requested':'split','route':'split'},
          'qsa_dispatch_metadata':{'sparse_prefill_rows':4096,'route':'split','split_dispatch_count':1,'query_dispatch_count':0}}
        self.after={**copy.deepcopy(self.before),'acquired_sequence':9,'released_sequence':9,'event_sequence':18,
          'queue_entered_sequence':9,'queue_acquired_sequence':9,'queue_event_sequence':18,
          'last_acquired_request_id':'req-100-9','last_released_request_id':'req-100-9',
          'last_cancelled_request_id':'req-100-9','last_release_cancelled':True,'cancelled_session_dropped':True}
        self.probe.baseline=copy.deepcopy(self.before)
        private_directory(self.probe.lease)
        atomic_private_json(self.probe.lease/'lease.json',self.after)
        atomic_private_json(self.data/'model-state.json',self.after)
    def check(self):
        atomic_private_json(self.data/'model-state.json',self.after)
        with patch.object(proof,'unchanged',return_value=True):return self.probe._probe(self.profile,'generation_cancelled')
    def test_exact_live_same_owner_plus_one_same_ID_DROP_and_empty_queue_pass(self):self.assertTrue(self.check())
    def test_missing_DROP_old_ID_no_release_invalid_metadata_or_pending_queue_fail(self):
        for key,value in [('cancelled_session_dropped',False),('last_cancelled_request_id','req-100-8'),
          ('last_release_cancelled',False),('released_sequence',8),('proof_valid',False),
          ('queued_request_ids',['req-100-10']),('active_lane_ids',['req-100-9']),('stderr_closed',True),
          ('child_exited',True),('native_context_tokens',131072),('queue_terminal_sequence',1)]:
            saved=copy.deepcopy(self.after);self.after[key]=value
            with self.subTest(field=key):self.assertFalse(self.check())
            self.after=saved
        self.probe.baseline['proof_valid']=False;self.assertFalse(self.check())
    def test_all_old_ID_replay_even_balanced_plus_one_refused(self):
        for key in ('last_acquired_request_id','last_released_request_id','last_cancelled_request_id'):self.after[key]='req-100-8'
        self.assertFalse(self.check())
    def test_new_start_PID_argv_profile_or_live_lease_mismatch_refused(self):
        for key,value in [('startup_id','b'*32),('argv_sha256','c'*64),('profile_id','ctx128k'),
                          ('child',{**self.after['child'],'started':'new birth'})]:
            saved=copy.deepcopy(self.after);self.after[key]=value
            with self.subTest(field=key):self.assertFalse(self.check())
            self.after=saved
        (self.probe.lease/'lease.json').unlink();self.assertFalse(self.check())
    def test_missing_baseline_or_unforwarded_no_lane_never_claim_cancelled(self):
        self.probe.baseline=None;self.assertFalse(self.check())
        self.probe.baseline=copy.deepcopy(self.before);self.after=copy.deepcopy(self.before);self.assertFalse(self.check())


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.profile=c.shadow_trusted()['registry'].by_id('ctx64k-mtp2');self.phases=[];self.stops=[];self.proven=True
        async def callback(profile,phase):self.phases.append(phase);return self.proven
        self.guard=guard_module._ContextLifecycleGuard(manager_url='http://127.0.0.1:8091',control_token='CPU',backend_token='CPU backend',transport=None,
            registry=c.shadow_trusted()['registry'],resource_probe=callback)
        self.guard.proof_wait_seconds=.04;self.guard.cleanup_seconds=.3
        async def stop(profile,deadline):self.stops.append(profile.profile_id);self.guard._resident_profile=None
        self.guard._stop_and_prove=stop
        self.ticket=guard_module._ProfileTicket(self.profile,asyncio.get_running_loop().create_future(),asyncio.get_running_loop().time()+1,forwarded=True,caller_cancelled=True)
        self.guard._ticket=self.ticket;self.guard._active_inferences=1;self.guard._resident_profile=self.profile
    def pending(self):
        ticket=guard_module._ProfileTicket(self.profile,asyncio.get_running_loop().create_future(),asyncio.get_running_loop().time()+1)
        self.guard._pending.append(ticket);return ticket
    async def test_cancel_before_headers_with_proof_keeps_resident_and_releases_FIFO(self):
        following=self.pending()
        await self.guard._finish(self.ticket,None,complete=False)
        self.assertEqual(self.phases,['generation_cancelled']);self.assertEqual(self.stops,[])
        self.assertIs(self.guard._resident_profile,self.profile);self.assertIs(self.guard._ticket,following)
        self.assertTrue(following.granted.result());self.assertEqual(self.guard._last_release_method,'native_cancelled_session_drop')
    async def test_false_or_error_cancel_proof_uses_existing_owned_exit_before_FIFO(self):
        for error in (False,True):
            self.phases=[];self.stops=[];self.guard._ticket=self.ticket;self.proven=False
            async def callback(profile,phase):
                self.phases.append(phase)
                if error:raise RuntimeError('CPU collector failed')
                return False
            self.guard.resource_probe=callback
            await self.guard._finish(self.ticket,None,complete=False)
            self.assertIn('generation_cancelled',self.phases);self.assertEqual(self.stops,['ctx64k-mtp2'])
            self.assertEqual(self.guard._last_release_method,'owned_child_exit')
    async def test_unknown_error_non200_close_failure_or_no_cancel_never_reuses(self):
        class BadClose:
            is_success=True
            async def aclose(self):raise RuntimeError('CPU close failed')
        for failure,response,cancel in [(True,None,True),(False,httpx.Response(500),True),
                                        (False,BadClose(),True),(False,None,False)]:
            self.phases=[];self.stops=[];self.guard._ticket=self.ticket
            self.ticket.generation_failed=failure;self.ticket.caller_cancelled=cancel
            await self.guard._finish(self.ticket,response,complete=False)
            self.assertNotIn('generation_cancelled',self.phases);self.assertEqual(self.stops,['ctx64k-mtp2'])
    async def test_owned_exit_failure_retains_permit_and_pauses_admission(self):
        self.proven=False;following=self.pending()
        async def fail_stop(*_):raise RuntimeError('CPU exit unknown')
        self.guard._stop_and_prove=fail_stop
        await self.guard._finish(self.ticket,None,complete=False)
        self.assertIs(self.guard._ticket,self.ticket);self.assertTrue(self.guard._admission_paused)
        self.assertIsInstance(following.granted.exception(),guard_module._GuardError)
    async def test_real_observed_disconnect_during_send_before_headers_reaches_new_proof(self):
        sent=asyncio.Event()
        class Client:
            def build_request(self,method,url,**kwargs):return httpx.Request(method,url,**kwargs)
            async def send(self,*args,**kwargs):sent.set();await asyncio.Event().wait()
        class Request:
            headers={'authorization':'Bearer CPU backend'};method='POST';url=types.SimpleNamespace(path='/v1/chat/completions')
            async def stream(self):yield json.dumps({'model':self_profile.public_alias,'max_tokens':16,'stream':True}).encode()
            async def receive(self):await sent.wait();return {'type':'http.disconnect'}
        self_profile=self.profile;self.guard.client=Client()
        async def prepare(ticket,disconnect):pass
        self.guard._prepare=prepare;self.guard._ticket=None;self.guard._active_inferences=0
        result=await self.guard._profile_inference(Request())
        self.assertIsInstance(result,guard_module._ClientGoneResponse)
        self.assertEqual(self.phases,['generation_cancelled']);self.assertEqual(self.stops,[])
        self.assertEqual(self.guard._resident_profile,self.profile);self.assertIsNone(self.guard._ticket)
    async def test_legacy_actual_disconnect_retains_immediate_owned_exit_policy(self):
        self.ticket.profile=c.shadow_trusted()['registry'].default
        await self.guard._finish(self.ticket,None,complete=False)
        self.assertEqual(self.phases,[]);self.assertEqual(self.stops,['ctx64k'])
        self.assertEqual(self.guard._last_release_method,'owned_child_exit')

    async def test_stage_disconnect_does_not_hide_known_HTTP_error_or_non200(self):
        async def receive_value(value):return value
        for value in (httpx.Response(500),):
            done=asyncio.create_task(receive_value(None));await done
            with self.assertRaises(guard_module._CallerGone) as seen:
                await guard_module._stage(receive_value(value),deadline=asyncio.get_running_loop().time()+1,disconnect=done)
            self.assertTrue(seen.exception.generation_failed)
        async def raise_error():raise httpx.ReadError('CPU upstream failure')
        done=asyncio.create_task(receive_value(None));await done
        with self.assertRaises(guard_module._CallerGone) as seen:
            await guard_module._stage(raise_error(),deadline=asyncio.get_running_loop().time()+1,disconnect=done)
        self.assertTrue(seen.exception.generation_failed)






class ShortCompletionProofTests(ProbeTests):
 def setUp(self):
  super().setUp()
  self.before['qsa_dispatch_metadata']=None;self.after['qsa_dispatch_metadata']=None
  self.before['ngram_preload_observed']=None;self.after['ngram_preload_observed']=None
  self.probe.baseline=copy.deepcopy(self.before)
 def complete(self,state):
  atomic_private_json(self.data/'model-state.json',state)
  with patch.object(proof,'unchanged',return_value=True):return self.probe._probe(self.profile,'generation_complete')
 def test_short_completion_with_actual_owned_lease_no_QSA_or_preload_observation_keeps_release(self):
  state={**copy.deepcopy(self.after),'last_release_cancelled':False,'last_cancelled_request_id':None,'cancelled_session_dropped':False}
  self.assertTrue(self.complete(state));self.assertIsNone(self.probe._state()['qsa_dispatch_metadata']);self.assertIsNone(self.probe._state()['ngram_preload_observed'])
 def test_None_never_waives_wrong_engine_route_owner_queue_or_unbalanced_release(self):
  state={**copy.deepcopy(self.after),'last_release_cancelled':False}
  for change in [{'proof_valid':False},{'qsa_route_effective':{'requested':'split','route':'query'}},{'queued_request_ids':['req-100-10']},
      {'active_lane_ids':['req-100-9']},{'released_sequence':8},{'queue_acquired_sequence':8},{'binary_sha256':'f'*64},
      {'startup_id':'b'*32},{'argv_sha256':'c'*64},{'stderr_closed':True}]:
   with self.subTest(change=change):self.assertFalse(self.complete({**copy.deepcopy(state),**change}))
  for key,value in [('context_tokens',131072),('mtp_drafts',0),('kv_cache','q8'),('max_batch',4),('max_batch',True)]:
   invalid=copy.deepcopy(state);invalid['native_engine_effective'][key]=value;self.assertFalse(self.complete(invalid))
 def test_observed_dispatch_still_must_pass_strict_shape_route_counts(self):
  valid={'sparse_prefill_rows':4096,'route':'split','split_dispatch_count':1,'query_dispatch_count':0}
  state={**copy.deepcopy(self.after),'qsa_dispatch_metadata':valid};self.assertTrue(self.complete(state))
  for change in [{'sparse_prefill_rows':0},{'route':'query'},{'split_dispatch_count':0},{'query_dispatch_count':1},{'split_dispatch_count':True}]:
   invalid=copy.deepcopy(state);invalid['qsa_dispatch_metadata'].update(change);self.assertFalse(self.complete(invalid));self.assertFalse(self.probe._runtime_matches(invalid,completed=True))

if __name__=="__main__":unittest.main(verbosity=2)
