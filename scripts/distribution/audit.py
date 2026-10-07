#!/usr/bin/env python3
"""Offline maintained scanner and SBOM gates. Only sanitized findings leave the private task folder."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from common import DistributionError, no_symlinks, private_directory, read_object, write_json_new
from package import selected_files, safe_extract, git, forbidden_content
ROOT = Path(__file__).resolve().parents[2]


def tool_versions(tools):
    receipt = read_object(tools / 'receipt.json')
    lock = read_object(ROOT / 'manifests/distribution/security-tools.lock.json')
    for name in ('gitleaks', 'syft'):
        file = no_symlinks(tools / name)
        record = receipt['tools'][name]
        if record['version'] != lock[name]['version'] or record['archive_sha256'] != lock[name]['archive_sha256'] or hashlib.sha256(file.read_bytes()).hexdigest() != record['binary_sha256']:
            raise DistributionError('security tool identity differs from pinned receipt')
        for notice in record.get('notices', []):
            if hashlib.sha256(no_symlinks(tools / notice['path']).read_bytes()).hexdigest() != notice['sha256']:
                raise DistributionError('security tool original notice differs from pinned receipt')
        if not record.get('notices'):
            raise DistributionError('security tool receipt lacks original notices')
    return receipt['tools']


def scanner(tools, target, task, *, history=False, history_ref=None, private_findings=None):
    report = task / 'scanner-private.json'
    config = task / 'default-rules.toml'
    config.write_text('[extend]\nuseDefault = true\n')
    command = [str(tools / 'gitleaks'), 'git' if history else 'dir', str(target), '--config', str(config),
               '--gitleaks-ignore-path', str(task), '--ignore-gitleaks-allow', '--redact=100', '--no-banner', '--no-color',
               '--log-level=error', '--max-archive-depth=3', '--max-decode-depth=5', '--timeout=180',
               '--report-format=json', '--report-path', str(report)]
    if history:
        command.append('--log-opts=' + (history_ref if history_ref else '--all') + ' --full-history')
    # No external credential validation or provider requests. Isolated tool config cannot inherit repo ignores.
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'TMPDIR': str(task)}
    result = subprocess.run(command, capture_output=True, env=env, cwd=task, timeout=200)
    if result.returncode not in (0, 1) or not report.is_file():
        raise DistributionError('secret scanner failed; raw output withheld')
    raw = json.loads(report.read_text()) or []
    if not isinstance(raw, list):
        raise DistributionError('scanner report is malformed')
    findings = []
    private_records=[]
    for item in raw:
        path = item.get('File', '')
        # For filesystem scans normalize to source-relative and refuse an unexpected external reference.
        if not history and Path(path).is_absolute():
            try:
                path = str(Path(path).relative_to(target))
            except ValueError:
                path = 'external-path-redacted'
        match_hash=hashlib.sha256(str(item.get('Match','')).encode()).hexdigest()
        fingerprint_hash=hashlib.sha256(str(item.get('Fingerprint','')).encode()).hexdigest()
        span_hash=None;file_hash=None
        if not history:
            candidate=no_symlinks(target/path)
            try:candidate.relative_to(target)
            except ValueError:raise DistributionError('scanner finding path escapes source')
            data=candidate.read_bytes();file_hash=hashlib.sha256(data).hexdigest();lines=data.splitlines(keepends=True)
            start=item.get('StartLine');end=item.get('EndLine')
            if type(start)is int and type(end)is int and 1<=start<=end<=len(lines):span_hash=hashlib.sha256(b''.join(lines[start-1:end])).hexdigest()
        private_records.append({'redaction_percent':100,'finding':item,'source_file_sha256':file_hash,'source_line_span_sha256':span_hash,'match_sha256':match_hash,'match_hash_basis':'Gitleaks redacted Match, not unredacted credential material','fingerprint_sha256':fingerprint_hash})
        findings.append({'rule': item.get('RuleID'), 'file': path, 'line': item.get('StartLine'),'end_line':item.get('EndLine'),'start_column':item.get('StartColumn'),'end_column':item.get('EndColumn'),'source_file_sha256':file_hash,'source_line_span_sha256':span_hash,'match_sha256':match_hash,'fingerprint_sha256':fingerprint_hash,
                         'commit': item.get('Commit') or None, 'status': 'NEEDS_PRIVATE_REVIEW'})
    if private_findings is not None:
        destination=no_symlinks(private_findings);private_directory(destination.parent)
        write_json_new(destination,{'schema_version':1,'scope':'redacted scanner detail for exact private review; never release/git','findings':private_records})
    if bool(findings) != (result.returncode == 1):
        raise DistributionError('scanner exit and finding count disagree')
    return {'status': 'FAIL' if findings else 'PASS', 'exit_code': result.returncode,
            'finding_count': len(findings), 'findings': findings,
            'archive_depth': 3, 'decode_depth': 5, 'credential_validation': False}


def syft(tools, source, task, commit):
    report = task / 'sbom-private.json'
    config = task / 'syft.yaml'
    config.write_text('check-for-app-update: false\nparallelism: 2\n')
    command = [str(tools / 'syft'), 'scan', 'dir:.', '--config', str(config), '--quiet',
               '--base-path', '.', '--source-name', 'LiliuxFlow', '--source-version', commit,
               '--parallelism', '2', '--output', 'cyclonedx-json=' + str(report)]
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'TMPDIR': str(task), 'SYFT_CHECK_FOR_APP_UPDATE': 'false'}
    result = subprocess.run(command, capture_output=True, env=env, cwd=source, timeout=180)
    if result.returncode or not report.is_file():
        raise DistributionError('SBOM cataloging failed; raw output withheld')
    sbom = json.loads(report.read_text())
    # Syft emits absolute names for file components even with base-path. Derive only those
    # source-relative names; package identities, purls, hashes and relationships stay intact.
    for component in sbom.get('components', []):
        if component.get('type') == 'file' and Path(component.get('name', '')).is_absolute():
            try:
                component['name'] = str(Path(component['name']).relative_to(source))
            except ValueError:
                raise DistributionError('SBOM file component escapes source root')
    sbom.setdefault('metadata', {}).setdefault('properties', []).append({
        'name': 'liliuxflow:normalization', 'value': 'Syft file component names made source-relative'})
    # CycloneDX reports must use source-relative paths, never machine-specific task roots.
    encoded = json.dumps(sbom, ensure_ascii=False)
    if str(task) in encoded or '/Users/' in encoded:
        raise DistributionError('SBOM contains machine-specific paths')
    return sbom


def audit(args):
    tools = no_symlinks(args.tools)
    versions = tool_versions(tools)
    output = private_directory(args.output)
    receipt = {'schema_version': 1, 'tools': versions, 'release_ready': False,
               'release_assets_scan': 'NOT_RUN', 'ci_artifacts_scan': 'NOT_RUN', 'lfs_objects_scan': 'NOT_RUN'}
    with tempfile.TemporaryDirectory(prefix='liliuxflow-audit-', dir=output) as tmp:
        task = Path(tmp)
        if args.history:
            receipt['scope'] = 'all local reachable Git refs; secrets only; no working tree or remote artifacts'
            resolved = git(args.root, 'rev-parse', '--verify', args.history_ref + '^{commit}').decode().strip() if args.history_ref else None
            receipt['resolved_history_ref'] = resolved
            receipt['scope'] = 'selected ref and ancestors; secrets and blob formats only' if resolved else receipt['scope']
            receipt['history'] = scanner(tools, no_symlinks(args.root), task, history=True, history_ref=resolved,private_findings=getattr(args,'private_findings',None))
            # Enumerate every reachable blob, not only filenames, for LFS pointer/weight/DB format checks.
            objects = git(args.root, 'rev-list', '--objects', resolved or '--all').splitlines()
            lfs, weights, oversize = [], [], []
            for line in objects:
                oid = line.split(b' ', 1)[0].decode()
                if git(args.root, 'cat-file', '-t', oid).strip() != b'blob':
                    continue
                size = int(git(args.root, 'cat-file', '-s', oid))
                if size > 16 * 1024**2:
                    oversize.append(oid)
                    continue
                data = git(args.root, 'cat-file', 'blob', oid)
                if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    lfs.append(oid)
                elif forbidden_content(data):
                    weights.append(oid)
            receipt['reachable_blob_checks'] = {'lfs_pointer_blob_ids': lfs, 'weight_or_db_magic_blob_ids': weights, 'oversize_unscanned_blob_ids': oversize}
        else:
            if args.archive:
                safe_extract(args.archive, task / 'source')
                source = task / 'source' / 'LiliuxFlow'
                if not source.is_dir():
                    raise DistributionError('archive lacks LiliuxFlow root')
                commit = 'archive-' + hashlib.sha256(args.archive.read_bytes()).hexdigest()
            else:
                commit, policy, files = selected_files(args.root, args.commit)
                source = task / 'source'
                source.mkdir(mode=0o700)
                for name, _, data in files:
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            receipt['scope'] = 'safe-extracted archive' if args.archive else 'exact commit source allowlist'
            receipt['commit'] = commit
            receipt['tree'] = scanner(tools, source, task,private_findings=getattr(args,'private_findings',None))
            sbom = syft(tools, source, task, commit)
            receipt['sbom_components'] = len(sbom.get('components', []))
            write_json_new(output / 'sbom.cdx.json', sbom)
        write_json_new(output / 'audit.json', receipt)
    result = receipt.get('history', receipt.get('tree'))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('history', 'tree')} | {'secret_scan_status': result['status'], 'finding_count': result['finding_count']}, sort_keys=True))
    return 2 if result['status'] != 'PASS' or any(receipt.get('reachable_blob_checks', {}).values()) else 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--tools', type=Path, default=ROOT / 'var/build/liliuxflow-security-tools')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private-findings',type=Path,help='new file in owned0700 private directory; retains redacted end-line/fingerprint/Match details, never printed')
    parser.add_argument('--history-ref', help='with --history, scan only this exact ref and ancestors')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--commit')
    mode.add_argument('--archive', type=Path)
    mode.add_argument('--history', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        raise SystemExit(audit(args))
    except (DistributionError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print(json.dumps({'status': 'BLOCKED', 'error': 'audit failed; private contents withheld'}))
        raise SystemExit(2)
