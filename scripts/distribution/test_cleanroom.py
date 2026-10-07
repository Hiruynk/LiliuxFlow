"""CPU archive provenance counterexamples; never start a service or read model data."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cleanroom import verify_archive
from common import DistributionError

class CleanroomTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='archive CPU 測試-');self.root=Path(self.temp.name).resolve()
        self.archive=self.root/'source.tar.gz';self.manifest=self.root/'source.manifest.json'
        self.commit='a'*40;self.data=json.dumps({'schema_version':1,'commit':self.commit}).encode()
        with tarfile.open(self.archive,'w:gz') as handle:
            member=tarfile.TarInfo('LiliuxFlow/SOURCE_COMMIT.json');member.size=len(self.data);handle.addfile(member,io.BytesIO(self.data))
        self.receipt={'schema_version':1,'commit':self.commit,'archive_sha256':hashlib.sha256(self.archive.read_bytes()).hexdigest(),'files':[{'path':'SOURCE_COMMIT.json','bytes':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}]}
    def tearDown(self):self.temp.cleanup()
    def run_receipt(self):
        self.manifest.write_text(json.dumps(self.receipt));return verify_archive(self.archive,self.manifest,self.root/'new unpacked')
    def test_exact_membership_and_embedded_commit(self):
        source,receipt=self.run_receipt();self.assertEqual((source/'SOURCE_COMMIT.json').read_bytes(),self.data);self.assertEqual(receipt['commit'],self.commit)
    def test_reject_manifest_escape_before_extraction(self):
        self.receipt['files'][0]['path']='../outside'
        with self.assertRaises(DistributionError):self.run_receipt()
        self.assertFalse((self.root/'new unpacked').exists())
    def test_reject_changed_archive(self):
        self.archive.write_bytes(self.archive.read_bytes()+b'altered')
        with self.assertRaises(DistributionError):self.run_receipt()
        self.assertFalse((self.root/'new unpacked').exists())
    def test_reject_member_identity_even_when_archive_digest_matches(self):
        self.receipt['files'][0]['sha256']='0'*64
        with self.assertRaises(DistributionError):self.run_receipt()
    def test_reject_embedded_commit_mismatch(self):
        self.receipt['commit']='b'*40
        with self.assertRaises(DistributionError):self.run_receipt()
    def test_reject_missing_member(self):
        self.receipt['files'].append({'path':'missing.py','bytes':0,'sha256':hashlib.sha256(b'').hexdigest()})
        with self.assertRaises(DistributionError):self.run_receipt()
if __name__=='__main__':unittest.main()
