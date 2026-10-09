# SPDX-License-Identifier: Apache-2.0
"""Named UI-user models-only grants; mocked official APIs and private temp backup."""
from pathlib import Path
import argparse,copy,json,sys,tempfile,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import agent,liliuxflow
from common import DistributionError
BASE='qwen3.8-flash-next-lily-q4-64k'
OPTS=tuple('qwen3.8-flash-next-lily-q4-mtp2-'+v for v in ('64k','128k','262k'))
ALL=(BASE,'qwen3.8-flash-next-lily-q4-128k','qwen3.8-flash-next-lily-q4-262k',*OPTS)
class UIUserACL(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.backup=Path(self.tmp.name).resolve()
  self.user={'user_id':'explicit-ui-user','models':[BASE],'user_role':'proxy_admin','user_email':'ui@example.invalid','max_budget':7,'tpm_limit':4096,'rpm_limit':2,'metadata':{'keep':'unchanged'}}
  self.before=copy.deepcopy(self.user);self.calls=[];self.mode='normal'
  self.registry=types.SimpleNamespace(enabled_profiles=tuple(types.SimpleNamespace(public_alias=a) for a in ALL))
 def http(self,url,method='GET',payload=None,token=None):
  self.calls.append((url,method,payload))
  if '/user/info?' in url:return 200,{'user_info':copy.deepcopy(self.user)}
  self.assertTrue(url.endswith('/user/update'));self.assertEqual(set(payload),{'user_id','models'});self.assertEqual(payload['user_id'],'explicit-ui-user')
  self.user['models']=list(payload['models'])
  if self.mode=='timeout':self.mode='normal';raise TimeoutError('CPU unknown response')
  if self.mode=='drift':self.user['rpm_limit']=99
  return 200,{}
 def invoke(self,aliases=OPTS,dry=True):
  with patch.object(agent,'http_json',side_effect=self.http):return agent.reconcile_designated_ui_user(user_id='explicit-ui-user',api_base='http://127.0.0.1:4000/v1',master_key='CPU-admin-not-live',registry=self.registry,backup_root=self.backup,dry_run=dry,authorized_opt_in_aliases=aliases)
 def test_dry_run_reads_only_and_admin_role_never_grants_implicitly(self):
  result=self.invoke((),True);self.assertEqual(result['models_after'],[BASE]);self.assertFalse(result['writes']);self.assertFalse(any(c[1]=='POST' for c in self.calls));self.assertEqual(list(self.backup.iterdir()),[])
  self.assertNotIn('ui@example.invalid',json.dumps(result));self.assertNotIn('CPU-admin-not-live',json.dumps(result))
 def test_exact_requested_opt64_does_not_add_unrequested_legacy_long_models(self):
  result=self.invoke((OPTS[0],),False);self.assertEqual(self.user['models'],[BASE,OPTS[0]]);self.assertTrue(result['writes'])
  self.assertEqual({k:self.user[k] for k in self.user if k!='models'},{k:self.before[k] for k in self.before if k!='models'})
  self.assertTrue(all('/key/' not in c[0] and '/user/update' in c[0] or c[1]=='GET' for c in self.calls))
 def test_disabled_unknown_or_wildcard_grant_refuses_without_write(self):
  self.registry=types.SimpleNamespace(enabled_profiles=(types.SimpleNamespace(public_alias=BASE),))
  with self.assertRaises(DistributionError):self.invoke((OPTS[0],),False)
  self.assertEqual(self.calls,[])
  self.registry=types.SimpleNamespace(enabled_profiles=tuple(types.SimpleNamespace(public_alias=a) for a in ALL))
  for models in ([],['*'],['all-router-models']):
   self.user['models']=models
   with self.assertRaises(DistributionError):self.invoke((OPTS[0],),False)
  self.assertFalse(any(c[1]=='POST' for c in self.calls))
 def test_wrong_user_identity_refuses(self):
  self.user['user_id']='another-user'
  with self.assertRaises(DistributionError):self.invoke((OPTS[0],),False)
  self.assertFalse(any(c[1]=='POST' for c in self.calls))
 def test_all3_explicit_optins_succeed_and_repeated_execute_is_idempotent(self):
  self.user['models']=list(ALL[:3]);self.before=copy.deepcopy(self.user)
  first=self.invoke(OPTS,False);self.assertTrue(first['writes']);self.assertEqual(self.user['models'],list(ALL))
  second=self.invoke(OPTS,False);self.assertFalse(second['writes']);self.assertEqual(self.user['models'],list(ALL))
  self.assertEqual(len([c for c in self.calls if c[1]=='POST']),1)
 def test_timeout_after_commit_restores_only_proven_original_models(self):
  self.mode='timeout'
  with self.assertRaises(DistributionError):self.invoke((OPTS[0],),False)
  self.assertEqual(self.user,self.before);self.assertEqual(len([c for c in self.calls if c[1]=='POST']),2)
 def test_concurrent_limit_drift_is_not_overwritten(self):
  self.mode='drift'
  with self.assertRaises(DistributionError):self.invoke((OPTS[0],),False)
  self.assertEqual(self.user['rpm_limit'],99);self.assertEqual(len([c for c in self.calls if c[1]=='POST']),1)
 def test_CLI_dispatch_is_explicit_named_user_and_exact_profiles(self):
  root=Path(__file__).resolve().parents[2]
  registry=types.SimpleNamespace(by_id=lambda pid:types.SimpleNamespace(public_alias={'ctx64k-mtp2':OPTS[0]}[pid]))
  trusted={'registry':registry};args=argparse.Namespace(profile_command='grant-ui-user',user_id='chosen-user',profile_ids=['ctx64k-mtp2'],data_root=self.backup,execute=False)
  with patch('profile_registry.load_registry',return_value=registry),patch('trust.validate',return_value=trusted),patch.object(agent,'reconcile_ui_user',return_value={'writes':False}) as reconcile,patch.object(liliuxflow,'emit'):
   self.assertEqual(liliuxflow.profiles(args),0)
  self.assertEqual(reconcile.call_args.args,(trusted,'chosen-user'));self.assertEqual(reconcile.call_args.kwargs,{'dry_run':True,'authorized_opt_in_aliases':(OPTS[0],)})

if __name__=='__main__':unittest.main(verbosity=2)
