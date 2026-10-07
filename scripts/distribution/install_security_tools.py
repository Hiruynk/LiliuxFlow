#!/usr/bin/env python3
"""Install checksum-pinned native scanners into a project-local private tool directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import urllib.request
from common import DistributionError, no_symlinks, private_directory, read_object, relative_path, write_json_new
ROOT = Path(__file__).resolve().parents[2]

def install(destination):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise DistributionError('this security tool lock is for Darwin arm64')
    destination = no_symlinks(destination)
    destination.relative_to(ROOT)
    private_directory(destination)
    if shutil.disk_usage(destination).free < 64 * 1024**3:
        raise DistributionError('free disk below 64 GiB floor')
    lock = read_object(ROOT / 'manifests/distribution/security-tools.lock.json')
    receipt = {'schema_version': 1, 'tools': {}}
    for name in ('gitleaks', 'syft'):
        info = lock[name]
        output = destination / name
        if output.exists():
            raise DistributionError('tool output already exists; inspect existing receipt before reusing it')
        archive = destination / (name + '.tar.gz')
        with urllib.request.urlopen(urllib.request.Request(info['url'], headers={'User-Agent': 'LiliuxFlow-security-builder'}), timeout=60) as response, archive.open('xb') as handle:
            while chunk := response.read(1024**2):
                handle.write(chunk)
                if handle.tell() > 128 * 1024**2:
                    raise DistributionError('tool download exceeds 128 MiB cap')
        if hashlib.sha256(archive.read_bytes()).hexdigest() != info['archive_sha256']:
            raise DistributionError('tool archive checksum mismatch')
        found = False
        notices = []
        with tarfile.open(archive, 'r:gz') as handle:
            for member in handle:
                relative_path(member.name)
                if not member.isfile():
                    raise DistributionError('tool archive has a link or special entry')
                if member.name in ('LICENSE', 'LICENSE.txt', 'LICENSE.md', 'NOTICE'):
                    if member.size > 128 * 1024:
                        raise DistributionError('tool notice exceeds cap')
                    notice = destination / (name + '-' + member.name)
                    with notice.open('xb') as target:
                        shutil.copyfileobj(handle.extractfile(member), target)
                    notices.append({'path': notice.name, 'sha256': hashlib.sha256(notice.read_bytes()).hexdigest()})
                if member.name == name:
                    if found or member.size > 128 * 1024**2:
                        raise DistributionError('duplicate or oversized tool binary')
                    with output.open('xb') as target:
                        shutil.copyfileobj(handle.extractfile(member), target)
                    output.chmod(0o700)
                    found = True
        if not notices:
            raise DistributionError('tool archive lacks an original license notice')
        if not found:
            raise DistributionError('tool archive lacks expected executable')
        receipt['tools'][name] = {'version': info['version'], 'archive_sha256': info['archive_sha256'],
                                  'binary_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'notices': notices}
        archive.unlink()
    write_json_new(destination / 'receipt.json', receipt)
    return receipt

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / 'var/build/liliuxflow-security-tools')
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(install(args.destination), sort_keys=True))
