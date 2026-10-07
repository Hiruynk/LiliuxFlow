"""CPU recovery preflight: malformed backups and nonempty targets never reach destructive SQL."""
import contextlib,io,json,os,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import backup_native
from common import DistributionError
class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='liliuxflow-recovery-');self.data=Path(self.tmp.name).resolve();(self.data/'backups').mkdir(mode=0o700);(self.data/'run').mkdir(mode=0o700)
  self.cfg={'database_initialized':True,'installation_id':'new-empty-identity','ports':{'postgresql':25432}}
  self.secret={k:'synthetic-long-'+k+'-fixture-secret' for k in ['LITELLM_MASTER_KEY','LITELLM_SALT_KEY','UI_PASSWORD','MANAGER_BACKEND_TOKEN','GUARD_CONTROL_TOKEN']};self.secret['UI_USERNAME']='admin'
  self.trusted={'data_root':self.data,'config':self.cfg,'secrets':dict(self.secret)}
 def tearDown(self):self.tmp.cleanup()
 def archive(self,parts):
  file=self.data/'backup.tar.gz'
  with tarfile.open(file,'w:gz') as tar:
   for name,payload in parts.items():
    info=tarfile.TarInfo(name);info.size=len(payload);tar.addfile(info,io.BytesIO(payload))
  file.chmod(0o600);return file
 def standard_parts(self):
  return {'database/litellm.dump':b'PGDMP-synthetic-only','installation/install.json':json.dumps({'installation_id':'different-source'}).encode(),'secrets/bootstrap.json':json.dumps(self.secret).encode()}
 def run_restore(self,file,sql=None):
  with patch.object(backup_native,'validate',return_value=self.trusted),patch.object(backup_native,'_verify_recovery_source'),patch.object(backup_native,'postgres_tools',return_value=Path('/synthetic-tools')),patch.object(backup_native,'mutation',return_value=contextlib.nullcontext()),patch.object(backup_native,'pg_environment',return_value={}),patch.object(backup_native,'pg_start',return_value={'pid':12000}) as start,patch.object(backup_native,'pg_stop') as stop,patch.object(backup_native,'private_run',side_effect=sql) as run:
   with self.assertRaises(DistributionError):backup_native.restore(self.data,file)
   return start,stop,run
 def test_malformed_credentials_refused_before_db_start(self):
  parts=self.standard_parts();parts['secrets/bootstrap.json']=b'{}';start,stop,run=self.run_restore(self.archive(parts));start.assert_not_called();run.assert_not_called()
 def test_source_identity_equal_target_refused_before_db_start(self):
  parts=self.standard_parts();parts['installation/install.json']=json.dumps({'installation_id':self.cfg['installation_id']}).encode();start,stop,run=self.run_restore(self.archive(parts));start.assert_not_called();run.assert_not_called()
 def test_path_traversal_member_refused_before_db_start(self):
  parts=self.standard_parts();parts['../outside']=b'x';start,stop,run=self.run_restore(self.archive(parts));start.assert_not_called();run.assert_not_called()
 def test_incomplete_backup_refused_before_db_start(self):
  parts=self.standard_parts();del parts['installation/install.json'];start,stop,run=self.run_restore(self.archive(parts));start.assert_not_called();run.assert_not_called()
 def test_nonempty_any_app_table_refused_without_schema_drop(self):
  start,stop,run=self.run_restore(self.archive(self.standard_parts()),['LiteLLM_UserTable\nLiteLLM_VerificationToken\n','0','1'])
  self.assertEqual(run.call_count,3);self.assertTrue(all('DROP SCHEMA' not in (c.kwargs.get('input_text') or '') for c in run.call_args_list));stop.assert_called_once()
 def test_unknown_table_identifier_refused_without_query_interpolation(self):
  start,stop,run=self.run_restore(self.archive(self.standard_parts()),['malicious;DROP_TABLE\n']);self.assertEqual(run.call_count,1);stop.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
