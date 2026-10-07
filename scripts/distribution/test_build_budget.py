"""Bounded CPU resource faults; no native compiler, listener, model or real signal."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_budget as budget
import build_native
from common import DistributionError

class BudgetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='budget CPU Unicode 空間-');self.base=Path(self.tmp.name).resolve();self.root=self.base/'owned';self.root.mkdir()
 def tearDown(self):self.tmp.cleanup()
 def result(self,code=0,reason=None,target=None,output=b'4\tfixture\n'):
  err=('du: '+str(target or self.root/'vanished')+': '+reason+'\n').encode() if reason else b''
  return subprocess.CompletedProcess([],code,output,err)
 def test_one_missing_descendant_retries_same_owned_root_then_measures(self):
  events=[];raw=[]
  with patch.object(budget.subprocess,'run',side_effect=[self.result(1,'No such file or directory'),self.result()]) as run:
   self.assertEqual(budget.directory_bytes(self.root,events=events,private_stderr=raw),4096)
  self.assertEqual(run.call_count,2);self.assertEqual(run.call_args_list[0].args,run.call_args_list[1].args);self.assertEqual(events[0]['errno'],2);self.assertEqual(len(raw),1)
  self.assertTrue(all(0<call.kwargs['timeout']<=budget.DU_TIMEOUT_SECONDS for call in run.call_args_list))
 def test_retry_exhaustion_is_collector_error_not_fake_cap(self):
  with patch.object(budget.subprocess,'run',return_value=self.result(1,'No such file or directory')) as run:
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(run.call_count,3);self.assertEqual(caught.exception.evidence['category'],'MISSING_DESCENDANT_RETRY_EXHAUSTED');self.assertEqual(len(caught.exception.private_stderr),3)
 def test_permission_and_outside_or_root_missing_errors_never_retry(self):
  cases=[self.result(1,'Permission denied'),self.result(1,'No such file or directory',target=self.base/'foreign'),self.result(1,'No such file or directory',target=self.root)]
  for result in cases:
   with patch.object(budget.subprocess,'run',return_value=result) as run:
    with self.assertRaises(budget.BudgetInventoryError):budget.directory_bytes(self.root)
   self.assertEqual(run.call_count,1)
 def test_owned_root_symlink_and_inode_replacement_fail_closed(self):
  link=self.base/'link';link.symlink_to(self.root,target_is_directory=True)
  with patch.object(budget.subprocess,'run') as run:
   with self.assertRaises(budget.BudgetInventoryError):budget.directory_bytes(link)
   run.assert_not_called()
  # Rename keeps the original inode allocated, so the new root cannot reuse it.
  def replace(*args,**kwargs):self.root.rename(self.base/'old');self.root.mkdir();return self.result()
  with patch.object(budget.subprocess,'run',side_effect=replace):
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(caught.exception.evidence['category'],'ROOT_CHANGED')
 def test_du_timeout_is_bounded_and_preserves_exception_class(self):
  with patch.object(budget.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',5)) as run:
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(run.call_count,1);self.assertLessEqual(run.call_args.kwargs['timeout'],5);self.assertEqual(caught.exception.evidence['cause_exception_class'],'TimeoutExpired')
 def test_shared_whole_inventory_deadline_refuses_another_du(self):
  with patch.object(budget.time,'monotonic',side_effect=[0,1,13]),patch.object(budget.subprocess,'run',return_value=self.result(1,'No such file or directory')) as run:
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(run.call_count,1);self.assertEqual(caught.exception.evidence['category'],'INVENTORY_TIMEOUT')
 def test_malformed_stdout_and_missing_du_executable_fail_closed(self):
  with patch.object(budget.subprocess,'run',return_value=self.result(output=b'not-a-size')):
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(caught.exception.evidence['category'],'DU_OUTPUT_MALFORMED')
  with patch.object(budget.subprocess,'run',side_effect=FileNotFoundError(2,'sensitive-exception-detail')):
   with self.assertRaises(budget.BudgetInventoryError) as caught:budget.directory_bytes(self.root)
  self.assertEqual(caught.exception.evidence['errno'],2);self.assertNotIn('sensitive-exception-detail',json.dumps(caught.exception.evidence))
 def test_real_thresholds_unchanged_and_runtime_excluded(self):
  free=type('Disk',(),{'free':budget.FLOOR-1})()
  with patch.object(budget,'directory_bytes',side_effect=[budget.CAP+1,999999]),patch.object(budget.shutil,'disk_usage',return_value=free):
   result=budget.snapshot([self.root],runtime=self.root)
  self.assertTrue(result['over_transient_cap']);self.assertTrue(result['below_disk_floor']);self.assertEqual(result['transient_build_payload_cache_bytes'],budget.CAP+1);self.assertEqual(result['runtime_bytes_observed_not_evicted'],999999);self.assertFalse(result['automatic_deletion'])
 def test_command_unknown_inventory_stops_owned_worker_without_claiming_cap_or_leaking_error(self):
  logs=self.base/'logs';logs.mkdir();log=logs/'build.txt'
  class FakeProcess:
   pid=987654;returncode=None
   def poll(self):return self.returncode
   def wait(self,timeout):raise subprocess.TimeoutExpired('CPU-fixture',timeout)
  proc=FakeProcess();normal={'over_transient_cap':False,'below_disk_floor':False}
  error=budget.BudgetInventoryError('DU_NONZERO_EXIT',stage='transient[0]',result=self.result(1,'Permission denied'))
  with patch.object(budget,'snapshot',side_effect=[normal,error]),patch.object(build_native.subprocess,'Popen',return_value=proc),patch('ownership.capture',return_value='owned-fixture'),patch('ownership.descendants',return_value=[]),patch('ownership.terminate',return_value=True) as stop:
   with self.assertRaises(DistributionError):build_native.command(['no-real-child'],cwd=self.base,env={},log=log)
  stop.assert_called_once_with('owned-fixture',grace=10);result=json.loads(log.with_suffix('.txt.resource-stop.json').read_text());self.assertEqual(result['stop_reason'],'RESOURCE_COLLECTOR_ERROR');self.assertIsNone(result['resource_snapshot']['over_transient_cap']);self.assertIsNone(result['resource_snapshot']['below_disk_floor']);self.assertEqual(result['resource_snapshot']['collector_error']['du_exit_code'],1)
  names=result['resource_snapshot']['private_collector_stderr_files'];self.assertEqual(len(names),1);raw=logs/names[0];self.assertEqual(raw.read_bytes(),error.private_stderr[0]);self.assertEqual(raw.stat().st_mode&0o777,0o600);self.assertNotIn(str(self.root),json.dumps(result))
 def test_preflight_inventory_failure_records_unknown_and_starts_no_worker(self):
  logs=self.base/'logs';logs.mkdir();log=logs/'preflight.txt'
  with patch.object(budget,'snapshot',side_effect=DistributionError('sensitive-exception-detail')),patch.object(build_native.subprocess,'Popen') as start:
   with self.assertRaises(DistributionError):build_native.command(['no-real-child'],cwd=self.base,env={},log=log)
  start.assert_not_called();result=json.loads(log.with_suffix('.txt.resource-stop.json').read_text());self.assertEqual(result['phase'],'preflight');self.assertIsNone(result['resource_snapshot']['over_transient_cap']);self.assertNotIn('sensitive-exception-detail',json.dumps(result))

if __name__=='__main__':unittest.main()
