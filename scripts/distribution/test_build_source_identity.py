# SPDX-License-Identifier: Apache-2.0
"""Source identity preflight and CPU-only build fixtures; no native/network/model action."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native as builder
from common import DistributionError
from ui_recipe import git_environment

ROOT = Path(__file__).resolve().parents[2]


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='source identity CPU 空間-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir(mode=0o700)
        self.data = self.base / 'installation'
        self.data.mkdir(mode=0o700)
        self.config = {'source_root': str(self.source), 'runtime_state': 'NOT_BUILT',
                       'installation_id': 'cpu-fixture-only'}
        self.real_run = subprocess.run

    def git(self, root, *args):
        return self.real_run(['git', '-C', str(root), *args], env=git_environment(root),
                             text=True, capture_output=True, check=True).stdout.strip()

    def checkout(self, destination=None):
        # An inert test repository works with shallow CI and a Git-less source
        # package. These fixture commits never identify or authorize product code.
        destination = destination or self.source
        destination.mkdir(mode=0o700, exist_ok=True)
        self.git(destination, 'init', '--quiet')
        for message in ('inert source fixture baseline', 'inert source fixture head'):
            self.git(destination, '-c', 'user.name=Source Identity Fixture',
                     '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                     'commit', '--quiet', '--allow-empty', '-m', message)
        return destination

    def record(self, value=None):
        if value is None:
            allowlist = self.source / 'manifests/distribution/source-allowlist.json'
            allowlist.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'manifests/distribution/source-allowlist.json', allowlist)
            value = {'schema_version': 1, 'commit': 'a' * 40, 'allowlist_sha256': builder.sha256(allowlist)}
        (self.source / 'SOURCE_COMMIT.json').write_text(json.dumps(value))

    def lock(self):
        target = self.source / 'manifests/distribution/native-sources.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'manifests/distribution/native-sources.json', target)
        baseline = self.source / 'manifests/distribution/lily-source-baseline.json'
        shutil.copyfile(ROOT / 'manifests/distribution/lily-source-baseline.json', baseline)

    def plan(self):
        with patch.object(builder, 'installation', return_value=self.config):
            return builder.build(self.data)

    def refused_before_work(self):
        before = sorted(p.relative_to(self.data).as_posix() for p in self.data.rglob('*'))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(builder, 'installation', return_value=self.config))
            spies = [stack.enter_context(patch.object(builder, name, side_effect=AssertionError('preflight must stop first')))
                     for name in ('download', 'extract', 'command', 'private_directory', 'install_source_view', 'write_json_new')]
            for target in ('build_budget.snapshot', 'model_installer._sdk', 'model_catalog.load_catalog'):
                spies.append(stack.enter_context(patch(target, side_effect=AssertionError('no snapshot/model work'))))
            with self.assertRaises(DistributionError) as failure:
                builder.build(self.data, execute=True)
            for spy in spies:
                spy.assert_not_called()
        self.assertIn('git clone', str(failure.exception))
        self.assertIn('source release asset', str(failure.exception))
        self.assertEqual(before, sorted(p.relative_to(self.data).as_posix() for p in self.data.rglob('*')))

    def prepare_mock_build(self):
        # Copy finite product inputs only; installation data never comes from ROOT.
        for prefix in ('scripts/distribution', 'services/compat/src/compat_api',
                       'manifests/distribution', 'profiles/distribution'):
            for file in (ROOT / prefix).rglob('*'):
                if file.is_file() and file.suffix in ('.py', '.json', '.patch') and '__pycache__' not in file.parts:
                    target = self.source / file.relative_to(ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, target)
        paths = ['services/model-installer/pyproject.toml', 'services/model-installer/uv.lock',
                 'docs/productization/MODEL_LICENSE.txt', 'patches/litellm-welcome/runtime.py']
        welcome = json.loads((ROOT / 'manifests/distribution/welcome-assets.json').read_text())
        paths += [item['path'] for item in [welcome['helper'], *welcome['assets']]]
        for value in paths:
            target = self.source / value
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / value, target)
        self.pg = self.base / 'fixture-pg'
        self.pg.mkdir(mode=0o700)
        for name in ('postgres', 'initdb', 'pg_ctl', 'psql', 'pg_dump', 'pg_restore'):
            (self.pg / name).write_text('inert CPU fixture; never executed')

    def mock_build(self, change_identity):
        self.prepare_mock_build()
        changed = False

        def fetch(url, target, expected, **kwargs):
            nonlocal changed
            if not changed:
                change_identity()
                changed = True
            return target

        def extract(archive, destination, **kwargs):
            destination.mkdir(mode=0o700)
            paths = {'lily': ['Cargo.toml', 'Cargo.lock'],
                     'llama_swap': ['go.mod', 'go.sum', 'ui/package.json', 'ui/package-lock.json'],
                     'litellm': ['ui/litellm-dashboard/package.json', 'ui/litellm-dashboard/package-lock.json',
                                 'ui/litellm-dashboard/out/index.html']}[archive.stem.split('.')[0]]
            for value in paths:
                target = destination / value
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('inert CPU fixture')
            return []

        def run(argv, **kwargs):
            if argv[0] == 'git' and 'rev-parse' in argv:
                return self.real_run(argv, **kwargs)
            value = ('1.102.1' if '-c' in argv else 'rustc 1.97.0' if str(argv[0]).endswith('cargo')
                     else 'go version go1.27.1' if str(argv[0]).endswith('go')
                     else 'v24.14.1' if str(argv[0]).endswith('node')
                     else 'postgres (PostgreSQL) 17.6' if str(argv[0]).endswith('postgres') else '')
            return subprocess.CompletedProcess(argv, 0, value + '\n', '')

        def command(argv, **kwargs):
            if 'sync' in argv:
                service = Path(argv[argv.index('--project') + 1]).name
                target = self.data / 'runtime' / service / 'bin/python'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('inert CPU interpreter fixture')
                if service == 'litellm':
                    (self.data / 'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/_experimental').mkdir(parents=True)
            elif str(argv[0]).endswith('cargo'):
                target = Path(argv[argv.index('--target-dir') + 1]) / 'release/lily'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('inert CPU Lily fixture')
            elif str(argv[0]).endswith('go'):
                Path(argv[argv.index('-o') + 1]).write_text('inert CPU manager fixture')
            return {'step': kwargs['log'].stem, 'exit_code': 0}

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(builder, 'installation', return_value=self.config))
            stack.enter_context(patch.object(builder.platform, 'system', return_value='Darwin'))
            stack.enter_context(patch.object(builder.platform, 'machine', return_value='arm64'))
            stack.enter_context(patch.object(builder, 'download', side_effect=fetch))
            stack.enter_context(patch.object(builder, 'extract', side_effect=extract))
            stack.enter_context(patch.object(builder.subprocess, 'run', side_effect=run))
            stack.enter_context(patch.object(builder, 'command', side_effect=command))
            stack.enter_context(patch.object(builder, 'install_project_node', return_value={}))
            stack.enter_context(patch.object(builder.shutil, 'which', return_value='/fixture/uv'))
            stack.enter_context(patch.object(builder, 'verified_file', side_effect=lambda base, item: base / item['path']))
            for target in ('native_recipe.apply_lily_source_patches', 'native_recipe.apply_manager_source_patches',
                           'welcome_native.install_welcome',
                           'patch_litellm_profiles.install_runtime_policy', 'model_catalog.load_catalog', 'profile_registry.load_registry'):
                stack.enter_context(patch(target, return_value={}))
            resolve = stack.enter_context(patch.object(builder, 'resolve_source_commit', wraps=builder.resolve_source_commit))
            result = builder.build(self.data, cargo=self.base / 'cargo', go=self.base / 'go',
                                   node=self.base / 'node', npm_cli=self.base / 'npm-cli', pg_bin=self.pg, execute=True)
            resolve.assert_called_once_with(self.source)
        self.assertTrue(changed)
        records = [json.loads((self.data / path).read_text()) for path in (
            'runtime/release-trust.json', 'runtime/dependency-inputs.json', 'runtime/source/SOURCE_COMMIT.json')]
        self.assertEqual([records[0]['source_commit'], records[1]['source_commit'], records[2]['commit']],
                         [result['source_commit']] * 3)
        return result['source_commit']

    def test_source_archive_record_precedes_git_and_is_frozen_through_mock_build(self):
        self.record()
        self.lock()
        with patch.object(builder.subprocess, 'run', side_effect=AssertionError('archive identity must not invoke Git')):
            self.assertEqual(self.plan()['source_commit'], 'a' * 40)
        self.assertEqual(self.mock_build(lambda: self.record({'schema_version': 1, 'commit': 'c' * 40,
            'allowlist_sha256': builder.sha256(self.source / 'manifests/distribution/source-allowlist.json')})), 'a' * 40)

    def test_checkout_identity_ignores_inherited_git_overrides_and_freezes_head(self):
        self.checkout()
        commit = self.git(self.source, 'rev-parse', 'HEAD')
        previous = self.git(self.source, 'rev-parse', 'HEAD^')
        self.lock()
        with patch.dict(os.environ, {'GIT_DIR': str(self.base / 'unrelated.git'), 'GIT_WORK_TREE': str(self.base),
                                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.bare', 'GIT_CONFIG_VALUE_0': 'true'}):
            self.assertEqual(self.plan()['source_commit'], commit)
        self.assertEqual(self.mock_build(lambda: self.git(self.source, 'update-ref', 'HEAD', previous)), commit)
        self.assertEqual(self.git(self.source, 'rev-parse', 'HEAD'), previous)

    def test_legal_linked_worktree_uses_its_own_root(self):
        self.checkout()
        linked = self.base / 'linked worktree 日本語'
        commit = self.git(self.source, 'rev-parse', 'HEAD')
        self.git(self.source, 'worktree', 'add', '--detach', '--no-checkout', str(linked), commit)
        self.assertTrue((linked / '.git').is_file())
        self.source = linked
        self.config['source_root'] = str(linked)
        self.lock()
        self.assertEqual(self.plan()['source_commit'], commit)

    def test_malformed_or_unsupported_record_never_falls_back_or_starts_work(self):
        values = [None, [], {'schema_version': True, 'commit': 'a' * 40},
                  {'schema_version': 2, 'commit': 'a' * 40}, {'schema_version': 1},
                  {'schema_version': 1, 'commit': 'a' * 40},
                  {'schema_version': 1, 'commit': 'a' * 7}, {'schema_version': 1, 'commit': 'g' * 40},
                  {'schema_version': 1, 'commit': '0' * 40}, {'schema_version': 1, 'commit': 123},
                  {'schema_version': 1, 'commit': 'a' * 40, 'allowlist_sha256': 'bad'},
                  {'schema_version': 1, 'commit': 'a' * 40, 'runtime_source_view': 'true'},
                  {'schema_version': 1, 'commit': 'a' * 40, 'unknown_schema_field': True}]
        for value in values:
            with self.subTest(record=value):
                (self.source / 'SOURCE_COMMIT.json').write_text(json.dumps(value))
                with patch.object(builder.subprocess, 'run', side_effect=AssertionError('invalid record cannot fall back to Git')):
                    self.refused_before_work()
        (self.source / 'SOURCE_COMMIT.json').write_text('{invalid-json')
        self.refused_before_work()

    def test_zip_without_source_record_stops_before_fetch_build_snapshot_or_model(self):
        self.refused_before_work()

    def test_zip_nested_under_unrelated_git_root_cannot_inherit_parent_head(self):
        parent = self.checkout(self.base / 'parent-checkout')
        self.source = parent / 'extracted ZIP'
        self.source.mkdir()
        self.config['source_root'] = str(self.source)
        self.assertEqual(self.real_run(['git', '-C', str(self.source), 'rev-parse', '--show-toplevel'],
                                     capture_output=True, text=True, check=True).stdout.strip(), str(parent))
        self.refused_before_work()

    def test_mismatched_git_root_and_invalid_git_commit_fail_closed(self):
        cases = [(str(self.base), 'a' * 40), ('', 'a' * 40),
                 (str(self.source), 'a' * 7), (str(self.source), '0' * 40)]
        for root, commit in cases:
            with self.subTest(root_matches=root == str(self.source), commit=commit):
                with patch.object(builder.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess([], 0, root + '\n', ''),
                    subprocess.CompletedProcess([], 0, commit + '\n', '')]):
                    self.refused_before_work()

    def test_unreadable_directory_and_symlink_records_fail_before_work(self):
        self.record()
        with patch.object(builder, 'read_object', side_effect=PermissionError('private detail must not escape')):
            self.refused_before_work()
        record = self.source / 'SOURCE_COMMIT.json'
        record.unlink()
        record.mkdir()
        self.refused_before_work()
        record.rmdir()
        record.symlink_to(self.base / 'unavailable-record')
        self.refused_before_work()

    def test_runtime_source_record_and_full_sha256_ids_are_supported(self):
        self.record()
        digest = builder.sha256(self.source / 'manifests/distribution/source-allowlist.json')
        for record in ({'schema_version': 1, 'commit': 'd' * 40, 'runtime_source_view': True},
                       {'schema_version': 1, 'commit': 'e' * 64, 'allowlist_sha256': digest}):
            self.record(record)
            self.assertEqual(builder.resolve_source_commit(self.source), record['commit'])

    def test_archive_allowlist_digest_must_match_before_build_writes(self):
        self.record()
        (self.source / 'manifests/distribution/source-allowlist.json').write_text('{"changed":true}')
        self.refused_before_work()


if __name__ == '__main__':
    unittest.main(verbosity=2)
