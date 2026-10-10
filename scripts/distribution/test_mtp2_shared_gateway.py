# SPDX-License-Identifier: Apache-2.0
"""Product ASGI/config/guard integration CPU checks; synthetic native HTTP only."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/distribution'))
sys.path.insert(0, str(ROOT / 'services/compat/src'))
sys.dont_write_bytecode = True
import httpx
import agent
import mtp2_shared_gateway as gateway
import mtp2_shared_ingress as ingress
import mtp2_shared_transport as protocol
import litellm_profile_policy as policy
import profile_registry
from common import DistributionError
from compat_api.llama_guard import create_app, _ContextLifecycleGuard
from test_mtp2_shared_ingress import AuthFixture

SECRET = 'synthetic-product-backend-credential-0123456789'
EPOCH = 'a' * 64


def registry():
    return profile_registry.parse_registry(json.loads((ROOT / profile_registry.REGISTRY_PATH).read_bytes()), enabled_optin_profiles=protocol.PROFILE_IDS)


class RawIngressTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, raw, *, headers=(), frames=None):
        received, sent = [], []
        async def inner(scope, receive, send):
            while True:
                part = await receive()
                received.append(part)
                if not part.get('more_body', False):
                    break
            received.append(gateway._raw_context.get())
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        app = gateway.RawMtp2Ingress(inner, registry(), capture=True, owner=object())
        queued = list(frames or [{'type':'http.request','body':raw,'more_body':False}])
        async def receive():
            return queued.pop(0) if queued else {'type':'http.disconnect'}
        async def send(message):
            sent.append(message)
        await app({'type':'http','method':'POST','path':'/v1/chat/completions','headers':headers},receive,send)
        return received, sent

    async def test_MTP0_bytes_frames_are_replayed_without_capture(self):
        raw = b'{ "model":"qwen3.8-flash-next-lily-q4-64k", "messages":[] }'
        frames=[{'type':'http.request','body':raw[:20],'more_body':True},{'type':'http.request','body':raw[20:],'more_body':False}]
        received,sent = await self.invoke(raw,frames=frames)
        self.assertEqual(received[:-1], frames)
        self.assertIsNone(received[-1])
        self.assertEqual(sent[0]['status'],200)

    async def test_raw_duplicate_model_rejects_before_downstream(self):
        raw = ('{"model":"qwen3.8-flash-next-lily-q4-64k","model":"'+protocol.PUBLIC_ALIASES[0]+'","messages":[]}').encode()
        received,sent = await self.invoke(raw)
        self.assertEqual(received,[])
        self.assertEqual(sent[0]['status'],400)

    async def test_internal_header_and_privatephysical_reject_before_auth(self):
        raw=json.dumps({'model':protocol.PUBLIC_ALIASES[0],'messages':[]}).encode()
        received,sent=await self.invoke(raw,headers=((b'X-Liliuxflow-Control-Mac',b'fake'),))
        self.assertEqual((received,sent[0]['status']),([],400))
        received,sent=await self.invoke(json.dumps({'model':'liliuxflow-internal-mtp2-shared'}).encode())
        self.assertEqual((received,sent[0]['status']),([],400))

    async def test_early_disconnect_never_creates_raw_capability_context(self):
        raw=json.dumps({'model':protocol.PUBLIC_ALIASES[0],'messages':[]}).encode()
        received,_sent=await self.invoke(raw,frames=[{'type':'http.request','body':raw,'more_body':True},{'type':'http.disconnect'}])
        self.assertIsNone(received[-1])

    async def test_MTP2_provider_route_overrides_reject_but_payload_data_and_MTP0_keep_same_keys(self):
        for key in ('api_base','api_key','base_url','litellm_credentials_name','litellm_credential_name'):
            for container in (None,'options','extra_body'):
                value={'model':protocol.PUBLIC_ALIASES[0],'messages':[]}
                if container is None:value[key]='synthetic-rejected'
                else:value[container]={key:'synthetic-rejected'}
                received,sent=await self.invoke(json.dumps(value).encode())
                self.assertEqual((received,sent[0]['status']),([],400))
        value={'model':protocol.PUBLIC_ALIASES[0],'messages':[{'role':'user','content':{'api_base':'payload data'}}],
               'tools':[{'type':'function','function':{'name':'synthetic','parameters':{'type':'object','properties':{'api_key':{'type':'string'},'base_url':{'type':'string'}}}}}]}
        received,sent=await self.invoke(json.dumps(value).encode())
        self.assertEqual(sent[0]['status'],200);self.assertIsNotNone(received[-1])
        value={'model':'qwen3.8-flash-next-lily-q4-64k','api_base':'synthetic-existing-MTP0-value','messages':[]}
        received,sent=await self.invoke(json.dumps(value).encode())
        self.assertEqual(sent[0]['status'],200);self.assertIsNone(received[-1])

    async def test_giant_single_frame_rejects_without_unbounded_join(self):
        received,sent=await self.invoke(b'x'*(8*1024**2+1))
        self.assertEqual((received,sent[0]['status']),([],413))


class ConfigTests(unittest.TestCase):
    def trusted(self, data):
        return {'data_root':data,'source_root':ROOT,'registry':registry(),
                'config':{'ports':{'guard':18080,'manager':18081,'litellm':14000,'compat':18001,'postgresql':25432}},
                'secrets':{'MANAGER_BACKEND_TOKEN':SECRET},'binaries':{'compat_python':Path(sys.executable)}}

    def test_explicit_OFF_config_exact_equivalence_and_no_shared_validation_import(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder).resolve();(data/'run').mkdir()
            trusted=self.trusted(data)
            first=agent.configs(trusted)
            before=tuple(path.read_bytes() for path in first)
            with patch.object(gateway,'require_shared_installation',side_effect=AssertionError('OFF should not validate shared')):
                second=agent.configs(trusted,shared_context=False)
            self.assertEqual(before,tuple(path.read_bytes() for path in second))

    def test_ON_source_config_onephysical_aliases2callbacks_without_MTP0_change(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder).resolve();(data/'run').mkdir();trusted=self.trusted(data)
            manager,lp=agent.configs(trusted);before=json.loads(manager.read_bytes());base_lp=json.loads(lp.read_bytes())
            with patch.object(gateway,'require_shared_installation',return_value=object()):
                manager,lp=agent.configs(trusted,shared_context=True)
            after=json.loads(manager.read_bytes());new_lp=json.loads(lp.read_bytes())
            self.assertEqual(after['models'][gateway.PHYSICAL_MODEL_ID]['concurrencyLimit'],2)
            self.assertEqual(after['models'][gateway.PHYSICAL_MODEL_ID]['ttl'],0)
            self.assertEqual(after['globalTTL'],before['globalTTL'])
            for alias in (row[1] for row in profile_registry.TARGETS):
                self.assertEqual(before['models'][alias],after['models'][alias])
            self.assertEqual(base_lp['model_list'],new_lp['model_list'])
            self.assertIn('mtp2_shared_gateway.shared_callback',new_lp['litellm_settings']['callbacks'])

    def test_missing_frozen_runner_expectation_fails_before_config_write(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder).resolve();(data/'run').mkdir();trusted=self.trusted(data)
            import mtp2_shared_runner as runner
            with patch.object(runner,'RELEASE_EXPECTATION',None),self.assertRaises(DistributionError):
                agent.configs(trusted,shared_context=True)
            self.assertEqual(list((data/'run').iterdir()),[])

    def test_OFF_portable_factories_do_not_import_missing_shared_module(self):
        from compat_api import portable
        import os
        trusted=self.trusted(Path('/synthetic-owned-data'))
        trusted.update(checkpoint=None,profile=trusted['registry'].default.as_dict(),model_configured=False)
        trusted['secrets']['GUARD_CONTROL_TOKEN']='synthetic-control'
        async def proof(profile,phase):return True
        with patch.dict(os.environ,{},clear=False):
            os.environ.pop(gateway.FEATURE_ENV,None)
            with patch.object(portable,'_validated',return_value=trusted),patch('runtime_proof.portable_guard_kwargs',return_value={'registry':trusted['registry'],'resource_probe':proof}),patch.dict(sys.modules,{'mtp2_shared_gateway':None}):
                guard=portable.create_guard_app()
                compat=portable.create_compat_app()
        self.assertIs(type(guard.state.lifecycle_guard),_ContextLifecycleGuard)
        self.assertTrue(hasattr(compat,'router'))

    def test_invalid_feature_flag_fails_portable_before_shared_import(self):
        from compat_api import portable
        trusted=self.trusted(Path('/synthetic-owned-data'));trusted.update(model_configured=False)
        async def proof(profile,phase):return True
        with patch.dict('os.environ',{gateway.FEATURE_ENV:'0'}),patch.object(portable,'_validated',return_value=trusted),patch.dict(sys.modules,{'mtp2_shared_gateway':None}):
            with self.assertRaisesRegex(ValueError,'feature flag'):
                portable.create_guard_app()

    def test_default_portable_validation_never_imports_candidate_selector(self):
        from compat_api import portable
        trusted={'source_root':ROOT,'data_root':Path('/synthetic-data-root')}
        with patch.dict('os.environ',{'LILIUXFLOW_DATA_ROOT':'/synthetic-data-root'}),patch.object(__import__('trust'),'validate',return_value=trusted),patch.dict(sys.modules,{'mtp2_shared_upgrade':None}):
            __import__('os').environ.pop('LILIUXFLOW_MTP2_SHARED_VALIDATION',None)
            self.assertIs(portable._validated(),trusted)


class PrivateRouteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Hash inputs/DB rows are synthetic; actual pinned config/reloader methods."""
    def fixture(self, folder):
        data=Path(folder).resolve();(data/'run').mkdir()
        trusted=ConfigTests().trusted(data)
        with patch.object(gateway,'require_shared_installation',return_value=object()):
            _manager,path=agent.configs(trusted,shared_context=True)
        document=json.loads(path.read_bytes())
        document['general_settings'].update(store_model_in_db=False,supported_db_objects=[])
        path.write_text(json.dumps(document));path.chmod(0o600)
        return path,document,trusted

    async def test_actual_get_config_keeps_local_routes_against_hostile_DB_config_and_env(self):
        from litellm.proxy import proxy_server as server
        with tempfile.TemporaryDirectory() as folder:
            path,document,trusted=self.fixture(folder)
            private=server.ProxyConfig();default=server.ProxyConfig()
            hostile=copy.deepcopy(document)
            hostile['model_list'][0]['litellm_params']['api_base']='http://127.0.0.1:8080/v1'
            hostile['environment_variables']={'SYNTHETIC_ROUTE_DRIFT':'hostile'}
            default._get_config_from_file=AsyncMock(return_value=copy.deepcopy(document))
            default._update_config_from_db=AsyncMock(return_value=hostile)
            with patch.object(server,'proxy_config',private),patch.object(server,'prisma_client',SimpleNamespace()),patch.object(server,'store_model_in_db',True),patch.object(server,'general_settings',{'supported_db_objects':[]}):
                boundary=gateway._PrivateRouteBoundary(server,path,hashlib.sha256(path.read_bytes()).hexdigest(),trusted['registry'],trusted['config']['ports']['guard']).install()
                self.assertEqual(await private._get_models_from_db(SimpleNamespace()),[])
                actual=await private.get_config(str(path))
                self.assertNotIn('environment_variables',actual)
                self.assertEqual([row['litellm_params']['api_base'] for row in actual['model_list']],[row['litellm_params']['api_base'] for row in document['model_list']])
                old=await default.get_config(str(path))
                self.assertEqual(old['model_list'][0]['litellm_params']['api_base'],'http://127.0.0.1:8080/v1')
                self.assertEqual(default._update_config_from_db.await_count,1)
                self.assertEqual(old['environment_variables'],hostile['environment_variables'])
            # Only the private instance is bound; class and default behavior remain original.
            self.assertEqual(private._get_models_from_db.__func__.__name__,'no_models')
            self.assertEqual(default._get_models_from_db.__func__,server.ProxyConfig._get_models_from_db)

    async def test_actual_reloader_protects_routes_but_preserves_DB_auth_limits(self):
        from litellm.proxy import proxy_server as server
        with tempfile.TemporaryDirectory() as folder:
            path,document,trusted=self.fixture(folder);private=server.ProxyConfig()
            with patch.object(server,'proxy_config',private),patch.object(server,'general_settings',{'supported_db_objects':[],'store_model_in_db':False}),patch.object(server,'store_model_in_db',True),patch.object(server,'prefetch_config_params',AsyncMock()),patch.object(server,'get_config_param',AsyncMock(return_value=SimpleNamespace(param_value={'store_model_in_db':True,'supported_db_objects':['models'],'global_max_parallel_requests':17}))):
                boundary=gateway._PrivateRouteBoundary(server,path,hashlib.sha256(path.read_bytes()).hexdigest(),trusted['registry'],trusted['config']['ports']['guard']).install()
                private.get_credentials=AsyncMock();private._init_non_llm_objects_in_db=AsyncMock()
                private._update_llm_router=AsyncMock(side_effect=AssertionError('private route must not reconcile DB model'))
                await private._add_deployment_locked(prisma_client=SimpleNamespace(),proxy_logging_obj=SimpleNamespace())
                self.assertEqual(private._update_llm_router.await_count,0)
                self.assertIs(server.store_model_in_db,False)
                self.assertEqual(server.general_settings['supported_db_objects'],[])
                self.assertEqual(server.general_settings['global_max_parallel_requests'],17)
                await private._apply_safe_litellm_settings_overrides_from_db(SimpleNamespace())

    async def test_pre_call_route_context_and_source_drift_are_rejected(self):
        from litellm.proxy import proxy_server as server
        with tempfile.TemporaryDirectory() as folder:
            path,document,trusted=self.fixture(folder)
            rows=copy.deepcopy(document['model_list'])
            router=SimpleNamespace(get_model_list=lambda:rows)
            with patch.object(server,'llm_router',router):
                boundary=gateway._PrivateRouteBoundary(server,path,hashlib.sha256(path.read_bytes()).hexdigest(),trusted['registry'],trusted['config']['ports']['guard'])
                boundary.assert_live_routes()
                rows[0]['model_info']['max_input_tokens']+=1
                with self.assertRaises(DistributionError):boundary.assert_live_routes()
                rows[:]=copy.deepcopy(document['model_list']);rows[0]['litellm_params']['api_base']='http://127.0.0.1:8080/v1'
                with self.assertRaises(DistributionError):boundary.assert_live_routes()
                rows[:]=copy.deepcopy(document['model_list']);path.write_text(json.dumps(document)+' ')
                with self.assertRaises(DistributionError):boundary.assert_live_routes()

    async def test_private_ASGI_startup_never_announces_ready_with_wrong_router(self):
        from litellm.proxy import proxy_server as server
        with tempfile.TemporaryDirectory() as folder:
            path,document,trusted=self.fixture(folder)
            rows=copy.deepcopy(document['model_list'])
            with patch.object(server,'llm_router',SimpleNamespace(get_model_list=lambda:rows)):
                boundary=gateway._PrivateRouteBoundary(server,path,hashlib.sha256(path.read_bytes()).hexdigest(),trusted['registry'],trusted['config']['ports']['guard'])
                async def app(scope,receive,send):await send({'type':'lifespan.startup.complete'})
                wrapper=gateway._PrivateRouteApp(app,boundary);sent=[]
                async def send(message):sent.append(message)
                async def receive():return {'type':'lifespan.startup'}
                await wrapper({'type':'lifespan'},receive,send)
                self.assertEqual(sent,[{'type':'lifespan.startup.complete'}])
                sent.clear();rows.pop()
                with self.assertRaises(DistributionError):await wrapper({'type':'lifespan'},receive,send)
                self.assertEqual(sent,[])


class ActualPinnedLiteLLMRouteTests(unittest.IsolatedAsyncioTestCase):
    """Official route/auth type/callback pipeline; synthetic DB auth fixture and backend."""
    async def asyncSetUp(self):
        import litellm
        from litellm.proxy import proxy_server, common_request_processing
        from litellm.proxy._types import UserAPIKeyAuth, ProxyException
        from fastapi import FastAPI
        self.litellm,self.ps,self.processing=litellm,proxy_server,common_request_processing
        self.registry=registry();self.policy=policy.ProfileAccessPolicy(frozenset(protocol.PUBLIC_ALIASES))
        self.auth=UserAPIKeyAuth(api_key=hashlib.sha256(b'sk-synthetic-official-route-user').hexdigest(),models=list(protocol.PUBLIC_ALIASES))
        self.auth.via_virtual_key=True  # Synthetic DB-auth boundary, not a live DB authentication test.
        self.auth_calls=0;self.captured=[]
        app=FastAPI();app.add_api_route('/v1/chat/completions',proxy_server.chat_completion,methods=['POST'])
        app.add_exception_handler(ProxyException,proxy_server.openai_exception_handler)
        async def dependency():
            self.auth_calls+=1
            return self.auth
        app.dependency_overrides[proxy_server.user_api_key_auth]=dependency
        owner=gateway.configure_callback(self.registry,SECRET.encode())
        self.wrapper=gateway.RawMtp2Ingress(app,self.registry,capture=True,owner=owner)
        async def route(**kwargs):
            self.assertIs(gateway._raw_context.get().owner,gateway._callback_config[2])
            self.captured.append(kwargs['data'])
            async def answer():
                return litellm.ModelResponse(model=protocol.PUBLIC_ALIASES[0],choices=[{'index':0,'message':{'role':'assistant','content':'synthetic'},'finish_reason':'stop'}],usage={'prompt_tokens':1,'completion_tokens':1,'total_tokens':2})
            return answer()
        self.patches=[patch.object(litellm,'callbacks',[gateway.shared_callback]),
                      patch.object(common_request_processing,'route_request',side_effect=route),
                      patch.object(proxy_server,'general_settings',{}),patch.object(proxy_server,'llm_router',None),
                      patch.object(policy,'load_policy',return_value=self.policy)]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.wrapper),base_url='http://official-route.test',
                                     headers={'Authorization':'Bearer sk-synthetic-official-route-user'})

    async def asyncTearDown(self):await self.client.aclose()

    async def test_official_route_actual_auth_type_and_callback_mint_capability(self):
        body={'model':protocol.PUBLIC_ALIASES[0],'messages':[{'role':'user','content':'synthetic'}]}
        response=await self.client.post('/v1/chat/completions',json=body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.auth_calls,1)
        cap=self.captured[0]['extra_headers'][ingress.CAPABILITY_HEADER]
        verified=ingress.verify_capability(cap,body,self.registry,backend_secret=SECRET.encode())
        self.assertEqual(verified.alias,body['model'])

    async def test_private_boundary_actual_official_auth_callback_usage_then_route_drift_rejects(self):
        from litellm.integrations.custom_logger import CustomLogger
        success=[]
        auth=self.auth
        class ObserveUsage(CustomLogger):
            async def async_post_call_success_hook(self,user_api_key_dict,data,response):
                success.append((user_api_key_dict is auth,response.usage.total_tokens))
        with tempfile.TemporaryDirectory() as folder:
            path,document,trusted=PrivateRouteBoundaryTests().fixture(folder)
            router=self.litellm.Router(model_list=copy.deepcopy(document['model_list']),router_general_settings={'async_only_mode':True})
            with patch.object(self.ps,'proxy_config',self.ps.ProxyConfig()),patch.object(self.ps,'llm_router',router),patch.object(self.ps,'proxy_logging_obj',self.ps.ProxyLogging(user_api_key_cache=self.ps.user_api_key_cache)),patch.object(self.litellm,'callbacks',[ObserveUsage(),gateway.shared_callback]):
                boundary=gateway._PrivateRouteBoundary(self.ps,path,hashlib.sha256(path.read_bytes()).hexdigest(),trusted['registry'],trusted['config']['ports']['guard']).install()
                self.wrapper.owner=gateway.configure_callback(self.registry,SECRET.encode(),private_boundary=boundary)
                body={'model':protocol.PUBLIC_ALIASES[0],'messages':[{'role':'user','content':'synthetic'}]}
                response=await self.client.post('/v1/chat/completions',json=body)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['usage']['total_tokens'],2)
                self.assertEqual(self.auth_calls,1)
                self.assertEqual(len(self.captured),1)
                self.assertEqual(success,[(True,2)])
                class LateOverride(CustomLogger):
                    async def async_pre_call_hook(self,user_api_key_dict,cache,data,call_type):
                        data['api_base']='http://127.0.0.1:8080/v1';return data
                with patch.object(self.litellm,'callbacks',[gateway.shared_callback,LateOverride()]):
                    response=await self.client.post('/v1/chat/completions',json=body)
                # All official pre-call processors finish before the final gate.
                self.assertEqual(response.status_code,400)
                self.assertEqual(self.auth_calls,2)
                self.assertEqual(len(self.captured),1)
                ingress.verify_capability(self.captured[0]['extra_headers'][ingress.CAPABILITY_HEADER],body,self.registry,backend_secret=SECRET.encode())
                hostile=router.get_model_list();hostile[0]['litellm_params']['api_base']='http://127.0.0.1:8080/v1'
                with patch.object(router,'get_model_list',return_value=hostile):
                    response=await self.client.post('/v1/chat/completions',json=body)
                self.assertEqual(response.status_code,503)
                self.assertEqual(self.auth_calls,3)
                self.assertEqual(len(self.captured),1)
                self.assertEqual(success,[(True,2)])

    async def test_official_route_duplicate_rejected_before_auth_dependency(self):
        raw=('{'+'"model":"'+protocol.PUBLIC_ALIASES[0]+'","model":"'+protocol.PUBLIC_ALIASES[-1]+'","messages":[]}').encode()
        response=await self.client.post('/v1/chat/completions',content=raw)
        self.assertEqual(response.status_code,400)
        self.assertEqual((self.auth_calls,len(self.captured)),(0,0))

    async def test_actual_official_route_rejects_provider_overrides_before_auth_or_backend(self):
        for key in ('api_base','api_key','base_url','litellm_credentials_name'):
            for container in (None,'extra_body'):
                body={'model':protocol.PUBLIC_ALIASES[0],'messages':[]}
                if container is None:body[key]='synthetic-rejected'
                else:body[container]={key:'synthetic-rejected'}
                response=await self.client.post('/v1/chat/completions',json=body)
                self.assertEqual(response.status_code,400)
        self.assertEqual((self.auth_calls,len(self.captured)),(0,0))

    async def test_later_official_callback_cannot_add_provider_route_override(self):
        from litellm.integrations.custom_logger import CustomLogger
        class InjectRoute(CustomLogger):
            async def async_pre_call_hook(self,user_api_key_dict,cache,data,call_type):
                data['api_base']='http://127.0.0.1:8080/v1'
                return data
        with patch.object(self.litellm,'callbacks',[InjectRoute(),gateway.shared_callback]):
            response=await self.client.post('/v1/chat/completions',json={'model':protocol.PUBLIC_ALIASES[0],'messages':[]})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.auth_calls,1);self.assertEqual(self.captured,[])

    async def test_official_key_alias_rewrite_cannot_escalate_original_profile(self):
        self.auth.aliases={protocol.PUBLIC_ALIASES[0]:protocol.PUBLIC_ALIASES[-1]}
        response=await self.client.post('/v1/chat/completions',json={'model':protocol.PUBLIC_ALIASES[0],'messages':[]})
        self.assertEqual(response.status_code,403)
        self.assertEqual(set(response.json()),{'error'})
        self.assertEqual(set(response.json()['error']),{'message','type','param','code'})
        self.assertEqual(int(response.json()['error']['code']),403)
        self.assertEqual(self.captured,[])

    async def test_official_auth_without_server_virtual_key_marker_fails(self):
        self.auth.via_virtual_key=False
        response=await self.client.post('/v1/chat/completions',json={'model':protocol.PUBLIC_ALIASES[0],'messages':[]})
        self.assertEqual(response.status_code,403)
        self.assertEqual(set(response.json()),{'error'})
        self.assertEqual(set(response.json()['error']),{'message','type','param','code'})
        self.assertEqual(int(response.json()['error']['code']),403)
        self.assertEqual(self.captured,[])

    def test_factory_uses_actual_pinned_worker_config_and_keeps_unsupported_params(self):
        from compat_api import portable
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder).resolve();(data/'run').mkdir(mode=0o700)
            trusted=ConfigTests().trusted(data)
            with patch.dict('os.environ',{gateway.FEATURE_ENV:'1'}),patch.object(portable,'_validated',return_value=trusted),patch.object(gateway,'require_shared_installation',return_value=object()), \
                 patch.object(self.ps,'proxy_config',copy.copy(self.ps.proxy_config)), \
                 patch.object(self.ps,'proxy_logging_obj',copy.copy(self.ps.proxy_logging_obj)), \
                 patch.object(gateway,'_callback_config',gateway._callback_config):
                # Isolate real pinned instances: factory installs bound config/
                # pre-call methods whose lifetime must not outlive this temp file.
                # Use actual finalized normal configuration bytes; the factory's
                # normal_shared_configuration_sha256 must match the saved file.
                agent.configs(trusted,shared_context=True)
                app=gateway.create_litellm_app()
                config=json.loads(__import__('os').environ['WORKER_CONFIG'])
        from mtp2_shared_ui import SharedModelInfo
        self.assertIsInstance(app,gateway._PrivateRouteApp)
        self.assertIsInstance(app.app,gateway.RawMtp2Ingress)
        self.assertIsInstance(app.app.app,SharedModelInfo)
        self.assertIs(app.app.app.app,self.ps.app)
        self.assertFalse(config['drop_params'])
        self.assertFalse(config['add_function_to_prompt'])
        self.assertIsNone(config['request_timeout'])


class FakeOwner:
    def __init__(self):
        self.binding=SimpleNamespace(port=19090)
        self.alive=True
    def current_owner(self):
        return self.binding
    def matches(self,binding):
        return self.alive and binding is self.binding
    def exited(self,binding):
        return not self.alive and binding is self.binding


class GuardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry=registry();self.owner=FakeOwner();self.records={};self.running=None
        self.loads=self.unloads=self.cancels=0
        self.hold_release=False;self.force_unknown=False;self.state_ready=True;self.state_active=0;self.state_gate=asyncio.Event();self.state_gate.set()
        self.reader_gate=None;self.cancelled_nonces=[]
        mocked=patch.object(ingress,'_official_auth_type',return_value=AuthFixture);mocked.start();self.addCleanup(mocked.stop)
        self.key=protocol.derive_native_key(SECRET.encode())
        async def proof(profile,phase):return True
        def factory(**kwargs):return gateway.make_shared_guard(owner_probe=self.owner,**kwargs)
        self.app=create_app(manager_url='http://127.0.0.1:18081',backend_token=SECRET,control_token='synthetic-control',
                            registry=self.registry,resource_probe=proof,transport=httpx.MockTransport(self.backend),shared_guard_factory=factory)
        self.guard=self.app.state.lifecycle_guard
        self.lifespan=self.app.router.lifespan_context(self.app);await self.lifespan.__aenter__()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://guard.test')
        self.lock_patch=patch.object(gateway,'_instance_free',return_value=True);self.lock_patch.start();self.addCleanup(self.lock_patch.stop)
        policy_patch=patch.object(policy,'load_policy',return_value=policy.ProfileAccessPolicy(frozenset(protocol.PUBLIC_ALIASES)));policy_patch.start();self.addCleanup(policy_patch.stop)

    async def asyncTearDown(self):
        if self.guard.poll_task is not None:
            self.guard.poll_task.cancel();await asyncio.gather(self.guard.poll_task,return_exceptions=True)
        await self.client.aclose();await self.lifespan.__aexit__(None,None,None)

    def signed(self,proof,value):
        body=json.dumps(value,separators=(',',':')).encode()
        return httpx.Response(200,headers=protocol.response_headers(proof,native_key=self.key,epoch=EPOCH,status=200,body=body),stream=httpx.ByteStream(body))

    async def backend(self,request):
        if request.url.port==18081:
            if request.url.path=='/running':return httpx.Response(200,json={'running':[] if self.running is None else [{'model':self.running}]})
            if request.url.path.startswith('/upstream/'):
                self.running=gateway.PHYSICAL_MODEL_ID;self.loads+=1
                return httpx.Response(200,json={'status':'ok','state':'ready'})
            if request.url.path=='/api/models/unload':
                self.running=None;self.owner.alive=False;self.unloads+=1
                return httpx.Response(200,json={'unloaded':True})
            if request.url.path in protocol.PATHS['infer']:
                value=json.loads(request.content);value['model']='Qwen3.8-Flash-Next';value['liliuxflow_profile']=request.headers[protocol.HEADER_PREFIX+'profile']
                proof=protocol.verify_native_request(request.method,request.url.path,tuple(request.headers.items()),json.dumps(value).encode(),native_key=self.key,now=int(__import__('time').time()),actual_epoch=EPOCH)
                row={'nonce':proof.nonce,'alias':proof.alias,'profile':proof.profile,'phase':'acquired','revision':1,'acquired':1,'released':0}
                self.records[proof.nonce]=row
                body=json.dumps({'model':proof.alias,'choices':[{'message':{'content':'synthetic answer'}}]}).encode()
                gate=self.reader_gate
                class ReaderStream(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield body
                        if gate is not None:await gate.wait()
                return httpx.Response(200,headers={'Content-Type':'application/json'},stream=ReaderStream())
        proof=protocol.verify_native_request(request.method,request.url.path,tuple(request.headers.items()),request.content,native_key=self.key,now=int(__import__('time').time()),actual_epoch=EPOCH)
        if proof.operation=='state':
            await self.state_gate.wait()
            return self.signed(proof,{'v':1,'epoch':EPOCH,'state':'ready' if self.state_ready else 'retiring','revision':1,'active':self.state_active,'pending':0,
                                      'cache':{'disk_bytes':0,'disk_attached':False,'writing':False,'durable_min_tokens':0,'write_ahead_enabled':False}})
        if proof.operation=='cancel':
            self.cancels+=1
            self.cancelled_nonces.append(proof.nonce)
            return self.signed(proof,{'v':1,'epoch':EPOCH,'nonce':proof.nonce,'result':'cancel_requested','revision':1})
        result=[]
        for nonce in json.loads(request.content)['requests']:
            row=copy.deepcopy(self.records.get(nonce))
            if row is None or self.force_unknown:
                result.append({'nonce':nonce,'phase':'unknown'});continue
            if not self.hold_release:
                row.update(phase='released',revision=2,released=1,proof=dict(protocol._RELEASE_PROOF))
            result.append(row)
        return self.signed(proof,{'v':1,'epoch':EPOCH,'records':result})

    def request(self,index=0):
        value={'model':protocol.PUBLIC_ALIASES[index],'messages':[{'role':'user','content':'synthetic'}],'max_tokens':8}
        raw=json.dumps(value).encode()
        cap=ingress.issue_capability(raw,value['model'],AuthFixture(),self.registry,backend_secret=SECRET.encode())
        return raw,{'Authorization':'Bearer '+SECRET,ingress.CAPABILITY_HEADER:cap}

    async def test_real_guard_route_signed_release_and_profile_reuse_no_reload(self):
        for index in (0,2):
            body,headers=self.request(index)
            response=await self.client.post('/v1/chat/completions',content=body,headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['model'],protocol.PUBLIC_ALIASES[index])
        self.assertEqual(self.loads,1)
        self.assertEqual(self.unloads,0)
        status=await self.guard.status()
        self.assertEqual((status['active_inferences'],status['pending_inferences']),(0,0))

    async def test_same_alias_two_requests_independent_and_bounded_busy(self):
        self.hold_release=True
        tasks=[]
        for _ in range(2):
            body,headers=self.request();tasks.append(asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers)))
        for _ in range(100):
            if len(self.records)==2:break
            await asyncio.sleep(.01)
        self.assertEqual(len(self.records),2)
        status=await self.guard.status()
        self.assertEqual(status['active_inferences'],2)
        self.assertEqual(len({row['request_id'] for row in status['shared_requests']}),2)
        response=await self.client.post('/api/models/unload')
        self.assertEqual(response.status_code,409)
        self.hold_release=False
        self.assertTrue(all(response.status_code==200 for response in await asyncio.gather(*tasks)))

    async def test_untrusted_capability_has_no_model_load_or_native_admission(self):
        body,headers=self.request();headers[ingress.CAPABILITY_HEADER]='forged'
        response=await self.client.post('/v1/chat/completions',content=body,headers=headers)
        self.assertEqual(response.status_code,503)
        self.assertEqual((self.loads,len(self.records)),(0,0))

    async def test_guard_rejects_provider_override_even_with_valid_original_model_capability(self):
        body,headers=self.request();value=json.loads(body);value['extra_body']={'api_base':'http://127.0.0.1:8080/v1'}
        response=await self.client.post('/v1/chat/completions',content=json.dumps(value).encode(),headers=headers)
        self.assertEqual(response.status_code,503)
        self.assertEqual((self.loads,len(self.records)),(0,0))

    async def test_private_native_control_cannot_be_reached_from_public_guard(self):
        response=await self.client.post('/_liliuxflow/mtp2/cancel',json={})
        self.assertEqual(response.status_code,403)

    async def wait_for(self,predicate):
        for _ in range(200):
            if predicate():return
            await asyncio.sleep(.005)
        self.fail('synthetic stage did not settle within CPU bound')

    async def test_cold_two_callers_loading_status_has_no_group_placeholder(self):
        self.state_gate.clear();tasks=[]
        for _ in range(2):
            body,headers=self.request();tasks.append(asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers)))
        await self.wait_for(lambda:len(self.guard.shared_rows)==2 and self.guard.shared_ticket is not None)
        status=await self.guard.status()
        self.assertEqual((status['active_inferences'],status['pending_inferences']),(0,2))
        self.assertEqual(len(status['shared_requests']),2)
        self.state_gate.set();await asyncio.gather(*tasks)

    async def test_signed_release_before_reader_close_never_false_pauses_or_recancels(self):
        self.reader_gate=asyncio.Event()
        body,headers=self.request();task=asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers))
        await self.wait_for(lambda:bool(self.guard.shared_rows) and all(row.native_done for row in self.guard.shared_rows.values()))
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.cancels,0)
        self.assertFalse((await self.guard.status())['admission_paused'])
        self.assertEqual((await self.guard.shared.status())['active'],0)

    async def test_reader_close_then_cancel_ack_then_signed_release_peer_independent(self):
        self.hold_release=True;self.reader_gate=asyncio.Event();tasks=[]
        for index in (0,2):
            body,headers=self.request(index);tasks.append(asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers)))
        await self.wait_for(lambda:len(self.records)==2)
        ids=tuple(self.records)
        tasks[0].cancel()
        await self.wait_for(lambda:self.cancels==1)
        self.assertEqual(self.cancelled_nonces,[ids[0]])
        self.assertEqual((await self.guard.shared.status())['active'],2)
        self.hold_release=False;self.reader_gate.set()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.assertFalse((await self.guard.status())['admission_paused'])
        self.assertEqual((await self.guard.shared.status())['active'],0)

    async def test_unknown_after_observed_request_retains_busy_and_pauses(self):
        self.hold_release=True;self.reader_gate=asyncio.Event()
        body,headers=self.request();task=asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers))
        await self.wait_for(lambda:bool(self.guard.native_actor._observations))
        self.force_unknown=True
        await self.wait_for(lambda:self.guard._admission_paused)
        self.assertEqual((await self.guard.shared.status())['active'],1)
        task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_MTP0_initial_guard_status_matches_legacy_exactly(self):
        async def proof(profile,phase):return True
        legacy=_ContextLifecycleGuard(manager_url='http://127.0.0.1:18081',backend_token=SECRET,control_token='synthetic-control',transport=None,
                                      registry=self.registry,resource_probe=proof)
        self.assertEqual(await legacy.status(),await self.guard.status())

    async def test_manual_shared_alias_Load_reuses_resident_and_catalog_is_logical(self):
        for alias in (protocol.PUBLIC_ALIASES[0],protocol.PUBLIC_ALIASES[-1]):
            response=await self.client.post('/api/models/load/'+alias)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['model'],alias)
        self.assertEqual(self.loads,1)
        self.assertEqual(self.records,{})  # No dummy inference/job/nonces for UI Load.
        response=await self.client.get('/api/context-profiles')
        rows=[row for row in response.json()['profiles'] if row['model'] in protocol.PUBLIC_ALIASES]
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['loaded'] and row['state']=='ready' for row in rows))

    async def test_any_idle_shared_alias_unloads_exact_physical_group(self):
        response=await self.client.post('/api/models/load/'+protocol.PUBLIC_ALIASES[0])
        self.assertEqual(response.status_code,200)
        response=await self.client.post('/api/models/unload/'+protocol.PUBLIC_ALIASES[-1])
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.unloads,1)
        self.assertIsNone(self.guard.shared_owner)
        self.assertIsNone(self.running)

    async def test_shared_group_TTL1800_is_guarded_and_never_loads(self):
        response=await self.client.post('/api/models/load/'+protocol.PUBLIC_ALIASES[0])
        self.assertEqual(response.status_code,200)
        self.guard.last_shared_work=asyncio.get_running_loop().time()-1799
        self.assertFalse(await self.guard._idle_once())
        self.guard.last_shared_work=asyncio.get_running_loop().time()-1801
        self.assertTrue(await self.guard._idle_once())
        self.assertEqual((self.loads,self.unloads),(1,1))

    async def test_shared_TTL_missing_authenticated_ready_retains_owner_and_pauses(self):
        response=await self.client.post('/api/models/load/'+protocol.PUBLIC_ALIASES[0])
        self.assertEqual(response.status_code,200)
        self.state_ready=False
        self.guard.last_shared_work=asyncio.get_running_loop().time()-1801
        self.assertFalse(await self.guard._idle_once())
        self.assertEqual((self.loads,self.unloads),(1,0))
        self.assertIs(self.guard.shared_owner,self.owner.binding)
        self.assertTrue((await self.guard.status())['admission_paused'])

    async def test_shared_TTL_authenticated_busy_state_does_not_retire(self):
        response=await self.client.post('/api/models/load/'+protocol.PUBLIC_ALIASES[0])
        self.assertEqual(response.status_code,200)
        self.state_active=1
        self.guard.last_shared_work=asyncio.get_running_loop().time()-1801
        self.assertFalse(await self.guard._idle_once())
        self.assertEqual(self.unloads,0)
        self.assertTrue((await self.guard.status())['admission_paused'])

    async def test_TTL_and_manual_unload_block_native_settled_reader_cleanup(self):
        self.reader_gate=asyncio.Event()
        body,headers=self.request();task=asyncio.create_task(self.client.post('/v1/chat/completions',content=body,headers=headers))
        await self.wait_for(lambda:bool(self.guard.shared_rows) and all(row.native_done for row in self.guard.shared_rows.values()))
        self.guard.last_shared_work=asyncio.get_running_loop().time()-1801
        self.assertFalse(await self.guard._idle_once())
        response=await self.client.post('/api/models/unload/'+protocol.PUBLIC_ALIASES[-1])
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.unloads,0)
        self.reader_gate.set();await task


if __name__=='__main__':unittest.main()
