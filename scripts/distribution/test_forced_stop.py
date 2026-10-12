# SPDX-License-Identifier: Apache-2.0
"""Portable c04 owned-tree checks on synthetic process/kernel rows only."""
import hashlib,os,pathlib,signal,subprocess,sys,types,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import forced_stop as stop
from common import DistributionError

class ForcedStopTests(unittest.TestCase):
    def row(self,pid,ppid,uid=None,ruid=None,stat='S',ucomm='python',started='Fri Oct 9 08:00:00 2026'):
        return {'pid':pid,'ppid':ppid,'uid':os.getuid() if uid is None else uid,'ruid':os.getuid() if ruid is None else ruid,'stat':stat,'ucomm':ucomm,'started':started}
    def ident(self,row):return {k:row[k] for k in ('pid','ppid','uid','started')}|{'pgid':row['pid'],'command_sha256':'a'*64}
    def test_same_uid_live_tree_returns_only_exact_owned_dicts(self):
        rows=[self.row(101,100),self.row(102,101)];by_pid={r['pid']:self.ident(r) for r in rows}
        with patch.object(stop,'_descendant_rows',return_value=rows),patch.object(stop,'inspect',side_effect=lambda pid:by_pid[pid]):
            self.assertEqual(stop.descendants(100),[by_pid[101],by_pid[102]])
    def test_foreign_live_UID_is_refused_never_reinterpreted_as_ruid(self):
        for row in (self.row(101,100,uid=0,ruid=os.getuid(),ucomm='python'),self.row(101,100,uid=777,ruid=os.getuid())):
            with patch.object(stop,'_descendant_rows',return_value=[row]),self.assertRaises(DistributionError):stop.descendants(100)
    def test_only_twice_identical_exited_SUID_ps_without_subtree_is_excluded(self):
        ps=self.row(101,100,uid=0,ruid=os.getuid(),stat='Z',ucomm='ps')
        with patch.object(stop,'_descendant_rows',side_effect=[[ps],[ps]]),patch.object(stop,'_suid_ps_identity',return_value=True),patch.object(stop,'inspect',side_effect=AssertionError('UID0 never a signal identity')):
            self.assertEqual(stop.descendants(100),[])
    def test_ps_zombie_changed_start_PPID_ruid_status_or_descendant_refuses(self):
        ps=self.row(101,100,uid=0,ruid=os.getuid(),stat='Z',ucomm='ps')
        for changed in ({**ps,'started':'new birth'},{**ps,'ppid':999},{**ps,'ruid':777},{**ps,'stat':'S'}):
            with patch.object(stop,'_descendant_rows',side_effect=[[ps],[changed]]),patch.object(stop,'_suid_ps_identity',return_value=True),self.assertRaises(DistributionError):stop.descendants(100)
        subtree=self.row(102,101)
        with patch.object(stop,'_descendant_rows',return_value=[ps,subtree]),patch.object(stop,'_suid_ps_identity',return_value=True),self.assertRaises(DistributionError):stop.descendants(100)
    def test_nonSUID_or_foreign_realUID_ps_zombie_is_not_allowed(self):
        ps=self.row(101,100,uid=0,stat='Z',ucomm='ps')
        with patch.object(stop,'_descendant_rows',return_value=[ps]),patch.object(stop,'_suid_ps_identity',return_value=False),self.assertRaises(DistributionError):stop.descendants(100)
        ps['ruid']=777
        with patch.object(stop,'_descendant_rows',return_value=[ps]),patch.object(stop,'_suid_ps_identity',return_value=True),self.assertRaises(DistributionError):stop.descendants(100)
    def test_known_live_ps_only_retries_then_captures_fresh_tree(self):
        ps=self.row(101,100,uid=0,ucomm='ps');child=self.row(102,100);ident=self.ident(child)
        with patch.object(stop,'_descendant_rows',side_effect=[[ps],[child]]),patch.object(stop,'_suid_ps_identity',return_value=True),patch.object(stop.time,'sleep'),patch.object(stop,'inspect',return_value=ident):
            self.assertEqual(stop.descendants(100),[ident])
    def test_live_ps_retry_is_bounded_without_signaling_UID0(self):
        ps=self.row(101,100,uid=0,ucomm='ps')
        with patch.object(stop,'_descendant_rows',return_value=[ps]),patch.object(stop,'_suid_ps_identity',return_value=True),patch.object(stop.time,'monotonic',side_effect=[0,1]),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.descendants(100)
        kill.assert_not_called()
    def test_same_uid_zombie_is_twice_checked_and_no_signal_identity_returned(self):
        row=self.row(101,100,stat='Z')
        with patch.object(stop,'_descendant_rows',side_effect=[[row],[row]]):self.assertEqual(stop.descendants(100),[])
    def test_capture_changed_PID_start_or_lineage_refused(self):
        row=self.row(101,100);ident=self.ident(row);ident['started']='new birth'
        with patch.object(stop,'_descendant_rows',return_value=[row]),patch.object(stop,'inspect',return_value=ident),self.assertRaises(DistributionError):stop.descendants(100)
    def test_signal_foreign_UID_self_parent_or_PID_reuse_never_signals(self):
        row=self.row(101,100);ident=self.ident(row)
        with patch.object(stop,'exact',return_value=False),patch.object(stop.os,'kill') as kill:self.assertFalse(stop.send(ident,signal.SIGTERM))
        kill.assert_not_called()
        for value in ({**ident,'uid':0},{**ident,'pid':os.getpid()},{**ident,'pid':os.getppid()}):
            with patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.send(value,signal.SIGTERM)
            kill.assert_not_called()
    def test_verified_send_targets_exact_PID_not_process_group(self):
        ident=self.ident(self.row(101,100))
        with patch.object(stop,'exact',return_value=True),patch.object(stop.os,'kill') as kill:self.assertTrue(stop.send(ident,signal.SIGTERM))
        kill.assert_called_once_with(101,signal.SIGTERM)
    def test_strict_probe_proven_absence_only_one_empty_error_code(self):
        absent=subprocess.CompletedProcess([],1,'','')
        with patch.object(stop.subprocess,'run',return_value=absent):self.assertIsNone(stop.inspect(101))
        for response in (subprocess.CompletedProcess([],2,'',''),subprocess.CompletedProcess([],1,'','unknown error'),subprocess.CompletedProcess([],0,'','')):
            with patch.object(stop.subprocess,'run',return_value=response),self.assertRaises(DistributionError):stop.inspect(101)
    def test_strict_probe_timeout_error_malformed_multiPID_and_numeric_refusal(self):
        valid='101 100 101 '+str(os.getuid())+' Fri Oct 9 08:00:00 2026 synthetic-command\n'
        for result in (subprocess.TimeoutExpired(['/bin/ps'],5),OSError('fixture'),
                       subprocess.CompletedProcess([],0,'malformed\n',''),subprocess.CompletedProcess([],0,valid+valid,''),
                       subprocess.CompletedProcess([],0,valid.replace('101 100','bad 100',1),''),subprocess.CompletedProcess([],0,valid.replace('101 100','102 100',1),'')):
            with patch.object(stop.subprocess,'run',side_effect=result if isinstance(result,Exception) else None,return_value=result),self.assertRaises(DistributionError):stop.inspect(101)
    def test_all_six_identity_fields_rechecked_before_any_signal(self):
        value=self.ident(self.row(101,100))
        for key,change in (('ppid',999),('pgid',999),('uid',0),('started','new birth'),('command_sha256','b'*64)):
            with patch.object(stop,'inspect',return_value={**value,key:change}),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.send(value,signal.SIGTERM)
            kill.assert_not_called()
    def test_strict_probe_captures_expected_uid_without_real_effective_rewrite(self):
        line='101 100 101 0 Fri Oct 9 08:00:00 2026 /bin/ps -p 101\n'
        with patch.object(stop.subprocess,'run',return_value=subprocess.CompletedProcess([],0,line,'')) as run:
            value=stop.inspect(101);self.assertEqual(value['uid'],0);self.assertEqual(value['command_sha256'],hashlib.sha256(b'/bin/ps -p 101').hexdigest())
        self.assertEqual(run.call_args.kwargs['timeout'],5)
        with patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.send(value,signal.SIGTERM)
        kill.assert_not_called()

    def test_exited_zombie_wait_proof_does_not_relax_any_signal_identity(self):
        row=self.row(101,100,stat='Z');value=self.ident(row);now={**value,'command_sha256':'b'*64}
        with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',side_effect=[[row],[row]]):
            self.assertTrue(stop.exited(value))
        with patch.object(stop,'inspect',return_value=now),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.send(value,signal.SIGTERM)
        kill.assert_not_called()

    def test_reparented_zombie_only_can_be_observed_never_signalled(self):
        original=self.row(101,100);value=self.ident(original);row={**original,'ppid':1,'stat':'Z'};now={**value,'ppid':1,'command_sha256':'b'*64}
        with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',side_effect=[[row],[row]]):self.assertTrue(stop.exited(value))
        for live in ({**row,'stat':'S'},{**row,'stat':'T'}):
            with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',return_value=[live]),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.terminate_all([value],grace=0)
            kill.assert_not_called()

    def test_exit_proof_reused_foreign_changed_group_or_unstable_zombie_refuses(self):
        row=self.row(101,100,stat='Z');value=self.ident(row);now={**value,'command_sha256':'b'*64}
        for changed in ({**now,'started':'new birth'},{**now,'uid':0},{**now,'pgid':999}):
            with patch.object(stop,'inspect',return_value=changed),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.exited(value)
            kill.assert_not_called()
        for changed in ({**row,'started':'new birth'},{**row,'ppid':999},{**row,'ruid':777},{**row,'stat':'S'}):
            with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',side_effect=[[row],[changed]]),self.assertRaises(DistributionError):stop.exited(value)

    def test_exit_proof_no_descendants_in_both_snapshots_and_unknown_probe_refuses(self):
        row=self.row(101,100,stat='Z');value=self.ident(row);now={**value,'command_sha256':'b'*64};child=self.row(102,101)
        for first,second in (([row,child],[row]),([row],[row,child])):
            with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',side_effect=[first,second]),self.assertRaises(DistributionError):stop.exited(value)
        with patch.object(stop,'inspect',side_effect=DistributionError('unknown probe')),self.assertRaises(DistributionError):stop.exited(value)

    def test_live_command_change_fails_closed_without_signals(self):
        row=self.row(101,100);value=self.ident(row);now={**value,'command_sha256':'b'*64}
        with patch.object(stop,'inspect',return_value=now),patch.object(stop,'_descendant_rows',return_value=[row]),patch.object(stop.os,'kill') as kill,self.assertRaises(DistributionError):stop.terminate_all([value],grace=0)
        kill.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
