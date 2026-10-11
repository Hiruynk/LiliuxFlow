# SPDX-License-Identifier: Apache-2.0
"""Controlled CPU snapshots only. No kernel queries, service or real signals."""
import json,os,signal,unittest
from unittest.mock import Mock,patch
import forced_stop as fs
from common import DistributionError
import test_forced_stop as fixture

class SharedExitTests(unittest.TestCase):
    row=fixture.ForcedStopTests.row
    ident=fixture.ForcedStopTests.ident
    def setUp(self):
        self.kernel=self.row(101,100);self.owner=self.ident(self.kernel);self.drift={**self.owner,'command_sha256':'b'*64}
        self.diagnostics=[];self.observe=fs.shared_exit_observer([self.owner],self.diagnostics.append)
    def probe(self,values,rows=None):
        from contextlib import ExitStack
        stack=ExitStack()
        def packet(value):return None if value is None else (value,'CPU_SECRET_COMMAND_MUST_NOT_BE_RECORDED')
        stack.enter_context(patch.object(fs,'_probe',side_effect=[packet(x) for x in values]))
        stack.enter_context(patch.object(fs,'_descendant_rows',side_effect=rows or [[self.kernel]]))
        stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('real signals forbidden')))
        return stack
    def test_cross_snapshot_live_drift_then_absence_twice_recovers_without_signals(self):
        with self.probe([self.drift,None,None]):self.assertTrue(self.observe(self.owner))
        first=self.diagnostics[0];self.assertEqual(first['pid'],101);self.assertEqual(first['changed_fields'],['command_sha256'])
        self.assertEqual(first['kernel']['stat_ascii'],[ord('S')]);self.assertEqual(first['reobservation_outcome'],'ABSENT_TWICE')
        self.assertNotIn('CPU_SECRET_COMMAND',json.dumps(first));self.assertFalse(first['signal_authorized'])
    def test_cross_snapshot_drift_then_twice_exact_zombie_recovers(self):
        zombie={**self.kernel,'stat':'Z'}
        with self.probe([self.drift,self.drift,self.drift],[[self.kernel],[zombie],[zombie]]):self.assertTrue(self.observe(self.owner))
        self.assertEqual(self.diagnostics[0]['reobservation_outcome'],'TWO_KERNEL_ZOMBIE_SNAPSHOTS')
    def test_original_default_exited_still_refuses_same_snapshot(self):
        with patch.object(fs,'inspect',return_value=self.drift),patch.object(fs,'_descendant_rows',return_value=[self.kernel]):
            with self.assertRaisesRegex(DistributionError,'live owned process identity changed'):fs.exited(self.owner)
    def test_persistent_live_command_or_parent_drift_never_recovers(self):
        for key,value in (('command_sha256','b'*64),('ppid',999)):
            current={**self.owner,key:value};row={**self.kernel,'ppid':current['ppid']}
            with self.subTest(key=key),self.probe([current]*4,[[row]]*4),self.assertRaisesRegex(DistributionError,'live owned process identity changed'):
                fs.shared_exit_observer([self.owner],lambda value:None)(self.owner)
    def test_live_exact_identity_after_drift_does_not_authorize_signal_or_return_live(self):
        with self.probe([self.drift,self.owner,self.owner,self.owner],[[self.kernel]]*4),self.assertRaises(DistributionError):self.observe(self.owner)
    def test_birth_group_owner_changes_never_settle_even_if_absence_follows(self):
        for key,value in (('pid',102),('pgid',102),('uid',0),('started','new birth')):
            with self.subTest(key=key),self.probe([self.drift,{**self.owner,key:value},None,None]),self.assertRaises(DistributionError):
                fs.shared_exit_observer([self.owner],lambda value:None)(self.owner)
    def test_kernel_uid_real_uid_start_or_parent_mismatch_never_settles(self):
        for key,value in (('uid',0),('ruid',777),('started','new birth'),('ppid',999)):
            with self.subTest(key=key),self.probe([self.drift],[[{**self.kernel,key:value}]]),self.assertRaises(DistributionError):
                fs.shared_exit_observer([self.owner],lambda value:None)(self.owner)
    def test_absence_requires_two_fresh_samples_and_PID_reappearance_refuses(self):
        with self.probe([None,None]):self.assertTrue(self.observe(self.owner))
        self.assertEqual(self.diagnostics,[])
        with self.probe([None,self.owner],[[self.kernel]]),self.assertRaisesRegex(DistributionError,'reappeared'):self.observe(self.owner)
    def test_zombie_changed_or_with_subtree_is_rejected(self):
        zombie={**self.kernel,'stat':'Z'};child=self.row(102,101)
        for first,second in (([zombie,child],[zombie]),([zombie],[zombie,child]),([zombie],[{**zombie,'stat':'S'}])):
            with self.subTest(second=second),self.probe([self.drift,self.drift],[first,second]),self.assertRaises(DistributionError):
                fs.shared_exit_observer([self.owner],lambda value:None)(self.owner)
    def test_uncaptured_changed_duplicate_or_foreign_identity_refuses(self):
        for value in ({**self.owner,'pid':102},{**self.owner,'ppid':999}):
            with self.assertRaisesRegex(DistributionError,'not captured'):self.observe(value)
        for values in ([self.owner,self.owner],[{**self.owner,'uid':0}]):
            with self.assertRaises(DistributionError):fs.shared_exit_observer(values,lambda value:None)
    def test_unknown_probe_refuses_without_json_or_status_fallback(self):
        with patch.object(fs,'_probe',side_effect=DistributionError('owned process probe is unknown')),self.assertRaises(DistributionError):self.observe(self.owner)
        self.assertEqual(self.diagnostics[0]['reobservation_outcome'],'REFUSED')
    def test_deadline_is_short_and_every_PS_probe_receives_remaining_bound(self):
        with patch.object(fs.time,'monotonic',side_effect=[0,1]),patch.object(fs,'_probe') as probe,self.assertRaisesRegex(DistributionError,'deadline'):
            self.observe(self.owner)
        probe.assert_not_called()
        with patch.object(fs,'_probe',return_value=None) as probe:self.assertTrue(self.observe(self.owner))
        self.assertTrue(all(0<call.kwargs['timeout']<=.1 for call in probe.call_args_list))
    def test_first_mismatch_only_is_recorded_for_entire_captured_shutdown(self):
        with self.probe([self.drift,None,None]):self.assertTrue(self.observe(self.owner))
        with self.probe([None,None]):self.assertTrue(self.observe(self.owner))
        self.assertEqual(len(self.diagnostics),1)
    def test_previous_absence_or_zombie_proof_never_allows_live_PID_again(self):
        for first in ('ABSENT','ZOMBIE'):
            self.observe=fs.shared_exit_observer([self.owner],lambda value:None)
            if first=='ABSENT':
                with self.probe([None,None]):self.assertTrue(self.observe(self.owner))
            else:
                zombie={**self.kernel,'stat':'Z'}
                with self.probe([self.drift,self.drift],[[zombie],[zombie]]):self.assertTrue(self.observe(self.owner))
            with self.probe([self.owner],[[self.kernel]]),self.assertRaises(DistributionError):self.observe(self.owner)
    def test_terminate_all_uses_observer_after_TERM_without_weakening_send(self):
        calls=[]
        def read(pid,**kwargs):
            value=self.owner if not calls else self.drift if len(calls)==1 else None
            calls.append(pid);return None if value is None else (value,'CPU secret')
        with patch.object(fs,'_probe',side_effect=read),patch.object(fs,'_descendant_rows',return_value=[self.kernel]),patch.object(fs,'send',return_value=True) as send,patch.object(fs.os,'kill',side_effect=AssertionError('real signals forbidden')):
            fs.terminate_all([self.owner],grace=0,resume=True,observer=self.observe)
        self.assertEqual([call.args[1] for call in send.call_args_list],[signal.SIGTERM,signal.SIGCONT])
        self.assertEqual(self.diagnostics[0]['pid'],101)
    def test_send_still_rejects_full_live_identity_even_with_exit_observer_available(self):
        with patch.object(fs,'inspect',return_value=self.drift),patch.object(fs.os,'kill') as kill,self.assertRaises(DistributionError):fs.send(self.owner,signal.SIGTERM)
        kill.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
