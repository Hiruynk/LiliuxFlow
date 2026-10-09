# SPDX-License-Identifier: Apache-2.0
"""CPU-only retry/refreeze tests using product APIs, dict identities, and a temp install."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
from pathlib import Path
import signal
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "scripts/distribution"))

import agent
import forced_stop
from common import DistributionError


STARTED = "Fri Oct 9 08:00:00 2026"


def process_identity(pid: int, ppid: int, *, started: str = STARTED, suffix: str = "a") -> dict:
    return {
        "pid": pid,
        "ppid": ppid,
        "pgid": pid,
        "uid": os.getuid(),
        "started": started,
        "command_sha256": suffix * 64,
    }


def kernel_row(
    pid: int,
    ppid: int,
    *,
    uid: int = 0,
    ruid: int | None = None,
    stat: str = "S",
    ucomm: str = "ps",
    started: str = STARTED,
) -> dict:
    return {
        "pid": pid,
        "ppid": ppid,
        "uid": uid,
        "ruid": os.getuid() if ruid is None else ruid,
        "stat": stat,
        "ucomm": ucomm,
        "started": started,
    }


class TypedReaderRetryTests(unittest.TestCase):
    def test_only_two_matching_live_suid_ps_rows_raise_retry_type(self):
        reader = kernel_row(101, 100)
        with (
            patch.object(forced_stop, "_descendant_rows", side_effect=[[reader], [reader]]),
            patch.object(forced_stop, "_suid_ps_identity", return_value=True),
            patch.object(forced_stop.time, "monotonic", side_effect=[0.0, 0.6]),
        ):
            with self.assertRaises(forced_stop.KnownLivePsPending):
                forced_stop.descendants(100)

    def test_foreign_uid_and_changed_ps_identity_are_not_retryable(self):
        foreign = kernel_row(101, 100, uid=0, ruid=os.getuid(), ucomm="worker")
        with (
            patch.object(forced_stop, "_descendant_rows", return_value=[foreign]),
            patch.object(forced_stop, "_suid_ps_identity", return_value=True),
        ):
            with self.assertRaises(DistributionError) as caught:
                forced_stop.descendants(100)
            self.assertNotIsInstance(caught.exception, forced_stop.KnownLivePsPending)

        original = kernel_row(101, 100)
        changed_rows = (
            {**original, "started": "reused PID birth"},
            {**original, "ppid": 999},
            {**original, "ruid": os.getuid() + 1},
            {**original, "ucomm": "python"},
        )
        for changed in changed_rows:
            with self.subTest(changed=changed):
                with (
                    patch.object(forced_stop, "_descendant_rows", side_effect=[[original], [changed]]),
                    patch.object(forced_stop, "_suid_ps_identity", return_value=True),
                    patch.object(forced_stop.time, "monotonic", side_effect=[0.0, 0.6]),
                ):
                    with self.assertRaises(DistributionError) as caught:
                        forced_stop.descendants(100)
                    self.assertNotIsInstance(caught.exception, forced_stop.KnownLivePsPending)


class ProductRefreezeTests(unittest.TestCase):
    def test_retry_refreezes_late_fork_and_never_terminates_children(self):
        owner = process_identity(100, 1)
        reader = process_identity(101, 100, suffix="b")
        late = process_identity(102, 100, suffix="c")
        by_pid = {item["pid"]: item for item in (owner, reader, late)}
        calls = []
        signals = []

        def capture(parent):
            self.assertEqual(parent, owner["pid"])
            calls.append(parent)
            index = len(calls)
            if index == 1:
                return [reader]
            if index == 2:
                raise forced_stop.KnownLivePsPending("CPU fixture: stable live ps")
            if index == 3:  # Bounded rescan after the typed retry.
                return [reader]
            return [reader, late]

        def signaler(identity, signum):
            signals.append((identity["pid"], signum))
            return True

        captured, frozen = [], []
        with patch.object(forced_stop, "inspect", side_effect=lambda pid: by_pid.get(pid)):
            forced_stop.freeze_tree_for_stop(
                owner,
                captured,
                frozen,
                lambda rows: rows,
                capture=capture,
                signaler=signaler,
                clock=lambda: 0.0,
            )

        self.assertEqual(len(calls), 5)
        self.assertEqual({item["pid"] for item in captured}, {reader["pid"], late["pid"]})
        self.assertIn((owner["pid"], signal.SIGCONT), signals)
        self.assertIn((reader["pid"], signal.SIGCONT), signals)
        self.assertIn((late["pid"], signal.SIGSTOP), signals)
        self.assertFalse(any(sig in (signal.SIGTERM, signal.SIGKILL) for _, sig in signals))

    def test_pid_reuse_of_paused_reader_refuses_without_resume_or_termination(self):
        owner = process_identity(100, 1)
        reader = process_identity(101, 100, suffix="b")
        reused = process_identity(101, 100, started="reused PID birth", suffix="d")
        calls, signals = [], []

        def capture(_parent):
            calls.append(True)
            if len(calls) == 1:
                return [reader]
            raise forced_stop.KnownLivePsPending("CPU fixture: stable live ps")

        def signaler(identity, signum):
            signals.append((identity["pid"], signum))
            return True

        with patch.object(forced_stop, "inspect", side_effect=lambda pid: reused if pid == reader["pid"] else owner):
            with self.assertRaises(DistributionError):
                forced_stop.freeze_tree_for_stop(
                    owner,
                    [],
                    [],
                    lambda rows: rows,
                    capture=capture,
                    signaler=signaler,
                    clock=lambda: 0.0,
                )

        self.assertNotIn((reader["pid"], signal.SIGCONT), signals)
        self.assertFalse(any(sig in (signal.SIGTERM, signal.SIGKILL) for _, sig in signals))

    def test_agent_filter_excludes_postgres_family_during_reader_retry_and_late_fork(self):
        with tempfile.TemporaryDirectory(prefix="reader-freeze-retry-") as temp:
            local_root = Path(temp).resolve()
            data_root = local_root / "data"
            source_root = local_root / "source"
            run_dir = data_root / "run"
            run_dir.mkdir(parents=True, mode=0o700)
            source_root.mkdir(mode=0o700)

            installation_id = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
            launchd_label = "com.diurnoctra.liliuxflow." + installation_id
            python = local_root / "python"
            arguments = [
                str(python),
                str(source_root / "scripts/distribution/agent.py"),
                "--data-root",
                str(data_root),
            ]
            owner = process_identity(
                100,
                1,
                suffix=hashlib.sha256(" ".join(arguments).encode()).hexdigest()[0],
            )
            pg = process_identity(200, owner["pid"], suffix="e")
            reader = process_identity(101, owner["pid"], suffix="b")
            late = process_identity(102, owner["pid"], suffix="c")
            pg_worker = process_identity(201, pg["pid"], suffix="f")
            pg_leaf = process_identity(202, pg_worker["pid"], suffix="1")
            pg_late = process_identity(203, pg_leaf["pid"], suffix="2")

            record = {
                "schema_version": 1,
                "installation_id": installation_id,
                "agent": owner,
                "children": {},
                "postgres": {"pid": pg["pid"]},
            }
            state_path = run_dir / "agent.json"
            state_path.write_text(json.dumps(record), encoding="utf-8")
            state_path.chmod(0o600)
            plist_path = run_dir / "stack.plist"
            plist_path.write_bytes(plistlib.dumps({
                "Label": launchd_label,
                "WorkingDirectory": str(source_root),
                "ProgramArguments": arguments,
            }))
            plist_path.chmod(0o600)

            trusted = {
                "config": {
                    "owner_uid": os.getuid(),
                    "installation_id": installation_id,
                    "launchd_label": launchd_label,
                },
                "data_root": data_root,
                "source_root": source_root,
                "binaries": {"compat_python": python},
            }
            tree_before_fork = [reader, pg, pg_worker, pg_leaf, pg_late]
            tree_after_fork = [reader, late, pg, pg_worker, pg_leaf, pg_late]
            capture_calls = []
            signals = []
            observed = {}

            def capture(parent):
                self.assertEqual(parent, owner["pid"])
                capture_calls.append(parent)
                index = len(capture_calls)
                if index == 2:
                    raise forced_stop.KnownLivePsPending("CPU fixture: stable live ps")
                return tree_before_fork if index < 4 else tree_after_fork

            def signaler(identity, signum):
                signals.append((identity["pid"], signum))
                return True

            original_freeze = forced_stop.freeze_tree_for_stop

            def stop_after_freeze(owner_value, captured, frozen, filter_tree):
                original_freeze(
                    owner_value,
                    captured,
                    frozen,
                    filter_tree,
                    capture=capture,
                    signaler=signaler,
                    clock=lambda: 0.0,
                )
                observed["captured"] = {value["pid"] for value in captured}
                observed["frozen"] = {value["pid"] for value in frozen}
                raise RuntimeError("CPU fixture stopped after freeze to avoid termination")

            current_by_pid = {x["pid"]: x for x in (owner, reader, late)}
            launchctl = SimpleNamespace(returncode=0, stdout=f"pid = {owner['pid']}\n", stderr="")
            with (
                patch.object(forced_stop, "exact", return_value=True),
                patch.object(forced_stop, "command", return_value=" ".join(arguments)),
                patch.object(forced_stop, "inspect", side_effect=lambda pid: current_by_pid.get(pid)),
                patch.object(forced_stop, "descendants", return_value=[pg_worker, pg_leaf]),
                patch.object(forced_stop, "send", side_effect=signaler),
                patch.object(forced_stop, "freeze_tree_for_stop", side_effect=stop_after_freeze),
                patch.object(agent, "_force_pg_identity", return_value=(pg, b"local synthetic pg identity")),
                patch.object(agent.subprocess, "run", return_value=launchctl),
            ):
                with self.assertRaisesRegex(RuntimeError, "stopped after freeze"):
                    agent._force_stop(trusted)

            self.assertEqual(observed["captured"], {reader["pid"], late["pid"]})
            self.assertEqual(observed["frozen"], {owner["pid"], reader["pid"], late["pid"]})
            postgres_family = {pg["pid"], pg_worker["pid"], pg_leaf["pid"], pg_late["pid"]}
            self.assertFalse(observed["captured"] & postgres_family)
            self.assertFalse(any(pid in postgres_family for pid, _ in signals))
            self.assertIn((late["pid"], signal.SIGSTOP), signals)
            self.assertFalse(any(sig in (signal.SIGTERM, signal.SIGKILL) for _, sig in signals))


if __name__ == "__main__":
    unittest.main(verbosity=2)
