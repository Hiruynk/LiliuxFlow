# SPDX-License-Identifier: Apache-2.0
"""CPU privacy cases for source selection, all tracked files, and tar readback."""
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from common import DistributionError
import package


class SourcePackagePolicyTests(unittest.TestCase):
    def test_private_and_macos_metadata_paths_cannot_override_allowlist(self):
        names = (
            '__MACOSX/scripts/code.py', 'scripts/distribution/._package.py',
            'scripts/distribution/.DS_Store', 'scripts/distribution/AGENTS.md',
            'docs/AGENTS_LOCAL_UPDATE.md', 'liliuxflow_public_readiness_plan/EXECUTION_PLAN.md',
            'docs/PUBLIC_READINESS.md', 'liliuxflow_readme_apache_plan/plan.md',
            'scripts/distribution/.env', 'scripts/distribution/.env.production',
            'services/cloudflare-edge/.dev.vars', 'services/cloudflare-edge/.npmrc',
            'llm_recovery_codex_pack/plan.md', 'assets/private-avatar.jpg',
            'services/cloudflare-edge/wrangler.local.json',
            'services/cloudflare-edge/wrangler.production.local.json',
            'services/cloudflare-edge/.wrangler/deploy/config.json',
        )
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(package.eligible(name, {'exact_paths': [name], 'prefixes': ['']}))
        self.assertTrue(package.eligible('services/cloudflare-edge/wrangler.json', {'exact_paths': ['services/cloudflare-edge/wrangler.json'], 'prefixes': []}))

    def test_renamed_appledouble_and_concrete_private_home_paths_are_forbidden(self):
        private_home = b'/' + b'Users/' + b'synthetic-private-owner/model'
        linux_home = b'/' + b'home/' + b'synthetic-private-owner/key'
        unicode_home = b'/' + b'Users/' + '使用者/foo'.encode()
        for data in (b'\x00\x05\x16\x07' + b'renamed-metadata', b'\x00\x05\x16\x00' + b'renamed-metadata', private_home, linux_home, unicode_home):
            self.assertTrue(package.forbidden_content(data))
        self.assertFalse(package.forbidden_content(b'Use <your-private-config> with a caller key.'))

    def commit_fixture(self, root, files):
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        for name, data in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=CPU Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Synthetic source fixture'], check=True)

    def test_whole_tree_checks_unselected_paths_instead_of_only_release_allowlist(self):
        cases = {
            'unselected/AGENTS.md': b'local-only planning',
            '__MACOSX/hidden': b'metadata',
            'unselected/renamed.txt': b'\x00\x05\x16\x07metadata',
            'unselected/note.txt': b'/' + b'Users/' + b'synthetic-private-owner/foo',
        }
        for name, data in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='liliuxflow-tree-cpu-') as directory:
                root = Path(directory) / 'source'
                self.commit_fixture(root, {'code.py': b'print("safe source")\n', name: data})
                with self.assertRaises(DistributionError) as result:
                    package.check_source_tree(root)
                self.assertNotIn('synthetic-private-owner', str(result.exception))

    def test_whole_tree_success_is_explicit_about_separate_secret_scan(self):
        with tempfile.TemporaryDirectory(prefix='liliuxflow-tree-cpu-') as directory:
            root = Path(directory) / 'source'
            self.commit_fixture(root, {'code.py': b'print("safe source")\n'})
            result = package.check_source_tree(root)
            self.assertEqual(result['file_count'], 1)
            self.assertEqual(result['path_format_privacy_check'], 'PASS')
            self.assertEqual(result['secret_scan'], 'SEPARATE_REQUIRED_GATE')

    def test_tar_readback_refuses_metadata_private_paths_and_private_home_content(self):
        cases = [('LiliuxFlow/__MACOSX/source', b'metadata'),
                 ('LiliuxFlow/scripts/._code.py', b'metadata'),
                 ('LiliuxFlow/docs/AGENTS.md', b'planning'),
                 ('LiliuxFlow/code.py', b'\x00\x05\x16\x07metadata'),
                 ('LiliuxFlow/code.py', b'/' + b'Users/' + b'synthetic-private-owner/foo')]
        with tempfile.TemporaryDirectory(prefix='liliuxflow-tar-cpu-') as directory:
            root = Path(directory)
            for index, (name, data) in enumerate(cases):
                archive = root / f'{index}.tar.gz'
                with tarfile.open(archive, 'w:gz') as handle:
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    handle.addfile(member, io.BytesIO(data))
                with self.assertRaises(DistributionError):
                    package.safe_extract(archive, root / f'extract-{index}')

    def test_exact_source_commit_rejects_renamed_metadata_even_when_selected(self):
        with tempfile.TemporaryDirectory(prefix='liliuxflow-package-cpu-') as directory:
            root = Path(directory) / 'source'
            policy = {'exact_paths': ['code.txt', 'manifests/distribution/source-allowlist.json'], 'prefixes': [], 'required_paths': ['code.txt'], 'max_file_bytes': 16777216, 'max_total_bytes': 268435456, 'stage': 'CPU_FIXTURE'}
            self.commit_fixture(root, {'code.txt': b'\x00\x05\x16\x07metadata', 'manifests/distribution/source-allowlist.json': json.dumps(policy).encode()})
            with self.assertRaises(DistributionError):
                package.selected_files(root, 'HEAD')


if __name__ == '__main__':
    unittest.main()
