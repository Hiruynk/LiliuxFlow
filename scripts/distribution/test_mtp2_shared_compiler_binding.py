#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Portable CPU checks for shared normal-build Rust compiler binding.

The test loads build_native.py and its standard helpers beside this file. It
runs actual shared-selected AST branches and mocks every subprocess call. Tiny
temporary files stand in for compiler binaries; it never compiles, downloads,
or starts a model.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION = ROOT / 'scripts' / 'distribution'
BUILDER_PATH = DISTRIBUTION / 'build_native.py'

sys.dont_write_bytecode = True
sys.path.insert(0, str(DISTRIBUTION))
spec = importlib.util.spec_from_file_location('shared_compiler_test_build_native', BUILDER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('adjacent build_native.py cannot be loaded')
build = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build
spec.loader.exec_module(build)


def node_names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def shared_selected_blocks() -> list[ast.If]:
    tree = ast.parse(BUILDER_PATH.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build')
    return [node for node in function.body
            if isinstance(node, ast.If) and 'shared_selected' in node_names(node.test)]


def normal_lily_build_statement() -> ast.stmt:
    tree = ast.parse(BUILDER_PATH.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build')
    for statement in function.body:
        for item in ast.walk(statement):
            if (isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                and item.func.id == 'command'
                and any(isinstance(value, ast.Constant) and value.value == 'lily-build.txt'
                        for value in ast.walk(item))):
                return statement
    raise AssertionError('normal Lily Cargo build call was not found')


def branch(kind: str) -> ast.If:
    blocks = shared_selected_blocks()
    if kind == 'select':
        return next(node for node in blocks
                    if 'shared_base_toolchain' in node_names(node)
                    and '_shared_rust_toolchain' in node_names(node)
                    and 'DistributionError' not in node_names(node))
    if kind == 'drift':
        return next(node for node in blocks
                    if 'shared_base_toolchain' in node_names(node)
                    and '_shared_rust_toolchain' in node_names(node)
                    and 'DistributionError' in node_names(node))
    if kind == 'receipt':
        return next(node for node in blocks
                    if any(isinstance(item, ast.Constant) and item.value == 'shared_base_rust'
                           for item in ast.walk(node)))
    raise AssertionError(f'unknown branch {kind}')


def execute_branch(node: ast.If, scope: dict[str, object]) -> None:
    module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(BUILDER_PATH), 'exec'), scope)


def toolchain_fixture(root: Path, *, include: tuple[str, ...] = ('cargo', 'rustc', 'rustdoc')) -> dict[str, Path]:
    folder = root / 'selected-toolchain' / 'bin'
    folder.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for name in include:
        path = folder / name
        path.write_bytes(('inert fixture file: ' + name + '\n').encode())
        paths[name] = path.resolve()
    return paths


def mocked_process(paths: dict[str, Path], *, wrong_rustc: bool = False):
    expected = {name: str(path) for name, path in paths.items()}
    calls: list[dict[str, object]] = []

    def run(argv, **kwargs):
        args = list(map(str, argv))
        env = kwargs.get('env', {})
        calls.append({'argv': args, 'env': dict(env)})
        executable = Path(args[0]).name
        if executable == 'cargo':
            output = 'cargo 1.97.0 (inert CPU fixture)\n'
        elif executable == 'rustc':
            version = '1.98.0' if wrong_rustc else '1.97.0'
            output = (f'rustc {version} (inert CPU fixture)\n'
                      'binary: rustc\n'
                      'host: aarch64-apple-darwin\n'
                      f'release: {version}\n'
                      'LLVM version: inert\n')
        elif executable == 'rustdoc':
            output = 'rustdoc 1.97.0 (inert CPU fixture)\n'
        elif executable == 'xcrun':
            output = '27.0\n'
        else:
            raise AssertionError(f'unexpected subprocess executable: {args[0]}')
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    return run, calls, expected


class SharedCompilerBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shared-compiler-cpu-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_shared_dispatch_uses_siblings_and_overrides_hostile_compiler_environment(self):
        paths = toolchain_fixture(self.root)
        hostile = self.root / 'hostile-path'
        hostile.mkdir()
        hostile_rustc = hostile / 'rustc'
        hostile_rustdoc = hostile / 'rustdoc'
        hostile_rustc.write_text('inert hostile rustc 1.99 fixture\n', encoding='utf-8')
        hostile_rustdoc.write_text('inert hostile rustdoc fixture\n', encoding='utf-8')
        run, calls, expected = mocked_process(paths)
        env = {'PATH': str(hostile), 'RUSTC': str(hostile_rustc), 'RUSTDOC': str(hostile_rustdoc),
               'RUSTUP_TOOLCHAIN': '1.97.0', 'CARGO_BUILD_JOBS': '2'}
        scope = {'shared_selected': True, 'cargo': paths['cargo'], 'env': env,
                 '_shared_rust_toolchain': build._shared_rust_toolchain}

        with patch.object(build.subprocess, 'run', side_effect=run):
            execute_branch(branch('select'), scope)

        self.assertEqual(scope['cargo'], paths['cargo'])
        self.assertEqual(scope['env']['PATH'], str(hostile))
        self.assertEqual(scope['env']['RUSTC'], expected['rustc'])
        self.assertEqual(scope['env']['RUSTDOC'], expected['rustdoc'])
        self.assertEqual([call['argv'] for call in calls], [
            [expected['cargo'], '--version'], [expected['rustc'], '-Vv'],
            [expected['rustdoc'], '--version'], ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-version']])
        self.assertTrue(all(call['env']['RUSTC'] == expected['rustc']
                            and call['env']['RUSTDOC'] == expected['rustdoc'] for call in calls))
        self.assertEqual(scope['shared_base_toolchain']['jobs'], 2)
        self.assertEqual(scope['shared_base_toolchain']['target'], 'aarch64-apple-darwin')

        build_calls = []
        def observe_build(argv, **kwargs):
            build_calls.append({'argv': list(map(str, argv)), 'env': kwargs['env']})
            return {'state': 'mocked build command'}
        build_scope = {'steps': [], 'command': observe_build, 'cargo': scope['cargo'],
                       'lily': self.root / 'lily-source', 'work': self.root / 'work',
                       'logs': self.root / 'logs', 'env': scope['env']}
        execute_branch(normal_lily_build_statement(), build_scope)
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(build_calls[0]['argv'][:5], [expected['cargo'], 'build', '--release', '--locked', '--bin'])
        self.assertEqual(build_calls[0]['env']['RUSTC'], expected['rustc'])
        self.assertEqual(build_calls[0]['env']['RUSTDOC'], expected['rustdoc'])

    def test_default_none_uses_rustup_to_select_sibling_rust_197(self):
        paths = toolchain_fixture(self.root)
        rustup_bin = self.root / 'inert-rustup-proxy' / 'bin'
        rustup_bin.mkdir(parents=True)
        rustup = rustup_bin / 'rustup'
        rustup.write_bytes(b'inert rustup proxy fixture; never executed\n')
        env = {'PATH': str(rustup_bin), 'RUSTUP_TOOLCHAIN': '1.97.0', 'CARGO_BUILD_JOBS': '2'}
        scope = {'shared_selected': True, 'cargo': None, 'env': env,
                 '_shared_rust_toolchain': build._shared_rust_toolchain}
        which_calls = []
        process_calls = []

        def which(name):
            which_calls.append(name)
            if name == 'rustup':
                return str(rustup)
            if name == 'cargo':
                self.fail('shared selector resolved cargo through PATH before rustup')
            return None

        def run(argv, **kwargs):
            args = list(map(str, argv))
            process_env = dict(kwargs.get('env', {}))
            process_calls.append({'argv': args, 'env': process_env})
            if args[0] == str(rustup):
                self.assertEqual(args[1:], ['which', '--toolchain', '1.97.0', 'cargo'])
                self.assertEqual(process_env['PATH'], str(rustup_bin))
                return SimpleNamespace(returncode=0, stdout=str(paths['cargo']) + '\n', stderr='')
            executable = Path(args[0]).name
            if executable == 'cargo' and args[1:] == ['--version']:
                output = 'cargo 1.97.0 (inert CPU fixture)\n'
            elif executable == 'rustc' and args[1:] == ['-Vv']:
                output = ('rustc 1.97.0 (inert CPU fixture)\n'
                          'binary: rustc\n'
                          'host: aarch64-apple-darwin\n'
                          'release: 1.97.0\n'
                          'LLVM version: inert\n')
            elif executable == 'rustdoc' and args[1:] == ['--version']:
                output = 'rustdoc 1.97.0 (inert CPU fixture)\n'
            elif args == ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-version']:
                output = '27.0\n'
            else:
                self.fail(f'unexpected subprocess invocation: {args}')
            self.assertEqual(process_env['RUSTC'], str(paths['rustc']))
            self.assertEqual(process_env['RUSTDOC'], str(paths['rustdoc']))
            return SimpleNamespace(returncode=0, stdout=output, stderr='')

        tree = ast.parse(BUILDER_PATH.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build')
        default_cargo = next(node for node in function.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == 'cargo' for target in node.targets)
                             and any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                                     and item.func.id == 'Path' for item in ast.walk(node.value))
                             and any(isinstance(item, ast.Attribute) and item.attr == 'which'
                                     and isinstance(item.value, ast.Name) and item.value.id == 'shutil'
                                     for item in ast.walk(node.value)))
        self.assertLess(branch('select').lineno, default_cargo.lineno)

        with patch.object(build.shutil, 'which', side_effect=which), \
             patch.object(build.subprocess, 'run', side_effect=run):
            execute_branch(branch('select'), scope)

        self.assertEqual(which_calls, ['rustup'])
        self.assertEqual(scope['cargo'], paths['cargo'])
        self.assertEqual(scope['env']['RUSTC'], str(paths['rustc']))
        self.assertEqual(scope['env']['RUSTDOC'], str(paths['rustdoc']))
        self.assertEqual([call['argv'] for call in process_calls], [
            [str(rustup), 'which', '--toolchain', '1.97.0', 'cargo'],
            [str(paths['cargo']), '--version'], [str(paths['rustc']), '-Vv'],
            [str(paths['rustdoc']), '--version'],
            ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-version']])
        self.assertEqual(scope['shared_base_toolchain']['target'], 'aarch64-apple-darwin')

    def test_wrong_rustc_sibling_version_fails_closed(self):
        paths = toolchain_fixture(self.root)
        run, calls, _ = mocked_process(paths, wrong_rustc=True)
        with patch.object(build.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(build.DistributionError, 'actual Rust compiler/version/target differs'):
                build._shared_rust_toolchain(paths['cargo'], {'PATH': '/hostile/path'})
        self.assertEqual(len(calls), 2)
        self.assertEqual(Path(calls[1]['argv'][0]), paths['rustc'])

    def test_missing_sibling_rejects_before_querying_any_tool(self):
        paths = toolchain_fixture(self.root, include=('cargo', 'rustc'))
        with patch.object(build.subprocess, 'run') as run:
            with self.assertRaises(FileNotFoundError):
                build._shared_rust_toolchain(paths['cargo'], {'PATH': '/hostile/path'})
        run.assert_not_called()

    def test_actual_normal_build_post_build_branch_rejects_compiler_drift(self):
        paths = toolchain_fixture(self.root)
        run, calls, _ = mocked_process(paths)
        env = {'PATH': '/fixture/path', 'RUSTC': '/hostile/rustc', 'RUSTDOC': '/hostile/rustdoc'}
        scope = {'shared_selected': True, 'cargo': paths['cargo'], 'env': env,
                 '_shared_rust_toolchain': build._shared_rust_toolchain,
                 'DistributionError': build.DistributionError}

        with patch.object(build.subprocess, 'run', side_effect=run):
            execute_branch(branch('select'), scope)
            paths['rustc'].write_bytes(b'inert compiler fixture changed after mocked build\n')
            with self.assertRaisesRegex(build.DistributionError, 'shared base compiler or SDK changed'):
                execute_branch(branch('drift'), scope)

        self.assertGreaterEqual(len(calls), 8)
        self.assertNotIn('build', [arg for call in calls for arg in call['argv']])

    def test_unshared_branch_preserves_environment_and_skips_helper(self):
        paths = toolchain_fixture(self.root)
        env = {'PATH': '/hostile/path', 'RUSTUP_TOOLCHAIN': '1.97.0'}
        before = dict(env)
        scope = {'shared_selected': False, 'cargo': paths['cargo'], 'env': env,
                 '_shared_rust_toolchain': lambda *_: self.fail('unshared build called shared helper')}
        execute_branch(branch('select'), scope)
        self.assertIs(scope['env'], env)
        self.assertEqual(env, before)
        self.assertNotIn('RUSTC', env)
        self.assertNotIn('RUSTDOC', env)

    def test_actual_receipt_branch_records_shared_toolchain_without_replacing_other_tools(self):
        actual = {'cargo': {'version': 'cargo 1.97.0'}, 'rustc': {'version': 'release: 1.97.0'},
                  'rustdoc': {'version': 'rustdoc 1.97.0'}, 'sdk_version': '27.0', 'jobs': 2}
        trust = {'toolchains': {'rust': 'cargo 1.97.0', 'go': 'go1.27.1'}}
        scope = {'shared_selected': True, 'trust': trust, 'shared_base_toolchain': actual}
        execute_branch(branch('receipt'), scope)
        self.assertIs(trust['toolchains']['shared_base_rust'], actual)
        self.assertEqual(trust['toolchains']['rust'], 'cargo 1.97.0')
        self.assertEqual(trust['toolchains']['go'], 'go1.27.1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
