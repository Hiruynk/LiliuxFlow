#!/usr/bin/env python3
"""Pure-memory control-plane guard reproducers; no process, network or disk mutation."""
import unittest
import ast
import hashlib
import json
import os
from pathlib import Path
import plistlib
import time
import types


class DistributionError(Exception):
    pass


class MemoryPath:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def __truediv__(self, child):
        return MemoryPath(self.name + '/' + str(child), self.store)

    def exists(self):
        return self.name in self.store

    def read_bytes(self):
        return self.store[self.name]

    def unlink(self, missing_ok=False):
        if not missing_ok and self.name not in self.store:
            raise FileNotFoundError(self.name)
        self.store.pop(self.name, None)

    def rmdir(self):
        self.store.pop(self.name, None)


class FakeCommands:
    def __init__(self, final_unknown=False):
        self.calls = []
        self.lsof_calls = 0
        self.final_unknown = final_unknown

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == '/usr/sbin/lsof':
            self.lsof_calls += 1
            return types.SimpleNamespace(returncode=2 if self.final_unknown and self.lsof_calls > 5 else 1)
        return types.SimpleNamespace(returncode=0)


def reproduce(source_file):
    source = source_file.read_text()
    tree = ast.parse(source)

    def globals_for(store, command):
        return {'DistributionError': DistributionError, 'read_object': lambda p: json.loads(p.read_bytes()),
                'private_file': lambda p: p, 'no_symlinks': lambda p: p, 'plistlib': plistlib, 'os': os,
                'subprocess': command, 'time': time, 'json': json,
                'write_json_new': lambda p, v: store.update({p.name: json.dumps(v).encode()}),
                'print': lambda *_args, **_kwargs: None}

    def function(name, scope):
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_file), 'exec'), scope)
        return scope[name]

    store = {'data/run/agent.json': json.dumps({'installation_id': 'TEST', 'agent': {'pid': 100}, 'postgres': None, 'children': {}}).encode(),
             'data/run/stack.plist': plistlib.dumps({'Label': 'TEST_SCOPE_ONLY', 'ProgramArguments': ['--controlplane-only']})}
    commands = FakeCommands()
    scope = globals_for(store, commands)
    scope.update(unchanged=lambda ident: True, terminate=lambda ident, **kw: False,
                 agent=types.SimpleNamespace(pg_stop=lambda *a: None))
    data = MemoryPath('data', store)
    trusted = {'data_root': data, 'config': {'installation_id': 'TEST', 'launchd_label': 'TEST_SCOPE_ONLY'}}
    try:
        result = function('emergency_startup_cleanup', scope)(trusted)
        refused = False
    except DistributionError as error:
        result = {'error': str(error)}
        refused = True
    term_case = {'test': 'owned_agent_term_returns_false', 'result': result, 'owned_agent_still_unchanged': True,
                 'registry_retained': (data / 'run/agent.json').exists(), 'plist_retained': (data / 'run/stack.plist').exists(),
                 'guard_pass': refused and (data / 'run/agent.json').exists() and (data / 'run/stack.plist').exists()}

    def exercise(mutate_caller=False, unknown_final_lsof=False):
        store = {'data/secrets/bootstrap.json': b'{"id":"TEST"}', 'data/secrets/caller.json': b'{"api_key":"SYNTHETIC_A"}'}
        data = MemoryPath('data', store)
        commands = FakeCommands(unknown_final_lsof)
        trusted = {'data_root': data, 'config': {'installation_id': 'TEST', 'launchd_label': 'TEST_SCOPE_ONLY',
                   'ports': {'litellm': 14000, 'compat': 18001, 'guard': 18080, 'manager': 18081, 'postgresql': 25432}},
                   'secrets': {'LITELLM_MASTER_KEY': 'SYNTHETIC_MASTER', 'MANAGER_BACKEND_TOKEN': 'SYNTHETIC_MANAGER'}}
        registry = {'agent': {'pid': 100}, 'postgres': {'pid': 101}, 'children': {'manager': {'pid': 102}}}

        def start(*args, **kwargs):
            store['data/run/agent.json'] = json.dumps(registry).encode()
            return {'state': 'started'}

        def stop(*args, **kwargs):
            store.pop('data/run/agent.json', None)
            if mutate_caller:
                store['data/secrets/caller.json'] = b'{"api_key":"SYNTHETIC_CHANGED"}' if store['data/secrets/caller.json'].endswith(b'A"}') else b'{"api_key":"SYNTHETIC_A"}'
            return {'state': 'stopped'}

        agent = types.SimpleNamespace(initialize=lambda *a, **k: {'state': 'initialized'}, start=start, stop=stop,
                                      http_json=lambda url, **k: (200, {'running': []} if '/running' in url else {}))
        scope = globals_for(store, commands)
        scope.update(validate=lambda *a, **k: trusted, unchanged=lambda ident: False, agent=agent,
                     backup=lambda *a: {'state': 'backup'}, emergency_startup_cleanup=lambda *a: {'state': 'not_needed'})
        evidence = data / 'evidence.json'
        exit_code = function('check', scope)(data, evidence)
        report = json.loads(evidence.read_bytes())
        return {'test': 'caller_changed_after_stop' if mutate_caller else 'final_lsof_unknown_exit2',
                'exit': exit_code, 'status': report['status'],
                'preservation_checks': [item.get('caller_preserved') for item in report['operations'] if item['command'] == 'ownership readback'],
                'remaining_listeners': report['remaining_listeners'],
                'final_lsof_returncodes': [2] * 5 if unknown_final_lsof else [1] * 5,
                'guard_pass': exit_code != 0 and report['status'] != 'PASS'}

    results = [term_case, exercise(mutate_caller=True), exercise(unknown_final_lsof=True)]
    summary = {'state': 'GUARDS_PASS_CPU_ONLY' if all(item['guard_pass'] for item in results) else 'CONCRETE_GUARD_BUGS_REPRODUCED',
               'source': str(source_file), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'tests': results,
               'real_process_actions': 0, 'real_network_actions': 0, 'real_file_mutations': 0,
               'model_or_gpu_actions': 0, 'source_modified': False, 'native_Q4_acceptance_claim': False}
    return summary


class ControlplaneGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = Path(os.environ.get('LILIUXFLOW_GUARD_TEST_SOURCE', str(Path(__file__).with_name('controlplane_check.py'))))
        cls.summary = reproduce(selected)
        print(json.dumps(cls.summary, ensure_ascii=False))

    def test_failed_owned_agent_termination_retains_ownership(self):
        self.assertTrue(self.summary['tests'][0]['guard_pass'], self.summary['tests'][0])

    def test_changed_caller_preservation_readback_fails(self):
        self.assertTrue(self.summary['tests'][1]['guard_pass'], self.summary['tests'][1])

    def test_unknown_final_listener_readback_fails(self):
        self.assertTrue(self.summary['tests'][2]['guard_pass'], self.summary['tests'][2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
