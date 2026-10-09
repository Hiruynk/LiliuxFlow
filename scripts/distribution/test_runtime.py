"""Portable serving boundaries on synthetic files/processes. No listeners, PostgreSQL or Q4 load."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import trust
import agent
import ownership
import build_native
from common import DistributionError
from test_distribution import SOURCE_ROOT

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='liliuxflow-runtime 測試 space-')
        self.base=Path(self.tmp.name).resolve();self.source=self.base/'source 原生';self.data=self.base/'data 私有';self.model=self.base/'model fixture'
        self.data.mkdir(mode=0o700);self.model.mkdir();self.source.mkdir()
        for directory in ('runtime','runtime/bin','secrets','run','cache','logs','backups'):(self.data/directory).mkdir(mode=0o700)
        for directory in ('scripts/distribution','services/compat/src/compat_api','manifests/distribution','profiles/distribution'):(self.source/directory).mkdir(parents=True,exist_ok=True)
        for name in ('app.py','profile.py','llama_guard.py','portable.py','__init__.py'):
            shutil.copyfile(SOURCE_ROOT/'services/compat/src/compat_api'/name,self.source/'services/compat/src/compat_api'/name)
        for name in ('trust.py','agent.py','model_runner.py','ownership.py','forced_stop.py','common.py','profile_registry.py','runtime_proof.py'):
            shutil.copyfile(SOURCE_ROOT/'scripts/distribution'/name,self.source/'scripts/distribution'/name)
        for name in ('native-sources.json',):shutil.copyfile(SOURCE_ROOT/'manifests/distribution'/name,self.source/'manifests/distribution'/name)
        shutil.copyfile(SOURCE_ROOT/'profiles/distribution/safe64k.json',self.source/'profiles/distribution/safe64k.json')
        (self.model/'config.json').write_text('{}')
        sha=trust.sha256(self.model/'config.json')
        expected={'schema_version':1,'revision':'synthetic-fixture-only','checkpoint_manifest_sha256':'0'*64,'files':[{'path':'config.json','sha256':sha,'size_bytes':2}]}
        (self.source/'manifests/distribution/checkpoint-files.json').write_text(json.dumps(expected))
        self.config={'schema_version':1,'installation_id':'ee50290b-e7a1-4938-a9ee-bb3c358920c8','owner_uid':os.getuid(),'data_root':str(self.data),'source_root':str(self.source),'model_dir':str(self.model),'bind_host':'127.0.0.1','ports':{'litellm':14000,'compat':18001,'guard':18080,'manager':18081,'postgresql':25432},'runtime_state':'BUILT','database_initialized':True,'launchd_label':'com.diurnoctra.liliuxflow.fixture'}
        trust.atomic_private_json(self.data/'install.json',self.config)
        secret={k:'synthetic-only-'+k+'-fixture' for k in ('LITELLM_MASTER_KEY','LITELLM_SALT_KEY','DB_PASSWORD','PG_OWNER_PASSWORD','UI_PASSWORD','MANAGER_BACKEND_TOKEN','GUARD_CONTROL_TOKEN')};secret['UI_USERNAME']='admin';trust.atomic_private_json(self.data/'secrets/bootstrap.json',secret)
        lock=json.loads((self.source/'manifests/distribution/native-sources.json').read_text())
        # Mirror the current builder's source-bound engine helper closure without
        # generating or enabling an opt-in engine in this legacy MTP0 fixture.
        helper_paths=['scripts/distribution/native_recipe.py','scripts/distribution/optin_engine.py',
                      'scripts/distribution/native_metadata.py','scripts/distribution/build_native.py',
                      'manifests/distribution/native-sources.json',
                      'manifests/distribution/lily-opt64-source-baseline.json']
        helper_paths += [row['path'] for row in lock['lily_opt64']['patches']]
        for path in helper_paths:
            target=self.source/path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(SOURCE_ROOT/path,target)
        binaries={}
        for name in ('lily','llama_swap','compat_python','litellm_python'):
            p=self.data/'runtime/bin'/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o700);binaries[name]={'path':str(p.relative_to(self.data)),'sha256':trust.sha256(p)}
        paths=['services/compat/src/compat_api/'+n for n in ('app.py','profile.py','llama_guard.py','portable.py','__init__.py')]+['scripts/distribution/'+n for n in ('trust.py','agent.py','model_runner.py','ownership.py','forced_stop.py','common.py','profile_registry.py','runtime_proof.py')]+helper_paths
        self.record={'schema_version':1,'installation_id':self.config['installation_id'],'source_commit':'cpu-fixture','engine_contract_support':{'schema_version':1},'lily_source_commit':lock['lily']['commit'],'llama_swap_source_commit':lock['llama_swap']['commit'],'lily_patch_sha256':[i['sha256'] for i in lock['lily']['patches']],'profile':{'path':'profiles/distribution/safe64k.json','sha256':trust.sha256(self.source/'profiles/distribution/safe64k.json')},'source_files':[{'path':p,'sha256':trust.sha256(self.source/p)} for p in paths],'binaries':binaries,'build_tools':{}}
        node_root=self.data/'runtime/toolchains/node/bin';node_root.mkdir(parents=True)
        for name in ('node','npm','npm_cli'):
            file=node_root/name;file.write_text('synthetic tool fixture');file.chmod(0o700);self.record['build_tools'][name]={'path':str(file.relative_to(self.data)),'sha256':trust.sha256(file)}
        trust.atomic_private_json(self.data/'runtime/release-trust.json',self.record)
        trust.verify_checkpoint(self.data)
    def tearDown(self):self.tmp.cleanup()
    def test_complete_synthetic_trust_validates_without_live_resources(self):
        result=trust.validate(self.data)
        self.assertEqual(result['profile']['mtp_drafts'],0)
        self.assertEqual(result['checkpoint']['revision'],'synthetic-fixture-only')
    def test_binary_and_serving_source_hash_mutation_refused(self):
        for path in ('runtime/bin/lily',):
            (self.data/path).write_text('changed')
            with self.assertRaises(DistributionError):trust.validate(self.data)
        (self.data/'runtime/bin/lily').write_text('#!/bin/sh\nexit 0\n')
        (self.source/'services/compat/src/compat_api/app.py').write_text('changed')
        with self.assertRaises(DistributionError):trust.validate(self.data)
    def test_profile_cannot_switch_mtp_route_context_or_boolean_type(self):
        file=self.source/'profiles/distribution/safe64k.json';original=file.read_bytes()
        for name,value in (('mtp_drafts',2),('qsa_route','tiled'),('context_tokens',131072),('long128k_enabled',0)):
            obj=json.loads(original);obj[name]=value;file.write_text(json.dumps(obj));self.record['profile']['sha256']=trust.sha256(file);trust.atomic_private_json(self.data/'runtime/release-trust.json',self.record)
            with self.assertRaises(DistributionError):trust.validate(self.data)
        file.write_bytes(original)
    def test_nonloopback_and_duplicate_ports_refused(self):
        for update in ({'bind_host':'0.0.0.0'},{'ports':dict(self.config['ports'],guard=14000)}):
            value={**self.config,**update};trust.atomic_private_json(self.data/'install.json',value)
            with self.assertRaises(DistributionError):trust.validate(self.data)
    def test_checkpoint_metadata_mutation_even_with_restored_timestamp_refused(self):
        file=self.model/'config.json';info=file.stat();file.write_text('[]');os.utime(file,ns=(info.st_atime_ns,info.st_mtime_ns))
        with self.assertRaises(DistributionError):trust.validate(self.data)
    def test_missing_canonical_patch_or_serving_boundary_refused(self):
        self.record['lily_patch_sha256'].pop();trust.atomic_private_json(self.data/'runtime/release-trust.json',self.record)
        with self.assertRaises(DistributionError):trust.validate(self.data)
    def test_private_config_generation_uses_only_own_credentials_and_correct_quoting(self):
        t=trust.validate(self.data);manager,lp=agent.configs(t);m=json.loads(manager.read_text());l=json.loads(lp.read_text())
        self.assertIn('--mtp-drafts',(__import__('model_runner').lily_argv(t,19000)))
        self.assertEqual(m['models'][trust.ALIAS]['ttl'],1800)
        self.assertEqual(l['model_list'][0]['litellm_params']['api_key'],'os.environ/MANAGER_BACKEND_TOKEN')
        self.assertEqual(manager.stat().st_mode&0o777,0o600)
        _,env=agent.safe_environment(t)
        self.assertEqual(env['STORE_MODEL_IN_DB'],'True')
        self.assertTrue(env['PATH'].startswith(str(self.data/'runtime/litellm/bin')+':'))
        self.assertNotIn('LILY_PROFILE',env)
    def test_drain_busy_refuses_before_any_signal_or_bootout(self):
        t=trust.validate(self.data)
        with patch.object(agent,'http_json',return_value=(409,{})),patch.object(agent,'terminate') as signal:
            with self.assertRaises(DistributionError):agent.drain(t)
            signal.assert_not_called()
    def test_http_control_refuses_nonloopback_before_network(self):
        with self.assertRaises(DistributionError):agent.http_json('https://example.invalid/')
    def test_exact_owned_process_stale_identity_is_not_signaled(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            ident=ownership.capture(child.pid,parent=os.getpid())
            changed=dict(ident,started='different-start')
            self.assertTrue(ownership.terminate(changed))
            self.assertIsNone(child.poll())
            self.assertTrue(ownership.terminate(ident,grace=5));child.wait(timeout=5)
        finally:
            if child.poll() is None:child.terminate();child.wait()
    def test_portable_factory_constructs_existing_api_with_verified_identity(self):
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(self.source/'services/compat/src'),'LILIUXFLOW_DATA_ROOT':str(self.data)}
        command=[sys.executable,'-c','from compat_api.portable import create_compat_app; app=create_compat_app(); print(app.title); print(len(app.routes))']
        result=subprocess.run(command,cwd=self.source,capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Local LLM Compat API',result.stdout)
        self.assertGreaterEqual(int(result.stdout.splitlines()[1]),5)
    def test_metadata_receipt_never_grants_model_payload_verification(self):
        trust.verify_metadata(self.data)
        result=trust.validate(self.data,require_checkpoint='metadata')
        self.assertFalse(result['checkpoint']['payload_verified'])
        (self.data/'runtime/checkpoint-verified.json').unlink()
        with self.assertRaises((DistributionError,OSError)):
            trust.validate(self.data)

    def test_runtime_source_copy_is_complete_and_does_not_copy_credentials_or_weights(self):
        destination,records=build_native.install_source_view(self.source,self.data,'cpu-fixture')
        self.assertTrue(records)
        for item in records:
            self.assertEqual(trust.sha256(destination/item['path']),trust.sha256(self.source/item['path']))
        self.assertFalse((destination/'secrets').exists())
        self.assertFalse((destination/'models').exists())
        self.assertEqual(json.loads((destination/'SOURCE_COMMIT.json').read_text())['commit'],'cpu-fixture')
    def test_preinitialization_stop_removes_only_own_validated_job(self):
        trusted=trust.validate(self.data)
        import plistlib
        (self.data/'run/stack.plist').write_bytes(plistlib.dumps(agent.launchd_plist(trusted)))
        (self.data/'run/stack.plist').chmod(0o600)
        responses=[subprocess.CompletedProcess([],0,'state = active\npid = 12000\n',''),subprocess.CompletedProcess([],0,b'',b'')]
        with patch.object(agent.subprocess,'run',side_effect=responses) as run,patch.object(agent,'capture',return_value={'pid':12000}),patch.object(agent,'descendants',return_value=[]),patch.object(ownership,'wait_gone',return_value=True):
            result=agent.stop(self.data)
        self.assertEqual(result['state'],'stopped_before_initialization')
        self.assertEqual(run.call_args_list[1].args[0],['/bin/launchctl','bootout','gui/'+str(os.getuid())+'/'+self.config['launchd_label']])
        self.assertFalse((self.data/'run/stack.plist').exists())
    def test_preinitialization_stop_refuses_unregistered_descendants(self):
        trusted=trust.validate(self.data)
        import plistlib
        (self.data/'run/stack.plist').write_bytes(plistlib.dumps(agent.launchd_plist(trusted)))
        (self.data/'run/stack.plist').chmod(0o600)
        with patch.object(agent.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'pid = 12000\n','')) as run,patch.object(agent,'capture',return_value={'pid':12000}),patch.object(agent,'descendants',return_value=[{'pid':12001}]):
            with self.assertRaises(DistributionError):agent.stop(self.data)
        self.assertEqual(run.call_count,1)
        self.assertTrue((self.data/'run/stack.plist').exists())
    def test_preinitialization_dry_run_loaded_never_boots_out_or_unlinks(self):
        trusted=trust.validate(self.data)
        import plistlib
        file=self.data/'run/stack.plist'
        file.write_bytes(plistlib.dumps(agent.launchd_plist(trusted)));file.chmod(0o600);before=file.read_bytes()
        with patch.object(agent.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'pid = 12000\n','')) as run,patch.object(agent,'capture',return_value={'pid':12000}),patch.object(agent,'descendants',return_value=[]):
            result=agent.stop(self.data,dry_run=True)
        self.assertEqual(result['state'],'validated_stop_plan');self.assertTrue(result['launchd_loaded'])
        self.assertEqual(run.call_count,1);self.assertEqual(file.read_bytes(),before)
    def test_preinitialization_dry_run_unloaded_never_unlinks(self):
        trusted=trust.validate(self.data)
        import plistlib
        file=self.data/'run/stack.plist'
        file.write_bytes(plistlib.dumps(agent.launchd_plist(trusted)));file.chmod(0o600);before=file.read_bytes()
        with patch.object(agent.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','not loaded')) as run:
            result=agent.stop(self.data,dry_run=True)
        self.assertEqual(result['state'],'validated_stop_plan');self.assertFalse(result['launchd_loaded'])
        self.assertEqual(run.call_count,1);self.assertEqual(file.read_bytes(),before)
    def test_unicode_executable_is_verified_without_ps_meta_escaping(self):
        file=self.base/'python 路徑 space'
        shutil.copyfile(Path(sys.executable).resolve(),file);file.chmod(0o700)
        child=subprocess.Popen([str(file),'-I','-S','-c','import time; time.sleep(3)'])
        try:
            ident=ownership.capture(child.pid,parent=os.getpid(),executable=file)
            self.assertTrue(ownership.unchanged(ident))
            self.assertTrue(ownership.terminate(ident,grace=3))
            child.wait(timeout=3)
        finally:
            if child.poll() is None:child.terminate();child.wait()
    def test_build_plan_does_not_download_or_initialize(self):
        shutil.copyfile(SOURCE_ROOT/'manifests/distribution/lily-source-baseline.json',self.source/'manifests/distribution/lily-source-baseline.json')
        allowlist = self.source/'manifests/distribution/source-allowlist.json'
        shutil.copyfile(SOURCE_ROOT/'manifests/distribution/source-allowlist.json', allowlist)
        source_commit = 'a'*40
        (self.source/'SOURCE_COMMIT.json').write_text(json.dumps({
            'schema_version':1,
            'commit':source_commit,
            'allowlist_sha256':trust.sha256(allowlist),
        }))
        with patch.object(build_native,'download') as download:
            plan=build_native.build(self.data)
            self.assertEqual(plan['source_commit'],source_commit)
            self.assertFalse(plan['live_services_started']);download.assert_not_called()
    def test_mutation_lease_busy_does_not_overwrite_holder(self):
        with agent.mutation(self.data):
            original=(self.data/'run/host-mutation.lease/lease.json').read_bytes()
            with self.assertRaises(DistributionError):
                with agent.mutation(self.data):pass
            self.assertEqual(original,(self.data/'run/host-mutation.lease/lease.json').read_bytes())
        self.assertFalse((self.data/'run/host-mutation.lease').exists())

if __name__=='__main__':unittest.main(verbosity=2)
