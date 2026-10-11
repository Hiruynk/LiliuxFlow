#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Code-bound shared-MTP2 launch boundary; explicit installed opt-in required.

No receipt, environment variable, HTTP request or private master artifact may
supply the code-side recipe. Release packaging freezes the accepted constant;
without it the public entry point refuses without process or lease mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import uuid

from common import DistributionError, private_directory, read_object, write_json_new
from mtp2_shared_runtime import (SharedInstallation, shared_argv, shared_env, CACHE_NAMESPACE,
                                global_gpu_lease, runtime_identity_fields,
                                validate_shared_extension)
from mtp2_shared_key_fd import inherited_control_key
from mtp2_shared_journal import NumericJournal, provenance, MAX_BYTES
from model_runner import (DiskCacheBudget, memory_available, pending_owned_identity)
from ownership import capture, terminate, unchanged
from trust import atomic_private_json, validate

# Code-side activation belongs to the reviewed source release after acceptance.
# Normal agent/factories still require explicit installed shared opt-in.
# No environment, operator receipt or caller input can replace this recipe.
from mtp2_shared_runtime import AUTHORITATIVE_IDENTITY_EXPECTATION
RELEASE_EXPECTATION = AUTHORITATIVE_IDENTITY_EXPECTATION


def authenticated_launch(installation, port, backend_secret, directory):
    """Yield argv/pass_fds for an already verified internal installation.

The context remains open through Popen. No backend secret appears in argv/env,
and the FD is explicitly passed to the child using Python's pass_fds interface.
Ownership, the existing physical lease and lifecycle belong to execute_trusted.
"""
    from contextlib import contextmanager

    @contextmanager
    def plan():
        if type(installation) is not SharedInstallation:
            raise DistributionError('shared MTP2 launch requires validated installation')
        argv = shared_argv(installation, port)
        with inherited_control_key(backend_secret, directory) as descriptor:
            yield (argv + ['--liliuxflow-mtp2-control',
                           '--liliuxflow-mtp2-control-key-fd', str(descriptor)],
                   (descriptor,))
    return plan()


def run(data, port, *, candidate_validation=False):
    if type(candidate_validation) is not bool:
        raise DistributionError('shared MTP2 validation mode must be explicit')
    if candidate_validation:
        from mtp2_shared_upgrade import resolve_validation_selection, validation_view
        trusted = validate(data)
        binding = resolve_validation_selection(trusted, explicit_validation=True)
        return execute_trusted(validation_view(trusted, binding), binding.installation, port)
    if RELEASE_EXPECTATION is None:
        raise DistributionError('shared MTP2 final recipe is not enabled')
    if RELEASE_EXPECTATION.service_patch_sha256 is None:
        raise DistributionError('shared MTP2 authenticated service recipe is missing')
    trusted = validate(data, shared_context=True)
    installation = validate_shared_extension(trusted, RELEASE_EXPECTATION)
    if installation is None:
        raise DistributionError('shared MTP2 extension is not installed')
    return execute_trusted(trusted, installation, port)


def execute_trusted(trusted, installation, port):
    """Sole child owner using the established physical lease, never a new lease.

    Lifecycle records prove process ownership only. Per-request native release
    authority comes exclusively from the authenticated transport in the guard.
    Stderr is continuously drained but cannot manufacture release events.
    """
    if type(installation) is not SharedInstallation:
        raise DistributionError('shared MTP2 installation is not verified')
    shared_argv(installation, port)  # validate before any lease/process mutation
    secret = trusted['secrets']['MANAGER_BACKEND_TOKEN']
    if not isinstance(secret, str):
        raise DistributionError('shared MTP2 backend credential is unavailable')
    data = installation.data_root
    run_root = private_directory(data / 'run')
    tmp = private_directory(run_root / 'tmp')
    # The shared disk tier is disabled; observe the existing six namespaces
    # (including preserved historical opt-in cache) through the same observer.
    roots = tuple(data / 'cache' / p.cache_namespace for p in trusted['registry'].profiles)
    cache = private_directory(data / 'cache' / CACHE_NAMESPACE)
    if any(cache.iterdir()):
        raise DistributionError('shared MTP2 disabled disk namespace is not empty')
    budget = DiskCacheBudget(trusted['registry'], roots, cache)
    budget.preflight()
    available = memory_available()
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise DistributionError('shared MTP2 backend port is occupied')
    lease = global_gpu_lease()
    private_directory(lease.parent)
    try:
        lease.mkdir(mode=0o700)
    except FileExistsError:
        raise DistributionError('GPU instance lease is busy; no model started') from None
    lease_identity = (lease.stat().st_dev, lease.stat().st_ino)
    try:
        runner = capture(os.getpid())
    except BaseException:
        if ((lease.stat().st_dev, lease.stat().st_ino) == lease_identity
                and not any(lease.iterdir())):
            lease.rmdir()
        raise
    record = {'schema_version': 1, 'installation_id': trusted['config']['installation_id'],
              'runner': runner, 'child': None, 'profile_id': 'mtp2-shared',
              'public_alias': 'liliuxflow-internal-mtp2-shared', 'context_tokens': 262144,
              'backend_port': port, 'binary_sha256': installation.engine.binary_sha256,
              'startup_id': uuid.uuid4().hex, 'ram_headroom_policy': 'advisory',
              **runtime_identity_fields(installation.engine)}
    if installation.validation_bundle_id is not None:
        from mtp2_shared_upgrade import require_validation_binding
        binding=require_validation_binding(trusted)
        record['validation_bundle_id']=binding.generation
        record['validation_bundle_manifest_sha256']=binding.manifest_sha256
    child = identity = drain = journal = None
    journal_init_errors = 0
    handlers = {}
    stopping = False
    written = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if identity is not None and unchanged(identity):
            os.kill(identity['pid'], signum)

    def drain_stderr(stream):
        # Diagnostic evidence only; no stderr event authorizes live release.
        if journal is not None:
            journal.drain(stream)
        else:
            read = getattr(stream, 'read1', stream.read)
            while read(65536): pass

    try:
        write_json_new(lease / 'lease.json', record)
        written = True
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, stop)
        env = shared_env({'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(Path.home()),
                          'TMPDIR': str(tmp), 'LANG': 'en_US.UTF-8'})
        with authenticated_launch(installation, port, secret.encode('utf-8'), tmp) as (argv, fds):
            record['argv_sha256'] = hashlib.sha256(json.dumps(argv, separators=(',', ':')).encode()).hexdigest()
            atomic_private_json(lease / 'lease.json', record)
            child = subprocess.Popen(argv, env=env, pass_fds=fds, stderr=subprocess.PIPE)
        identity = capture(child.pid, parent=os.getpid(), executable=installation.binary)
        record['child'] = identity
        atomic_private_json(lease / 'lease.json', record)
        atomic_private_json(run_root / 'mtp2-shared-model.json', record)
        try:
            journal = NumericJournal(run_root, provenance(record), max_bytes=MAX_BYTES)
        except Exception:
            journal_init_errors = 1  # No raw exception/record is persisted.
        drain = threading.Thread(target=drain_stderr, args=(child.stderr,), daemon=True)
        drain.start()
        if stopping and unchanged(identity):
            os.kill(identity['pid'], signal.SIGTERM)
        while child.poll() is None:
            if any(cache.iterdir()):
                raise DistributionError('shared MTP2 disabled disk namespace changed')
            resource = budget.read()
            available = memory_available()
            atomic_private_json(run_root / 'mtp2-shared-resources.json', {
                **resource, 'ram_headroom_bytes': available, 'ram_headroom_policy': 'advisory',
                'recommended_ram_headroom_bytes': 15 * 1024**3,
                'target_ram_headroom_bytes': 24 * 1024**3,
                'installation_id': record['installation_id'], 'startup_id': record['startup_id'],
                'child': identity, 'native_journal': (journal.snapshot() if journal is not None else
                    {'schema_version': 1, 'startup_id': record['startup_id'], 'initialization_errors': journal_init_errors,
                     'live_release_authorized': False, 'runtime_acceptance': False})})
            if resource['stop_reason'] is not None:
                if not terminate(identity, grace=20):
                    raise DistributionError('shared MTP2 owned exit unverified; lease retained')
                break
            time.sleep(1)
        return child.wait()
    finally:
        if child is not None and child.poll() is None and identity is None:
            identity = pending_owned_identity(child, runner, argv)
            record['child'] = identity
            atomic_private_json(lease / 'lease.json', record)
        if child is not None and child.poll() is None and identity is not None:
            terminate(identity, grace=20)
        if child is not None and child.poll() is not None:
            child.wait()
            if drain is not None:
                drain.join(timeout=5)
        if journal is not None:
            if drain is not None and drain.is_alive(): journal._bump('read_errors')
            status = journal.close(timeout=1)
            try:
                atomic_private_json(run_root / 'mtp2-shared-journal-status.json', status)
            except Exception:
                journal._bump('writer_errors')  # Existing journal/footer/resources still expose loss.
        for signum, previous in handlers.items():
            signal.signal(signum, previous)
        if child is None or child.poll() is not None:
            same_lease = (lease.stat().st_dev, lease.stat().st_ino) == lease_identity
            if same_lease and written and read_object(lease / 'lease.json') == record:
                (lease / 'lease.json').unlink()
                lease.rmdir()
            elif same_lease and not written and not any(lease.iterdir()):
                lease.rmdir()
            model = run_root / 'mtp2-shared-model.json'
            if model.exists() and read_object(model) == record:
                model.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--candidate-validation', action='store_true', default=False)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        raise SystemExit(run(args.data_root, args.port, candidate_validation=args.candidate_validation))
    except DistributionError:
        print('LILIUXFLOW_SHARED_MODEL_START_REFUSED', flush=True)
        raise SystemExit(2)
