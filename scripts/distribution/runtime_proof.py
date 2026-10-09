"""Private owner-bound evidence for guard generation and profile switching.

SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations
import asyncio
import errno
import os
from pathlib import Path
import secrets
import socket
import time

from common import DistributionError, no_symlinks, read_object
from ownership import unchanged
from trust import private_file
from optin_engine import OPT64, engine_for, record_fields, native_matches


OWNER_KEYS = ('schema_version', 'installation_id', 'runner', 'child', 'profile_id', 'public_alias',
              'context_tokens', 'backend_port', 'binary_sha256', 'startup_id', 'argv_sha256')


def validation_authorized(trusted, profile_id):
    """A short-lived owner-bound validation record is distinct from production enablement."""
    try:
        record = read_object(private_file(trusted['data_root'] / 'run/context-validation.json'))
        registry_record = trusted['trust']['profile_registry']
        return (
            record.get('schema_version') == 1 and type(record.get('schema_version')) is int
            and record.get('installation_id') == trusted['config']['installation_id']
            and record.get('registry_sha256') == registry_record['sha256']
            and record.get('binary_sha256') == trusted['trust']['binaries']['lily']['sha256']
            and isinstance(record.get('profile_ids'), list)
            and len(record['profile_ids']) <= 2
            and len(set(record['profile_ids'])) == len(record['profile_ids'])
            and all(pid in ('ctx128k', 'ctx262k') for pid in record['profile_ids'])
            and profile_id in record['profile_ids']
            and type(record.get('expires_at')) in (int, float)
            and time.time() < record['expires_at'] <= time.time() + 3600
        )
    except (DistributionError, KeyError, OSError, ValueError, TypeError):
        return False


class RunnerResourceProbe:
    """Prove a newly forwarded native lane released, or exact owned exit.

    The guard forwards exactly one generation. Recording the acquire sequence
    before forwarding therefore binds its next acquire/release to that request,
    without guessing wall-clock offsets or trusting HTTP-reader completion.
    """
    def __init__(self, trusted, *, run_root=None, lease_root=None):
        self.installation_id = trusted['config']['installation_id']
        self.binary_sha256 = trusted['trust']['binaries']['lily']['sha256']
        self.trusted=trusted
        self.binary_sha256s={profile.profile_id:trusted['trust']['binaries']['lily_opt64' if profile.engine_id==OPT64 and OPT64 in trusted.get('engines',{}) else 'lily']['sha256']
                             for profile in trusted['registry'].profiles}
        self.profiles = {profile.profile_id: profile for profile in trusted['registry'].profiles}
        self.run_root = no_symlinks(run_root if run_root is not None else trusted['data_root'] / 'run')
        self.lease = no_symlinks((lease_root if lease_root is not None else
                                  Path.home() / 'Library/Application Support/LiliuxFlow-runtime-leases') / 'gpu.lease')
        self.baseline = None
        self.exit_snapshot = None

    def _owner_keys(self, profile):
        return OWNER_KEYS + (('engine_id','engine_source_commit','engine_source_inventory_sha256') if profile.engine_id==OPT64 else ())

    def _runtime_matches(self, record, *, completed=False):
        if not isinstance(record,dict):return False
        if record.get('engine_id') != OPT64:
            return record.get('native_context_tokens') == record.get('context_tokens')
        counters = ('event_sequence', 'acquired_sequence', 'released_sequence')
        if (any(type(record.get(k)) is not int or record[k] < 0 for k in counters)
            or record['event_sequence'] != record['acquired_sequence'] + record['released_sequence']):
            return False
        queue_fields = ('queue_event_sequence','queue_entered_sequence','queue_terminal_sequence','queue_acquired_sequence')
        if (record.get('queued_request_ids') != []
            or any(type(record.get(k)) is not int or record[k] < 0 for k in queue_fields)
            or record['queue_entered_sequence'] != record['queue_terminal_sequence'] + record['queue_acquired_sequence']
            or record['queue_event_sequence'] != sum(record[k] for k in queue_fields[1:])):
            return False
        # Lane release is independent of optional sparse-prefill coverage.
        # None remains NOT_OBSERVED; a present record keeps the strict gate.
        return native_matches(record, require_dispatch=completed and record.get('qsa_dispatch_metadata') is not None)

    def _state(self):
        path = self.run_root / 'model-state.json'
        if not path.exists():
            return None
        return read_object(private_file(path))

    def _owned(self, record, profile, *, live):
        if not isinstance(record, dict) or record.get('schema_version') != 1:
            return False
        if record.get('installation_id') != self.installation_id or record.get('binary_sha256') != self.binary_sha256s.get(profile.profile_id):
            return False
        if record.get('profile_id') != profile.profile_id or record.get('context_tokens') != profile.context_tokens:
            return False
        if profile.engine_id==OPT64:
            engine=engine_for(self.trusted,profile)
            if any(record.get(k)!=v for k,v in record_fields(engine).items()):return False
            if (type(record.get('schema_version')) is not int or record.get('public_alias')!=profile.public_alias
                or not isinstance(record.get('argv_sha256'),str) or len(record['argv_sha256'])!=64
                or any(c not in '0123456789abcdef' for c in record['argv_sha256'])
                or not isinstance(record.get('startup_id'),str) or len(record['startup_id'])!=32
                or any(c not in '0123456789abcdef' for c in record['startup_id'])):return False
        runner, child = record.get('runner'), record.get('child')
        if not isinstance(runner, dict) or not isinstance(child, dict):
            return False
        if runner.get('uid') != os.getuid() or child.get('uid') != os.getuid() or child.get('ppid') != runner.get('pid'):
            return False
        port = record.get('backend_port')
        if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(record.get('startup_id'), str):
            return False
        if live:
            if not unchanged(runner) or not unchanged(child):
                return False
            lease = read_object(private_file(self.lease / 'lease.json'))
            for key in ('installation_id', 'runner', 'child', 'profile_id', 'context_tokens', 'backend_port', 'binary_sha256', 'startup_id'):
                if lease.get(key) != record.get(key):
                    return False
            if profile.engine_id==OPT64 and any(lease.get(k)!=record.get(k) for k in self._owner_keys(profile)):return False
        return True

    @staticmethod
    def _port_free(port):
        with socket.socket() as sock:
            sock.settimeout(.2)
            return sock.connect_ex(('127.0.0.1', port)) == errno.ECONNREFUSED

    def _gone(self, record):
        return (not unchanged(record['child']) and not unchanged(record['runner'])
                and self._port_free(record['backend_port']) and not self.lease.exists())

    def _recover_closed(self, profile):
        """Reap only an exact exited-owner lease; preserve native event counters."""
        state = self._state()
        previous = self.profiles.get(state.get('profile_id')) if state is not None else None
        # A failed switch can leave the preceding profile's closed lease. The
        # record's trusted profile binds release; the caller's new target does
        # not establish ownership of that preceding native process.
        if previous is None or not self._owned(state, previous, live=False):
            return False
        if type(state.get('schema_version')) is not int or state.get('public_alias') != previous.public_alias:
            return False
        if unchanged(state['runner']) or unchanged(state['child']) or not self._port_free(state['backend_port']):
            return False
        if not self.lease.exists():
            return self._gone(state)
        record = read_object(private_file(self.lease / 'lease.json'))
        if any(record.get(key) != state.get(key) for key in self._owner_keys(previous)):
            return False
        directory_stat = self.lease.stat()
        if directory_stat.st_uid != os.getuid() or directory_stat.st_mode & 0o077:
            return False
        if {path.name for path in self.lease.iterdir()} != {'lease.json'}:
            return False
        # The closed runner cannot legitimately rewrite this lease. Recheck
        # its bytes, state and inode immediately before the atomic rename.
        if self._state() != state or read_object(private_file(self.lease / 'lease.json')) != record:
            return False
        now = self.lease.stat()
        if (now.st_dev, now.st_ino) != (directory_stat.st_dev, directory_stat.st_ino):
            return False
        if unchanged(state['runner']) or unchanged(state['child']) or not self._port_free(state['backend_port']):
            return False
        quarantine = self.lease.with_name('gpu.lease.recovered-' + secrets.token_hex(8))
        os.rename(self.lease, quarantine)
        moved = quarantine.stat()
        matched = ((moved.st_dev, moved.st_ino) == (directory_stat.st_dev, directory_stat.st_ino)
                   and read_object(private_file(quarantine / 'lease.json')) == record)
        if not matched:
            # Never remove an unexpected record. Restore it when the original
            # path is still free; otherwise retain it for operator inspection.
            if not self.lease.exists():
                os.rename(quarantine, self.lease)
            return False
        (quarantine / 'lease.json').unlink()
        quarantine.rmdir()
        self.exit_snapshot = state
        return self._gone(state)

    def _probe(self, profile, phase):
        try:
            state = self._state()
            if phase == 'before_forward':
                if state is None:
                    self.baseline = None
                    return not self.lease.exists()
                previous = self.profiles.get(state.get('profile_id'))
                if (previous is not None and self._owned(state, previous, live=False)
                    and (self._gone(state) or self._recover_closed(profile))):
                    self.baseline = None
                    return True
                if not self._owned(state, profile, live=True) or state.get('proof_valid') is not True:
                    return False
                if state.get('native_context_tokens') != profile.context_tokens:
                    return False
                if not self._runtime_matches(state):return False
                if state.get('active_lane_ids') != [] or state.get('acquired_sequence') != state.get('released_sequence'):
                    return False
                self.baseline = state
                return True
            if phase == 'pre_admission_rejected':
                before = self.baseline
                if (before is None or state is None or any(key not in before or key not in state for key in self._owner_keys(profile))
                    or type(before.get('schema_version')) is not int or type(state.get('schema_version')) is not int
                    or not self._owned(before, profile, live=True) or not self._owned(state, profile, live=True)
                    or any(state.get(key) != before.get(key) for key in self._owner_keys(profile))):
                    return False
                if any(record.get('proof_valid') is not True or record.get('stderr_closed') is not False
                       or record.get('child_exited') is not False
                       or record.get('native_context_tokens') != profile.context_tokens
                       or record.get('active_lane_ids') != [] for record in (before, state)):
                    return False
                if not self._runtime_matches(before) or not self._runtime_matches(state):return False
                fields = ('event_sequence', 'acquired_sequence', 'released_sequence')
                if any(type(record.get(key)) is not int or record[key] < 0
                       for record in (before, state) for key in fields):
                    return False
                return (all(state[key] == before[key] for key in fields)
                        and state['acquired_sequence'] == state['released_sequence']
                        and state['event_sequence'] == state['acquired_sequence'] + state['released_sequence']
                        and state.get('last_released_request_id') == before.get('last_released_request_id')
                        and state.get('last_prefill_progress') == before.get('last_prefill_progress'))
            if phase == 'generation_complete':
                if profile.engine_id==OPT64:
                    if (not self._runtime_matches(state,completed=True)
                        or state.get('last_released_request_id')!=state.get('last_acquired_request_id')
                        or (state.get('last_release_cancelled') is True and
                            (state.get('cancelled_session_dropped') is not True or
                             state.get('last_cancelled_request_id')!=state.get('last_released_request_id')))):return False
                if not self._owned(state, profile, live=True) or state.get('proof_valid') is not True:
                    return False
                before = self.baseline
                if before is not None and before.get('startup_id') != state['startup_id']:
                    return False
                count = 0 if before is None else before['acquired_sequence']
                return (type(state.get('acquired_sequence')) is int
                        and state['acquired_sequence'] == count + 1
                        and state.get('released_sequence') == state['acquired_sequence']
                        and state.get('active_lane_ids') == []
                        and isinstance(state.get('last_released_request_id'), str)
                        and state.get('stderr_closed') is False)
            if phase == 'generation_cancelled':
                before = self.baseline
                if (profile.engine_id!=OPT64 or before is None or state is None
                    or not self._owned(before,profile,live=True) or not self._owned(state,profile,live=True)
                    or any(before.get(key)!=state.get(key) for key in self._owner_keys(profile))
                    or not self._runtime_matches(before) or not self._runtime_matches(state,completed=True)
                    or any(record.get('stderr_closed') is not False or record.get('child_exited') is not False
                           or record.get('active_lane_ids')!=[] or record.get('queued_request_ids')!=[]
                           for record in (before,state))):
                    return False
                request_id = state.get('last_acquired_request_id')
                if (not isinstance(request_id,str) or request_id in
                    (before.get('last_acquired_request_id'),before.get('last_released_request_id'))
                    or state.get('last_released_request_id')!=request_id
                    or state.get('last_cancelled_request_id')!=request_id
                    or state.get('last_release_cancelled') is not True
                    or state.get('cancelled_session_dropped') is not True):
                    return False
                increments = {'acquired_sequence':1,'released_sequence':1,'event_sequence':2,
                              'queue_entered_sequence':1,'queue_acquired_sequence':1,
                              'queue_terminal_sequence':0,'queue_event_sequence':2}
                return all(type(before.get(key)) is int and type(state.get(key)) is int
                           and state[key]==before[key]+value for key,value in increments.items())
            if phase == 'before_unload':
                if state is None:
                    self.exit_snapshot = None
                    return not self.lease.exists()
                previous = self.profiles.get(state.get('profile_id'))
                if (previous is not None and self._owned(state, previous, live=False)
                    and (self._gone(state) or self._recover_closed(profile))):
                    self.exit_snapshot = state
                    return True
                if not self._owned(state, profile, live=False):
                    return False
                if not self._gone(state) and not self._owned(state, profile, live=True):
                    return False
                self.exit_snapshot = state
                return True
            if phase == 'unloaded':
                if self.exit_snapshot is not None:
                    before = self.exit_snapshot
                    if self._gone(before):
                        return True
                    if (state is not None and all(state.get(key) == before.get(key) for key in self._owner_keys(profile))
                        and self._recover_closed(profile)):
                        return self._gone(before)
                    return False
                # Startup may publish the private record while Stop is running.
                # Capture it before allowing another runner to acquire the lease.
                if state is not None:
                    previous = self.profiles.get(state.get('profile_id'))
                    if previous is None or not self._owned(state, previous, live=False):
                        return False
                    self.exit_snapshot = state
                    return self._gone(state) or self._recover_closed(profile)
                return not self.lease.exists()
            if phase == 'recover_closed':
                return self._recover_closed(profile)
            return False
        except (DistributionError, OSError, KeyError, TypeError, ValueError):
            return False

    async def __call__(self, profile, phase):
        return await asyncio.to_thread(self._probe, profile, phase)


def portable_guard_kwargs(trusted, *, validation_profile_ids=()):
    """Construct serving hooks only from the already verified installation."""
    if (not isinstance(validation_profile_ids, tuple) or len(validation_profile_ids) > 2
        or any(not isinstance(pid, str) for pid in validation_profile_ids)
        or len(set(validation_profile_ids)) != len(validation_profile_ids)):
        raise DistributionError('validation factory profiles must be an explicit unique tuple')
    if trusted.get('model_configured') is False:
        if validation_profile_ids:
            raise DistributionError('no-model installation cannot authorize a validation profile')
        from dataclasses import replace
        registry = replace(trusted['registry'], profiles=tuple(replace(p, production_enabled=False, validation_only=False)
                                                              for p in trusted['registry'].profiles))
        return {'registry': registry, 'resource_probe': RunnerResourceProbe(trusted), 'validation_aliases': ()}
    aliases = []
    for profile_id in validation_profile_ids:
        profile = trusted['registry'].by_id(profile_id, allow_validation=True)
        if profile.production_enabled or not validation_authorized(trusted, profile_id):
            raise DistributionError('validation factory lacks its scoped candidate authorization')
        aliases.append(profile.public_alias)
    return {'registry': trusted['registry'], 'resource_probe': RunnerResourceProbe(trusted),
            'validation_aliases': tuple(aliases)}
