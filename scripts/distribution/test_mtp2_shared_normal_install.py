# SPDX-License-Identifier: Apache-2.0
"""Portable CPU installation boundaries, using synthetic files only.

No compiler, archive, UI build, network, service, database or real model payload
is required. The existing runtime fixture has a two-byte synthetic config file;
its inert binaries/credentials are test data and never authorize a real install.
"""
from __future__ import annotations
import argparse,ast,contextlib,copy,hashlib,json,os,plistlib,shutil,signal,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts/distribution'))
import agent,build_native as build,forced_stop as fs,liliuxflow as cli,notices,profile_registry,trust,ui_recipe
import mtp2_shared_runtime as shared,mtp2_shared_runner as runner
from mtp2_shared_recipe import serving_inputs
from common import DistributionError
import test_runtime as runtime_fixture


def put(root,name,value):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_bytes(value if type(value) is bytes else value.encode());path.chmod(0o600)
    return path


def obj(root,name,value):return put(root,name,json.dumps(value,indent=2)+'\n')


def fixture_source(root):
    """Small declared source graph, never executed as Python or UI code."""
    source=root/'source';source.mkdir(mode=0o700)
    for name in serving_inputs(True)|{'scripts/distribution/package.py'}:put(source,name,'# inert CPU source fixture\n')
    for name in ('LICENSE','NOTICE','docs/productization/licenses/lily/NOTICE',
                 'manifests/distribution/original-notices.json'):
        put(source,name,(ROOT/name).read_bytes())
    pins={'litellm':{'commit':'a'*40,'archive_sha256':'b'*64},'llama_swap':{'commit':'c'*40,'archive_sha256':'d'*64}}
    obj(source,'manifests/distribution/native-sources.json',pins)
    extras=[];apps={}
    for name,prefix in ui_recipe.UI_ROOTS.items():
        rel='patches/ui/cpu/'+name+'/Fixture.tsx';file=put(source,rel,'export const synthetic = true;\n');extras.append(rel)
        pin=pins['litellm' if name=='litellm' else 'llama_swap']
        apps[name]={'upstream_commit':pin['commit'],'archive_sha256':pin['archive_sha256'],
            'source_tree_sha256':'e'*64,'patches':[],
            'extra_files':[{'source':rel,'target':prefix+'/src/Fixture.tsx','sha256':trust.sha256(file),'before_sha256':None}],
            'lockfiles':[{'path':prefix+'/package-lock.json','sha256':'f'*64}]}
    obj(source,build.SHARED_UI_MANIFEST,{'schema_version':1,'apps':apps})
    obj(source,'manifests/distribution/source-allowlist.json',{'exact_paths':extras,'prefixes':[]})
    for name in ('secrets.env','private/raw.json'):put(source,name,'INERT_CPU_MARKER_NOT_A_CREDENTIAL\n')
    return source


class UIAndSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='normal-shared-cpu-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.source=fixture_source(self.root)
    def data(self,name):
        path=self.root/name;path.mkdir(mode=0o700);return path
    def record(self,ui=True):
        paths=set(serving_inputs(True))
        record={'source_commit':'a'*40,'source_files':[{'path':p,'sha256':trust.sha256(self.source/p)} for p in sorted(paths)]}
        if ui:
            _,checked=build.checked_shared_ui_recipe(self.source)
            record=build.append_shared_ui_sources(self.source,record,checked)
        return record
    def test_canonical_UI_recipe_auto_absolute_and_relative_no_build(self):
        path,checked=build.checked_shared_ui_recipe(self.source)
        self.assertEqual(path,self.source/build.SHARED_UI_MANIFEST)
        self.assertEqual(build.checked_shared_ui_recipe(self.source,path),(path,checked))
        self.assertEqual(build.checked_shared_ui_recipe(self.source,Path(build.SHARED_UI_MANIFEST)),(path,checked))
        self.assertFalse((self.source/'.ui-input-preflight').exists())
        self.assertIs(checked['ui_locale_acceptance'],False);self.assertEqual(checked['bundlers']['litellm'],'webpack')
    def test_wrong_explicit_manifest_rejects_before_tool_network_or_work(self):
        with patch.object(build,'installation',return_value={'source_root':str(self.source),'runtime_state':'NOT_BUILT'}), \
             patch.object(build,'resolve_source_commit',return_value='a'*40), \
             patch('native_recipe.select_lily_recipe',return_value=({},{})), \
             patch.object(build,'download') as download,patch.object(build,'command') as compiler, \
             patch.object(build,'private_directory') as work:
            with self.assertRaisesRegex(DistributionError,'canonical UI source recipe'):
                build.build(self.data('wrong'),optin_engine=shared.ENGINE_ID,
                    ui_manifest=Path('manifests/distribution/ui-recipe.json'),execute=True)
            download.assert_not_called();compiler.assert_not_called();work.assert_not_called()
    def test_UI_graph_missing_input_changed_hash_link_and_secret_ref_reject(self):
        _,checked=build.checked_shared_ui_recipe(self.source)
        extra=self.source/'patches/ui/cpu/litellm/Fixture.tsx';original=extra.read_bytes()
        recipe_path=self.source/build.SHARED_UI_MANIFEST;recipe=recipe_path.read_bytes()
        for kind in ('missing','hash','link','secret'):
            try:
                if kind=='missing':extra.unlink()
                elif kind=='hash':extra.write_bytes(original+b'drift')
                elif kind=='link':extra.unlink();extra.symlink_to(put(self.root,'outside.tsx',original))
                else:
                    value=json.loads(recipe);value['apps']['litellm']['extra_files'][0].update(source='secrets.env',sha256=trust.sha256(self.source/'secrets.env'))
                    recipe_path.write_text(json.dumps(value))
                with self.subTest(kind=kind),patch.object(ui_recipe,'replay') as replay,self.assertRaises(DistributionError):build.checked_shared_ui_recipe(self.source)
                replay.assert_not_called()
            finally:
                if extra.is_symlink():extra.unlink()
                extra.write_bytes(original);extra.chmod(0o600);recipe_path.write_bytes(recipe)
    def test_complete_shared_source_view_binds_LP_locks_and_UI_graph(self):
        record=self.record();before=copy.deepcopy(record)
        view,copied=build.install_shared_source_view(self.source,self.data('shared'),record)
        self.assertEqual(record,before)
        required=serving_inputs(True)|{r['path'] for r in record['shared_ui_source']['source_files']}
        self.assertTrue(required.issubset({r['path'] for r in copied}))
        for row in record['source_files']:trust.verified_file(view,row)
        for name in ('services/litellm/pyproject.toml','services/litellm/uv.lock'):
            self.assertEqual((view/name).read_bytes(),(self.source/name).read_bytes())
        for name in ('secrets.env','private/raw.json'):self.assertFalse((view/name).exists())
        self.assertFalse((view/'.ui-input-preflight').exists())
    def test_shared_source_view_copies_real_firstparty_originals_with_hash_bound_verifier(self):
        record=self.record();view,copied=build.install_shared_source_view(self.source,self.data('notices'),record)
        expected={'LICENSE','NOTICE','docs/productization/licenses/lily/NOTICE',
                  'manifests/distribution/original-notices.json','scripts/distribution/notices.py'}
        self.assertTrue(expected.issubset(serving_inputs(True)))
        by_path={row['path']:row for row in copied}
        for name in expected:
            self.assertEqual((view/name).read_bytes(),(self.source/name).read_bytes())
            self.assertEqual(by_path[name]['sha256'],trust.sha256(self.source/name))
        self.assertTrue(notices.verify_first_party(view)['first_party_license_adopted'])
    def test_shared_firstparty_missing_drift_or_symlink_rejects_before_source_target_creation(self):
        for index,kind in enumerate(('missing_license','missing_notice','drift','symlink')):
            record=self.record();data=self.data('notice-bad'+str(index));name='NOTICE' if kind=='missing_notice' else 'LICENSE'
            file=self.source/name;old=file.read_bytes()
            try:
                if kind.startswith('missing'):file.unlink()
                elif kind=='drift':file.write_bytes(old+b'changed')
                else:file.unlink();file.symlink_to(put(self.root,'outside-license',old))
                with self.subTest(kind=kind),patch.object(build,'private_directory') as target,self.assertRaises(DistributionError):
                    build.install_shared_source_view(self.source,data,record)
                target.assert_not_called();self.assertFalse((data/'runtime/source').exists())
            finally:
                if file.is_symlink():file.unlink()
                file.write_bytes(old);file.chmod(0o600)
    def test_shared_corrupted_copied_firstparty_original_rejects_before_provenance_publication(self):
        record=self.record();data=self.data('notice-corrupt');real=shutil.copyfile
        def corrupt(source,target,*args,**kwargs):
            result=real(source,target,*args,**kwargs)
            if Path(target).name=='LICENSE':Path(target).write_bytes(b'inert corrupt original')
            return result
        with patch.object(build.shutil,'copyfile',side_effect=corrupt),self.assertRaises(DistributionError):
            build.install_shared_source_view(self.source,data,record)
        self.assertFalse((data/'runtime/source/SOURCE_COMMIT.json').exists())
    def test_missing_lock_false_boolean_duplicate_hash_or_secret_record_refuse(self):
        original=self.record()
        for index,kind in enumerate(('lock','bool','duplicate','hash','secret')):
            record=copy.deepcopy(original);data=self.data('bad'+str(index))
            if kind=='lock':record['source_files']=[r for r in record['source_files'] if r['path']!='services/litellm/uv.lock']
            elif kind=='duplicate':record['source_files'].append(record['source_files'][0])
            elif kind=='hash':next(r for r in record['source_files'] if r['path']=='services/litellm/uv.lock')['sha256']='0'*64
            elif kind=='secret':record['source_files'].append({'path':'secrets.env','sha256':trust.sha256(self.source/'secrets.env')})
            with self.subTest(kind=kind),self.assertRaises(DistributionError):
                if kind=='bool':build.install_source_view(self.source,data,'a'*40,shared_selected=1,shared_source_files=record['source_files'])
                else:build.install_shared_source_view(self.source,data,record)
            self.assertFalse((data/'install.json').exists());self.assertFalse((data/'runtime/release-trust.json').exists())
    def test_changed_source_UI_graph_or_corrupt_copy_rechecked(self):
        record=self.record();extra=self.source/'patches/ui/cpu/litellm/Fixture.tsx';original=extra.read_bytes()
        extra.write_bytes(original+b'drift')
        with self.assertRaises(DistributionError):build.install_shared_source_view(self.source,self.data('changed'),record)
        extra.write_bytes(original)
        copyfile=shutil.copyfile
        def corrupt(source,target,*args,**kwargs):
            value=copyfile(source,target,*args,**kwargs)
            if str(target).endswith('services/litellm/uv.lock'):Path(target).write_bytes(b'inert corrupted copy')
            return value
        with patch.object(build.shutil,'copyfile',side_effect=corrupt),self.assertRaises(DistributionError):
            build.install_shared_source_view(self.source,self.data('copied'),record)
    def test_default_source_view_never_copies_shared_LP_locks_or_UI(self):
        with patch.object(notices,'verify_first_party',side_effect=AssertionError('unshared must preserve original path')):
            view,rows=build.install_source_view(self.source,self.data('plain'),'a'*40)
        paths={r['path'] for r in rows}
        self.assertNotIn('services/litellm/uv.lock',paths)
        self.assertFalse((view/'patches/ui/cpu/litellm/Fixture.tsx').exists())
        self.assertFalse((view/'secrets.env').exists())
        for name in ('LICENSE','NOTICE','docs/productization/licenses/lily/NOTICE'):self.assertFalse((view/name).exists())


class ParserAndDefaultTests(unittest.TestCase):
    def parse(self,args):
        source=Path(cli.__file__);tree=ast.parse(source.read_text());fn=next(n for n in tree.body if getattr(n,'name',None)=='main')
        nodes=[]
        for node in fn.body:
            nodes.append(node)
            if isinstance(node,ast.Assign) and any(getattr(t,'id',None)=='args' for t in node.targets):break
        nodes.append(ast.Return(value=ast.Name(id='args',ctx=ast.Load())))
        helper=ast.FunctionDef(name='parse_only',args=fn.args,body=nodes,decorator_list=[])
        ns=dict(cli.__dict__);exec(compile(ast.fix_missing_locations(ast.Module(body=[helper],type_ignores=[])),str(source),'exec'),ns)
        with patch.object(sys,'argv',['cli',*args]):return ns['parse_only']()
    def test_actual_explicit_shared_parser_fields_no_handler_dispatch(self):
        value=self.parse(['build','--optin-engine',shared.ENGINE_ID,'--ui-manifest',build.SHARED_UI_MANIFEST])
        self.assertEqual(value.optin_engine,shared.ENGINE_ID);self.assertFalse(value.execute)
        self.assertEqual(value.ui_manifest,Path(build.SHARED_UI_MANIFEST))
        self.assertTrue(self.parse(['start','--mtp2-shared-context']).mtp2_shared_context)
        self.assertFalse(self.parse(['start']).mtp2_shared_context)
        grant=self.parse(['profiles','grant-owner','ctx128k-mtp2','--mtp2-shared-context','--dry-run'])
        self.assertTrue(grant.dry_run);self.assertFalse(grant.execute)
    def test_nonexistent_profile_enable_is_not_documented_authority(self):
        with self.assertRaises(SystemExit):self.parse(['profiles','enable','ctx128k-mtp2'])
    def test_default_factory_delegates_without_shared_pointer_or_import(self):
        path=ROOT/'services/compat/src/compat_api/portable.py';tree=ast.parse(path.read_text())
        fn=next(n for n in tree.body if getattr(n,'name',None)=='_validated')
        ns={'Path':Path,'sys':sys,'os':os,'__file__':str(path)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
        original=__import__
        def imports(name,*args,**kwargs):
            if name.startswith('mtp2_shared'):raise AssertionError('default must not read/select shared capability')
            return original(name,*args,**kwargs)
        with patch.dict(os.environ,{'LILIUXFLOW_DATA_ROOT':str(ROOT/'CPU_NOT_OPENED')},clear=True), \
             patch.object(trust,'validate',return_value={'source_root':ROOT}) as validate, \
             patch('builtins.__import__',side_effect=imports),patch.object(sys,'path',list(sys.path)):
            self.assertEqual(ns['_validated'](),{'source_root':ROOT})
            validate.assert_called_once_with(ROOT/'CPU_NOT_OPENED',require_checkpoint='available')


class InstalledTrustTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing public synthetic fixture; do not import private tests.
        self.f=runtime_fixture.RuntimeTests('test_complete_synthetic_trust_validates_without_live_resources')
        self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f
        for name in serving_inputs(True):
            target=f.source/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target);target.chmod(0o600)
        registry=f.source/profile_registry.REGISTRY_PATH
        f.record['profile_registry']={'path':profile_registry.REGISTRY_PATH,'sha256':trust.sha256(registry)}
        prefix=Path('runtime/litellm/lib/python3.12/site-packages/litellm/proxy')
        module=put(f.data,(prefix/'_liliuxflow_context_profiles.py').as_posix(),(f.source/'scripts/distribution/litellm_profile_policy.py').read_bytes())
        proxy=put(f.data,(prefix/'proxy_server.py').as_posix(),'# LILIUXFLOW_CONTEXT_PROFILE_CATALOG_V1\n'*3)
        f.record['litellm_profile_policy']={'module':{'path':module.relative_to(f.data).as_posix(),'sha256':trust.sha256(module)},
            'proxy':{'path':proxy.relative_to(f.data).as_posix(),'sha256':trust.sha256(proxy),'before_sha256':'8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7'},'catalog_filters':3}
        manifest=json.loads((f.source/'manifests/distribution/checkpoint-files.json').read_bytes())
        manifest['revision']=profile_registry.SHARED['model_revision'];obj(f.source,'manifests/distribution/checkpoint-files.json',manifest)
        binary=put(f.data,shared.BINARY_PATH,b'INERT_CPU_NATIVE_BYTES_NEVER_EXECUTED\n');binary.chmod(0o700)
        expected=shared.candidate_expectation(True)
        engine={'engine_id':shared.ENGINE_ID,'source_commit':shared.LATEST_COMMIT,'archive_sha256':shared.ARCHIVE_SHA256,
            'patch_sha256':[r['sha256'] for r in expected.patches],'source_inventory_sha256':expected.source_inventory_sha256,
            'source_file_count':expected.source_file_count,'default':False,'binary_sha256':trust.sha256(binary)}
        f.record['mtp2_shared_runtime']={'schema_version':1,'engine':engine,'binary':{'path':shared.BINARY_PATH,'sha256':trust.sha256(binary)},'enabled_profiles':list(shared.PROFILE_IDS),'runtime_policy':shared.runtime_policy()}
        f.record['source_files']=[{'path':name,'sha256':trust.sha256(f.source/name)} for name in sorted({r['path'] for r in f.record['source_files']}|serving_inputs(True))]
        self.save();trust.verify_checkpoint(f.data)
        self.release=patch.object(runner,'RELEASE_EXPECTATION',expected);self.release.start();self.addCleanup(self.release.stop)
    def save(self):trust.atomic_private_json(self.f.data/'runtime/release-trust.json',self.f.record)
    def stop_inputs(self):
        import shlex
        base=trust.validate(self.f.data,require_checkpoint=False)
        self.assertIsNone(base['checkpoint'])
        selected=trust.validate(self.f.data,shared_context=True)
        installation=shared.validate_shared_extension(selected,runner.RELEASE_EXPECTATION)
        self.assertIsNotNone(installation)
        port=19000
        def ident(pid,ppid):
            return {'pid':pid,'ppid':ppid,'pgid':pid,'uid':os.getuid(),
                    'started':'CPU_ONLY_BIRTH','command_sha256':'f'*64}
        record={'schema_version':1,'installation_id':base['config']['installation_id'],
            'profile_id':'mtp2-shared','context_tokens':262144,
            'binary_sha256':installation.engine.binary_sha256,'backend_port':port,
            'runner':ident(98201,98200),'child':ident(98202,98201),
            **shared.runtime_identity_fields(installation.engine)}
        commands={record['runner']['pid']:shlex.join([
            str(base['binaries']['compat_python']),
            str(selected['source_root']/'scripts/distribution/mtp2_shared_runner.py'),
            '--data-root',str(selected['data_root']),'--port',str(port)]),
            record['child']['pid']:shlex.join(shared.shared_argv(installation,port)+[
                '--liliuxflow-mtp2-control','--liliuxflow-mtp2-control-key-fd','3'])}
        return base,record,commands
    def test_normal_shared_stop_revalidates_tiny_checkpoint_receipt_without_payload_hash(self):
        base,record,commands=self.stop_inputs();hashed=[];real_hash=trust.sha256
        def bounded_hash(path):
            self.assertNotEqual(Path(path).suffix,'.safetensors','Stop must not hash full weights')
            hashed.append(Path(path).name);return real_hash(path)
        with patch.object(fs,'exact',return_value=True), \
             patch.object(fs,'command',side_effect=lambda ident:commands[ident['pid']]), \
             patch.object(fs.os,'kill',side_effect=AssertionError('classifier must not signal')), \
             patch.object(trust,'sha256',side_effect=bounded_hash), \
             patch.object(trust,'verify_checkpoint',side_effect=AssertionError('stop must not reverify weight payload')) as verify, \
             patch.object(trust,'validate',wraps=trust.validate) as validated:
            self.assertEqual(fs.shared_model_record(base,record),(record['runner'],record['child']))
            validated.assert_called_once_with(base['data_root'],require_checkpoint=True)
            verify.assert_not_called()
        self.assertIsNone(base['checkpoint']);self.assertTrue(hashed)
    def test_normal_shared_stop_refuses_wrong_identity_binding_and_unverified_receipt_before_signal(self):
        base,record,commands=self.stop_inputs()
        for fault in ('uid','binary','argv'):
            wrong=copy.deepcopy(record);actual_commands=dict(commands)
            if fault=='uid':wrong['child']['uid']+=1
            elif fault=='binary':wrong['binary_sha256']='0'*64
            else:actual_commands[wrong['child']['pid']]+=' --unauthorized'
            with self.subTest(fault=fault), \
                 patch.object(fs,'exact',return_value=True) as exact, \
                 patch.object(fs,'command',side_effect=lambda ident:actual_commands[ident['pid']]), \
                 patch.object(fs.os,'kill',side_effect=AssertionError('classifier must not signal')), \
                 self.assertRaises(DistributionError):
                fs.shared_model_record(base,wrong)
            if fault in ('uid','binary'):exact.assert_not_called()
        original_validate=trust.validate
        def changed_binding(*args,**kwargs):
            value=original_validate(*args,**kwargs);changed=copy.copy(value)
            changed['config']=dict(value['config'],owner_uid=value['config']['owner_uid']+1)
            return changed
        with patch.object(trust,'validate',side_effect=changed_binding), \
             patch.object(fs,'exact',side_effect=AssertionError('changed binding must reject before process access')), \
             patch.object(fs,'command',side_effect=AssertionError('changed binding must reject before command inspection')), \
             patch.object(fs.os,'kill',side_effect=AssertionError('classifier must not signal')), \
             self.assertRaisesRegex(DistributionError,'installation changed during revalidation'):
            fs.shared_model_record(base,record)
        receipt=self.f.data/'runtime/checkpoint-verified.json';original=receipt.read_bytes()
        try:
            value=json.loads(original);value['validation']='metadata_only';value['payload_verified']=False
            receipt.write_text(json.dumps(value));receipt.chmod(0o600)
            with patch.object(fs,'exact',side_effect=AssertionError('unverified receipt must reject before process access')), \
                 patch.object(fs,'command',side_effect=AssertionError('unverified receipt must reject before command inspection')), \
                 patch.object(fs.os,'kill',side_effect=AssertionError('classifier must not signal')), \
                 self.assertRaises(DistributionError):
                fs.shared_model_record(base,record)
        finally:
            receipt.write_bytes(original);receipt.chmod(0o600)
    def test_actual_installed_shared_extension_and_source_view_with_tiny_checkpoint(self):
        plain=trust.validate(self.f.data);selected=trust.validate(self.f.data,shared_context=True)
        self.assertEqual(len(selected['registry'].enabled_profiles),6);self.assertEqual(len(plain['registry'].enabled_profiles),3)
        self.assertEqual(selected['registry'].default,plain['registry'].default)
        view,records=build.install_shared_source_view(self.f.source,self.f.data,self.f.record)
        self.f.config['source_root']=str(view);self.f.record['runtime_source_copy']=records
        trust.atomic_private_json(self.f.data/'install.json',self.f.config);self.save()
        self.assertEqual(trust.validate(self.f.data,shared_context=True)['source_root'],view)
    def test_installed_missing_LP_lock_never_authorizes_shared(self):
        path=self.f.source/'services/litellm/uv.lock';path.unlink()
        with self.assertRaises(DistributionError):trust.validate(self.f.data,shared_context=True)
    def test_schema_and_false_boolean_engine_policy_trust_refuse(self):
        original=copy.deepcopy(self.f.record)
        for kind in ('schema','default','policy','profiles'):
            self.f.record=copy.deepcopy(original);ext=self.f.record['mtp2_shared_runtime']
            if kind=='schema':ext['schema_version']=True
            elif kind=='default':ext['engine']['default']=0
            elif kind=='policy':ext['runtime_policy']['mtp_drafts']=True
            else:ext['enabled_profiles']=[shared.PROFILE_IDS[0]]
            self.save()
            with self.subTest(kind=kind),self.assertRaises(DistributionError):trust.validate(self.f.data,shared_context=True)
    def test_default_config_environment_exact_false_branch(self):
        trusted=trust.validate(self.f.data)
        first=agent.configs(trusted);before=[p.read_bytes() for p in first]
        env=agent.safe_environment(trusted)
        with patch.object(agent,'normal_shared_configuration_sha256',side_effect=AssertionError('default must not select shared')):
            second=agent.configs(trusted,shared_context=False);after=[p.read_bytes() for p in second]
            explicit=agent.safe_environment(trusted,shared_context=False)
        self.assertEqual(before,after);self.assertEqual(env,explicit)
        self.assertEqual(env[1]['STORE_MODEL_IN_DB'],'True')
        self.assertNotIn('LILIUXFLOW_MTP2_SHARED_GATEWAY',env[1])
    def test_nonbool_shared_selection_refuses_before_file_open(self):
        for bad in (0,1,'1',None):
            with self.subTest(bad=bad),patch.object(trust,'installation',side_effect=AssertionError('must reject before reads')),self.assertRaises(DistributionError):
                trust.validate(self.f.data,shared_context=bad)

class SharedStopCleanupTests(unittest.TestCase):
    """Normal stop's real trust/classifier/cleanup on inert CPU lifecycle files."""
    def setUp(self):
        self.fixture=InstalledTrustTests('test_actual_installed_shared_extension_and_source_view_with_tiny_checkpoint')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture.f
        f.config['launchd_label']='com.diurnoctra.liliuxflow.'+f.config['installation_id']
        f.config['database_initialized']=False
        trust.atomic_private_json(f.data/'install.json',f.config)
        self.base,self.record,self.commands=self.fixture.stop_inputs()
        for key in ('runner','child'):
            value=self.record[key];value['command_sha256']=hashlib.sha256(self.commands[value['pid']].encode()).hexdigest()
        import shlex
        argv_hash=hashlib.sha256(json.dumps(shlex.split(self.commands[self.record['child']['pid']]),separators=(',',':')).encode()).hexdigest()
        self.record.update(public_alias='liliuxflow-internal-mtp2-shared',startup_id='1'*32,argv_sha256=argv_hash)
        self.captured=[copy.deepcopy(self.record[key]) for key in ('runner','child')]
        self.home=f.data/'CPU-only-home';self.home.mkdir(mode=0o700)
        self.lease=self.home/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease'
        self.model=f.data/'run/mtp2-shared-model.json';self.live={};self.signals=[]
        self.write(self.record)
    def write(self,record):
        agent.private_directory(self.lease)
        trust.atomic_private_json(self.lease/'lease.json',record);trust.atomic_private_json(self.model,record)
    def patches(self):
        stack=contextlib.ExitStack()
        stack.enter_context(patch.object(Path,'home',return_value=self.home))
        stack.enter_context(patch.object(fs,'inspect',side_effect=lambda pid:self.live.get(pid)))
        stack.enter_context(patch.object(fs,'command',side_effect=lambda value:self.commands[value['pid']]))
        stack.enter_context(patch.object(fs,'_descendant_rows',side_effect=lambda:[
            {**value,'ruid':os.getuid(),'stat':'S','ucomm':'CPU-fixture'} for value in self.live.values()]))
        stack.enter_context(patch.object(fs.os,'kill',side_effect=AssertionError('CPU fixture must not signal')))
        return stack
    def cleanup(self,snapshot=None,captured=None):
        return agent._force_clear_model_records(self.base,self.captured if captured is None else captured,fs,
                                               shared_record=self.record if snapshot is None else snapshot)
    def retained(self,before):
        self.assertEqual((self.lease/'lease.json').read_bytes(),before[0])
        self.assertEqual(self.model.read_bytes(),before[1])
        self.assertFalse(list(self.lease.parent.glob('gpu.lease.portable-stopped-*')))
        self.assertFalse(list(self.model.parent.glob('mtp2-shared-model.json.portable-stopped-*')))
    def before(self):return (self.lease/'lease.json').read_bytes(),self.model.read_bytes()
    def test_shared_cleanup_uses_additive_binary_and_removes_exact_ended_pair(self):
        self.assertNotEqual(self.record['binary_sha256'],self.base['trust']['binaries']['lily']['sha256'])
        keep=obj(self.base['data_root'],'run/CPU-unrelated.json',{'preserved':True});before=keep.read_bytes()
        with self.patches(),patch.object(fs,'shared_model_record',wraps=fs.shared_model_record) as classify:
            self.cleanup();classify.assert_called_once_with(self.base,self.record)
        self.assertFalse(self.lease.exists());self.assertFalse(self.model.exists());self.assertEqual(keep.read_bytes(),before)
    def test_shared_cleanup_accepts_already_removed_exact_record_without_touching_other_data(self):
        for missing in ('lease','model','both'):
            with self.subTest(missing=missing):
                self.write(self.record)
                if missing in ('lease','both'):(self.lease/'lease.json').unlink();self.lease.rmdir()
                if missing in ('model','both'):self.model.unlink()
                with self.patches():self.cleanup()
                self.assertFalse(self.lease.exists());self.assertFalse(self.model.exists())
    def test_wrong_captured_source_binary_owner_installation_or_profile_retains_pair(self):
        for field,value in (('installation_id','foreign'),('binary_sha256','0'*64),('engine_source_commit','0'*40),
                            ('engine_source_inventory_sha256','0'*64),('profile_id','ctx64k'),('context_tokens',65536),
                            ('child.uid',os.getuid()+1),('child.ppid',99999)):
            with self.subTest(field=field):
                wrong=copy.deepcopy(self.record)
                if '.' in field:a,b=field.split('.');wrong[a][b]=value
                else:wrong[field]=value
                self.write(wrong);before=self.before()
                with self.patches(),self.assertRaises(DistributionError):self.cleanup(snapshot=wrong)
                self.retained(before)
    def test_changed_or_foreign_lease_or_model_after_capture_retains_pair(self):
        for target in ('lease','model'):
            for field,value in (('installation_id','foreign'),('startup_id','3'*32),('argv_sha256','4'*64),
                                ('binary_sha256','0'*64),('engine_source_commit','0'*40),('profile_id','ctx64k')):
                with self.subTest(target=target,field=field):
                    self.write(self.record);changed={**self.record,field:value}
                    trust.atomic_private_json(self.lease/'lease.json' if target=='lease' else self.model,changed)
                    before=self.before()
                    with self.patches(),self.assertRaises(DistributionError):self.cleanup()
                    self.retained(before)
    def test_missing_or_changed_captured_runner_child_identity_retains_pair(self):
        for target in ('runner','child'):
            for fault in ('missing','started','command_sha256','uid'):
                with self.subTest(target=target,fault=fault):
                    captured=copy.deepcopy(self.captured);index=0 if target=='runner' else 1
                    if fault=='missing':captured.pop(index)
                    elif fault=='uid':captured[index][fault]+=1
                    else:captured[index][fault]='0'*64
                    before=self.before()
                    with self.patches(),self.assertRaises(DistributionError):self.cleanup(captured=captured)
                    self.retained(before)
    def test_live_reused_or_changed_process_never_authorizes_shared_cleanup(self):
        for target in ('runner','child'):
            for fault in ('live','started','uid','command_sha256'):
                with self.subTest(target=target,fault=fault):
                    current=copy.deepcopy(self.record[target]);self.live={current['pid']:current}
                    if fault=='uid':current[fault]+=1
                    elif fault!='live':current[fault]='0'*64
                    before=self.before()
                    with self.patches(),self.assertRaises(DistributionError):self.cleanup()
                    self.retained(before)
        self.live={}
    def test_revoked_checkpoint_receipt_or_changed_installed_source_binary_retains_pair(self):
        f=self.fixture.f
        for target in (f.data/'runtime/checkpoint-verified.json',f.source/'scripts/distribution/agent.py',
                       f.data/shared.BINARY_PATH):
            with self.subTest(target=target.name):
                original=target.read_bytes();before=self.before()
                try:
                    if target.name=='checkpoint-verified.json':
                        value=json.loads(original);value['validation']='metadata_only';value['payload_verified']=False
                        target.write_text(json.dumps(value))
                    else:target.write_bytes(original+b'CPU-only corruption\n')
                    with self.patches(),self.assertRaises(DistributionError):self.cleanup()
                    self.retained(before)
                finally:target.write_bytes(original)
    def test_changed_record_during_quarantine_restores_owned_inodes_without_deleting(self):
        original=self.before();lease_stamp=self.lease.stat().st_ino;model_stamp=self.model.stat().st_ino
        rename=os.rename
        def drift(source,target):
            rename(source,target)
            if Path(source)==self.model:
                trust.atomic_private_json(Path(target),{**self.record,'startup_id':'4'*32})
        with self.patches(),patch.object(agent.os,'rename',side_effect=drift),self.assertRaises(DistributionError):self.cleanup()
        # atomic_private_json replaced the model inode; unknown quarantine stays
        # retained for review. The untouched exact lease inode is restored.
        self.assertEqual(self.lease.stat().st_ino,lease_stamp)
        self.assertEqual((self.lease/'lease.json').read_bytes(),original[0]);self.assertFalse(self.model.exists())
        retained=list(self.model.parent.glob('mtp2-shared-model.json.portable-stopped-*'))
        self.assertEqual(len(retained),1);self.assertNotEqual(retained[0].stat().st_ino,model_stamp)
        self.assertEqual(json.loads(retained[0].read_text())['startup_id'],'4'*32)
    def test_mtp0_cleanup_keeps_original_binary_path_and_never_calls_shared_classifier(self):
        self.model.unlink();record={**self.record,'profile_id':'ctx64k',
                                  'binary_sha256':self.base['trust']['binaries']['lily']['sha256']}
        trust.atomic_private_json(self.lease/'lease.json',record)
        obj(self.base['data_root'],'run/model.json',record)
        with self.patches(),patch.object(fs,'shared_model_record',side_effect=AssertionError('MTP0 must not select shared')):
            agent._force_clear_model_records(self.base,self.captured,fs)
        self.assertFalse(self.lease.exists());self.assertFalse((self.base['data_root']/'run/model.json').exists())
    def test_normal_source_generator_copies_stop_fix_and_binds_installed_hash(self):
        f=self.fixture.f;name='scripts/distribution/agent.py'
        view,copied=build.install_shared_source_view(f.source,f.data,f.record)
        expected=trust.sha256(ROOT/name)
        self.assertEqual((view/name).read_bytes(),(ROOT/name).read_bytes())
        self.assertEqual(next(row['sha256'] for row in copied if row['path']==name),expected)
        self.assertEqual(next(row['sha256'] for row in f.record['source_files'] if row['path']==name),expected)
        f.config['source_root']=str(view);f.record['runtime_source_copy']=copied
        trust.atomic_private_json(f.data/'install.json',f.config);self.fixture.save()
        selected=trust.validate(f.data,shared_context=True)
        self.assertEqual(selected['source_root'],view)
        self.assertEqual(selected['trust']['binaries']['lily'],self.base['trust']['binaries']['lily'])
        self.assertEqual(selected['trust']['mtp2_shared_runtime'],self.base['trust']['mtp2_shared_runtime'])
    def normal_stop(self,*,change_after_exit=False,wrong_argv=False):
        cfg=self.base['config'];data=self.base['data_root'];source=self.base['source_root']
        args=[str(self.base['binaries']['compat_python']),str(source/'scripts/distribution/agent.py'),
              '--data-root',str(data),'--mtp2-shared-context']
        owner={**self.record['runner'],'pid':self.record['runner']['ppid'],'ppid':1,
               'command_sha256':hashlib.sha256(' '.join(args).encode()).hexdigest()}
        self.commands[owner['pid']]=' '.join(args)
        if wrong_argv:
            child=self.record['child'];self.commands[child['pid']]+=' --unauthorized'
            child['command_sha256']=hashlib.sha256(self.commands[child['pid']].encode()).hexdigest();self.write(self.record)
        self.live={value['pid']:copy.deepcopy(value) for value in [owner,self.record['runner'],self.record['child']]}
        state=obj(data,'run/agent.json',{'schema_version':1,'installation_id':cfg['installation_id'],
                                     'agent':owner,'children':{'runner':self.record['runner']},'postgres':None})
        plist=put(data,'run/stack.plist',plistlib.dumps({'Label':cfg['launchd_label'],
                   'WorkingDirectory':str(source),'ProgramArguments':args}))
        def terminate(values,**kwargs):
            for value in values:self.live.pop(value['pid'],None)
            if change_after_exit:trust.atomic_private_json(self.model,{**self.record,'startup_id':'5'*32})
        probe=SimpleNamespace(_state=lambda:None,_probe=lambda *_:True)
        with self.patches(),patch.object(agent,'validate',return_value=self.base),patch.object(agent,'unchanged',return_value=True), \
             patch.object(agent,'drain'),patch.object(agent,'guard_admission_status',return_value={
                 'admission_paused':True,'pause_reason':'session_maintenance','active_inferences':0,'pending_inferences':0}), \
             patch('runtime_proof.RunnerResourceProbe',return_value=probe),patch.object(agent,'_force_pg_identity',return_value=(None,None)), \
             patch.object(fs,'descendants',side_effect=lambda _:list(self.live.values())[1:]), \
             patch.object(fs,'send',side_effect=lambda value,number:self.signals.append((value['pid'],number)) or True), \
             patch.object(fs,'terminate_all',side_effect=terminate) as ended, \
             patch.object(agent.subprocess,'Popen',return_value=SimpleNamespace(wait=lambda timeout:0,returncode=0)) as bootout, \
             patch.object(agent.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'pid = '+str(owner['pid'])+'\n',''),
                                                             subprocess.CompletedProcess([],1,'','Could not find service')]), \
             patch.object(fs,'shared_model_record',wraps=fs.shared_model_record) as classify:
            if change_after_exit or wrong_argv:
                with self.assertRaises(DistributionError):agent.stop(data)
                self.assertTrue(state.exists());self.assertTrue(plist.exists());self.assertTrue(self.lease.exists());self.assertTrue(self.model.exists())
                if wrong_argv:bootout.assert_not_called();ended.assert_not_called()
            else:
                result=agent.stop(data);self.assertTrue(result['normal_idle_drain']);self.assertFalse(result['force'])
                self.assertFalse(state.exists());self.assertFalse(plist.exists());self.assertFalse(self.lease.exists());self.assertFalse(self.model.exists())
                self.assertEqual(classify.call_count,2)
    def test_real_normal_stop_dispatch_carries_authenticated_snapshot_through_cleanup(self):self.normal_stop()
    def test_normal_stop_changed_record_after_teardown_retains_lifecycle_for_review(self):self.normal_stop(change_after_exit=True)
    def test_normal_stop_wrong_native_argv_rejects_before_bootout_or_termination(self):self.normal_stop(wrong_argv=True)

if __name__=='__main__':unittest.main()
