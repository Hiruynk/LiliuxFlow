"""CPU-only log-location and safety fixtures. No launchd/service/model action."""
import os,sys,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE));sys.path.append(os.environ.get('LILIUXFLOW_LOG_TEST_SUPPORT',str(HERE)))
import agent
from common import DistributionError
class UserLogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='userlog-CPU-');self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name).resolve();self.id='ee50290b-e7a1-4938-a9ee-bb3c358920c8';self.trusted={'config':{'installation_id':self.id,'owner_uid':os.getuid()}};self.mock=patch.object(Path,'home',return_value=self.home);self.mock.start();self.addCleanup(self.mock.stop)
 def test_path_bound_UUID_standard_location_not_dataDesktop(self):
  p=agent.user_launchd_log(self.trusted);self.assertEqual(p,self.home/'Library/Logs/LiliuxFlow'/self.id/'agent.log');self.assertFalse(p.exists())
 def test_prepare_private_regular_file_and_append_preserve_bytes(self):
  p=agent.user_launchd_log(self.trusted,prepare=True);self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertEqual(p.parent.stat().st_mode&0o777,0o700);p.write_text('retained public fixture');agent.user_launchd_log(self.trusted,prepare=True);self.assertEqual(p.read_text(),'retained public fixture')
 def test_symlink_log_refused(self):
  p=agent.user_launchd_log(self.trusted,prepare=True);p.unlink();other=self.home/'other';other.write_text('unchanged');p.symlink_to(other)
  with self.assertRaises(DistributionError):agent.user_launchd_log(self.trusted,prepare=True)
  self.assertEqual(other.read_text(),'unchanged')
 def test_directory_link_refused(self):
  (self.home/'Library').mkdir();outside=self.home/'outside';outside.mkdir();(self.home/'Library/Logs').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(DistributionError):agent.user_launchd_log(self.trusted,prepare=True)
 def test_badUUID_or_owner_refused(self):
  for cfg in [dict(self.trusted['config'],installation_id='../outside'),dict(self.trusted['config'],owner_uid=os.getuid()+1)]:
   with self.assertRaises(DistributionError):agent.user_launchd_log({'config':cfg},prepare=True)
 def test_existing_public_mode_log_not_chmod_silently(self):
  p=agent.user_launchd_log(self.trusted,prepare=True);p.chmod(0o644)
  with self.assertRaises(DistributionError):agent.user_launchd_log(self.trusted,prepare=True)
  self.assertEqual(p.stat().st_mode&0o777,0o644)
 def test_hardlink_log_refused(self):
  p=agent.user_launchd_log(self.trusted,prepare=True);os.link(p,self.home/'linked')
  with self.assertRaises(DistributionError):agent.user_launchd_log(self.trusted,prepare=True)
if __name__=='__main__':unittest.main(verbosity=2)
