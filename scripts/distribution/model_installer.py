#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit optional downloads through the locked official Hugging Face SDK."""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import subprocess

from common import DistributionError, no_symlinks, private_directory, read_object, write_json_new
from model_catalog import HUB_VERSION, select_model
from trust import atomic_private_json, checkpoint_records, private_file, sha256

STAGING_MARKER = '.liliuxflow-partial.json'
COMPLETE_MARKER = '.liliuxflow-model.json'
# local_dir downloads use one file plus its resumable incomplete file, then rename.
# Xet chunk caching is disabled; this reserve covers SDK metadata and helper packages.
SDK_RESERVE_BYTES = 1024**3
BUILD_RESERVE_BYTES = 64 * 1024**3


def _overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def model_destination(output, source_root, data_root=None):
    output = no_symlinks(output)
    source = no_symlinks(source_root)
    protected = [source]
    # A developer worktree may sit below another checkout. Neither tree is a model store.
    protected += [p for p in source.parents if (p / '.git').exists()]
    if data_root is not None:
        protected.append(no_symlinks(data_root))
        installation = Path(data_root) / 'install.json'
        if installation.is_file():
            from trust import installation as read_installation
            config = read_installation(data_root)
            protected += [no_symlinks(Path(config[k])) for k in ('source_root', 'package_source_root') if config.get(k)]
    if any(_overlaps(output, boundary) for boundary in protected):
        raise DistributionError('model destination must be outside source, release and private runtime directories')
    if not output.name or not output.parent.is_dir():
        raise DistributionError('model destination needs an existing local parent directory')
    parent = output.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise DistributionError('model destination parent must be owned and not writable by other users')
    return output


def attached_model_directory(model_dir, source_root, data_root):
    """Validate read-only attachment without imposing a download store's Git ancestry."""
    directory = no_symlinks(model_dir)
    source = no_symlinks(source_root)
    data = no_symlinks(data_root)
    from trust import installation
    config = installation(data)
    boundaries = [source, data]
    boundaries += [no_symlinks(Path(config[k])) for k in ('source_root', 'package_source_root') if config.get(k)]
    if any(_overlaps(directory, boundary) for boundary in boundaries):
        raise DistributionError('attached checkpoint must be separate from this installation source and private data')
    if not directory.is_dir():
        raise DistributionError('local checkpoint directory is unavailable')
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise DistributionError('local checkpoint directory must be owned and not writable by other users')
    if directory.name.endswith('.liliuxflow-partial') or (directory / STAGING_MARKER).exists():
        raise DistributionError('checkpoint download is still partial; finish models install before attach')
    return directory


def _marker(model, output):
    return {'schema_version': 1, 'model_id': model.model_id, 'repository': model.entry['repository'],
            'revision': model.entry['revision'], 'inventory_sha256': sha256(model.inventory_path),
            'output': str(output)}


def _launchd_loaded(config):
    if sys.platform != 'darwin':
        return False
    result = subprocess.run(['/bin/launchctl', 'print', 'gui/' + str(os.getuid()) + '/' + config['launchd_label']], capture_output=True)
    return result.returncode == 0


def _owned_tree(folder):
    private_directory(folder)
    for path in folder.rglob('*'):
        no_symlinks(path)
        info = path.stat()
        if info.st_uid != os.getuid() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise DistributionError('partial model directory contains an unowned or non-regular entry')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise DistributionError('partial model directory contains a hard link')


def _publish(staging, output):
    """Atomic same-filesystem publication which never replaces even an empty target."""
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        function = library.renamex_np
        function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        result = function(os.fsencode(staging), os.fsencode(output), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        function = library.renameat2
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        result = function(-100, os.fsencode(staging), -100, os.fsencode(output), 1)  # RENAME_NOREPLACE
    else:
        raise DistributionError('filesystem lacks exclusive atomic publication; owned partial retained')
    if result:
        raise DistributionError('model publication refused; existing destination preserved and owned partial retained')


@contextlib.contextmanager
def download_lock(output):
    path = no_symlinks(output.with_name('.' + output.name + '.liliuxflow-install.lock'))
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise DistributionError('model install lock must be a private owned regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DistributionError('another model install owns this destination; retry after it finishes')
        yield
    finally:
        os.close(fd)  # OS lock releases on cancellation or process exit as well.


def _sdk():
    # Set before import: the Hub reads several settings when initializing constants.
    os.environ.update({'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_XET': '1',
                       'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_PROGRESS_BARS': '1',
                       'HF_HUB_VERBOSITY': 'error'})
    import huggingface_hub
    if huggingface_hub.__version__ != HUB_VERSION:
        raise DistributionError('model helper SDK differs from its locked version')
    logging.getLogger('huggingface_hub').setLevel(logging.CRITICAL)
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    return huggingface_hub.snapshot_download


def _snapshot(snapshot, model, local_dir, *, dry_run, token):
    # Endpoint and repo cannot be overridden by a caller URL or HF_ENDPOINT.
    # No remote code, shell files or library loaders are executed.
    kwargs = dict(repo_id=model.entry['repository'], revision=model.entry['revision'], repo_type='model',
                  local_dir=local_dir, cache_dir=local_dir / '.cache/huggingface/hub',
                  allow_patterns=[row['path'] for row in model.inventory['files']],
                  token=token, endpoint='https://huggingface.co', force_download=False,
                  etag_timeout=15, max_workers=2, dry_run=dry_run,
                  library_name='liliuxflow', library_version='0.1.0')
    try:
        return snapshot(**kwargs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        # SDK error strings may include a URL, signed query or operator token.
        name = type(error).__name__
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        if name == 'RevisionNotFoundError':
            reason = 'fixed model revision is unavailable; no fallback to main was attempted'
        elif name == 'GatedRepoError' or status in (401, 403):
            reason = 'upstream access denied; check the original model terms and an explicitly supplied HF token'
        elif name in ('RepositoryNotFoundError', 'EntryNotFoundError', 'RemoteEntryNotFoundError') or status == 404:
            reason = 'upstream model or required file is unavailable'
        elif isinstance(error, OSError) and error.errno == errno.ENOSPC:
            reason = 'disk is full; owned partial download retained for resume'
        elif 'Timeout' in name or name in ('ConnectError', 'LocalEntryNotFoundError', 'IncompleteSnapshotError'):
            reason = 'upstream connection failed; owned partial download retained for resume'
        else:
            reason = 'upstream download failed; owned partial download retained for resume'
        raise DistributionError(reason) from None


def _preview(rows, model, staging):
    expected = {row['path']: row for row in model.inventory['files']}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise DistributionError('upstream metadata omits a required checkpoint file')
    seen = set()
    missing = 0
    resumable = 0
    files = []
    for row in rows:
        name = getattr(row, 'filename', None)
        item = expected.get(name)
        if (item is None or name in seen or getattr(row, 'commit_hash', None) != model.entry['revision']
            or type(getattr(row, 'file_size', None)) is not int or row.file_size != item['size_bytes']
            or type(getattr(row, 'will_download', None)) is not bool):
            raise DistributionError('upstream file metadata differs from the fixed checkpoint inventory')
        seen.add(name)
        required = row.file_size if row.will_download else 0
        partial = 0
        # SDK local_dir incomplete names include ETag; only LFS sha256 ETags are
        # known from the inventory. Other small files count conservatively in full.
        if required and Path(name).suffix == '.safetensors' and (staging / '.cache/huggingface/download').is_dir():
            from huggingface_hub._local_folder import get_local_download_paths
            paths = get_local_download_paths(staging, name)
            path = no_symlinks(paths.incomplete_path(item['sha256']))
            if path.is_file():
                partial = path.stat().st_size
                if partial > required:
                    raise DistributionError('owned partial file exceeds its expected checkpoint size')
        resumable += partial
        missing += required - partial
        files.append({'path': name, 'size_bytes': row.file_size, 'download_bytes': required - partial,
                      'cached': not row.will_download, 'resumable_bytes': partial})
    free = shutil.disk_usage(staging if staging.exists() else staging.parent).free
    required_free = missing + SDK_RESERVE_BYTES + BUILD_RESERVE_BYTES
    return {'network_operation': True, 'operation': 'upstream_metadata_only', 'model_id': model.model_id,
            'revision': model.entry['revision'], 'file_count': len(files), 'size_bytes': model.total_bytes,
            'remaining_download_bytes': missing, 'resumable_bytes': resumable,
            'sdk_reserve_bytes': SDK_RESERVE_BYTES, 'build_reserve_bytes': BUILD_RESERVE_BYTES,
            'required_free_bytes': required_free, 'free_disk_bytes': free,
            'capacity_sufficient': free >= required_free, 'files': files, 'downloaded_bytes': 0,
            'license_name': model.entry['license_name'], 'license_url': model.entry['homepage'] + '/blob/' + model.entry['revision'] + '/LICENSE',
            'license_acceptance': 'operator must review original terms; no gated agreement is accepted automatically'}


def install_model(source_root, model_id, output, *, data_root=None, execute=False, dry_run=False, token=False, snapshot=None):
    if type(execute) is not bool or type(dry_run) is not bool or execute == dry_run:
        raise DistributionError('choose --dry-run for network metadata or --execute to download the optional model')
    model = select_model(source_root, model_id)
    output = model_destination(output, source_root, data_root)
    staging = no_symlinks(output.with_name('.' + output.name + '.liliuxflow-partial'))
    owner = _marker(model, output)
    with download_lock(output):
        if output.exists():
            _owned_tree(output)
            receipt = read_object(private_file(output / COMPLETE_MARKER))
            if any(receipt.get(k) != v for k, v in owner.items()):
                raise DistributionError('existing model destination is preserved; choose another new directory')
            checkpoint_records(output, model.inventory)
            return {'state': 'already_installed', 'model_id': model_id, 'file_count': len(model.inventory['files']),
                    'payload_verified': True, 'network_operation': False, 'downloaded_bytes': 0, 'model_loaded': False}
        if staging.exists():
            _owned_tree(staging)
            if read_object(private_file(staging / STAGING_MARKER)) != owner:
                raise DistributionError('existing partial download belongs to another source or destination')
        else:
            private_directory(staging)
            write_json_new(staging / STAGING_MARKER, owner)
        snapshot = snapshot or _sdk()
        rows = _snapshot(snapshot, model, staging, dry_run=True, token=token)
        plan = _preview(rows, model, staging)
        if dry_run:
            return {'state': 'model_download_plan', 'output': str(output), **plan, 'execute_required': True,
                    'model_loaded': False, 'installation_changed': False}
        if not plan['capacity_sufficient']:
            raise DistributionError('insufficient disk for remaining model bytes, SDK reserve and build headroom; partial retained')
        _snapshot(snapshot, model, staging, dry_run=False, token=token)
        _owned_tree(staging)
        records = checkpoint_records(staging, model.inventory)
        receipt = {**owner, 'payload_verified': True, 'files': records}
        atomic_private_json(staging / COMPLETE_MARKER, receipt)
        if output.exists():
            raise DistributionError('model destination appeared during download; partial retained and existing files preserved')
        # Same filesystem: no second complete checkpoint and never replace a directory.
        _publish(staging, output)
        (output / STAGING_MARKER).unlink()
        return {'state': 'model_installed', 'model_id': model_id, 'output': str(output),
                'revision': model.entry['revision'], 'file_count': len(records), 'payload_verified': True,
                'model_loaded': False, 'installation_changed': False, 'weights_copied': False,
                'next_step': 'setup --model-dir PATH or models attach ' + model_id + ' --model-dir PATH'}


def attach_model(data_root, model_id, model_dir):
    """Offline verification and explicit configuration; never restart or mutate a DB/key."""
    import copy
    from agent import mutation
    from liliuxflow import checkpoint_metadata, load_install
    from trust import installation, model_configured, validate
    data = no_symlinks(data_root)
    config = load_install(data)
    source = no_symlinks(Path(config['source_root']))
    model = select_model(source, model_id)
    directory = attached_model_directory(model_dir, source, data)
    if model_configured(config) and config['model_dir'] != str(directory):
        raise DistributionError('installation already has a different checkpoint path; existing configuration is preserved')
    with mutation(data):
        if installation(data) != config:
            raise DistributionError('installation changed during attach; retry with its current configuration')
        if any((data / ('run/' + name)).exists() for name in ('agent.json', 'stack.plist', 'model.json')):
            raise DistributionError('attach requires this installation to be stopped; no service was restarted')
        if _launchd_loaded(config):
            raise DistributionError('own LaunchAgent is still registered; stop it before attach')
        if config['runtime_state'] != 'NOT_BUILT':
            trusted = validate(data, require_checkpoint=False)
            if trusted['trust'].get('optional_model_support') != {'schema_version': 1}:
                raise DistributionError('runtime source must be refreshed for optional model state before attach')
        metadata = checkpoint_metadata(directory)
        records = checkpoint_records(directory, model.inventory)
        runtime = private_directory(data / 'runtime')
        receipt = {'schema_version': 1, 'model_dir': str(directory), 'revision': model.entry['revision'],
                   'manifest_sha256': sha256(model.inventory_path), 'payload_verified': True, 'files': records}
        # Stage receipts before the final install.json update. A crash cannot turn a
        # no-model installation into a routable model without the final configuration.
        atomic_private_json(runtime / 'checkpoint-verified.json', receipt)
        metadata_receipt = copy.deepcopy(receipt)
        metadata_receipt.update({'validation': 'metadata_only', 'payload_verified': False,
                                'origin': 'metadata fingerprints derived from complete inventory verification'})
        for item in metadata_receipt['files']:
            if Path(item['path']).suffix == '.safetensors':
                item['sha256'] = None
        atomic_private_json(runtime / 'checkpoint-metadata.json', metadata_receipt)
        config.update({'schema_version': 2, 'model_state': 'attached', 'model_id': model_id,
                       'model_dir': str(directory), 'checkpoint': {**metadata, 'revision_verified': True,
                       'tensor_payload_verified': True, 'validation': 'complete pinned inventory'}})
        atomic_private_json(data / 'install.json', config)
    return {'state': 'model_attached', 'model_id': model_id, 'file_count': len(records), 'payload_verified': True,
            'installation_id': config['installation_id'], 'credentials_preserved': True, 'database_preserved': True,
            'profile_policy_preserved': True, 'weights_copied': False, 'network_operation': False,
            'model_loaded': False, 'service_restarted': False, 'next_step': 'build if needed, then initialize and start'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--dry-run', action='store_true')
    choice.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    try:
        result = install_model(args.source_root, args.model_id, args.output, data_root=args.data_root,
                               execute=args.execute, dry_run=args.dry_run,
                               token=os.environ.get('LILIUXFLOW_MODEL_INSTALL_TOKEN', False))
    except KeyboardInterrupt:
        result = {'state': 'cancelled', 'error': 'owned partial download retained; repeat the same install command to resume'}
        print(json.dumps(result)); return 130
    except (DistributionError, OSError, ValueError, KeyError) as error:
        print(json.dumps({'state': 'error', 'error': str(error) if isinstance(error, DistributionError) else 'model filesystem operation failed'}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
