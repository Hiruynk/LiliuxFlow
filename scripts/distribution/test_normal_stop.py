"""Idle normal shutdown regressions. No service, signal, DB or model actions."""
from pathlib import Path
import sys,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import agent

class NormalStopTests(unittest.TestCase):
 def setUp(self):
  self.trusted={'config':{'installation_id':'CPU','database_initialized':True},'registry':types.SimpleNamespace(default='CPU profile')}
  self.held={'admission_paused':True,'pause_reason':'session_maintenance','active_inferences':0,'pending_inferences':0}
  self.registry={'installation_id':'CPU','agent':{'pid':123}}
 def invoke(self,drain_error=None,native_ok=True,pg=True):
  probe=types.SimpleNamespace(_state=lambda:None,_probe=lambda *_:native_ok)
  with patch.object(agent,'validate',return_value=self.trusted),patch.object(Path,'exists',return_value=True),patch.object(agent,'private_file',side_effect=lambda p:p),patch.object(agent,'read_object',return_value=self.registry),patch.object(agent,'unchanged',return_value=True),patch.object(agent,'drain',side_effect=drain_error),patch.object(agent,'guard_admission_status',return_value=self.held),patch('runtime_proof.RunnerResourceProbe',return_value=probe),patch.object(agent,'_force_stop',return_value={'state':'stopped','data_preserved':True,'postgres_fast_stop':pg}) as stop,patch.object(agent.subprocess,'run') as spawn:
   if drain_error or not native_ok or not pg:
    with self.assertRaises(agent.DistributionError):agent.stop(Path('/CPU'))
   else:self.assertTrue(agent.stop(Path('/CPU'))['normal_idle_drain'])
   if drain_error or not native_ok:stop.assert_not_called()
   else:stop.assert_called_once()
   spawn.assert_not_called()
 def test_busy_drain_never_calls_owned_stop_or_bootout(self):self.invoke(agent.DistributionError('CPU busy'))
 def test_native_and_PG_exit_are_required(self):self.invoke(native_ok=False);self.invoke(pg=False)
 def test_normal_reuses_owned_tool_only_after_verified_drain(self):self.invoke()

if __name__=='__main__':unittest.main()
