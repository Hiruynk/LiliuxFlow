#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exact-commit, allowlisted source archives; no working tree content is bundled."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from common import DistributionError, no_symlinks, relative_path, write_json_new

DENIED_PARTS = {'.git', '.venv', '.wrangler', 'node_modules', '__pycache__', '__macosx', 'models', 'weights', 'secrets', 'cache', 'var', 'artifacts', 'runtime', 'private', 'before-private'}
DENIED_SUFFIXES = {'.safetensors', '.gguf', '.ggml', '.pt', '.pth', '.ckpt', '.onnx', '.db', '.sqlite', '.sqlite3', '.dump', '.env', '.pem', '.key', '.p12', '.pfx', '.pyc', '.log'}
PRIVATE_HOME_PATH = re.compile(rb'/(?:' + rb'Users|home' + rb')/[^/\s\x22\x27<>{}\[\]()|+?*]+(?:/|(?=[\s\x22\x27]))')

def denied_source_path(name):
    path = relative_path(name)
    for part in path.parts:
        folded = part.casefold()
        if folded in DENIED_PARTS or folded == '.ds_store' or part.startswith('._'):
            return True
        if folded.endswith(('_codex_pack', '_execution_pack', '_plan')):
            return True
    filename = path.name.casefold()
    if filename == 'agents' or (filename.startswith('agents') and filename.endswith('.md')) or filename.startswith(('public_readiness', '.env', '.dev.vars')) or filename in {'.npmrc', '.netrc', 'wrangler.local.json'} or re.fullmatch(r'wrangler\..+\.local\.json', filename):
        return True
    return path.suffix.lower() in DENIED_SUFFIXES or ('avatar' in name.lower() and path.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif','.avif'})

def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise DistributionError('Git object lookup failed')
    return result.stdout

def eligible(name, allowlist):
    path = relative_path(name)
    if name in allowlist.get('exclude_paths',[]) or any(name.startswith(prefix) for prefix in allowlist.get('exclude_prefixes',[])):
        return False
    if denied_source_path(name):
        return False
    return name in allowlist['exact_paths'] or any(name.startswith(p) for p in allowlist['prefixes'])

def forbidden_content(data):
    # Detect formats as well as names; never print raw headers or contents.
    if data.startswith((b'GGUF', b'ggml', b'SQLite format 3\0', b'PGDMP', b'version https://git-lfs.github.com/spec/v1', b'\x00\x05\x16\x07', b'\x00\x05\x16\x00')) or PRIVATE_HOME_PATH.search(data):
        return True
    if len(data) >= 9:
        size = int.from_bytes(data[:8], 'little')
        if 0 < size < min(len(data), 16 * 1024**2):
            try:
                header = json.loads(data[8:8+size])
                if isinstance(header, dict) and any(isinstance(v, dict) and 'data_offsets' in v and 'dtype' in v for v in header.values()):
                    return True
            except (ValueError, UnicodeError):
                pass
    return False

def check_source_tree(root, commit='HEAD'):
    """Check every tracked blob in the exact tree, including unselected paths."""
    commit = git(root, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    count = total = 0
    for line in git(root, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
        if not line:
            continue
        info, raw_name = line.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        mode, kind, oid = info.decode().split()
        if denied_source_path(name) or mode not in ('100644', '100755') or kind != 'blob':
            raise DistributionError('tracked source contains a forbidden private/metadata path or special file; contents withheld')
        size = int(git(root, 'cat-file', '-s', oid))
        if size > 16 * 1024**2:
            raise DistributionError('tracked source exceeds per-file scan cap; contents withheld')
        data = git(root, 'cat-file', 'blob', oid)
        if forbidden_content(data):
            raise DistributionError('tracked source contains forbidden payload, metadata or private home path; contents withheld')
        count += 1
        total += size
        if count > 20000 or total > 268435456:
            raise DistributionError('tracked source exceeds whole-tree scan cap; contents withheld')
    return {'scope': 'all tracked paths and blob formats in exact source tree', 'commit': commit, 'file_count': count, 'source_bytes': total, 'path_format_privacy_check': 'PASS', 'secret_scan': 'SEPARATE_REQUIRED_GATE'}

def selected_files(root, commit):
    commit = git(root, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    policy_data = git(root, 'show', commit + ':manifests/distribution/source-allowlist.json')
    policy = json.loads(policy_data)
    files = []
    total = 0
    for line in git(root, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
        if not line:
            continue
        info, raw_name = line.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        mode, kind, oid = info.decode().split()
        if not eligible(name, policy):
            continue
        if mode not in ('100644', '100755') or kind != 'blob':
            raise DistributionError('selected source contains a link, gitlink or special file')
        size = int(git(root, 'cat-file', '-s', oid))
        if size > policy['max_file_bytes']:
            raise DistributionError('selected source file exceeds release size cap')
        total += size
        if total > policy['max_total_bytes']:
            raise DistributionError('selected source exceeds release total cap')
        data = git(root, 'cat-file', 'blob', oid)
        if forbidden_content(data):
            raise DistributionError('selected source contains weights, database, LFS, metadata or private home path; contents withheld')
        files.append((name, mode, data))
    paths = {x[0] for x in files}
    if not set(policy['required_paths']).issubset(paths):
        raise DistributionError('required portable source is missing from exact commit')
    # A manifest alone is not a portable UI bundle: every hash-bound input must
    # be present in this same exact commit, never only in the working tree.
    by_path={name:data for name,_mode,data in files}
    for name in policy['required_paths']:
        if name.startswith('manifests/distribution/ui-recipe-') and name.endswith('.json'):
            recipe=json.loads(by_path[name])
            for app in recipe.get('apps',{}).values():
                inputs=[{'path':x['path'],'sha256':x['sha256']} for x in app.get('patches',[])]+[{'path':x['source'],'sha256':x['sha256']} for x in app.get('extra_files',[])]
                for item in inputs:
                    relative_path(item['path'])
                    if item['path'] not in by_path or hashlib.sha256(by_path[item['path']]).hexdigest()!=item['sha256']:
                        raise DistributionError('portable UI recipe input is absent or differs in the exact source commit')
    provenance = json.dumps({'schema_version': 1, 'commit': commit, 'allowlist_sha256': hashlib.sha256(policy_data).hexdigest()}, sort_keys=True).encode() + b'\n'
    if 'SOURCE_COMMIT.json' in paths:
        raise DistributionError('source provenance filename is reserved')
    files.append(('SOURCE_COMMIT.json', '100644', provenance))
    return commit, policy, sorted(files)

def safe_extract(archive: Path, destination: Path, *, max_bytes=268435456):
    destination = no_symlinks(destination)
    if destination.exists():
        raise DistributionError('extraction destination must be new')
    destination.mkdir(parents=True, mode=0o700)
    seen = set()
    total = 0
    with tarfile.open(archive, 'r:gz') as handle:
        for member in handle:
            path = relative_path(member.name)
            if member.name in seen or not member.isfile() or member.size < 0 or member.size > 16 * 1024**2 or denied_source_path(member.name):
                raise DistributionError('archive has duplicate, link, directory or special member')
            seen.add(member.name)
            if len(seen) > 20000:
                raise DistributionError('archive exceeds member count cap')
            total += member.size
            if total > max_bytes:
                raise DistributionError('archive exceeds extraction size cap')
            target = no_symlinks(destination / str(path))
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = handle.extractfile(member)
            with target.open('xb') as output:
                shutil.copyfileobj(stream, output)
            if forbidden_content(target.read_bytes()):
                raise DistributionError('archive contains forbidden payload, metadata or private home path; contents withheld')
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return seen

def build_archive(root, commit, output):
    root, output = no_symlinks(root), no_symlinks(output)
    if output.exists() or output.with_suffix(output.suffix + '.manifest.json').exists():
        raise DistributionError('release output or manifest already exists')
    if not output.parent.is_dir():
        raise DistributionError('release output parent must exist')
    if shutil.disk_usage(output.parent).free < 64 * 1024**3:
        raise DistributionError('free disk below 64 GiB floor')
    commit, policy, files = selected_files(root, commit)
    receipt = {'schema_version': 1, 'commit': commit, 'stage': policy['stage'], 'release_ready': False,
               'files': [{'path': n, 'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()} for n, _, d in files],
               'excluded': ['working tree and Git history', 'weights', 'DB', 'secrets', 'personal avatars', 'private evidence', 'build caches']}
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, mode, data in files:
                info = tarfile.TarInfo('LiliuxFlow/' + name)
                info.mode = 0o755 if mode == '100755' else 0o644
                info.size = len(data)
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
        receipt['archive_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
        receipt['archive_bytes'] = output.stat().st_size
        # Task-specific private folder: never unpack at /tmp root or trust tar extraction helpers.
        with tempfile.TemporaryDirectory(prefix='liliuxflow-verify-', dir=output.parent) as task:
            extracted = Path(task) / 'extracted'
            seen = safe_extract(output, extracted, max_bytes=policy['max_total_bytes'])
            if seen != {'LiliuxFlow/' + n for n, _, _ in files}:
                raise DistributionError('archive path readback mismatch')
            for item in receipt['files']:
                if hashlib.sha256((extracted / 'LiliuxFlow' / item['path']).read_bytes()).hexdigest() != item['sha256']:
                    raise DistributionError('archive byte readback mismatch')
        write_json_new(output.with_suffix(output.suffix + '.manifest.json'), receipt)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {k: v for k, v in receipt.items() if k != 'files'} | {'file_count': len(files), 'safe_extract_verified': True}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check-tree', action='store_true', help='check all tracked paths and blob formats, not only the source allowlist')
    args = parser.parse_args()
    try:
        if args.check_tree:
            if args.output: raise DistributionError('tree check does not create a release archive')
            result = check_source_tree(args.root, args.commit)
        else:
            if not args.output: raise DistributionError('source archive output is required')
            result = build_archive(args.root, args.commit, args.output)
        print(json.dumps(result, sort_keys=True))
    except (DistributionError, OSError, ValueError):
        print(json.dumps({'state': 'error', 'error': 'unsafe, missing or inaccessible source package'}))
        raise SystemExit(2)
