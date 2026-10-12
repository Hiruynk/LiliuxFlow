"""Finite MTP2 context profiles; no model/build/API/Root mutation."""
from pathlib import Path
import copy,dataclasses,json,os,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/distribution'))
import profile_registry as r,optin_engine as e,model_runner as run,native_metadata as meta,runtime_proof as proof
from common import DistributionError,private_directory
from trust import atomic_private_json

class MtpContexts(unittest.TestCase):
 def setUp(self):
  self.doc=json.loads((ROOT/r.REGISTRY_PATH).read_text())
  self.engine=e.from_generated_trust({'engine_id':e.OPT64,'source_commit':e.LATEST_COMMIT,'patch_sha256':list(e.PATCHES),'source_inventory_sha256':e.SOURCE_SHA,'binary_sha256':'f'*64,'default':False})
 def registry(self,enabled=()):return r.parse_registry(self.doc,enabled_optin_profiles=enabled)
 def test_all_three_MTP_rows_initially_disabled_old3_default_semantics_unchanged(self):
  registry=self.registry();self.assertEqual([p.profile_id for p in registry.enabled_profiles],['ctx64k','ctx128k','ctx262k'])
  self.assertEqual(registry.default.profile_id,'ctx64k');self.assertEqual(registry.default.as_dict()['mtp_drafts'],0)
  self.assertEqual([(p.profile_id,p.context_tokens) for p in registry.profiles[3:]],list(e.OPT_CONTEXTS.items()))
  for pid in e.OPT_CONTEXTS:
   with self.assertRaises(DistributionError):registry.by_id(pid)
 def test_128_can_enable_without_262_and_no_automatic_long_enable(self):
  registry=self.registry(['ctx64k-mtp2','ctx128k-mtp2']);self.assertTrue(registry.by_id('ctx128k-mtp2').production_enabled)
  with self.assertRaises(DistributionError):registry.by_id('ctx262k-mtp2')
  for p in registry.profiles[3:]:self.assertEqual(p.default_output_tokens,65536)
 def test_registry_rejects_arbitrary_context_alias_cache_duplicate_and_boolean(self):
  for key,value in [('context_tokens',99999),('context_tokens',True),('public_alias','arbitrary'),('max_sessions',4),('cache_bytes',12*1024**3),('disk_cache_bytes',8*1024**3),('production_enabled',True)]:
   doc=copy.deepcopy(self.doc);doc['optin_profiles'][1][key]=value
   with self.assertRaises(DistributionError):r.parse_registry(doc)
  for enabled in [['ctx128k-mtp2','ctx64k-mtp2'],['ctx128k-mtp2','ctx128k-mtp2'],['foreign']]:
   with self.assertRaises(DistributionError):self.registry(enabled)
 def test_all_argv_use_same_binary_and_exact_bf16_batch1_full_thinking_context(self):
  registry=self.registry(list(e.OPT_CONTEXTS))
  with tempfile.TemporaryDirectory() as tmp:
   trusted={'registry':registry,'engines':{e.OPT64:self.engine},'data_root':Path(tmp).resolve(),'binaries':{'lily':Path('/CPU/A7'),'lily_opt64':Path('/CPU/fb1')},'config':{'model_dir':'/CPU/read-only-Q4','ports':{'guard':18080}}}
   caches=[]
   for pid,context in e.OPT_CONTEXTS.items():
    argv=run.lily_argv(trusted,19111,profile_id=pid);self.assertEqual(argv[0],'/CPU/fb1')
    for flag,value in {'--max-seq':str(context),'--mtp-drafts':'2','--max-batch':'1','--kv-cache':'bf16','--max-sessions':'1','--cache-bytes':str(8*1024**3),'--disk-cache-bytes':'0','--thinking-budget':'off','--ngram-preload':'true'}.items():self.assertEqual(argv[argv.index(flag)+1],value)
    self.assertNotIn('--max-images',argv);caches.append(argv[argv.index('--disk-cache-dir')+1])
   self.assertEqual(len(set(caches)),3)
 def test_engine_identity_record_is_unchanged_and_contexts_remain_finite(self):
  self.assertEqual(self.engine.engine_id,'latest13f-defer-pc123-mtp2-opt64k');self.assertEqual(self.engine.source_inventory_sha256,e.SOURCE_SHA)
  for pid,context in e.OPT_CONTEXTS.items():
   p=self.registry(list(e.OPT_CONTEXTS)).by_id(pid)
   for invalid in [context//2,context+1,True]:
    with self.assertRaises(ValueError):e.latest_argv(self.engine,dataclasses.replace(p,context_tokens=invalid),'CPU','CPU',19111,'CPU')
 def test_parser_accepts_actual_context_for_each_profile_and_never_self_declared_wrong(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve()
   for pid,context in e.OPT_CONTEXTS.items():
    state=run.LaneState(root/(pid+'.json'),{'engine_id':e.OPT64,'profile_id':pid,'context_tokens':context})
    self.assertTrue(state.consume(f'LILY_ENGINE_EFFECTIVE context_tokens={context} kv_cache=bf16 mtp_drafts=2 max_batch=1'))
    state.consume('LILY_QSA_ROUTE_EFFECTIVE requested=split route=split');self.assertTrue(e.native_matches(state.record))
    self.assertIsNone(state.record['qsa_dispatch_metadata'])
    self.assertFalse(state.consume(f'LILY_ENGINE_EFFECTIVE context_tokens=65536 kv_cache=q8 mtp_drafts=0 max_batch=4'))
    self.assertFalse(e.native_matches(state.record))
 def test_selected_long_context_progress_and_dispatch_numeric_bounds(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();pid='ctx262k-mtp2';state=run.LaneState(root/'state.json',{'engine_id':e.OPT64,'profile_id':pid,'context_tokens':262144})
   for line in ['LILY_ENGINE_EFFECTIVE context_tokens=262144 kv_cache=bf16 mtp_drafts=2 max_batch=1','LILY_QSA_ROUTE_EFFECTIVE requested=split route=split','LILY_QSA_DISPATCH_METADATA sparse_prefill_rows=131072 route=split split_dispatch_count=1 query_dispatch_count=0','[lily-meta] request_id=req-1-1 phase=queue_enter','[lily-meta] request_id=req-1-1 phase=lane_acquired queue_wait_ms=0','[lily-meta] request_id=req-1-1 phase=prefill_progress completed_chunks=17 prefilled_tokens=131072']:
    self.assertTrue(state.consume(line))
   self.assertEqual(state.record['last_prefill_progress']['prefilled_tokens'],131072)
   self.assertFalse(state.consume('[lily-meta] request_id=req-1-1 phase=prefill_cancelled stage=prefill_chunk completed_chunks=20 prefilled_tokens=262145 session_disposition=dropped'))
 def test_effective_context_clamp_or_precision_change_is_not_accepted(self):
  for pid,ctx in e.OPT_CONTEXTS.items():
   state={'engine_id':e.OPT64,'profile_id':pid,'context_tokens':ctx,'native_context_tokens':ctx,'proof_valid':True,'native_engine_effective':{'context_tokens':ctx,'kv_cache':'bf16','mtp_drafts':2,'max_batch':1},'qsa_route_effective':{'requested':'split','route':'split'}}
   self.assertTrue(e.native_matches(state))
   for key,value in [('context_tokens',65536 if ctx!=65536 else 131072),('kv_cache','q8'),('mtp_drafts',0),('max_batch',4)]:
    bad=copy.deepcopy(state);bad['native_engine_effective'][key]=value;self.assertFalse(e.native_matches(bad))
 def test_RAM_recommendations_are_advisory_and_disk_sum_stays_bounded(self):
  registry=self.registry(list(e.OPT_CONTEXTS))
  for pid in e.OPT_CONTEXTS:
   p=registry.by_id(pid);status=run.ram_headroom_status(p,10*1024**3)
   self.assertEqual(status['ram_headroom_policy'],'advisory');self.assertIsNone(status['ram_stop_reason']);self.assertEqual(status['ram_headroom_status'],'below_recommended')
  self.assertLessEqual(sum(p.disk_cache_bytes for p in registry.profiles)+registry.temporary_cache_reserve_bytes,registry.total_disk_cache_cap_bytes)
  self.assertEqual({p.max_sessions for p in registry.profiles[3:]},{1})

 def probe_fixture(self,root,pid):
  registry=self.registry(list(e.OPT_CONTEXTS));profile=registry.by_id(pid)
  trusted={'registry':registry,'engines':{e.OPT64:self.engine},'data_root':root,'config':{'installation_id':'CPU-MTP-contexts'},
   'trust':{'binaries':{'lily':{'sha256':'a'*64},'lily_opt64':{'sha256':'f'*64}}}}
  resource=proof.RunnerResourceProbe(trusted,run_root=root,lease_root=root/'leases')
  ident=lambda pid,parent:{'pid':pid,'ppid':parent,'pgid':101,'uid':os.getuid(),'started':'CPU fake birth','command_sha256':'a'*64}
  state={'schema_version':1,'installation_id':'CPU-MTP-contexts','runner':ident(101,100),'child':ident(102,101),
   'profile_id':pid,'public_alias':profile.public_alias,'context_tokens':profile.context_tokens,'backend_port':19111,
   'binary_sha256':'f'*64,'startup_id':'a'*32,'argv_sha256':'b'*64,**e.record_fields(self.engine),
   'proof_valid':True,'stderr_closed':False,'child_exited':False,'active_lane_ids':[],'queued_request_ids':[],
   'acquired_sequence':8,'released_sequence':8,'event_sequence':16,'queue_entered_sequence':8,
   'queue_acquired_sequence':8,'queue_terminal_sequence':0,'queue_event_sequence':16,
   'last_acquired_request_id':'req-100-8','last_released_request_id':'req-100-8','last_release_cancelled':False,
   'last_cancelled_request_id':None,'cancelled_session_dropped':False,'native_context_tokens':profile.context_tokens,
   'native_engine_effective':{'context_tokens':profile.context_tokens,'kv_cache':'bf16','mtp_drafts':2,'max_batch':1},
   'qsa_route_effective':{'requested':'split','route':'split'},'qsa_dispatch_metadata':None}
  private_directory(resource.lease);atomic_private_json(resource.lease/'lease.json',state);atomic_private_json(root/'model-state.json',state)
  return profile,resource,state
 def test_actual_long_probe_complete_same_owner_balanced_queue_and_context(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve()
   for pid in ('ctx128k-mtp2','ctx262k-mtp2'):
    data=private_directory(root/pid);profile,resource,before=self.probe_fixture(data,pid)
    with patch.object(proof,'unchanged',return_value=True):self.assertTrue(resource._probe(profile,'before_forward'))
    after={**copy.deepcopy(before),'acquired_sequence':9,'released_sequence':9,'event_sequence':18,
     'queue_entered_sequence':9,'queue_acquired_sequence':9,'queue_event_sequence':18,
     'last_acquired_request_id':'req-100-9','last_released_request_id':'req-100-9'}
    atomic_private_json(data/'model-state.json',after)
    with patch.object(proof,'unchanged',return_value=True):self.assertTrue(resource._probe(profile,'generation_complete'))
    for key,value in [('context_tokens',65536),('native_context_tokens',65536),('engine_id','foreign'),
      ('binary_sha256','a'*64),('profile_id','ctx64k-mtp2'),('released_sequence',8),('proof_valid',False),
      ('queued_request_ids',['req-100-10']),('queue_entered_sequence',10),('child',{**after['child'],'started':'new birth'})]:
     atomic_private_json(data/'model-state.json',{**after,key:value})
     with patch.object(proof,'unchanged',return_value=True),self.subTest(profile=pid,field=key):self.assertFalse(resource._probe(profile,'generation_complete'))
 def test_actual_long_cancel_requires_same_ID_DROP_exact_owner_queue_release_and_late_cleanup(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();profile,resource,before=self.probe_fixture(root,'ctx262k-mtp2');resource.baseline=copy.deepcopy(before)
   after={**copy.deepcopy(before),'acquired_sequence':9,'released_sequence':9,'event_sequence':18,
    'queue_entered_sequence':9,'queue_acquired_sequence':9,'queue_event_sequence':18,
    'last_acquired_request_id':'req-100-9','last_released_request_id':'req-100-9',
    'last_cancelled_request_id':'req-100-9','last_release_cancelled':True,'cancelled_session_dropped':True,
    'last_prefill_progress':{'request_id':'req-100-9','prefilled_tokens':131072,'completed_chunks':17}}
   atomic_private_json(root/'model-state.json',after)
   with patch.object(proof,'unchanged',return_value=True):self.assertTrue(resource._probe(profile,'generation_cancelled'))
   for key,value in [('cancelled_session_dropped',False),('last_cancelled_request_id','req-100-8'),
    ('last_release_cancelled',False),('released_sequence',8),('queued_request_ids',['req-100-10']),
    ('proof_valid',False),('startup_id','c'*32)]:
    atomic_private_json(root/'model-state.json',{**after,key:value})
    with patch.object(proof,'unchanged',return_value=True),self.subTest(field=key):self.assertFalse(resource._probe(profile,'generation_cancelled'))
   atomic_private_json(root/'model-state.json',after)
   resource.profiles[profile.profile_id]=dataclasses.replace(profile,production_enabled=False,validation_only=True)
   with patch.object(proof,'unchanged',return_value=True):self.assertTrue(resource._owned(after,resource.profiles[profile.profile_id],live=True))

if __name__=='__main__':unittest.main(verbosity=2)
