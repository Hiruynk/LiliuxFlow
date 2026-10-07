"""Local real-tool check. Separate from portable bootstrap tests; no provider validation."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit
ROOT = Path(__file__).resolve().parents[2]

class ToolTests(unittest.TestCase):
    def test_real_default_scanner_detects_synthetic_credential_and_withholds_match(self):
        tools = Path(os.environ.get('LILIUXFLOW_TEST_SECURITY_TOOLS', ROOT / 'var/build/liliuxflow-security-tools-c1'))
        self.assertTrue(tools.is_dir(), 'install the locked tools into the C1 private directory first')
        audit.tool_versions(tools)
        with tempfile.TemporaryDirectory(prefix='scanner-test-', dir=tools.parent) as tmp:
            task = Path(tmp).resolve()
            source = task / 'source'
            source.mkdir()
            synthetic = 'AK' + 'IA' + ''.join(secrets.SystemRandom().sample('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567', 16))
            (source / 'synthetic.txt').write_text('aws_access_key_id = "' + synthetic + '"\n')
            private_detail=task/'private-detail.json'
            result = audit.scanner(tools, source, task,private_findings=private_detail)
            self.assertEqual(result['status'], 'FAIL')
            self.assertGreater(result['finding_count'], 0)
            self.assertNotIn(synthetic, json.dumps(result))
            self.assertTrue(all({'rule','file','line','end_line','commit','status','fingerprint_sha256','match_sha256','source_line_span_sha256'} <= set(f) for f in result['findings']))
            detail=json.loads(private_detail.read_text())
            self.assertEqual(private_detail.stat().st_mode & 0o777,0o600)
            self.assertNotIn(synthetic,json.dumps(detail))
            self.assertTrue(all(record['finding'].get('EndLine') and record['finding'].get('Fingerprint') for record in detail['findings']))
            self.assertTrue(all('Match' not in f and 'Secret' not in f and 'Fingerprint' not in f for f in result['findings']))
            (source / 'synthetic.txt').write_text('no credentials here\n')
            # A new task report location preserves the first result for this scoped test.
            clean = task / 'clean'
            clean.mkdir()
            self.assertEqual(audit.scanner(tools, source, clean)['status'], 'PASS')

if __name__ == '__main__':
    unittest.main(verbosity=2)
