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
    def task_parent(self, tools):
        return Path(os.environ.get('LILIUXFLOW_TEST_SECURITY_TMPDIR', tools.parent))

    def test_real_default_scanner_detects_synthetic_credential_and_withholds_match(self):
        tools = Path(os.environ.get('LILIUXFLOW_TEST_SECURITY_TOOLS', ROOT / 'var/build/liliuxflow-security-tools-c1'))
        self.assertTrue(tools.is_dir(), 'install the locked tools into the C1 private directory first')
        audit.tool_versions(tools)
        with tempfile.TemporaryDirectory(prefix='scanner-test-', dir=self.task_parent(tools)) as tmp:
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

    def test_real_syft_includes_dev_build_and_uv_lock_with_release_provenance(self):
        tools = Path(os.environ.get('LILIUXFLOW_TEST_SECURITY_TOOLS', ROOT / 'var/build/liliuxflow-security-tools-c1'))
        audit.tool_versions(tools)
        with tempfile.TemporaryDirectory(prefix='sbom-test-', dir=self.task_parent(tools)) as tmp:
            task = Path(tmp).resolve()
            source = task / 'source'
            source.mkdir()
            lock = {'name': 'cpu-source-fixture', 'version': '0.1.2', 'lockfileVersion': 3, 'packages': {
                '': {'name': 'cpu-source-fixture', 'version': '0.1.2', 'devDependencies': {'cpu-build-tool': '1.2.3'}},
                'node_modules/cpu-build-tool': {'version': '1.2.3', 'dev': True,
                    'dependencies': {'cpu-transitive-build': '4.5.6'}, 'license': 'Apache-2.0'},
                'node_modules/cpu-transitive-build': {'version': '4.5.6', 'dev': True, 'license': 'MIT'}}}
            (source / 'package-lock.json').write_text(json.dumps(lock))
            (source / 'uv.lock').write_text('version = 1\nrevision = 3\nrequires-python = ">=3.12"\n'
                '[[package]]\nname = "cpu-python-lock"\nversion = "2.3.4"\n'
                'source = { registry = "https://pypi.org/simple" }\n')
            commit = 'a' * 40
            sbom = audit.syft(tools, source, task, commit, source_version='0.1.2')
            identities = {(c.get('name'), c.get('version')) for c in sbom.get('components', [])}
            self.assertTrue({('cpu-build-tool', '1.2.3'), ('cpu-transitive-build', '4.5.6'),
                             ('cpu-python-lock', '2.3.4')} <= identities)
            self.assertEqual(sbom['metadata']['component']['version'], '0.1.2')
            self.assertIn({'name': 'liliuxflow:source_commit', 'value': commit}, sbom['metadata']['properties'])
            encoded = json.dumps(sbom)
            self.assertNotIn(str(task), encoded)
            self.assertNotIn('/Users/', encoded)
            self.assertNotIn('/home/', encoded)
            # Other callers retain their original source-identity version.
            default_task = task / 'default'
            default_task.mkdir()
            default = audit.syft(tools, source, default_task, commit)
            self.assertEqual(default['metadata']['component']['version'], commit)
            archive_task = task / 'archive'
            archive_task.mkdir()
            archive_identity = 'archive-' + 'b' * 64
            archived = audit.syft(tools, source, archive_task, archive_identity)
            self.assertNotIn('liliuxflow:source_commit', {p['name'] for p in archived['metadata']['properties']})
            self.assertIn({'name': 'liliuxflow:source_identity', 'value': archive_identity},
                          archived['metadata']['properties'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
