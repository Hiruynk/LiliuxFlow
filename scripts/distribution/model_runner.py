#!/usr/bin/env python3
"""Sole lazy Lily child entry point for a finite trusted context profile.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import stat
import subprocess
import threading
import time
import uuid
from common import DistributionError, private_directory, read_object, write_json_new
from ownership import capture, unchanged, terminate
from profile_registry import ProfileRegistry, legacy_registry, MINIMUM_RAM_HEADROOM_GIB, OPENAI_MAX_IMAGES
from trust import validate, atomic_private_json


def memory_available():
    result = subprocess.run(['/usr/bin/vm_stat'], capture_output=True, text=True)
    if result.returncode:
        raise DistributionError('RAM observer cannot read host memory')
    match = re.search(r'page size of (\d+) bytes', result.stdout)
    if not match or int(match.group(1)) <= 0:
        raise DistributionError('RAM observer page size is unknown')
    counts = {}
    for line in result.stdout.splitlines()[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            value = value.strip().rstrip('.')
            if value.isdigit():
                counts[key] = int(value)
    required = ('Pages free', 'Pages inactive', 'Pages speculative')
    if any(key not in counts for key in required):
        raise DistributionError('RAM observer page counters are unknown')
    return int(match.group(1)) * sum(counts[key] for key in required)


def selected_profile(trusted, profile_id, *, allow_validation=False):
    registry = trusted.get('registry')
    if registry is None:
        registry = legacy_registry(trusted['profile'])
    if not isinstance(registry, ProfileRegistry):
        raise DistributionError('runner requires a validated immutable context registry')
    return registry.by_id(profile_id, allow_validation=allow_validation)


def ram_headroom_status(profile, available_bytes):
    """Observe validated headroom against the profile's advisory recommendation.

    The existing minimum_* metadata remains hash compatible with trusted
    profiles; it names the recommendation and no longer authorizes a RAM stop.
    Unknown profile metadata or measurements still fail closed.
    """
    recommendation = MINIMUM_RAM_HEADROOM_GIB.get(profile.profile_id)
    if (type(profile.minimum_ram_headroom_gib) is not int or profile.minimum_ram_headroom_gib != recommendation
            or type(available_bytes) is not int or available_bytes < 0):
        raise DistributionError('RAM observer profile or headroom is unknown')
    below = available_bytes < recommendation * 1024**3
    return {'profile_id': profile.profile_id, 'ram_headroom_bytes': available_bytes,
            'minimum_ram_headroom_gib': recommendation, 'minimum_ram_headroom_bytes': recommendation * 1024**3,
            'recommended_ram_headroom_gib': recommendation,
            'recommended_ram_headroom_bytes': recommendation * 1024**3,
            'target_ram_headroom_gib': 24, 'ram_headroom_policy': 'advisory',
            'ram_headroom_status': 'below_recommended' if below else 'meets_recommended',
            'ram_warning_reason': 'RAM_BELOW_RECOMMENDED' if below else None,
            'ram_stop_reason': None}


def _warn_ram_headroom_once(status, warned):
    """Emit at most one scalar warning per runner invocation, including recovery."""
    if not warned and status['ram_warning_reason'] is not None:
        print('LILIUXFLOW_RESOURCE_WARNING reason=RAM_BELOW_RECOMMENDED policy=advisory'
              + ' profile_id=' + status['profile_id']
              + ' available_bytes=' + str(status['ram_headroom_bytes'])
              + ' recommended_bytes=' + str(status['recommended_ram_headroom_bytes']), flush=True)
        return True
    return warned


def lily_argv(trusted, port, *, profile_id='ctx64k', allow_validation=False, cache_dir=None):
    if type(port) is not int or not 1024 <= port <= 65535 or port in trusted['config']['ports'].values():
        raise DistributionError('Lily backend port conflicts with a control-plane port')
    profile = selected_profile(trusted, profile_id, allow_validation=allow_validation)
    cache = private_directory(cache_dir if cache_dir is not None else trusted['data_root'] / 'cache' / profile.cache_namespace)
    return [str(trusted['binaries']['lily']), '--model', trusted['config']['model_dir'],
            '--bind', '127.0.0.1:' + str(port), '--max-seq', str(profile.context_tokens),
            '--mtp-drafts', '0', '--ngram-table', 'paged', '--ngram-preload', 'false',
            '--cache-bytes', str(profile.cache_bytes), '--max-sessions', str(profile.max_sessions),
            '--disk-cache-bytes', str(profile.disk_cache_bytes), '--disk-cache-ttl', '24h',
            '--durable-min-tokens', '1024', '--idle-unload', '0', '--queue', '4',
            '--vision', 'auto', '--max-images', str(OPENAI_MAX_IMAGES), '--disk-cache-dir', str(cache)]


def cache_footprint(roots):
    """Count all regular cache files, including unfinished native writes.

    This observes private metadata only. It never opens cache payloads, follows
    links, removes production entries or scans the external checkpoint.
    """
    from common import no_symlinks
    sizes = []
    count = 0
    for root in roots:
        root = no_symlinks(Path(root))
        total = 0
        if not root.exists():
            sizes.append(0)
            continue
        private_directory(root)
        pending = [root]
        while pending:
            path = pending.pop()
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        count += 1
                        if count > 100000:
                            raise DistributionError('cache inventory exceeds the bounded observer limit')
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue  # Normal native eviction may race the scan.
                        if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode):
                            raise DistributionError('cache inventory ownership or links differ')
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode):
                            total += info.st_size
                        else:
                            raise DistributionError('cache inventory contains an unknown file type')
            except FileNotFoundError:
                continue
        sizes.append(total)
    return sum(sizes), sum(sizes[1:])


class DiskCacheBudget:
    """A conservative preflight/1 Hz stop policy, not a filesystem quota.

    The total 52 GiB trigger always preserves 12 GiB of graceful-write room
    below the unchanged 64 GiB total cap. Preflight also preserves 12 GiB in
    the added-cache 32 GiB cap; live serialized writes may consume that added
    reserve, while the total trigger and the added 32 GiB boundary stay active.
    This observer is not a filesystem quota; the pinned one-entry-at-a-time
    writer and its bounded entry/grace-write sizes are part of the policy.
    """
    def __init__(self, registry, cache_roots, volume):
        from common import no_symlinks
        if not isinstance(cache_roots, (tuple, list)) or not 1 <= len(cache_roots) <= 6:
            raise DistributionError('cache observer roots must be a bounded trusted sequence')
        self.roots = tuple(no_symlinks(Path(path)) for path in cache_roots)
        if len(set(self.roots)) != len(self.roots):
            raise DistributionError('cache observer roots must be distinct')
        for first in self.roots:
            for other in self.roots:
                if first != other and first in other.parents:
                    raise DistributionError('cache observer roots cannot overlap')
        self.volume = no_symlinks(Path(volume))
        self.total = registry.total_disk_cache_cap_bytes
        self.added = registry.maximum_added_cache_bytes
        self.reserve = registry.temporary_cache_reserve_bytes
        self.free_floor = registry.minimum_free_disk_bytes

    def read(self, *, preflight=False):
        if type(preflight) is not bool:
            raise DistributionError('disk budget phase must be an internal boolean')
        try:
            total, added = cache_footprint(self.roots)
            free = shutil.disk_usage(self.volume).free
        except (OSError, ValueError) as error:
            raise DistributionError('disk/cache observer is unavailable') from error
        if any(type(value) is not int or value < 0 for value in (total, added, free)):
            raise DistributionError('disk/cache byte counts are unknown')
        reason = ('DISK_FREE_FLOOR' if free < self.free_floor else
                  'CACHE_HARD_CAP' if total > self.total else
                  'ADDED_CACHE_HARD_CAP' if added >= self.added else
                  'CACHE_TEMPORARY_RESERVE' if total > self.total - self.reserve else
                  'ADDED_CACHE_TEMPORARY_RESERVE' if preflight and added > self.added - self.reserve else None)
        return {'cache_bytes': total, 'added_cache_bytes': added, 'free_disk_bytes': free,
                'temporary_reserve_bytes': self.reserve, 'budget_phase': 'preflight' if preflight else 'inflight',
                'total_cache_hard_cap_bytes': self.total, 'added_cache_hard_cap_bytes': self.added,
                'total_cache_inflight_trigger_bytes': self.total - self.reserve,
                'total_cache_preflight_limit_bytes': self.total - self.reserve,
                'added_cache_preflight_limit_bytes': self.added - self.reserve, 'stop_reason': reason}

    def preflight(self):
        result = self.read(preflight=True)
        if result['stop_reason'] is not None:
            raise DistributionError('disk/cache policy lacks its conservative temporary reserve')
        return result


class LaneState:
    """Owner-bound proof from pinned native events after run/session cleanup."""
    EVENT = re.compile(r'^\[lily-meta\] request_id=(req-[0-9]+-[0-9]+) phase=(lane_acquired|lane_released)(?:\s|$)')
    PROGRESS = re.compile(r'^\[lily-meta\] request_id=(req-[0-9]+-[0-9]+) phase=prefill_progress completed_chunks=([0-9]{1,12}) prefilled_tokens=([0-9]{1,12})(?:\s|$)')
    ROUTE = re.compile(r'^LILY_QSA_ROUTE_EFFECTIVE requested=(unset|tile|split|invalid) route=(tile|split)$')
    DISPATCH = re.compile(r'^LILY_QSA_DISPATCH_METADATA sparse_prefill_rows=([0-9]{1,12}) route=(tile|split) split_dispatch_count=([0-9]{1,12}) tiled_dispatch_count=([0-9]{1,12})$')
    MEMORY = re.compile(r'^memory: ([0-9]{1,5}\.[0-9]) GB allocated, ([0-9]{1,5}\.[0-9]) GB recommended working set, ([0-9]{1,5}\.[0-9]) GB session cache budget \(([0-9]{1,12}) B/token of context; a full ([0-9]{1,12})-token request needs ([0-9]{1,5}\.[0-9]) GB\)$')

    def __init__(self, path, record):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.record = {**record, 'event_sequence': 0, 'acquired_sequence': 0,
                       'released_sequence': 0, 'active_lane_ids': [], 'last_released_request_id': None,
                       'last_prefill_progress': None,
                       'qsa_route_effective': None, 'qsa_dispatch_metadata': None,
                       'native_context_tokens': None, 'native_memory': None,
                       'proof_valid': True, 'stderr_closed': False, 'child_exited': False}
        self._write()

    def _write(self):
        atomic_private_json(self.path, self.record)

    def consume(self, raw):
        route, dispatch, memory = self.ROUTE.fullmatch(raw), self.DISPATCH.fullmatch(raw), self.MEMORY.fullmatch(raw)
        if route or dispatch or memory:
            with self.lock:
                if route:
                    requested, selected = route.groups()
                    self.record['qsa_route_effective'] = {'requested': requested, 'route': selected}
                    if requested != 'split' or selected != 'split':
                        self.record['proof_valid'] = False
                if dispatch:
                    rows, selected, split_count, tiled_count = dispatch.groups()
                    self.record['qsa_dispatch_metadata'] = {'sparse_prefill_rows': int(rows), 'route': selected,
                        'split_dispatch_count': int(split_count), 'tiled_dispatch_count': int(tiled_count)}
                    if selected != 'split' or int(split_count) != 1 or int(tiled_count) != 0:
                        self.record['proof_valid'] = False
                if memory:
                    allocated, working_set, budget, per_token, context, full = memory.groups()
                    self.record['native_context_tokens'] = int(context)
                    self.record['native_memory'] = {'allocated_gb_decimal': float(allocated),
                        'recommended_working_set_gb_decimal': float(working_set),
                        'session_cache_budget_gb_decimal': float(budget),
                        'bytes_per_token': int(per_token), 'full_context_gb_decimal': float(full)}
                    if 'context_tokens' in self.record and int(context) != self.record['context_tokens']:
                        self.record['proof_valid'] = False
                self._write()
            return True
        progress = self.PROGRESS.match(raw)
        if progress:
            request_id, chunks, tokens = progress.groups()
            with self.lock:
                if self.record['active_lane_ids'] != [request_id]:
                    self.record['proof_valid'] = False
                else:
                    self.record['last_prefill_progress'] = {'request_id': request_id,
                        'completed_chunks': int(chunks), 'prefilled_tokens': int(tokens)}
                self._write()
            return True
        match = self.EVENT.match(raw)
        if not match:
            return False
        request_id, phase = match.groups()
        with self.lock:
            self.record['event_sequence'] += 1
            active = self.record['active_lane_ids']
            if phase == 'lane_acquired':
                self.record['last_prefill_progress'] = None
                if active:
                    self.record['proof_valid'] = False
                else:
                    active.append(request_id)
                self.record['acquired_sequence'] += 1
            else:
                if active != [request_id]:
                    self.record['proof_valid'] = False
                else:
                    active.clear()
                self.record['released_sequence'] += 1
                self.record['last_released_request_id'] = request_id
            self._write()
        return True

    def closed(self, *, failed=False, exited=False):
        with self.lock:
            self.record['stderr_closed'] = True
            self.record['child_exited'] = exited
            if failed:
                self.record['proof_valid'] = False
            self._write()


def _drain_metadata(pipe, state):
    try:
        while True:
            raw = pipe.readline(8193)
            if not raw:
                break
            if len(raw) > 8192 or not raw.endswith(b'\n'):
                while raw and not raw.endswith(b'\n'):
                    raw = pipe.readline(8193)
                continue
            line = raw.decode('utf-8', errors='replace').rstrip('\n')
            if state.consume(line):
                # Only scalar native lane metadata is mirrored. Arbitrary stderr
                # is drained without buffering or persisting prompt-bearing text.
                print(line, flush=True)
        state.closed()
    except Exception:
        state.closed(failed=True)
    finally:
        pipe.close()


def execute_trusted(trusted, port, *, profile_id='ctx64k', allow_validation=False,
                    cache_dir=None, run_root=None, lease_root=None, cache_roots=None):
    """Run a validated shape; a root bridge validates its original provenance.

    Root paths are private internal arguments, never public request/CLI input.
    """
    profile = selected_profile(trusted, profile_id, allow_validation=allow_validation)
    argv = lily_argv(trusted, port, profile_id=profile_id, allow_validation=allow_validation, cache_dir=cache_dir)
    registry = trusted.get('registry') or legacy_registry(trusted['profile'])
    if cache_roots is None:
        cache_roots = tuple(trusted['data_root'] / 'cache' / p.cache_namespace for p in registry.profiles)
        if cache_dir is not None:
            raise DistributionError('a root cache override requires its complete trusted cache inventory')
    disk_budget = DiskCacheBudget(registry, cache_roots, Path(argv[argv.index('--disk-cache-dir') + 1]))
    disk_budget.preflight()
    preflight_ram = ram_headroom_status(profile, memory_available())
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            raise DistributionError('Lily backend port is occupied; owner left untouched')
    data = trusted['data_root']
    run_root = private_directory(run_root if run_root is not None else data / 'run')
    leases = private_directory(lease_root if lease_root is not None else
                               Path.home() / 'Library/Application Support/LiliuxFlow-runtime-leases')
    lease = leases / 'gpu.lease'
    try:
        lease.mkdir(mode=0o700)
    except FileExistsError:
        raise DistributionError('GPU instance lease is busy; no model started')
    runner = capture(os.getpid())
    record = {'schema_version': 1, 'installation_id': trusted['config']['installation_id'],
              'runner': runner, 'child': None, 'profile_id': profile.profile_id,
              'public_alias': profile.public_alias, 'context_tokens': profile.context_tokens,
              'minimum_ram_headroom_gib': profile.minimum_ram_headroom_gib,
              'ram_headroom_policy': 'advisory',
              'recommended_ram_headroom_gib': profile.minimum_ram_headroom_gib,
              'backend_port': port, 'binary_sha256': trusted['trust']['binaries']['lily']['sha256'],
              'argv_sha256': hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest(),
              'startup_id': uuid.uuid4().hex}
    write_json_new(lease / 'lease.json', record)
    child = identity = state = drain = None
    handlers = {}
    stopping = False
    def forward(signum, _frame):
        nonlocal stopping
        stopping = True
        if identity and unchanged(identity):
            os.kill(identity['pid'], signum)
    try:
        # Handlers precede Popen so a Stop during startup cannot orphan a child.
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, forward)
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(Path.home()),
               'TMPDIR': str(private_directory(run_root / 'tmp')), 'LANG': 'en_US.UTF-8', 'LILY_QSA_ROUTE': 'split'}
        ram_warned = _warn_ram_headroom_once(preflight_ram, False)
        child = subprocess.Popen(argv, env=env, stderr=subprocess.PIPE)
        identity = capture(child.pid, parent=os.getpid(), executable=trusted['binaries']['lily'])
        record['child'] = identity
        atomic_private_json(lease / 'lease.json', record)
        atomic_private_json(run_root / 'model.json', record)
        state = LaneState(run_root / 'model-state.json', record)
        drain = threading.Thread(target=_drain_metadata, args=(child.stderr, state), daemon=True)
        drain.start()
        if stopping and unchanged(identity):
            os.kill(identity['pid'], signal.SIGTERM)
        while child.poll() is None:
            reason = None
            try:
                resource = disk_budget.read()
                reason = resource['stop_reason']
                ram = ram_headroom_status(profile, memory_available())
                ram_warned = _warn_ram_headroom_once(ram, ram_warned)
                atomic_private_json(run_root / 'model-resources.json',
                                    {**resource, **ram, 'stop_reason': reason,
                                     'installation_id': record['installation_id'],
                                     'startup_id': record['startup_id'], 'child': identity})
            except (DistributionError, OSError):
                reason = 'RESOURCE_OBSERVER_UNAVAILABLE'
            if reason is not None:
                print('LILIUXFLOW_RESOURCE_STOP reason=' + reason, flush=True)
                if not terminate(identity, grace=20):
                    raise DistributionError('owned Lily did not exit after resource stop; instance lease retained')
                break
            time.sleep(1)
        return child.wait()
    finally:
        if child is not None and child.poll() is None and identity:
            terminate(identity, grace=20)
        if child is not None and child.poll() is not None:
            child.wait()
            if drain is not None:
                drain.join(timeout=5)
            if state is not None:
                state.closed(failed=bool(drain and drain.is_alive()), exited=True)
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if child is None or child.poll() is not None:
            current = read_object(lease / 'lease.json')
            if current == record:
                (lease / 'lease.json').unlink()
                lease.rmdir()
            model_record = run_root / 'model.json'
            if model_record.exists() and read_object(model_record) == record:
                model_record.unlink()


def run(data, port, *, profile_id='ctx64k', validation=False):
    trusted = validate(data)
    if validation:
        from runtime_proof import validation_authorized
        if not validation_authorized(trusted, profile_id):
            raise DistributionError('validation profile authorization is unavailable')
    return execute_trusted(trusted, port, profile_id=profile_id, allow_validation=validation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--profile', default='ctx64k', choices=('ctx64k', 'ctx128k', 'ctx262k'))
    parser.add_argument('--validation', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        raise SystemExit(run(args.data_root, args.port, profile_id=args.profile, validation=args.validation))
    except DistributionError:
        print('LILIUXFLOW_MODEL_START_REFUSED', flush=True)
        raise SystemExit(2)
