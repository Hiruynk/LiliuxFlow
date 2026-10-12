# SPDX-License-Identifier: Apache-2.0
"""Exact shared child/lease proof; no inference or resource-release authority."""
from dataclasses import dataclass, field
import hashlib
import json
import os
import stat
import socket

from common import DistributionError, read_object
from mtp2_shared_runtime import (SharedInstallation, global_gpu_lease,
                                runtime_identity_fields)
from ownership import inspect
from trust import private_file


@dataclass(frozen=True, slots=True)
class ProcessBinding:
    port: int
    startup_id: str
    binary_sha256: str
    record_sha256: str
    lease_device: int
    lease_inode: int
    child_pid: int
    child_started: str
    _owner: object = field(repr=False, compare=False)


def _hash(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Mtp2SharedOwnerProbe:
    def __init__(self, trusted, installation):
        if type(installation) is not SharedInstallation:
            raise DistributionError('shared MTP2 owner requires validated installation')
        self._installation = installation
        self._installation_id = trusted['config']['installation_id']
        self._owner = object()

    def current_owner(self):
        """Read and recheck exact lease identity; never guess resident epoch."""
        lease = global_gpu_lease()
        before = lease.stat()
        if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o700):
            raise DistributionError('shared MTP2 lease directory ownership differs')
        lease_record = read_object(private_file(lease / 'lease.json'))
        runtime = self._installation.data_root / 'run/mtp2-shared-model.json'
        record = read_object(private_file(runtime))
        fixed = {'schema_version': 1, 'installation_id': self._installation_id,
                 'profile_id': 'mtp2-shared', 'public_alias': 'liliuxflow-internal-mtp2-shared',
                 'context_tokens': 262144, 'binary_sha256': self._installation.engine.binary_sha256,
                 **runtime_identity_fields(self._installation.engine)}
        if (record != lease_record or any(type(record.get(k)) is not type(v) or record.get(k) != v
                                          for k, v in fixed.items())):
            raise DistributionError('shared MTP2 lease/model identity differs')
        port, startup = record.get('backend_port'), record.get('startup_id')
        if (type(port) is not int or not 1024 <= port <= 65535 or port in self._installation.control_ports
                or type(startup) is not str or len(startup) != 32
                or any(c not in '0123456789abcdef' for c in startup)):
            raise DistributionError('shared MTP2 port/startup identity differs')
        runner, child = record.get('runner'), record.get('child')
        if (type(runner) is not dict or type(child) is not dict
                or runner.get('uid') != os.getuid() or child.get('uid') != os.getuid()
                or child.get('ppid') != runner.get('pid')):
            raise DistributionError('shared MTP2 process lineage differs')
        actual_runner = inspect(runner['pid'])
        actual_child = inspect(child['pid'], executable=self._installation.binary)
        if actual_runner != runner or actual_child != child:
            raise DistributionError('shared MTP2 process identity changed')
        after = lease.stat()
        if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or read_object(private_file(lease / 'lease.json')) != record
                or read_object(private_file(runtime)) != record):
            raise DistributionError('shared MTP2 ownership changed during observation')
        return ProcessBinding(port, startup, self._installation.engine.binary_sha256,
                              _hash(record), before.st_dev, before.st_ino,
                              child['pid'], child['started'], self._owner)

    def matches(self, binding):
        if type(binding) is not ProcessBinding or binding._owner is not self._owner:
            return False
        try:
            current = self.current_owner()
        except (DistributionError, OSError, KeyError, ValueError, TypeError):
            return False
        return current == binding

    def exited(self, binding):
        """Prove this actor's captured child gone and physical ownership released.

        Conservative: even PID reuse, a new global lease, a retained model
        record or an occupied port returns False. Caller must additionally read
        manager inventory and Lily's original instance-lock availability.
        """
        if type(binding) is not ProcessBinding or binding._owner is not self._owner:
            return False
        try:
            if inspect(binding.child_pid) is not None:
                return False
            lease = global_gpu_lease()
            if lease.exists() or (self._installation.data_root / 'run/mtp2-shared-model.json').exists():
                return False
            with socket.socket() as probe:
                probe.settimeout(.2)
                return probe.connect_ex(('127.0.0.1', binding.port)) != 0
        except (DistributionError, OSError, ValueError, TypeError):
            return False
