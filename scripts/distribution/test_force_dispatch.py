# SPDX-License-Identifier: Apache-2.0
"""Explicit portable force dispatch on fake registries/processes only."""
import argparse,contextlib,hashlib,json,os,pathlib,plistlib,signal,subprocess,sys,tempfile,types,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import agent,forced_stop,liliuxflow
from common import DistributionError
class ForceDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='portable-force CPU 空間-');self.addCleanup(self.temp.cleanup)
        self.base=pathlib.Path(self.temp.name).resolve();self.data=self.base/'data private';self.source=self.base/'source';(self.data/'run').mkdir(parents=True);self.source.mkdir()
        identity='12345678-1234-5678-1234-567812345678';self.cfg={'installation_id':identity,'owner_uid':os.getuid(),'launchd_label':'com.diurnoctra.liliuxflow.'+identity,'ports':{'postgresql':15432}}
        self.trusted={'config':self.cfg,'data_root':self.data,'source_root':self.source,'binaries':{'compat_python':self.base/'python'},'trust':{'binaries':{'lily':{'sha256':'a'*64}}}}
        self.args=[str(self.base/'python'),str(self.source/'scripts/distribution/agent.py'),'--data-root',str(self.data)]
        self.owner={'pid':12000,'ppid':1,'pgid':12000,'uid':os.getuid(),'started':'Fri Oct 9 08:00:00 2026','command_sha256':hashlib.sha256(' '.join(self.args).encode()).hexdigest()}
        self.child={**self.owner,'pid':12001,'ppid':12000,'command_sha256':'b'*64}
        self.record={'schema_version':1,'installation_id':identity,'agent':self.owner,'children':{'manager':self.child},'postgres':None}
        self.state=self.data/'run/agent.json';self.state.write_text(json.dumps(self.record));self.state.chmod(0o600)
        self.plist=self.data/'run/stack.plist';self.plist.write_bytes(plistlib.dumps({'Label':self.cfg['launchd_label'],'WorkingDirectory':str(self.source),'ProgramArguments':self.args}));self.plist.chmod(0o600)
        self.live={12000,12001};self.signals=[]
    def exact(self,value):return value['pid'] in self.live
    def send(self,value,number):
        self.signals.append((value['pid'],number))
        if number==signal.SIGTERM:self.live.discard(value['pid'])
        return True
    def patches(self):
        stack=contextlib.ExitStack();stack.enter_context(patch.object(forced_stop,'exact',side_effect=self.exact));stack.enter_context(patch.object(forced_stop,'exited',side_effect=lambda x:not self.exact(x)));stack.enter_context(patch.object(forced_stop,'command',return_value=' '.join(self.args)))
        stack.enter_context(patch.object(forced_stop,'descendants',return_value=[self.child]));stack.enter_context(patch.object(forced_stop,'send',side_effect=self.send))
        stack.enter_context(patch.object(forced_stop,'terminate_all',side_effect=lambda values,**_:self.live.difference_update(x['pid'] for x in values)))
        stack.enter_context(patch.object(agent,'_force_pg_identity',return_value=(None,None)));stack.enter_context(patch.object(agent,'_force_clear_model_records'))
        stack.enter_context(patch.object(agent.subprocess,'Popen',return_value=types.SimpleNamespace(wait=lambda timeout:0,returncode=0)))
        stack.enter_context(patch.object(agent.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'pid = 12000\n',''),subprocess.CompletedProcess([],1,'','Could not find service')]))
        return stack
    def test_explicit_force_skips_busy_drain_and_stops_exact_owned_only(self):
        with self.patches(),patch.object(agent,'drain',side_effect=AssertionError('force must cancel own active request')):result=agent._force_stop(self.trusted)
        self.assertTrue(result['force']);self.assertFalse(self.state.exists());self.assertFalse(self.plist.exists())
        self.assertTrue(all(pid in (12000,12001) for pid,_ in self.signals));self.assertIn((12000,signal.SIGSTOP),self.signals)
    def test_wrong_plist_or_agent_identity_never_signals(self):
        decoded=plistlib.loads(self.plist.read_bytes());decoded['Label']='foreign';self.plist.write_bytes(plistlib.dumps(decoded))
        with self.patches(),self.assertRaises(DistributionError):agent._force_stop(self.trusted)
        self.assertEqual(self.signals,[]);self.assertTrue(self.state.exists())
    def test_failed_capture_resumes_exact_frozen_parent_and_retains_records(self):
        with self.patches(),patch.object(forced_stop,'descendants',side_effect=DistributionError('fixture foreign UID')),self.assertRaises(DistributionError):agent._force_stop(self.trusted)
        self.assertIn((12000,signal.SIGSTOP),self.signals);self.assertIn((12000,signal.SIGCONT),self.signals);self.assertTrue(self.state.exists())
    def test_dry_force_plan_never_freezes_or_boots_out(self):
        with self.patches(),patch.object(agent.subprocess,'Popen') as spawn:result=agent._force_stop(self.trusted,dry_run=True)
        self.assertEqual(result['state'],'validated_force_stop_plan');self.assertEqual(self.signals,[]);spawn.assert_not_called()
    def test_pg_family_excluded_from_TERM_KILL_and_normal_fast_path_only(self):
        pg={**self.owner,'pid':12002,'ppid':12000,'command_sha256':'c'*64};worker={**pg,'pid':12003,'ppid':12002};self.record['postgres']=pg;self.state.write_text(json.dumps(self.record));self.live|={12002,12003}
        pgdata=self.data/'postgres/data';pgdata.mkdir(parents=True);pidfile=pgdata/'postmaster.pid';pidfile.write_bytes(b'owned fixture bytes');pidfile.chmod(0o600)
        def descendants(pid):return [worker] if pid==12002 else [self.child,pg,worker]
        def normal_fast(argv,**_):self.assertIn('-m',argv);self.assertIn('fast',argv);self.assertEqual(argv[argv.index('-D')+1],pgdata);self.live-={12002,12003}
        with self.patches(),patch.object(agent,'_force_pg_identity',return_value=(pg,b'owned fixture bytes')),patch.object(forced_stop,'descendants',side_effect=descendants),patch.object(agent,'postgres_tools',return_value=self.base/'pg'),patch.object(agent,'pg_environment',return_value={}),patch.object(agent,'private_run',side_effect=normal_fast):result=agent._force_stop(self.trusted)
        self.assertTrue(result['postgres_fast_stop']);self.assertFalse(any(pid in (12002,12003) for pid,_ in self.signals))
    def test_cli_force_is_explicit_stop_only_and_default_stop_stays_graceful(self):
        for tokens,force in ((['stop','--force','--dry-run'],True),(['stop','--dry-run'],False)):
            captured=[]
            with patch.object(sys,'argv',['liliuxflow',*tokens]),patch.object(liliuxflow,'require_runtime',side_effect=lambda args:captured.append(getattr(args,'force',False)) or 0):self.assertEqual(liliuxflow.main(),0)
            self.assertEqual(captured,[force])
        with patch.object(agent,'validate',return_value=self.trusted),patch.object(agent,'_force_stop',return_value={'force':True}) as force:agent.stop(self.data,force=True)
        force.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
