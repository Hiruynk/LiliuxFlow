# SPDX-License-Identifier: Apache-2.0
"""Known captured owner/job proofs on synthetic paths and observations only."""
import hashlib,json,os,pathlib,plistlib,subprocess,tempfile,unittest
from unittest.mock import Mock,patch
import agent,forced_stop as fs
from common import DistributionError

class EndedAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=pathlib.Path(self.temp.name).resolve()
        self.run=self.root/'run';self.run.mkdir(mode=0o700);self.state=self.run/'agent.json';self.plist=self.run/'stack.plist'
        self.arguments=['/CPU/python','/CPU/source/scripts/distribution/agent.py','--data-root',str(self.root),'--mtp2-shared-context']
        self.owner={'pid':70001,'ppid':1,'pgid':70001,'uid':os.getuid(),'started':'Sun Oct 11 16:33:13 2026','command_sha256':hashlib.sha256(' '.join(self.arguments).encode()).hexdigest()}
        self.record={'schema_version':1,'agent':self.owner};self.state.write_text(json.dumps(self.record));self.state.chmod(0o600)
        self.decoded={'Label':'com.cpu.test','WorkingDirectory':'/CPU/source','ProgramArguments':self.arguments}
        self.raw=plistlib.dumps(self.decoded);self.plist.write_bytes(self.raw);self.plist.chmod(0o600)
        info=self.plist.stat();self.stamp=(info.st_dev,info.st_ino);self.target='gui/'+str(os.getuid())+'/com.cpu.test'
        self.observe=Mock(return_value=True);self.trusted={'data_root':self.root}
    def job(self,*,pid=None,state='spawn scheduled'):
        lines=[self.target+' = {','\tactive count = 0','\tpath = '+str(self.plist),'\ttype = LaunchAgent','\tprogram = '+self.arguments[0],'\tworking directory = '+self.decoded['WorkingDirectory'],'\tstate = '+state,'\targuments = {',*['\t\t'+value for value in self.arguments],'\t}']
        if pid is not None:lines+=['\tpid = '+str(pid)]
        lines+=['}'];return subprocess.CompletedProcess([],0,'\n'.join(lines)+'\n','')
    def call(self,*,current=None,job=None,observations=None):
        from contextlib import ExitStack
        with ExitStack() as stack:
            inspect=stack.enter_context(patch.object(fs,'inspect',side_effect=observations if observations is not None else None,return_value=current))
            stack.enter_context(patch.object(fs,'command',return_value=' '.join(self.arguments)))
            stack.enter_context(patch.object(fs,'exact',return_value=current==self.owner))
            launch=stack.enter_context(patch.object(agent.subprocess,'run',side_effect=job if isinstance(job,list) else None,return_value=job or self.job()))
            kill=stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('no real signals')))
            agent._shared_stop_bootout_proof(self.trusted,self.state,self.record,self.plist,self.decoded,self.raw,self.stamp,self.owner,self.arguments,self.observe,fs,self.target)
            kill.assert_not_called();return inspect,launch
    def test_captured_ended_owner_with_two_fresh_exact_no_PID_jobs_passes(self):
        inspect,launch=self.call();self.assertEqual(launch.call_count,2);self.assertEqual(self.observe.call_count,3)
        diagnostics=list(self.run.glob('shared-stop-agent-check-*.json'));self.assertEqual(len(diagnostics),1)
        value=json.loads(diagnostics[0].read_text());self.assertTrue(value['owner_absent_at_check']);self.assertEqual(value['pid'],70001)
        self.assertEqual(diagnostics[0].stat().st_mode&0o777,0o600);self.assertEqual(value['cause'],'UNKNOWN');self.assertFalse(value['signal_authorized'])
    def test_cached_old_launchd_PID_requires_fresh_double_absence(self):
        self.call(job=self.job(pid=70001))
        with self.assertRaises(DistributionError):self.call(job=self.job(pid=70001),observations=[None,self.owner])
    def test_exact_live_owner_requires_same_job_PID_without_ended_adoption(self):
        self.call(current=self.owner,job=self.job(pid=70001,state='running'));self.observe.assert_not_called()
        with self.assertRaises(DistributionError):self.call(current=self.owner,job=self.job(pid=70002,state='running'))
    def test_persistent_changed_live_or_foreign_reused_owner_refuses(self):
        self.observe.return_value=False
        with self.assertRaisesRegex(DistributionError,'frozen agent'):self.call(current={**self.owner,'command_sha256':'b'*64})
        for key,value in (('pid',70002),('uid',0),('pgid',70002),('started','new birth')):
            self.observe.reset_mock()
            with self.subTest(key=key),self.assertRaisesRegex(DistributionError,'birth/group/owner'):self.call(current={**self.owner,key:value})
            self.observe.assert_not_called()
    def test_replacement_PID_malformed_PID_or_second_job_drift_refuses(self):
        for job in (self.job(pid=70002),self.job(pid='bad'),self.job(pid=-1)):
            with self.subTest(job=job.stdout),self.assertRaises(DistributionError):self.call(job=job)
        with self.assertRaises(DistributionError):self.call(job=[self.job(),self.job(pid=70002)])
    def test_changed_job_path_program_arguments_directory_header_or_state_refuses(self):
        raw=self.job().stdout
        for old,new in ((str(self.plist),'other.plist'),('program = /CPU/python','program = /CPU/foreign'),('--mtp2-shared-context','--foreign'),('working directory = /CPU/source','working directory = /CPU/foreign'),(self.target+' = {','gui/0/foreign = {'),('state = spawn scheduled','state = unknown')):
            with self.subTest(old=old),self.assertRaises(DistributionError):self.call(job=subprocess.CompletedProcess([],0,raw.replace(old,new),'') )
    def test_unknown_launchd_status_and_duplicate_PID_refuse(self):
        for job in (subprocess.CompletedProcess([],1,'','unknown'),subprocess.CompletedProcess([],0,self.job().stdout,'unexpected stderr'),subprocess.CompletedProcess([],0,self.job(pid=70001).stdout+'pid = 70001\n','')):
            with self.assertRaises(DistributionError):self.call(job=job)
    def test_changed_plist_bytes_inode_or_registry_refuses(self):
        self.plist.write_bytes(self.raw+b' ')
        with self.assertRaisesRegex(DistributionError,'plist changed'):self.call()
        self.plist.write_bytes(self.raw);self.state.write_text(json.dumps({**self.record,'new':True}))
        with self.assertRaisesRegex(DistributionError,'registry changed'):self.call()
        self.state.write_text(json.dumps(self.record));self.stamp=(0,0)
        with self.assertRaisesRegex(DistributionError,'plist changed'):self.call()
    def test_owned_agent_can_remove_its_old_registry_before_exact_ended_job_cleanup(self):
        self.state.unlink();self.call()
    def test_owner_exit_proof_drift_between_job_checks_refuses(self):
        self.observe.side_effect=[True,True,False]
        with self.assertRaisesRegex(DistributionError,'captured agent exit changed'):self.call()
    def nested_job(self,*,pid=None):
        value=self.job(pid=pid);lines=value.stdout.splitlines()
        # Representative launchctl resource/keepalive scopes. Nested names
        # collide with every checked job field; none supplies job authority.
        nested=['\tevent triggers = {','\t\tkeepalive = {','\t\t\tstate = active',
            '\t\t\tpid = 80002','\t\t\tpath = nested-path','\t\t\tprogram = nested-program',
            '\t\t\tworking directory = nested-directory','\t\t\targuments = {','\t\t\t\tnested-arg','\t\t\t}',
            '\t\t}','\t}','\tresource coalition = {','\t\tID = 100008','\t\ttype = resource','\t\tstate = active','\t}',
            '\tenvironment = {','\t\tCPU_SYNTHETIC_ENV => NOT_CAPTURED','\t}']
        return subprocess.CompletedProcess([],0,'\n'.join(lines[:-1]+nested+[lines[-1]])+'\n','')
    def test_realistic_nested_active_states_and_fields_do_not_override_top_job(self):
        job=self.nested_job();self.call(job=job)
        fields,args=agent._shared_launchd_job_fields(job.stdout.splitlines())
        self.assertEqual(fields['state'],'spawn scheduled');self.assertNotIn('pid',fields);self.assertEqual(args,self.arguments)
        self.assertNotIn('CPU_SYNTHETIC_ENV',fields);self.assertNotIn('NOT_CAPTURED',json.dumps(fields))
    def test_nested_metadata_never_hides_missing_or_changed_top_fields(self):
        raw=self.nested_job().stdout
        for changed in (raw.replace('\tpath = '+str(self.plist)+'\n','',1),raw.replace('\tprogram = /CPU/python','\tprogram = /CPU/foreign',1),raw.replace('\tstate = spawn scheduled','\tstate = unknown',1)):
            with self.assertRaises(DistributionError):self.call(job=subprocess.CompletedProcess([],0,changed,''))
    def test_duplicate_top_fields_or_top_arguments_refuse_with_nested_metadata(self):
        raw=self.nested_job().stdout
        for line in ('\tstate = spawn scheduled','\tpath = '+str(self.plist),'\tprogram = /CPU/python','\tworking directory = /CPU/source','\targuments = {\n\t\t/CPU/python\n\t}'):
            changed=raw.rsplit('}',1)[0]+line+'\n}\n'
            with self.assertRaisesRegex(DistributionError,'duplicate top-level'):self.call(job=subprocess.CompletedProcess([],0,changed,''))
        job=self.nested_job(pid=70001);changed=job.stdout.rsplit('}',1)[0]+'\tpid = 70001\n}\n'
        with self.assertRaisesRegex(DistributionError,'duplicate top-level'):self.call(job=subprocess.CompletedProcess([],0,changed,''))
    def test_incomplete_blocks_or_extra_top_indentation_refuse(self):
        raw=self.nested_job().stdout
        for changed in (raw[:-2],raw.replace('\tpath =','\t\tpath =',1),raw+'\tpid = 70001\n',raw.replace('\t\tstate = active\n\t}','\t\tstate = active\n}',1)):
            with self.assertRaises(DistributionError):self.call(job=subprocess.CompletedProcess([],0,changed,''))

if __name__=='__main__':unittest.main(verbosity=2)
