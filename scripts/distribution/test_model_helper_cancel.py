# SPDX-License-Identifier: Apache-2.0
"""Owned stdlib child cancellation and credential boundaries; no HTTP, ports or weights."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DistributionError
import liliuxflow as cli
import ownership

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
SENSITIVE_FIXTURE = 'synthetic-private-value-never-printed'


class ModelHelperCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='owned model helper CPU 測試-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.wrapper = None
        self.identities = []

    def tearDown(self):
        if self.wrapper is not None and self.wrapper.poll() is None:
            self.wrapper.terminate()
            try:self.wrapper.wait(timeout=7)
            except subprocess.TimeoutExpired:self.wrapper.kill();self.wrapper.wait(timeout=2)
        for identity in reversed(self.identities):
            if ownership.unchanged(identity):
                os.kill(identity['pid'], signal.SIGKILL)

    def scripts(self, *, ignore_sdk_term=False):
        sdk = self.folder / 'sdk fixture.py'
        sdk.write_text('''import json,os,signal,sys,time
from pathlib import Path
folder=Path(sys.argv[1])
def cancelled(_number,_frame):
    print(json.dumps({'state':'cancelled'}),flush=True)
    raise SystemExit(130)
signal.signal(signal.SIGTERM,signal.SIG_IGN if sys.argv[2]=='ignore' else cancelled)
signal.signal(signal.SIGINT,cancelled)
(folder/'owned-partial.json').write_text(json.dumps({'payload_verified':False}))
(folder/'sdk.pid').write_text(str(os.getpid()))
print(os.environ['PRIVATE_TEST_TOKEN'],file=sys.stderr,flush=True)
time.sleep(30)
(folder/'published.json').write_text(json.dumps({'payload_verified':True}))
''')
        launcher = self.folder / 'uv fixture.py'
        launcher.write_text('''import os,signal,subprocess,sys
from pathlib import Path
folder=Path(sys.argv[1])
child=subprocess.Popen([sys.executable,str(folder/'sdk fixture.py'),str(folder),sys.argv[2]])
(folder/'uv.pid').write_text(str(os.getpid()))
raise SystemExit(child.wait())
''')
        wrapper = self.folder / 'lf wrapper fixture.py'
        wrapper.write_text('''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import liliuxflow as cli
folder=Path(sys.argv[2]);ignore=sys.argv[3]
def operation(_args):
    result=cli.run_model_helper([sys.executable,str(folder/'uv fixture.py'),str(folder),ignore],dict(os.environ),cancellation_grace_seconds=0.25)
    cli.emit(json.loads(result.stdout));return result.returncode
cli.models=operation
sys.argv=['lf','models','install','qwen3.8-flash-next-lily-q4','--output',str(folder/'model'),'--execute']
raise SystemExit(cli.main())
''')
        env = dict(os.environ, PRIVATE_TEST_TOKEN=SENSITIVE_FIXTURE)
        self.wrapper = subprocess.Popen([sys.executable, str(wrapper), str(SCRIPT_DIRECTORY), str(self.folder), 'ignore' if ignore_sdk_term else 'normal'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (self.folder / 'sdk.pid').exists() and (self.folder / 'uv.pid').exists():
                for name in ('uv.pid', 'sdk.pid'):
                    self.identities.append(ownership.capture(int((self.folder / name).read_text())))
                return
            if self.wrapper.poll() is not None:
                output = self.wrapper.communicate()
                self.fail('fixture wrapper exited before its owned child was ready: ' + str(output))
            time.sleep(0.02)
        self.fail('bounded fixture startup timed out')

    def interrupt(self, number, *, ignore_sdk_term=False):
        self.scripts(ignore_sdk_term=ignore_sdk_term)
        started = time.monotonic()
        # Signal only the wrapper PID. Its separate child session cannot receive
        # this signal through a terminal group; forwarding must actually work.
        os.kill(self.wrapper.pid, number)
        stdout, stderr = self.wrapper.communicate(timeout=8)
        self.assertEqual(self.wrapper.returncode, 130)
        result = json.loads(stdout)
        self.assertEqual(result['state'], 'interrupted');self.assertIn('partial', result['error'])
        self.assertNotIn('Traceback', stdout + stderr);self.assertNotIn(SENSITIVE_FIXTURE, stdout + stderr)
        self.assertTrue((self.folder / 'owned-partial.json').is_file());self.assertFalse((self.folder / 'published.json').exists())
        self.assertTrue(all(not ownership.unchanged(identity) for identity in self.identities))
        return time.monotonic() - started

    def test_sigterm_to_wrapper_stops_exact_sdk_subtree_and_preserves_unrelated_child(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
        try:
            self.interrupt(signal.SIGTERM)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate();unrelated.wait(timeout=2)

    def test_sigint_to_wrapper_is_sanitized_130_and_keeps_partial_unpublished(self):
        self.interrupt(signal.SIGINT)

    def test_ignored_sdk_sigterm_escalates_only_owned_pid_within_bound(self):
        elapsed = self.interrupt(signal.SIGTERM, ignore_sdk_term=True)
        self.assertLess(elapsed, 7)

    def test_normal_helper_restores_handlers_and_returns_result(self):
        before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        result = cli.run_model_helper([sys.executable, '-c', 'print("{\\"state\\":\\"fixture\\"}")'], dict(os.environ))
        self.assertEqual(result.returncode, 0);self.assertEqual(json.loads(result.stdout), {'state': 'fixture'})
        self.assertEqual(before, {number: signal.getsignal(number) for number in before})

    def test_noninstall_keyboard_interrupt_has_no_model_resume_claim(self):
        with patch.object(sys, 'argv', ['lf', 'status']), patch.object(cli, 'status', side_effect=KeyboardInterrupt()), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(), 130)
        self.assertEqual(json.loads(output.getvalue()), {'state': 'interrupted', 'error': 'operation cancelled'})

    def test_other_application_credentials_and_invalid_names_refuse_before_read_or_spawn(self):
        blocked = ('LITELLM_MASTER_KEY', 'UI_PASSWORD', 'DB_PASSWORD', 'MANAGER_BACKEND_TOKEN',
                   'GUARD_CONTROL_TOKEN', 'RECOVERY_GUARD_BACKEND_TOKEN', 'RECOVERY_DB_PASSWORD',
                   'RECOVERY_LITELLM_MASTER_KEY', 'OPENAI_API_KEY', 'CLOUDFLARE_API_TOKEN',
                   'bad-name', 'HF_TOKEN;echo', 'A' * 129, '')
        for name in blocked:
            with self.subTest(name=name), patch.object(cli, 'run_model_helper', side_effect=AssertionError('blocked name must not spawn')), patch.object(cli.os.environ, 'get', side_effect=AssertionError('blocked name must not read a credential')):
                args = argparse.Namespace(model_command='install', model_id='qwen3.8-flash-next-lily-q4', token_env=name)
                with self.assertRaises(DistributionError) as error:cli.models(args)
                self.assertNotIn(SENSITIVE_FIXTURE, str(error.exception))
        for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HF_MODEL_TOKEN_CUSTOM'):
            self.assertEqual(cli.model_token_environment_name(name), name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
