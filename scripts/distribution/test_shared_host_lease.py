# SPDX-License-Identifier: Apache-2.0
"""Captured agent lease retirement on CPU fixtures; no process or service actions."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import plistlib
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import agent
import forced_stop as fs
from common import DistributionError
import test_force_dispatch as fixture


class HostLeaseTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='shared-host-lease-CPU-')
        self.addCleanup(tmp.cleanup)
        self.data=Path(tmp.name).resolve();(self.data/'run').mkdir(mode=0o700)
        self.lock=self.data/'run/host-mutation.lease';self.lock.mkdir(mode=0o700)
        self.path=self.lock/'lease.json'
        self.owner={'pid':12000,'ppid':1,'pgid':12000,'uid':os.getuid(),
                    'started':'Fri Oct 9 08:00:00 2026','command_sha256':'a'*64}
        self.write(self.owner)
        self.target='gui/'+str(os.getuid())+'/com.diurnoctra.liliuxflow.CPU'

    def write(self,value):
        self.path.write_bytes(json.dumps(value).encode());self.path.chmod(0o600)

    def capture(self):
        return agent._shared_stop_host_lease_snapshot(self.data,self.owner,fs)

    def retire(self,capture,released=None):
        released=released or Mock(return_value=True)
        def absent(target,probe,**kwargs):
            self.assertEqual(target,self.target)
            if probe() is not True:raise DistributionError('CPU resources remain')
        with patch.object(agent,'_wait_launchd_removed',side_effect=absent):
            agent._shared_stop_retire_host_lease(self.data,self.owner,fs,capture,released,self.target)

    def test_exact_ended_captured_holder_preserves_bytes_and_both_inodes(self):
        capture=self.capture();self.retire(capture)
        self.assertFalse(self.lock.exists())
        q=list((self.data/'run').glob('host-mutation.lease.portable-stopped-*'))
        self.assertEqual(len(q),1)
        self.assertEqual((q[0].stat().st_dev,q[0].stat().st_ino),capture[0])
        f=q[0]/'lease.json'
        self.assertEqual((f.stat().st_dev,f.stat().st_ino),capture[1])
        self.assertEqual(f.read_bytes(),capture[2])

    def test_own_finally_already_removed_lease_needs_no_adoption(self):
        capture=self.capture();self.path.unlink();self.lock.rmdir();self.retire(capture)
        self.assertFalse(self.lock.exists())

    def test_initial_absence_cannot_adopt_new_same_holder(self):
        with self.assertRaisesRegex(DistributionError,'not captured'):self.retire(None)
        self.assertTrue(self.path.exists())

    def test_foreign_full_holder_or_reused_pid_is_refused(self):
        for key,value in (('pid',12001),('ppid',12002),('pgid',12003),('uid',os.getuid()+1),
                          ('started','Fri Oct 9 08:00:01 2026'),('command_sha256','b'*64)):
            with self.subTest(key=key):
                self.write({**self.owner,key:value})
                with self.assertRaises(DistributionError):self.capture()
                self.assertTrue(self.path.exists())

    def test_changed_bytes_or_file_inode_or_directory_inode_refuses(self):
        capture=self.capture();self.path.write_bytes(capture[2]+b' ')
        with self.assertRaises(DistributionError):self.retire(capture)
        self.path.write_bytes(capture[2])
        replacement=self.lock/'new';replacement.write_bytes(capture[2]);replacement.chmod(0o600)
        os.replace(replacement,self.path)
        with self.assertRaises(DistributionError):self.retire(capture)
        old=self.lock.with_name('old');os.rename(self.lock,old);self.lock.mkdir(mode=0o700);self.write(self.owner)
        with self.assertRaises(DistributionError):self.retire(capture)

    def test_live_or_unknown_owned_resource_never_quarantines(self):
        capture=self.capture()
        for result in (False,None):
            with self.subTest(result=result),self.assertRaises(DistributionError):
                self.retire(capture,Mock(return_value=result))
        self.assertTrue(self.path.exists())

    def test_new_job_or_unverified_job_removal_retains_lease(self):
        capture=self.capture()
        with patch.object(agent,'_wait_launchd_removed',side_effect=DistributionError('CPU replacement job')):
            with self.assertRaises(DistributionError):
                agent._shared_stop_retire_host_lease(self.data,self.owner,fs,capture,lambda:True,self.target)
        self.assertTrue(self.path.exists())

    def test_change_between_capture_and_rename_is_refused(self):
        capture=self.capture()
        def change(*args,**kwargs):self.path.write_bytes(capture[2]+b' ')
        with patch.object(agent,'_wait_launchd_removed',side_effect=change):
            with self.assertRaises(DistributionError):
                agent._shared_stop_retire_host_lease(self.data,self.owner,fs,capture,lambda:True,self.target)
        self.assertTrue(self.path.exists())

    def test_post_quarantine_identity_failure_rolls_back_same_directory(self):
        capture=self.capture();calls=[0]
        def replacement(*args,**kwargs):
            calls[0]+=1
            if calls[0]==2:raise DistributionError('CPU job appeared')
        with patch.object(agent,'_wait_launchd_removed',side_effect=replacement):
            with self.assertRaises(DistributionError):
                agent._shared_stop_retire_host_lease(self.data,self.owner,fs,capture,lambda:True,self.target)
        self.assertEqual(self.capture(),capture)

    def test_new_lease_after_quarantine_is_retained_and_never_overwritten(self):
        capture=self.capture();calls=[0]
        def replacement(*args,**kwargs):
            calls[0]+=1
            if calls[0]==2:self.lock.mkdir(mode=0o700);self.write(self.owner)
        with patch.object(agent,'_wait_launchd_removed',side_effect=replacement):
            with self.assertRaises(DistributionError):
                agent._shared_stop_retire_host_lease(self.data,self.owner,fs,capture,lambda:True,self.target)
        self.assertTrue(self.path.exists())
        self.assertEqual(len(list((self.data/'run').glob('host-mutation.lease.portable-stopped-*'))),1)

    def test_extra_file_bad_mode_symlink_and_oversize_refuse(self):
        extra=self.lock/'foreign';extra.write_bytes(b'')
        with self.assertRaises(DistributionError):self.capture()
        extra.unlink();self.lock.chmod(0o755)
        with self.assertRaises(DistributionError):self.capture()
        self.lock.chmod(0o700);self.path.chmod(0o644)
        with self.assertRaises(DistributionError):self.capture()
        self.path.chmod(0o600);self.path.write_bytes(b' '*4097)
        with self.assertRaises(DistributionError):self.capture()
        self.write(self.owner);other=self.data/'copy';self.path.rename(other);self.path.symlink_to(other)
        with self.assertRaises(DistributionError):self.capture()


class PublicStopIntegrationTests(unittest.TestCase):
    exact=fixture.ForceDispatchTests.exact
    send=fixture.ForceDispatchTests.send
    patches=fixture.ForceDispatchTests.patches
    def setUp(self):
        fixture.ForceDispatchTests.setUp(self)
        self.args+=['--mtp2-shared-context']
        self.owner['command_sha256']=hashlib.sha256(' '.join(self.args).encode()).hexdigest()
        self.record['shared_context']=True;self.state.write_text(json.dumps(self.record))
        decoded=plistlib.loads(self.plist.read_bytes());decoded['ProgramArguments']=self.args
        self.plist.write_bytes(plistlib.dumps(decoded))
        self.lock=self.data/'run/host-mutation.lease';self.lock.mkdir(mode=0o700)
        (self.lock/'lease.json').write_bytes(json.dumps(self.owner).encode());(self.lock/'lease.json').chmod(0o600)
        self.trusted['registry']=types.SimpleNamespace(default='CPU')

    def invoke(self,force):
        with self.patches(),contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(agent,'validate',return_value=self.trusted))
            stack.enter_context(patch.object(agent,'unchanged',return_value=True))
            stack.enter_context(patch.object(agent,'drain'))
            stack.enter_context(patch.object(agent,'guard_admission_status',return_value={
                'admission_paused':True,'pause_reason':'session_maintenance','active_inferences':0,'pending_inferences':0}))
            stack.enter_context(patch('runtime_proof.RunnerResourceProbe',return_value=types.SimpleNamespace(_state=lambda:None,_probe=lambda *_:True)))
            stack.enter_context(patch.object(fs,'shared_exit_observer',side_effect=lambda *args:fs.exited))
            stack.enter_context(patch.object(agent,'_shared_stop_bootout_proof'))
            def removed(target,released,**kwargs):self.assertIs(released(),True)
            stack.enter_context(patch.object(agent,'_wait_launchd_removed',side_effect=removed))
            stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('real signal forbidden')))
            result=agent.stop(self.data,force=force)
        self.assertFalse(self.lock.exists());self.assertFalse(self.state.exists());self.assertFalse(self.plist.exists())
        self.assertEqual(result['force'],force)
        if not force:self.assertTrue(result['normal_idle_drain']);self.assertTrue(result['native_exit_proven'])
        # Existing mutation() accepts the free exact installation path on restart.
        with patch.object(agent,'capture',return_value={**self.owner,'pid':12004}):
            with agent.mutation(self.data):self.assertTrue((self.lock/'lease.json').exists())
        self.assertFalse(self.lock.exists())

    def test_force_then_next_mutation_can_start(self):self.invoke(True)
    def test_ordinary_then_next_mutation_can_start(self):self.invoke(False)

if __name__=='__main__':unittest.main(verbosity=2)
