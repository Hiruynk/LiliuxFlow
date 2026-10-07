"""First-party Apache and original-notice CPU checks; no network or runtime execution."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import notices
from common import DistributionError
ROOT=Path(__file__).resolve().parents[2]
class NoticeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='notice CPU fixture-');self.source=Path(self.temp.name).resolve()
  (self.source/'manifests/distribution').mkdir(parents=True)
  for name in ['original-notices.json','native-sources.json','security-tools.lock.json']:shutil.copyfile(ROOT/'manifests/distribution'/name,self.source/'manifests/distribution'/name)
  self.path=self.source/'manifests/distribution/original-notices.json';self.manifest=json.loads(self.path.read_text())
  for name in ('LICENSE','NOTICE'):shutil.copyfile(ROOT/name,self.source/name)
  for item in self.manifest['files']:
   file=self.source/item['path'];file.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/item['path'],file)
 def tearDown(self):self.temp.cleanup()
 def save(self):self.path.write_text(json.dumps(self.manifest))
 def test_apache_license_and_original_notice_bytes(self):
  result=notices.verify(self.source);self.assertEqual(result['notice_files'],13);self.assertEqual(result['first_party_license'],'Apache-2.0');self.assertTrue(result['first_party_license_adopted']);self.assertFalse(result['new_third_party_terms_accepted'])
  self.assertIn((self.source/'docs/productization/licenses/lily/NOTICE').read_bytes(),(self.source/'NOTICE').read_bytes())
 def test_wrong_firstparty_license_refused(self):
  for value in ('UNSPECIFIED','MIT','Apache-2.0 AND LicenseRef-NonCommercial'):
   with self.subTest(license=value):
    self.manifest['first_party_license']=value;self.save()
    with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_missing_license_refused(self):
  (self.source/'LICENSE').unlink()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_wrong_license_text_even_with_matching_manifest_hash_refused(self):
  file=self.source/'LICENSE';file.write_text('Apache License 2.0 with a conflicting added condition')
  self.manifest['first_party_license_file'].update(sha256=hashlib.sha256(file.read_bytes()).hexdigest(),bytes=file.stat().st_size);self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_missing_notice_refused(self):
  (self.source/'NOTICE').unlink()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_lily_attribution_removed_even_with_rehashed_notice_refused(self):
  file=self.source/'NOTICE';file.write_text('Only first-party attribution remains')
  self.manifest['first_party_notice_file'].update(sha256=hashlib.sha256(file.read_bytes()).hexdigest(),bytes=file.stat().st_size);self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_license_symlink_refused(self):
  (self.source/'LICENSE').unlink();(self.source/'LICENSE').symlink_to(ROOT/'LICENSE')
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_firstparty_adoption_and_thirdparty_acceptance_remain_distinct(self):
  for field,value in [('first_party_license_adopted',False),('new_third_party_terms_accepted',True)]:
   with self.subTest(field=field):
    self.manifest=json.loads((ROOT/'manifests/distribution/original-notices.json').read_text());self.manifest[field]=value;self.save()
    with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_changed_original_notice_refused(self):
  (self.source/self.manifest['files'][0]['path']).write_text('changed fixture notice')
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_missing_component_or_duplicate_refused(self):
  original=list(self.manifest['files'])
  for entries in [original[:-1],original+[original[0]]]:
   self.manifest['files']=entries;self.save()
   with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_wrong_upstream_pin_refused(self):
  self.manifest['files'][0]['source_identity']['commit']='0'*40;self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_unsafe_notice_source_path_refused(self):
  self.manifest['files'][0]['path']='../outside';self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_unsafe_upstream_notice_path_refused(self):
  self.manifest['files'][0]['upstream_path']='/outside/LICENSE';self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_unsafe_firstparty_license_path_refused(self):
  self.manifest['first_party_license_file']['path']='../LICENSE';self.save()
  with self.assertRaises(DistributionError):notices.verify(self.source)
 def test_refresh_preserves_upstream_and_unrelated_manifest_fields(self):
  self.manifest['first_party_license']='UNSPECIFIED';self.manifest['new_terms_accepted']=False
  self.manifest['unrelated_provenance_fixture']={'exact':'preserve nested source data'};self.save()
  before=json.loads(self.path.read_text())
  result=notices.refresh_first_party(self.source);after=json.loads(self.path.read_text())
  self.assertEqual(result['first_party_license'],'Apache-2.0');self.assertNotIn('new_terms_accepted',after)
  policy={'first_party_license','first_party_license_adopted','new_third_party_terms_accepted','first_party_license_file','first_party_notice_file','new_terms_accepted'}
  self.assertEqual({k:v for k,v in before.items() if k not in policy},{k:v for k,v in after.items() if k not in policy})
  self.assertEqual(notices.verify(self.source)['notice_files'],13)
 def test_refresh_wrong_license_refused_without_manifest_write(self):
  before=self.path.read_bytes();(self.source/'LICENSE').write_text('wrong license')
  with self.assertRaises(DistributionError):notices.refresh_first_party(self.source)
  self.assertEqual(self.path.read_bytes(),before)
if __name__=='__main__':unittest.main()
