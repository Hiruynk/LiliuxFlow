# SPDX-License-Identifier: Apache-2.0
"""Real CPU-only owned Python tree; never launchd, DB, network or native code."""
import json,os,pathlib,signal,subprocess,sys,tempfile,time,unittest,uuid
from unittest.mock import patch
import forced_stop as fs
from common import DistributionError

FIXTURE='''import json,os,pathlib,signal,subprocess,sys,time
folder=pathlib.Path(sys.argv[1]);role=sys.argv[2];token=sys.argv[3]
deadline=float(sys.argv[4]);ignore=sys.argv[5]=='ignore';stopping=False
child=None
if role!='grandchild':
 child=subprocess.Popen([sys.executable,'-B',__file__,str(folder),'child' if role=='parent' else 'grandchild',token,str(deadline),sys.argv[5]])
(folder/(role+'.json')).write_text(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),'role':role,'token':token}))
def stop(*_):
 global stopping
 if not (ignore and role=='grandchild'):stopping=True
signal.signal(signal.SIGTERM,stop)
while not stopping and time.time()<deadline:
 if child is not None:child.poll()
 time.sleep(.01)
if child is not None:child.wait(timeout=2)
'''

class RealForceProcessTests(unittest.TestCase):
    def exercise(self,ignore):
        with tempfile.TemporaryDirectory(prefix='liliuxflow-owned-cpu-') as temporary:
            folder=pathlib.Path(temporary).resolve();script=folder/'fixture.py';script.write_text(FIXTURE)
            token=uuid.uuid4().hex;started=time.monotonic();parent=None;owner=None;captured=[];frozen=[]
            report={'scope':'isolated_owned_python_tree_only','ignore_term_leaf':ignore,'signals':[]}
            real_kill=os.kill;allowed=set()
            def audit(pid,number):
                self.assertIn(pid,allowed);self.assertIn(number,(signal.SIGSTOP,signal.SIGCONT,signal.SIGTERM,signal.SIGKILL))
                report['signals'].append({'pid':pid,'signal':number});real_kill(pid,number)
            try:
                parent=subprocess.Popen([sys.executable,'-B',str(script),str(folder),'parent',token,str(time.time()+5),'ignore' if ignore else 'normal'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+2
                while not all((folder/(role+'.json')).exists() for role in ('parent','child','grandchild')):
                    self.assertLess(time.monotonic(),deadline,'fixture startup bound');time.sleep(.01)
                owner=fs.inspect(parent.pid);captured=fs.descendants(parent.pid);self.assertEqual(len(captured),2)
                roles={role:json.loads((folder/(role+'.json')).read_text()) for role in ('parent','child','grandchild')}
                self.assertTrue(all(row['token']==token for row in roles.values()))
                self.assertEqual({x['pid'] for x in [owner,*captured]},{x['pid'] for x in roles.values()})
                allowed.update(x['pid'] for x in [owner,*captured]);report['captured']=[owner,*captured]
                with patch.object(fs.os,'kill',side_effect=audit):
                    self.assertTrue(fs.send(owner,signal.SIGSTOP));frozen.append(owner)
                    for value in captured:
                        self.assertTrue(fs.send(value,signal.SIGSTOP));frozen.append(value)
                    fs.terminate_all(captured,grace=.15 if ignore else .5,resume=True)
                    report['exit_observations']=[{'recorded':x,'now':fs.inspect(x['pid']),'exited':fs.exited(x)} for x in captured]
                    self.assertTrue(all(x['exited'] for x in report['exit_observations']))
                    child=next(x for x in captured if x['pid']==roles['child']['pid'])
                    kernels=[fs._descendant_rows(),fs._descendant_rows()]
                    report['post_term_kernel']=[[x for x in rows if x['pid'] in allowed] for rows in kernels]
                    zombie=[next(x for x in rows if x['pid']==child['pid']) for rows in kernels]
                    self.assertTrue(zombie[0]['stat'].startswith('Z'));self.assertEqual(zombie[0],zombie[1])
                    self.assertFalse(any(x['ppid']==child['pid'] for rows in kernels for x in rows))
                    # A frozen parent cannot reap its child. This is a real Z,
                    # whose changed command must NEVER authorize another signal.
                    self.assertNotEqual(fs.inspect(child['pid']),child)
                    count=len(report['signals'])
                    with self.assertRaises(DistributionError):fs.send(child,signal.SIGTERM)
                    self.assertEqual(len(report['signals']),count)
                    leaf=roles['grandchild']['pid'];middle=roles['child']['pid']
                    terms=[x['pid'] for x in report['signals'] if x['signal']==signal.SIGTERM]
                    self.assertEqual(terms[:2],[leaf,middle]);self.assertNotIn(owner['pid'],terms)
                    if ignore:self.assertIn({'pid':leaf,'signal':signal.SIGKILL},report['signals'])
                    fs.terminate_all([owner],grace=.5,resume=True)
                report['parent_exit']=parent.wait(timeout=1)
                self.assertEqual(report['parent_exit'],0)
                report['status']='REAL_FIXTURE_PASS'
            finally:
                # Only the original six-field identities can be resumed or
                # signalled. The fixture also has its own five-second lifetime.
                with patch.object(fs.os,'kill',side_effect=audit):
                    for value in reversed(frozen):
                        try:fs.send(value,signal.SIGCONT)
                        except DistributionError:pass
                    for value in reversed([*([owner] if owner else []),*captured]):
                        try:fs.send(value,signal.SIGTERM)
                        except DistributionError:pass
                if parent is not None:
                    try:parent.wait(timeout=7)
                    except subprocess.TimeoutExpired:
                        if owner is not None:
                            with patch.object(fs.os,'kill',side_effect=audit):fs.send(owner,signal.SIGKILL)
                        parent.wait(timeout=1)
                deadline=time.monotonic()+1
                while any(fs.inspect(x['pid']) is not None for x in [*captured,*([owner] if owner else [])]) and time.monotonic()<deadline:time.sleep(.01)
                report['cleanup']=[{'pid':x['pid'],'now':fs.inspect(x['pid'])} for x in [*captured,*([owner] if owner else [])]]
                report['cleanup_complete']=all(x['now'] is None for x in report['cleanup'])
                report['elapsed_seconds']=round(time.monotonic()-started,3)
                output=os.environ.get('LILIUXFLOW_FORCE_FIXTURE_RAW_DIRECTORY')
                if output:
                    destination=pathlib.Path(output);destination.mkdir(mode=0o700,parents=True,exist_ok=True)
                    path=destination/('kill-' if ignore else 'graceful-');path=path.with_name(path.name+token+'.json')
                    with path.open('x') as handle:json.dump(report,handle,indent=2);handle.write('\n')
                    path.chmod(0o600)
                self.assertTrue(report['cleanup_complete'],'owned fixture process remained')

    def test_real_frozen_parent_leaf_order_and_zombie_command_change(self):self.exercise(False)
    def test_real_unresponsive_leaf_killed_before_parent_exit(self):self.exercise(True)

if __name__=='__main__':unittest.main(verbosity=2)
