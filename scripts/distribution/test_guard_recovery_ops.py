# SPDX-License-Identifier: Apache-2.0
"""CPU-only admission health and privileged closed-cleanup stop flow."""
import argparse,copy,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import agent,liliuxflow as cli,ownership,trust
from common import DistributionError

class RecoveryOpsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='portable recovery CPU-');self.root=Path(self.temp.name).resolve();(self.root/'run').mkdir(mode=0o700)
  self.trusted={'data_root':self.root,'config':{'ports':{'guard':18080,'manager':18081}},'secrets':{'GUARD_CONTROL_TOKEN':'synthetic-control','MANAGER_BACKEND_TOKEN':'synthetic-backend'}}
  self.closed={'admission_paused':True,'pause_reason':'resource_release_unverified','active_stage':'cleanup','active_inferences':1,'pending_inferences':0}
  self.ready={'admission_paused':False,'pause_reason':None,'active_stage':None,'active_inferences':0,'pending_inferences':0}
 def tearDown(self):self.temp.cleanup()
 def test_closed_cleanup_is_recovered_before_lock_then_normal_unload(self):
  calls=[];states=iter([self.closed,self.ready])
  def http(url,**kwargs):
   calls.append((url,kwargs))
   if url.endswith('/__recovery/status'):return 200,next(states)
   if url.endswith('/maintenance/recover'):
    self.assertEqual(kwargs['control'],'synthetic-control');self.assertEqual(kwargs['payload'],{});return 200,dict(self.ready,recovered=True,inference_completed=False,native_lane_counters_modified=False)
   if url.endswith('/maintenance/lock'):return 200,{}
   if url.endswith('/api/models/unload'):return 200,{}
   if url.endswith('/running'):return 200,[]
   self.fail('Unexpected request')
  with patch.object(agent,'http_json',http):agent.drain(self.trusted)
  paths=[url.split('18080')[-1] for url,_ in calls]
  self.assertLess(paths.index('/__recovery/maintenance/recover'),paths.index('/__recovery/maintenance/lock'))
 def test_active_stage_and_failed_proof_refuse_stop_before_unload(self):
  for stage in ('active','loading'):
   state=dict(self.closed,active_stage=stage)
   with patch.object(agent,'http_json',return_value=(200,state)) as http:
    with self.assertRaises(DistributionError):agent.drain(self.trusted)
   self.assertEqual(http.call_count,1)
  def denied(url,**kwargs):return (200,self.closed) if url.endswith('/status') else (409,{'error':'exit_unverified'})
  with patch.object(agent,'http_json',side_effect=denied) as http:
   with self.assertRaises(DistributionError):agent.drain(self.trusted)
  self.assertEqual(http.call_count,2)
 def test_new_request_race_after_recovery_preserves_busy_refusal(self):
  states=iter([self.closed,dict(self.ready,active_inferences=1,active_stage='active')]);calls=[]
  def http(url,**kwargs):
   calls.append(url)
   if url.endswith('/status'):return 200,next(states)
   return 200,dict(self.ready,recovered=True,inference_completed=False,native_lane_counters_modified=False)
  with patch.object(agent,'http_json',http):
   with self.assertRaises(DistributionError):agent.drain(self.trusted)
  self.assertFalse(any(url.endswith('/api/models/unload') for url in calls))
 def test_unpaused_active_guard_busy409_remains_no_unload(self):
  def http(url,**kwargs):return (200,dict(self.ready,active_inferences=1,active_stage='active')) if url.endswith('/status') else (409,{'error':'inference_busy'})
  with patch.object(agent,'http_json',side_effect=http) as http:
   with self.assertRaises(DistributionError):agent.drain(self.trusted)
  self.assertEqual(http.call_count,2)

class DoctorAdmissionTests(unittest.TestCase):
 def test_paused_owned_guard_is_degraded_readonly_and_not_serving_ready(self):
  for paused in (True,False):
   with tempfile.TemporaryDirectory(prefix='doctor admission CPU-') as name:
    data=Path(name).resolve();(data/'run').mkdir();(data/'run/agent.json').write_text(json.dumps({'agent':{'pid':12345}}))
    ports=dict(cli.DEFAULT_PORTS);config={'schema_version':1,'runtime_state':'BUILT','model_dir':str(data/'synthetic-model'),'checkpoint':{'config_sha256':'a','index_sha256':'b'},'ports':ports}
    args=argparse.Namespace(data_root=data,controlplane_only=False);state={'admission_paused':paused,'pause_reason':'resource_release_unverified' if paused else None}
    with patch.object(cli.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3)),patch.object(cli,'load_install',return_value=config),patch.object(cli,'host_metadata',return_value={'os':'Darwin','machine':'arm64','chip':'Apple M5 Max','ram_bytes':128*1024**3}),patch.object(cli,'checkpoint_metadata',return_value={'config_sha256':'a','index_sha256':'b'}),patch.object(cli,'listening',side_effect=lambda port:port==ports['guard']),patch.object(ownership,'unchanged',return_value=True),patch.object(trust,'validate',return_value={'config':config}),patch.object(agent,'guard_admission_status',return_value=state),patch.object(agent,'recover_closed_cleanup') as recover,patch.object(cli,'emit') as emit:
     code=cli.doctor(args)
    self.assertEqual(code,2 if paused else 0);body=emit.call_args.args[0];self.assertEqual(body['state'],'DEGRADED' if paused else 'ready');self.assertEqual(body['serving_ready'],not paused);recover.assert_not_called()
if __name__=='__main__':unittest.main()
