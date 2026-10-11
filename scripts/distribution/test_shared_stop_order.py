# SPDX-License-Identifier: Apache-2.0
"""Shutdown ordering on fake owned registries only; no real service or signals."""
import ast
import hashlib
import json
import pathlib
import plistlib
import signal
import types
import unittest
from unittest.mock import patch

import agent
import forced_stop
from common import DistributionError
import test_force_dispatch as fixture


class SharedStopOrderTests(unittest.TestCase):
    setUp = fixture.ForceDispatchTests.setUp
    exact = fixture.ForceDispatchTests.exact
    send = fixture.ForceDispatchTests.send
    patches = fixture.ForceDispatchTests.patches
    def shared(self, *, registry=True, suffix=True):
        if suffix:
            self.args += ['--mtp2-shared-context']
        self.owner['command_sha256'] = hashlib.sha256(' '.join(self.args).encode()).hexdigest()
        if registry:
            self.record['shared_context'] = True
        self.state.write_text(json.dumps(self.record))
        decoded = plistlib.loads(self.plist.read_bytes())
        decoded['ProgramArguments'] = self.args
        self.plist.write_bytes(plistlib.dumps(decoded))

    def order(self, *, pg=False, fail=None, drift=False):
        events = []
        owned_pg = {**self.owner, 'pid': 12002, 'ppid': 1, 'pgid': 12002, 'command_sha256': 'c'*64}
        pgcontent = b'owned fixture bytes'
        if pg:
            self.live.add(12002)
            pgdata = self.data/'postgres/data'
            pgdata.mkdir(parents=True)
            pidfile = pgdata/'postmaster.pid'
            pidfile.write_bytes(pgcontent)
            pidfile.chmod(0o600)

        def terminate(values, **kwargs):
            stage = 'owner' if values == [self.owner] else 'descendants'
            events.append(stage)
            if stage == 'descendants' and self.record.get('shared_context') is True and self.args[4:] == ['--mtp2-shared-context']:
                self.assertIn(self.owner['pid'], self.live)
                self.assertIn((self.owner['pid'], signal.SIGSTOP), self.signals)
                self.assertNotIn((self.owner['pid'], signal.SIGCONT), self.signals)
                self.assertNotIn('bootout', events)
            if fail == stage:
                raise DistributionError('fixture teardown failed')
            self.live.difference_update(value['pid'] for value in values)
            if drift and stage == 'descendants':
                self.live.discard(self.owner['pid'])

        def fast(argv, **kwargs):
            events.append('pg_fast')
            self.assertIn('fast', argv)
            if fail == 'pg_fast':
                raise DistributionError('fixture PostgreSQL refused')
            self.live.discard(12002)

        def bootout(*args, **kwargs):
            events.append('bootout')
            # A launchd removal can exit/reparent processes; shared descendants
            # must have exited before this independently controlled action.
            if self.record.get('shared_context') is True and self.args[4:] == ['--mtp2-shared-context']:
                self.assertNotIn(self.child['pid'], self.live)
                if pg:
                    self.assertNotIn(12002, self.live)
                self.assertIn(self.owner['pid'], self.live)
            return types.SimpleNamespace(wait=lambda timeout: events.append('wait'), returncode=0)

        stack = self.patches()
        stack.enter_context(patch.object(forced_stop, 'shared_exit_observer', side_effect=lambda identities,diagnostic: forced_stop.exited))
        stack.enter_context(patch.object(forced_stop, 'descendants', side_effect=lambda pid: [] if pg and pid == 12002 else [self.child]))
        stack.enter_context(patch.object(forced_stop, 'terminate_all', side_effect=terminate))
        stack.enter_context(patch.object(agent.subprocess, 'Popen', side_effect=bootout))
        stack.enter_context(patch.object(agent, '_force_pg_identity', return_value=(owned_pg, pgcontent) if pg else (None, None)))
        stack.enter_context(patch.object(agent, 'postgres_tools', return_value=self.base/'pg'))
        stack.enter_context(patch.object(agent, 'pg_environment', return_value={}))
        stack.enter_context(patch.object(agent, 'private_run', side_effect=fast))
        # No path through this test can signal a real process.
        stack.enter_context(patch.object(forced_stop.os, 'kill', side_effect=AssertionError('real signal forbidden')))
        return stack, events

    def test_shared_unloaded_service_orders_descendants_pg_bootout_owner(self):
        self.shared()
        stack, events = self.order(pg=True)
        with stack:
            result = agent._force_stop(self.trusted)
        self.assertEqual(events, ['descendants', 'pg_fast', 'bootout', 'owner', 'wait'])
        self.assertTrue(result['postgres_fast_stop'])
        self.assertFalse(self.state.exists())

    def test_shared_without_database_orders_descendants_bootout_owner(self):
        self.shared()
        stack, events = self.order()
        with stack:
            agent._force_stop(self.trusted)
        self.assertEqual(events, ['descendants', 'bootout', 'owner', 'wait'])

    def test_original_nonshared_order_is_preserved(self):
        stack, events = self.order(pg=True)
        with stack:
            agent._force_stop(self.trusted)
        self.assertEqual(events, ['bootout', 'descendants', 'pg_fast', 'owner', 'wait'])

    def test_shared_selection_needs_both_registry_and_exact_suffix(self):
        for registry, suffix in ((False, True), (True, False)):
            with self.subTest(registry=registry, suffix=suffix):
                self.setUp()
                self.shared(registry=registry, suffix=suffix)
                stack, events = self.order()
                with stack:
                    agent._force_stop(self.trusted)
                self.assertEqual(events, ['bootout', 'descendants', 'owner', 'wait'])

    def test_descendant_or_pg_failure_retains_records_and_never_boots_out(self):
        for stage in ('descendants', 'pg_fast'):
            with self.subTest(stage=stage):
                self.setUp()
                self.shared()
                stack, events = self.order(pg=True, fail=stage)
                with stack, self.assertRaises(DistributionError):
                    agent._force_stop(self.trusted)
                self.assertNotIn('bootout', events)
                self.assertNotIn('owner', events)
                self.assertTrue(self.state.exists())
                self.assertTrue(self.plist.exists())
                self.assertIn((self.owner['pid'], signal.SIGCONT), self.signals)

    def test_changed_frozen_owner_refuses_before_bootout(self):
        self.shared()
        stack, events = self.order(drift=True)
        with stack, self.assertRaisesRegex(DistributionError, 'shared stop frozen agent identity/command differs'):
            agent._force_stop(self.trusted)
        self.assertEqual(events, ['descendants'])
        self.assertTrue(self.state.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
