# SPDX-License-Identifier: Apache-2.0
"""Portable snapshot source lock contracts; no compiler, model or network."""
import hashlib,json,pathlib,sys,tempfile,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts/distribution'))
import build_native,trust
from common import DistributionError
class SnapshotSourceTests(unittest.TestCase):
    def test_sixth_patch_is_exact_minimal_public_relative_input(self):
        lock=json.loads((ROOT/'manifests/distribution/native-sources.json').read_bytes());patches=lock['lily']['patches']
        self.assertEqual(len(patches),6);self.assertEqual(lock['lily']['commit'],'db3f8a7cdb33f1e88b68c6889331abd31098c923')
        sixth=patches[-1];file=trust.verified_file(ROOT,sixth)
        self.assertEqual(sixth['sha256'],'c56c8b0480e0c48dace10c03d59bc0647430d3217b8489f8b3cc94f0d70a5334')
        self.assertEqual(sixth['upstream_fix_commit'],'44807f28f65711fbf06a68ee874310bdca323fbf')
        text=file.read_text();self.assertEqual([line for line in text.splitlines() if line.startswith('+++ ')],['+++ b/src/qwen4exp/model.rs'])
        self.assertNotIn('/Users/',text);self.assertNotIn('private/',text)
        baseline=json.loads((ROOT/'manifests/distribution/lily-source-baseline.json').read_bytes());model=next(r for r in baseline['files'] if r['path']=='src/qwen4exp/model.rs')
        self.assertEqual(model['sha256'],'485ab9b3c76afa24241a6f512b8ef912dad0a69a2b1f5771a35b62951c78f77f')
    def test_tampered_sixth_patch_is_rejected(self):
        item=json.loads((ROOT/'manifests/distribution/native-sources.json').read_bytes())['lily']['patches'][-1]
        with tempfile.TemporaryDirectory(prefix='snapshot-lock-cpu-') as name:
            base=pathlib.Path(name).resolve();file=base/item['path'];file.parent.mkdir(parents=True);file.write_bytes((ROOT/item['path']).read_bytes()+b'\n')
            with self.assertRaises(DistributionError):trust.verified_file(base,item)
    def test_normal_build_plan_reports_six_and_force_helper_is_source_bound(self):
        config={'source_root':str(ROOT),'installation_id':'synthetic-plan-only','runtime_state':'NOT_BUILT'}
        with patch.object(build_native,'installation',return_value=config):plan=build_native.build(pathlib.Path('/synthetic-uncreated-data'))
        self.assertTrue(any('replay 6 canonical Lily patches' in value for value in plan['steps']))
        text=(ROOT/'scripts/distribution/build_native.py').read_text();self.assertIn("'ownership.py','forced_stop.py','common.py'",text)
    def test_source_allowlist_covers_new_helper_test_and_patch(self):
        policy=json.loads((ROOT/'manifests/distribution/source-allowlist.json').read_bytes())
        for name in ('scripts/distribution/forced_stop.py','scripts/distribution/test_forced_stop.py','scripts/distribution/patches/lily/06-snapshot-batch-copy.patch'):
            self.assertTrue(name in policy['exact_paths'] or any(name.startswith(prefix) for prefix in policy['prefixes']))
if __name__=='__main__':unittest.main(verbosity=2)
