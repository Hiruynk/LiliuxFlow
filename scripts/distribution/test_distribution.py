"""CPU-only safety and portability checks; never bind listeners or load model tensors."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import liliuxflow as cli
import package

SOURCE_ROOT = Path(__file__).resolve().parents[2]

class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='liliuxflow-cpu 測試 space-')
        self.root = Path(self.tmp.name).resolve()
        self.model = self.root / 'external 模型'
        self.model.mkdir()
        self.data = self.root / 'data 安裝 space'
        config = {'model_type': 'qwen4_exp', 'lily': {'format': 'qwen4_exp-affine-v1', 'quantization': {'default': {'bits': 4, 'mode': 'affine', 'group_size': 64}}}}
        (self.model / 'config.json').write_text(json.dumps(config))
        for name in ('tokenizer.json', 'tokenizer_config.json', 'generation_config.json'):
            (self.model / name).write_text('{}')
        for name in ('chat_template.jinja', 'LICENSE'):
            (self.model / name).write_text('synthetic CPU fixture')
        (self.model / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'fixture': 'shard.safetensors'}}))
        (self.model / 'shard.safetensors').write_bytes(b'fixture!')
        self.args = argparse.Namespace(data_root=self.data, model_dir=self.model, port=None)

    def tearDown(self):
        self.tmp.cleanup()

    def setup_cli(self):
        return subprocess.run([sys.executable, str(SOURCE_ROOT / 'scripts/distribution/liliuxflow.py'), '--data-root', str(self.data), 'setup', '--model-dir', str(self.model)], capture_output=True, text=True)

    def test_setup_and_repeat_preserve_identity_credentials_and_readonly_model(self):
        before = {p.name: p.read_bytes() for p in self.model.iterdir()}
        result = self.setup_cli()
        self.assertEqual(result.returncode, 0, result.stdout)
        install = (self.data / 'install.json').read_bytes()
        secret = (self.data / 'secrets/bootstrap.json').read_bytes()
        self.assertEqual(self.setup_cli().returncode, 0)
        self.assertEqual(install, (self.data / 'install.json').read_bytes())
        self.assertEqual(secret, (self.data / 'secrets/bootstrap.json').read_bytes())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.model.iterdir()})
        self.assertEqual((self.data.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.data / 'secrets/bootstrap.json').stat().st_mode & 0o777), 0o600)
        self.assertNotIn(json.loads(secret)['DB_PASSWORD'], result.stdout)
        self.assertFalse(json.loads(install)['database_initialized'])

    def test_existing_unrelated_data_is_not_overwritten(self):
        self.data.mkdir(mode=0o700)
        (self.data / 'user-file').write_text('keep')
        self.assertEqual(self.setup_cli().returncode, 2)
        self.assertEqual((self.data / 'user-file').read_text(), 'keep')
        self.assertFalse((self.data / 'install.json').exists())

    def test_setup_refuses_changed_ports_and_credentials_remain(self):
        self.setup_cli()
        secret = (self.data / 'secrets/bootstrap.json').read_bytes()
        args = argparse.Namespace(**vars(self.args))
        args.port = [('litellm', 14000)]
        with self.assertRaises(DistributionError):
            cli.setup(args)
        self.assertEqual(secret, (self.data / 'secrets/bootstrap.json').read_bytes())

    def test_invalid_and_duplicate_ports(self):
        for bad in (True, 80, 65536, '4000'):
            with self.assertRaises(DistributionError):
                cli.validate_ports(dict(cli.DEFAULT_PORTS, litellm=bad))
        with self.assertRaises(DistributionError):
            cli.validate_ports(dict(cli.DEFAULT_PORTS, guard=4000))

    def test_read_command_does_not_create_missing_install(self):
        with self.assertRaises(DistributionError):
            cli.load_install(self.data)
        self.assertFalse(self.data.exists())

    def test_metadata_rejects_wrong_quantization_missing_and_traversal_shards(self):
        config = json.loads((self.model / 'config.json').read_text())
        config['lily']['quantization']['default']['bits'] = 3
        (self.model / 'config.json').write_text(json.dumps(config))
        with self.assertRaises(DistributionError):
            cli.checkpoint_metadata(self.model)
        config['lily']['quantization']['default']['bits'] = 4
        (self.model / 'config.json').write_text(json.dumps(config))
        for shard in ('../shard.safetensors', '/shard.safetensors', 'missing.safetensors'):
            (self.model / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'fixture': shard}}))
            with self.assertRaises(DistributionError):
                cli.checkpoint_metadata(self.model)

    def test_symlink_model_and_data_refused(self):
        linked = self.root / 'linked'
        linked.symlink_to(self.model)
        with self.assertRaises(DistributionError):
            cli.checkpoint_metadata(linked)
        self.data.symlink_to(self.root / 'destination')
        self.assertEqual(self.setup_cli().returncode, 2)
        self.assertFalse((self.root / 'destination').exists())

    def test_start_stop_never_fallback_to_development_ops(self):
        self.setup_cli()
        for command in ('start', 'stop'):
            result = subprocess.run([sys.executable, str(SOURCE_ROOT / 'scripts/distribution/liliuxflow.py'), '--data-root', str(self.data), command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('not built', result.stdout)
        self.assertEqual(list((self.data / 'run').iterdir()), [])

    def test_backup_private_unencrypted_and_uninstall_preserves_data(self):
        self.setup_cli()
        out = self.root / 'backups'
        out.mkdir(mode=0o700)
        args = argparse.Namespace(data_root=self.data, output=out / 'bootstrap.tar.gz')
        self.assertEqual(cli.backup(args), 0)
        self.assertEqual(args.output.stat().st_mode & 0o777, 0o600)
        with tarfile.open(args.output) as handle:
            self.assertEqual(set(handle.getnames()), {'install.json', 'secrets/bootstrap.json'})
        before = (self.data / 'secrets/bootstrap.json').read_bytes()
        self.assertEqual(cli.uninstall(args), 0)
        self.assertEqual(before, (self.data / 'secrets/bootstrap.json').read_bytes())
        self.assertTrue(self.model.exists())

    def test_backup_refuses_initialized_database(self):
        self.setup_cli()
        file = self.data / 'install.json'
        obj = json.loads(file.read_text())
        obj['database_initialized'] = True
        file.write_text(json.dumps(obj))
        with self.assertRaises((DistributionError, OSError)):
            cli.backup(argparse.Namespace(data_root=self.data, output=self.root / 'no.tar.gz'))

    def test_archive_rejects_absolute_traversal_symlink_hardlink_duplicate_special(self):
        for kind in ('absolute', 'traversal', 'symlink', 'hardlink', 'duplicate', 'special'):
            archive = self.root / (kind + '.tar.gz')
            with tarfile.open(archive, 'w:gz') as handle:
                info = tarfile.TarInfo({'absolute': '/escape', 'traversal': '../escape'}.get(kind, 'LiliuxFlow/safe'))
                if kind == 'symlink':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '../../escape'
                elif kind == 'hardlink':
                    info.type = tarfile.LNKTYPE
                    info.linkname = '../../escape'
                elif kind == 'special':
                    info.type = tarfile.FIFOTYPE
                handle.addfile(info)
                if kind == 'duplicate':
                    handle.addfile(info)
            with self.assertRaises(DistributionError):
                package.safe_extract(archive, self.root / ('extract-' + kind))
            self.assertFalse((self.root / 'escape').exists())

    def test_forbidden_weight_db_and_lfs_magic(self):
        for prefix in (b'GGUF', b'SQLite format 3\0', b'PGDMP', b'version https://git-lfs.github.com/spec/v1'):
            self.assertTrue(package.forbidden_content(prefix))
        header = json.dumps({'weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]}}).encode()
        self.assertTrue(package.forbidden_content(len(header).to_bytes(8, 'little') + header + b'fake'))
        self.assertFalse(package.forbidden_content(b'safe source'))

    def test_exclusions_override_broad_and_exact_allowlist(self):
        policy={'exact_paths':['scripts/ui/replay.py'],'prefixes':['packages/ui-locale/','scripts/ui/','scripts/distribution/'],'exclude_prefixes':['scripts/ui/','packages/ui-locale/proposals/'],'exclude_paths':['packages/ui-locale/source-map.json']}
        for path in ('scripts/ui/replay.py','packages/ui-locale/proposals/private.json','packages/ui-locale/source-map.json','scripts/distribution/VAR/leak.json','scripts/distribution/avatar.png'):
            self.assertFalse(package.eligible(path,policy))
        self.assertTrue(package.eligible('packages/ui-locale/src/core.mjs',policy))

    def test_exact_commit_cannot_reference_untracked_UI_input(self):
        repo=self.root/'UI source exact snapshot';folder=repo/'manifests/distribution';folder.mkdir(parents=True)
        policy={'exact_paths':[],'prefixes':['manifests/distribution/','patches/ui/'],'required_paths':['manifests/distribution/source-allowlist.json','manifests/distribution/ui-recipe-v2.json'],'max_file_bytes':16777216,'max_total_bytes':268435456,'stage':'CPU_FIXTURE'}
        (folder/'source-allowlist.json').write_text(json.dumps(policy));data=b'synthetic UI patch fixture';sha=hashlib.sha256(data).hexdigest();recipe={'apps':{'litellm':{'patches':[{'path':'patches/ui/required.patch','sha256':sha}]}}};(folder/'ui-recipe-v2.json').write_text(json.dumps(recipe))
        subprocess.run(['git','init','-q',str(repo)],check=True);subprocess.run(['git','-C',str(repo),'add','.'],check=True);subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Recipe only'],check=True)
        file=repo/'patches/ui/required.patch';file.parent.mkdir(parents=True);file.write_bytes(data)
        with self.assertRaises(DistributionError):package.selected_files(repo,'HEAD')
        subprocess.run(['git','-C',str(repo),'add','patches/ui/required.patch'],check=True);subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Bind exact input'],check=True)
        self.assertIn('patches/ui/required.patch',{x[0] for x in package.selected_files(repo,'HEAD')[2]})

    def test_exact_commit_archive_ignores_worktree_private_files(self):
        repo = self.root / 'source 源碼 space'
        repo.mkdir()
        for parent in ('scripts/distribution', 'profiles/distribution', 'manifests/distribution'):
            shutil.copytree(SOURCE_ROOT / parent, repo / parent, ignore=shutil.ignore_patterns('__pycache__'))
        policy_file = repo / 'manifests/distribution/source-allowlist.json'
        policy = json.loads(policy_file.read_text())
        policy['exact_paths'] = []
        policy['required_paths'] = ['scripts/distribution/liliuxflow.py','scripts/distribution/package.py','scripts/distribution/common.py','profiles/distribution/safe64k.json','manifests/distribution/source-allowlist.json']
        policy_file.write_text(json.dumps(policy))
        (repo / 'scripts/distribution/private-avatar.png').write_bytes(b'private')
        (repo / 'scripts/distribution/renamed.bin').write_bytes(b'GGUFsynthetic')
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'CPU fixture'], check=True)
        with self.assertRaises(DistributionError):
            package.selected_files(repo, 'HEAD')
        (repo / 'scripts/distribution/renamed.bin').unlink()
        subprocess.run(['git', '-C', str(repo), 'add', '-u'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Remove synthetic forbidden payload'], check=True)
        original = (repo / 'scripts/distribution/common.py').read_bytes()
        (repo / 'scripts/distribution/common.py').write_text('dirty content')
        out = self.root / 'source.tar.gz'
        result = package.build_archive(repo, 'HEAD', out)
        self.assertTrue(result['safe_extract_verified'])
        self.assertFalse(result['release_ready'])
        with tarfile.open(out) as handle:
            self.assertEqual(handle.extractfile('LiliuxFlow/scripts/distribution/common.py').read(), original)
            self.assertNotIn('LiliuxFlow/scripts/distribution/private-avatar.png', handle.getnames())

if __name__ == '__main__':
    unittest.main(verbosity=2)
