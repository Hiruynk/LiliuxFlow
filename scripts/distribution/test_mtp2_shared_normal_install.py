# SPDX-License-Identifier: Apache-2.0
"""Portable CPU installation boundaries, using synthetic files only.

No compiler, archive, UI build, network, service, database or real model payload
is required. The existing runtime fixture has a two-byte synthetic config file;
its inert binaries/credentials are test data and never authorize a real install.
"""
from __future__ import annotations
import argparse,ast,copy,hashlib,json,os,shutil,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts/distribution'))
import agent,build_native as build,liliuxflow as cli,profile_registry,trust,ui_recipe
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
        view,rows=build.install_source_view(self.source,self.data('plain'),'a'*40)
        paths={r['path'] for r in rows}
        self.assertNotIn('services/litellm/uv.lock',paths)
        self.assertFalse((view/'patches/ui/cpu/litellm/Fixture.tsx').exists())
        self.assertFalse((view/'secrets.env').exists())


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
        expected=shared.BOUNDED_OUTPUT_EXPECTATION
        engine={'engine_id':shared.ENGINE_ID,'source_commit':shared.LATEST_COMMIT,'archive_sha256':shared.ARCHIVE_SHA256,
            'patch_sha256':[r['sha256'] for r in expected.patches],'source_inventory_sha256':expected.source_inventory_sha256,
            'source_file_count':expected.source_file_count,'default':False,'binary_sha256':trust.sha256(binary)}
        f.record['mtp2_shared_runtime']={'schema_version':1,'engine':engine,'binary':{'path':shared.BINARY_PATH,'sha256':trust.sha256(binary)},'enabled_profiles':list(shared.PROFILE_IDS),'runtime_policy':shared.runtime_policy()}
        f.record['source_files']=[{'path':name,'sha256':trust.sha256(f.source/name)} for name in sorted({r['path'] for r in f.record['source_files']}|serving_inputs(True))]
        self.save();trust.verify_checkpoint(f.data)
        self.release=patch.object(runner,'RELEASE_EXPECTATION',expected);self.release.start();self.addCleanup(self.release.stop)
    def save(self):trust.atomic_private_json(self.f.data/'runtime/release-trust.json',self.f.record)
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

if __name__=='__main__':unittest.main()
