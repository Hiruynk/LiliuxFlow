# SPDX-License-Identifier: Apache-2.0
"""Complete captured shutdowns on CPU fixtures only; no real processes or DB."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import types
import unittest
from unittest.mock import patch

import agent
import forced_stop as fs
from common import DistributionError
import test_shared_ended_agent as ended_fixture
import test_force_dispatch as force_fixture


class EndedCaptureProofTests(unittest.TestCase):
    setUp = ended_fixture.EndedAgentTests.setUp
    job = ended_fixture.EndedAgentTests.job
    nested_job = ended_fixture.EndedAgentTests.nested_job

    def prepare(self,shared_record=None):
        self.child={**self.owner,'pid':70002,'ppid':self.owner['pid'],'command_sha256':'b'*64}
        with patch.object(Path,'home',return_value=self.root):
            self.records=agent._shared_stop_ended_record_snapshot(self.trusted,self.state,self.record,shared_record)

    def call(self,*,probe=None,rows=None,jobs=None,kernel_error=False):
        self.prepare() if not hasattr(self,'records') else None
        with contextlib.ExitStack() as stack:
            kill=stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('real signals forbidden')))
            packet=stack.enter_context(patch.object(fs,'_probe',side_effect=probe,return_value=None))
            stack.enter_context(patch.object(fs,'_descendant_rows',return_value=rows or [],side_effect=DistributionError('CPU unknown') if kernel_error else None))
            launch=stack.enter_context(patch.object(agent.subprocess,'run',side_effect=jobs,return_value=self.nested_job()))
            stack.enter_context(patch.object(agent.time,'sleep'))
            agent._shared_stop_recheck_ended_descendants(self.trusted,self.state,self.record,self.plist,self.decoded,
                self.raw,self.stamp,self.owner,self.arguments,[self.child],[self.owner],self.records,None,set(),fs,self.target)
            kill.assert_not_called()
        return packet,launch

    def test_double_actual_absence_and_real_nested_no_PID_job_pass(self):
        packet,launch=self.call()
        self.assertEqual(packet.call_count,4);self.assertEqual(launch.call_count,2)
        self.assertTrue(all(0<call.kwargs['timeout']<=.1 for call in packet.call_args_list))
        self.assertTrue(all(0<call.kwargs['timeout']<=1 for call in launch.call_args_list))
        values=list(self.run.glob('shared-stop-ended-completion-*.json'))
        self.assertEqual(len(values),1);value=json.loads(values[0].read_text())
        self.assertEqual(value['descendant_absence_samples'],2);self.assertEqual(value['job_samples'],2);self.assertTrue(value['owner_absent'])
        self.assertFalse(value['signal_authorized']);self.assertEqual(values[0].stat().st_mode&0o777,0o600)

    def test_live_drift_reused_foreign_or_zombie_PID_never_passes(self):
        self.prepare()
        for value in (self.child,{**self.child,'uid':0},{**self.child,'started':'new birth'},
                      {**self.child,'command_sha256':'c'*64},{**self.child,'ppid':1}):
            with self.subTest(value=value),self.assertRaisesRegex(DistributionError,'not absent'):
                self.call(probe=lambda *args,**kwargs:(value,'CPU_PRIVATE_COMMAND'))

    def test_second_absence_sample_reappearance_refuses(self):
        self.prepare();values=iter([None,None,(self.child,'CPU_PRIVATE_COMMAND')])
        with self.assertRaisesRegex(DistributionError,'not absent'):
            self.call(probe=lambda *args,**kwargs:next(values))

    def test_unknown_probe_and_unknown_kernel_refuse(self):
        self.prepare()
        with self.assertRaises(DistributionError):
            self.call(probe=lambda *args,**kwargs:(_ for _ in ()).throw(DistributionError('CPU unknown')))
        with self.assertRaises(DistributionError):self.call(kernel_error=True)

    def test_extra_uncaptured_descendant_or_kernel_PID_refuses(self):
        self.prepare()
        for row in ({'pid':70003,'ppid':70002},{'pid':70002,'ppid':1}):
            with self.subTest(row=row),self.assertRaisesRegex(DistributionError,'subtree'):
                self.call(rows=[row])

    def test_cached_old_or_replacement_job_PID_is_never_adopted(self):
        self.prepare()
        for pid in (70001,80000):
            with self.subTest(pid=pid),self.assertRaisesRegex(DistributionError,'no-PID'):
                self.call(jobs=[self.nested_job(pid=pid)])
        with self.assertRaisesRegex(DistributionError,'no-PID'):
            self.call(jobs=[self.nested_job(),self.nested_job(pid=80000)])

    def test_unknown_launchd_or_foreign_job_refuses(self):
        self.prepare();raw=self.nested_job().stdout
        for result in (subprocess.CompletedProcess([],1,'','unknown'),
                       subprocess.CompletedProcess([],0,raw,'unknown'),
                       subprocess.CompletedProcess([],0,raw.replace('/CPU/python','/CPU/foreign'),'')):
            with self.subTest(status=result.returncode),self.assertRaises(DistributionError):self.call(jobs=[result])

    def test_changed_registry_bytes_inode_or_new_native_record_refuses(self):
        self.prepare();before=self.state.read_bytes();self.state.write_bytes(before+b' ')
        with self.assertRaisesRegex(DistributionError,'preimage'):self.call()
        self.state.write_bytes(before);replacement=self.state.with_name('replacement')
        replacement.write_bytes(before);replacement.chmod(0o600);os.replace(replacement,self.state)
        with self.assertRaisesRegex(DistributionError,'preimage'):self.call()
        with patch.object(Path,'home',return_value=self.root):
            self.records=agent._shared_stop_ended_record_snapshot(self.trusted,self.state,self.record,None)
        model=self.run/'mtp2-shared-model.json';model.write_text('{}');model.chmod(0o600)
        with self.assertRaisesRegex(DistributionError,'appeared'):self.call()

    def test_changed_plist_or_foreign_host_lease_refuses(self):
        self.prepare();self.plist.write_bytes(self.raw+b' ')
        with self.assertRaisesRegex(DistributionError,'plist changed'):self.call()
        self.plist.write_bytes(self.raw)
        lock=self.run/'host-mutation.lease';lock.mkdir(mode=0o700)
        path=lock/'lease.json';path.write_text(json.dumps({**self.owner,'pid':80000}));path.chmod(0o600)
        with self.assertRaisesRegex(DistributionError,'holder'):self.call()

    def test_full_native_lease_record_and_inode_are_bound(self):
        self.model=self.run/'mtp2-shared-model.json';record={'runner':self.owner,'child':{'pid':70002},'source':'CPU_SOURCE'}
        self.model.write_text(json.dumps(record));self.model.chmod(0o600)
        lease=self.root/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease'
        lease.mkdir(parents=True,mode=0o700);path=lease/'lease.json';path.write_text(json.dumps(record));path.chmod(0o600)
        self.prepare(shared_record=record)
        path.write_text(json.dumps({**record,'source':'CPU_FOREIGN'}))
        with self.assertRaisesRegex(DistributionError,'preimage'):self.call()

    def test_initial_foreign_native_lease_never_authorizes_fallback(self):
        lease=self.root/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease'
        lease.mkdir(parents=True,mode=0o700);path=lease/'lease.json'
        raw=json.dumps({'installation_id':'CPU_FOREIGN'}).encode();path.write_bytes(raw);path.chmod(0o600)
        self.prepare()
        with self.assertRaisesRegex(DistributionError,'not bound'):self.call()
        self.assertEqual(path.read_bytes(),raw)

    def test_exact_frozen_owner_survives_recheck_without_resume_or_signal(self):
        self.prepare()
        kernel={'pid':self.owner['pid'],'ppid':self.owner['ppid'],'uid':os.getuid(),'ruid':os.getuid(),
                'started':self.owner['started'],'stat':'T'}
        read=lambda pid,**kwargs:(self.owner,' '.join(self.arguments)) if pid==self.owner['pid'] else None
        self.call(probe=read,rows=[kernel],jobs=[self.nested_job(pid=self.owner['pid'])]*2)
        value=json.loads(next(self.run.glob('shared-stop-ended-completion-*.json')).read_text())
        self.assertFalse(value['owner_absent']);self.assertFalse(value['signal_authorized'])
        for key,value in (('stat','S'),('uid',0),('ruid',0),('started','new birth'),('ppid',80000)):
            with self.subTest(key=key),self.assertRaises(DistributionError):
                self.call(probe=read,rows=[{**kernel,key:value}],jobs=[self.nested_job(pid=self.owner['pid'])]*2)

    def test_deadline_refuses_before_unbounded_PS_or_launchctl(self):
        self.prepare()
        with patch.object(agent.time,'monotonic',side_effect=[0,6]),patch.object(fs,'_probe') as probe:
            with self.assertRaisesRegex(DistributionError,'deadline'):
                agent._shared_stop_recheck_ended_descendants(self.trusted,self.state,self.record,self.plist,self.decoded,
                    self.raw,self.stamp,self.owner,self.arguments,[self.child],[self.owner],self.records,None,set(),fs,self.target)
            probe.assert_not_called()


class PublicEndedCompletionTests(unittest.TestCase):
    exact=force_fixture.ForceDispatchTests.exact
    send=force_fixture.ForceDispatchTests.send

    def setUp(self):
        force_fixture.ForceDispatchTests.setUp(self)
        (self.data/'run').chmod(0o700)
        self.cfg['ports']['postgresql']=15433
        self.args+=['--mtp2-shared-context'];self.record['shared_context']=True
        self.owner['command_sha256']=hashlib.sha256(' '.join(self.args).encode()).hexdigest()
        self.pg={**self.owner,'pid':12002,'ppid':1,'pgid':12002,'command_sha256':'c'*64}
        self.extra={**self.child,'pid':12003,'ppid':self.child['pid'],'command_sha256':'d'*64}
        self.record['postgres']=self.pg;self.state.write_text(json.dumps(self.record))
        self.decoded={'Label':self.cfg['launchd_label'],'WorkingDirectory':str(self.source),'ProgramArguments':self.args}
        self.plist.write_bytes(plistlib.dumps(self.decoded))
        self.pgdata=self.data/'postgres/data';self.pgdata.mkdir(parents=True)
        self.pidfile=self.pgdata/'postmaster.pid';self.pidfile.write_bytes(b'CPU_EXACT_PGDATA');self.pidfile.chmod(0o600)
        self.live|={12002,12003};self.events=[];self.loaded=True
        self.trusted['registry']=types.SimpleNamespace(default='CPU')
        self.cfg['database_initialized']=True
        self.lock=self.data/'run/host-mutation.lease';self.lock.mkdir(mode=0o700)
        path=self.lock/'lease.json';path.write_text(json.dumps(self.owner));path.chmod(0o600)

    def job(self,pid=None):
        target='gui/'+str(os.getuid())+'/'+self.cfg['launchd_label']
        lines=[target+' = {','\tpath = '+str(self.plist),'\tprogram = '+self.args[0],
               '\tworking directory = '+str(self.source),'\tstate = spawn scheduled','\targuments = {',
               *['\t\t'+arg for arg in self.args],'\t}',
               '\tevent triggers = {','\t\tkeepalive = {','\t\t\tstate = active','\t\t}','\t}']
        if pid is not None:lines+=['\tpid = '+str(pid)]
        return subprocess.CompletedProcess([],0,'\n'.join(lines+['}'])+'\n','')

    def invoke(self,*,force=True,still_live=False,unknown=False,owner_frozen=True,partial_error=True):
        identities={value['pid']:value for value in (self.owner,self.child,self.extra,self.pg)}
        def inspect(pid):return identities[pid] if pid in self.live else None
        def terminate(values,**kwargs):
            if values==[self.owner]:
                self.assertFalse(self.loaded);self.events.append('owner_shutdown');self.live.discard(self.owner['pid']);return
            self.events.append('captured_teardown')
            self.live-={self.child['pid'],self.extra['pid']}
            if not owner_frozen:self.live.discard(self.owner['pid'])
            if still_live:self.live.add(self.extra['pid'])
            if partial_error:raise DistributionError('live owned process identity changed')
        def read(pid,**kwargs):
            if unknown:raise DistributionError('CPU unknown probe')
            value=inspect(pid);return None if value is None else (value,' '.join(self.args) if pid==self.owner['pid'] else 'CPU_PRIVATE_COMMAND')
        def launch(argv,**kwargs):
            return self.job(self.owner['pid'] if self.owner['pid'] in self.live else None) if self.loaded else subprocess.CompletedProcess([],1,'','Could not find service')
        def bootout(argv,**kwargs):
            self.assertNotIn(self.pg['pid'],self.live)
            if owner_frozen:
                self.assertIn(self.owner['pid'],self.live)
                self.assertIn((self.owner['pid'],signal.SIGSTOP),self.signals)
                self.assertNotIn((self.owner['pid'],signal.SIGCONT),self.signals)
            self.events.append('bootout');self.loaded=False
            return types.SimpleNamespace(returncode=0,wait=lambda **kwargs:0)
        def fast(argv,**kwargs):
            self.assertEqual(argv[argv.index('-D')+1],self.pgdata);self.assertIn('fast',argv)
            self.events.append('pg_fast');self.live.discard(self.pg['pid'])
        with contextlib.ExitStack() as stack:
            for name,options in (
                ('exact',{'side_effect':self.exact}),('exited',{'side_effect':lambda value:not self.exact(value)}),
                ('command',{'return_value':' '.join(self.args)}),('inspect',{'side_effect':inspect}),
                ('_probe',{'side_effect':read}),('_descendant_rows',{'side_effect':lambda **kwargs:[{'pid':self.owner['pid'],'ppid':self.owner['ppid'],'uid':os.getuid(),'ruid':os.getuid(),'started':self.owner['started'],'stat':'T'}] if self.owner['pid'] in self.live else []}),
                ('descendants',{'side_effect':lambda pid:[] if pid==self.pg['pid'] else [self.child,self.extra]}),
                ('send',{'side_effect':self.send}),('terminate_all',{'side_effect':terminate}),
                ('shared_exit_observer',{'side_effect':lambda *args:fs.exited})):
                stack.enter_context(patch.object(fs,name,**options))
            stack.enter_context(patch.object(Path,'home',return_value=self.base))
            stack.enter_context(patch.object(agent,'_force_pg_identity',return_value=(self.pg,b'CPU_EXACT_PGDATA')))
            stack.enter_context(patch.object(agent,'postgres_tools',return_value=self.base/'pg'))
            stack.enter_context(patch.object(agent,'pg_environment',return_value={}))
            stack.enter_context(patch.object(agent,'private_run',side_effect=fast))
            stack.enter_context(patch.object(agent.subprocess,'run',side_effect=launch))
            stack.enter_context(patch.object(agent.subprocess,'Popen',side_effect=bootout))
            stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('real signals forbidden')))
            stack.enter_context(patch.object(agent,'validate',return_value=self.trusted))
            stack.enter_context(patch.object(agent,'unchanged',return_value=True))
            stack.enter_context(patch.object(agent,'drain'))
            stack.enter_context(patch.object(agent,'guard_admission_status',return_value={
                'admission_paused':True,'pause_reason':'session_maintenance','active_inferences':0,'pending_inferences':0}))
            stack.enter_context(patch('runtime_proof.RunnerResourceProbe',return_value=types.SimpleNamespace(_state=lambda:None,_probe=lambda *_:True)))
            return agent.stop(self.data,force=force)

    def test_public_force_same_capture_finishes_PG_and_no_PID_job_then_host_lease(self):
        result=self.invoke();self.assertEqual(result['state'],'stopped');self.assertTrue(result['postgres_fast_stop'])
        self.assertEqual(self.events,['captured_teardown','pg_fast','bootout','owner_shutdown'])
        self.assertFalse(self.state.exists());self.assertFalse(self.plist.exists());self.assertFalse(self.lock.exists())
        self.assertFalse(self.live);self.assertTrue(all(number in (signal.SIGSTOP,signal.SIGCONT) for _,number in self.signals))
        value=json.loads(next((self.data/'run').glob('shared-stop-ended-completion-*.json')).read_text())
        self.assertEqual(value['captured_pid_count'],3);self.assertFalse(value['owner_absent'])

    def test_public_same_operation_already_absent_owner_requires_no_PID_job(self):
        self.invoke(owner_frozen=False)
        value=json.loads(next((self.data/'run').glob('shared-stop-ended-completion-*.json')).read_text())
        self.assertTrue(value['owner_absent']);self.assertFalse(self.live)

    def test_original_successful_shared_path_preserves_unrelated_native_lease(self):
        lease=self.base/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease'
        lease.mkdir(parents=True,mode=0o700);path=lease/'lease.json'
        raw=json.dumps({'schema_version':1,'installation_id':'CPU_FOREIGN'}).encode()
        path.write_bytes(raw);path.chmod(0o600);stamp=(path.stat().st_dev,path.stat().st_ino)
        self.invoke(partial_error=False)
        self.assertEqual(path.read_bytes(),raw);self.assertEqual((path.stat().st_dev,path.stat().st_ino),stamp)
        self.assertFalse(list((self.data/'run').glob('shared-stop-ended-completion-*.json')))

    def test_public_ordinary_drain_reuses_same_strict_completion(self):
        result=self.invoke(force=False);self.assertFalse(result['force']);self.assertTrue(result['normal_idle_drain'])
        self.assertFalse(self.lock.exists());self.assertFalse(self.live)

    def test_still_live_extra_or_unknown_probe_retains_original_error_and_PG(self):
        for still_live,unknown in ((True,False),(False,True)):
            self.setUp()
            with self.subTest(still_live=still_live,unknown=unknown),self.assertRaisesRegex(DistributionError,'live owned process identity changed'):
                self.invoke(still_live=still_live,unknown=unknown)
            self.assertEqual(self.events,['captured_teardown']);self.assertIn(self.pg['pid'],self.live)
            self.assertTrue(self.state.exists());self.assertTrue(self.plist.exists());self.assertTrue(self.lock.exists())

    def test_new_invocation_cannot_adopt_absent_agent_or_reconstruct_partial_tree(self):
        self.live.discard(self.owner['pid'])
        with self.assertRaisesRegex(DistributionError,'agent identity/command differs'):self.invoke()
        self.assertEqual(self.events,[]);self.assertEqual(self.signals,[]);self.assertTrue(self.state.exists())


if __name__=='__main__':unittest.main(verbosity=2)
