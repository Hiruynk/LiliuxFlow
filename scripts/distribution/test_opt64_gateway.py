"""CPU-only typed registry/config/ASGI routing; no real API, model or credentials."""
import asyncio,contextlib,copy,importlib.util,json,pathlib,sys,tempfile,types,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2];SOURCE=ROOT;HERE=ROOT
sys.dont_write_bytecode=True;sys.path[:0]=[str(ROOT/'scripts/distribution'),str(ROOT/'services/compat/src')]
import agent,liliuxflow,litellm_profile_policy as policy
from compat_api.app import Settings,CompatModelProfile,create_app
import httpx
import profile_registry as registry_module
DOCUMENT=json.loads((ROOT/'profiles/distribution/context-registry.json').read_bytes())
OPT='qwen3.8-flash-next-lily-q4-mtp2-64k';ENGINE='latest13f-defer-pc123-mtp2-opt64k';BASE='qwen3.8-flash-next-lily-q4-64k'
def registry(enabled=True):return registry_module.parse_registry(copy.deepcopy(DOCUMENT),enabled_optin_profiles=('ctx64k-mtp2',) if enabled else ())
def compat_profiles(r):return tuple(CompatModelProfile(p.public_alias,p.context_tokens,p.total_deadline_seconds,p.default_output_tokens,profile_id=p.profile_id,engine_id=p.as_dict().get('engine_id'),mtp_drafts=p.as_dict().get('mtp_drafts',0),kv_cache=p.as_dict().get('kv_cache'),max_batch=p.as_dict().get('max_batch')) for p in r.enabled_profiles)
class Wiring(unittest.TestCase):
    def test_actual_registry_generates_exact_profile_routed_cmd_and_baseline_default(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder).resolve();(root/'run').mkdir(mode=0o700)
            trusted={'data_root':root,'source_root':SOURCE,'registry':registry(),'config':{'ports':{'guard':8080,'manager':8081,'litellm':4000,'compat':8001,'postgresql':15432}},'secrets':{'MANAGER_BACKEND_TOKEN':'CPU'},'binaries':{'compat_python':pathlib.Path(sys.executable)}}
            manager,lp=agent.configs(trusted);models=json.loads(manager.read_bytes())['models'];routes=json.loads(lp.read_bytes())['model_list']
            self.assertEqual(len(models),4);self.assertIn('--profile ctx64k-mtp2',models[OPT]['cmd']);self.assertNotIn('--engine',models[OPT]['cmd']);self.assertIn('MTP2 opt-in',models[OPT]['name'])
            info=next(r['model_info'] for r in routes if r['model_name']==OPT);self.assertEqual(info['engine_id'],ENGINE);self.assertEqual(info['mtp_drafts'],2);self.assertEqual(info['default_output_tokens'],65536)
            self.assertEqual(trusted['registry'].default.profile_id,'ctx64k');self.assertEqual(len(registry(False).enabled_profiles),3)
    def test_owner_models_append_only_requires_explicit_new_alias_and_never_wildcard(self):
        enabled=[p.public_alias for p in registry().enabled_profiles];prior=[BASE,'existing-private-model']
        self.assertNotIn(OPT,agent._expanded_owner_models(prior,enabled));self.assertEqual(agent._expanded_owner_models(prior,enabled,(OPT,))[:2],prior)
        self.assertIn(OPT,agent._expanded_owner_models(prior,enabled,(OPT,)))
        self.assertIn(OPT,agent._expanded_owner_models([BASE,OPT],enabled))
        for current in ([],['*'],['all-proxy-models']):
            with self.assertRaises(Exception):agent._expanded_owner_models(current,enabled,(OPT,))
        with self.assertRaises(Exception):agent._owner_grant_models([BASE],(OPT,))
    def test_policy_exact_optin_only_preserves_three_existing_model_rules(self):
        enabled=[p.public_alias for p in registry().enabled_profiles];p=policy.parse_policy({'schema_version':1,'enabled_models':enabled,'validation_keys':{}})
        self.assertTrue(policy.explicit_profile_access(OPT,[OPT],policy=p))
        for grants in ([],['*'],['all-proxy-models'],['all-team-models'],['all-router-models'],['public'],[BASE]):self.assertFalse(policy.explicit_profile_access(OPT,grants,policy=p))
        self.assertTrue(policy.explicit_profile_access(BASE,[],policy=p))
        for alias in ('qwen3.8-flash-next-lily-q4-128k','qwen3.8-flash-next-lily-q4-262k'):
            self.assertTrue(policy.explicit_profile_access(alias,[alias],policy=p));self.assertFalse(policy.explicit_profile_access(alias,['*'],policy=p))
        disabled=policy.parse_policy({'schema_version':1,'enabled_models':[BASE],'validation_keys':{}});self.assertFalse(policy.explicit_profile_access(OPT,[OPT],policy=disabled))
        scoped=policy.parse_policy({'schema_version':1,'enabled_models':[BASE],'validation_keys':{OPT:['a'*64]}})
        self.assertTrue(policy.explicit_profile_access(OPT,[OPT],key_fingerprint='a'*64,policy=scoped))
        self.assertFalse(policy.explicit_profile_access(OPT,[OPT],key_fingerprint='b'*64,policy=scoped))
        with self.assertRaises(ValueError):policy.parse_policy({'schema_version':1,'enabled_models':enabled,'validation_keys':{'unknown':['a'*64]}})
    def test_CLI_build_forwards_only_exact_optin_seam_without_build_or_network(self):
        calls=[];module=types.SimpleNamespace(build=lambda *a,**k:calls.append((a,k)) or {'CPU':'NO_BUILD'})
        args=types.SimpleNamespace(data_root=HERE,cargo=None,go=None,node=None,npm_cli=None,pg_bin=None,ui_manifest=None,execute=False,optin_engine=ENGINE)
        with patch.dict(sys.modules,{'build_native':module}),patch.object(liliuxflow,'emit'):liliuxflow.build_runtime(args)
        self.assertEqual(calls[0][1]['optin_engine'],ENGINE);self.assertFalse(calls[0][1]['execute'])
        calls=[];module=types.SimpleNamespace(build_optin_candidate=lambda *a,**k:calls.append(('existing',a,k)) or {},build_optin_source_candidate=lambda *a,**k:calls.append(('source_only',a,k)) or {})
        with patch.dict(sys.modules,{'build_native':module}),patch.object(liliuxflow,'emit'):
            for source_only in (False,True):liliuxflow.build_optin_runtime(types.SimpleNamespace(data_root=HERE,cargo=None,execute=False,source_only=source_only))
        self.assertEqual([x[0] for x in calls],['existing','source_only']);self.assertTrue(all(not x[2]['execute'] for x in calls))
    def test_opt64_cannot_be_default_or_use_mismatched_engine_mode(self):
        profiles=compat_profiles(registry());arguments=('http://CPU.invalid',BASE,'Qwen3.8-Flash-Next',65536,'rev','a'*64,67)
        with self.assertRaises(ValueError):Settings('http://CPU.invalid',OPT,'Qwen3.8-Flash-Next',65536,'rev','a'*64,67,profiles=profiles)
        bad=CompatModelProfile(OPT,65536,3672,profile_id='ctx64k-mtp2',engine_id=ENGINE,mtp_drafts=0,kv_cache='bf16',max_batch=1)
        with self.assertRaises(ValueError):Settings(*arguments,profiles=(profiles[0],bad))
    def test_CLI_catalog_uses_source_disabled_or_generated_trusted_enablement(self):
        outputs=[];source_registry=types.SimpleNamespace(load_registry=lambda _:registry(False))
        with tempfile.TemporaryDirectory() as folder:
            data=pathlib.Path(folder).resolve();args=types.SimpleNamespace(data_root=data,profile_command='list')
            with patch.dict(sys.modules,{'profile_registry':source_registry}),patch.object(liliuxflow,'emit',side_effect=outputs.append):liliuxflow.profiles(args)
            self.assertEqual(outputs[-1]['catalog_source'],'source_catalog');self.assertFalse(next(p for p in outputs[-1]['profiles'] if p['profile_id']=='ctx64k-mtp2')['production_enabled'])
            (data/'install.json').write_text(json.dumps({'runtime_state':'BUILT'}));calls=[]
            trusted=types.SimpleNamespace(validate=lambda *a,**k:calls.append(k) or {'registry':registry(True)})
            with patch.dict(sys.modules,{'profile_registry':source_registry,'trust':trusted}),patch.object(liliuxflow,'emit',side_effect=outputs.append):liliuxflow.profiles(args)
            self.assertEqual(outputs[-1]['catalog_source'],'trusted_installation');self.assertTrue(next(p for p in outputs[-1]['profiles'] if p['profile_id']=='ctx64k-mtp2')['production_enabled']);self.assertEqual(calls,[{'require_checkpoint':False}])
    def test_finite_six_rows_long_disabled_and_all_mtp_explicit(self):
        r=registry(True)
        self.assertEqual(len(r.profiles),6)
        self.assertEqual(r.default.profile_id,'ctx64k')
        for pid in ('ctx128k-mtp2','ctx262k-mtp2'):
            profile=next(p for p in r.profiles if p.profile_id==pid)
            self.assertFalse(profile.production_enabled)
            with self.assertRaises(Exception):r.by_id(pid)
        aliases=[p.public_alias for p in r.profiles]
        enabled_policy=policy.parse_policy({'schema_version':1,'enabled_models':aliases,'validation_keys':{}})
        for alias in aliases[3:]:
            for models in ([],['*'],['all-router-models'],[BASE]):self.assertFalse(policy.explicit_profile_access(alias,models,policy=enabled_policy))
            self.assertTrue(policy.explicit_profile_access(alias,[alias],policy=enabled_policy))
class Routing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[];r=registry();self.profiles=compat_profiles(r)
        self.policy=policy.parse_policy({'schema_version':1,'enabled_models':[p.public_alias for p in self.profiles],'validation_keys':{}})
        async def handler(request):
            if request.url.path=='/v1/models':
                name=request.headers['authorization'].removeprefix('Bearer ')
                grant_map={'empty':[],'wildcard':['*'],'all-router-models':['all-router-models'],'public':['public']}
                grants=grant_map.get(name,[p.public_alias for p in self.profiles if name=='explicit-opt' or p.public_alias!=OPT])
                aliases=[p.public_alias for p in self.profiles if policy.explicit_profile_access(p.public_alias,grants,policy=self.policy)]
                return httpx.Response(200,json={'data':[{'id':x} for x in aliases]})
            self.calls.append(json.loads(request.content));wire=b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire)
        settings=Settings('http://CPU.invalid',BASE,'Qwen3.8-Flash-Next',65536,'rev','a'*64,67,profiles=self.profiles)
        self.app=create_app(settings,transport=httpx.MockTransport(handler));self.life=self.app.router.lifespan_context(self.app);await self.life.__aenter__();self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://CPU.invalid')
    async def asyncTearDown(self):await self.client.aclose();await self.life.__aexit__(None,None,None)
    async def test_same_context_aliases_stay_distinct_and_catalog_is_caller_filtered(self):
        response=await self.client.get('/api/capabilities',headers={'authorization':'Bearer restricted'})
        self.assertNotIn(OPT,[p['model'] for p in response.json()['models']])
        response=await self.client.get('/api/capabilities',headers={'authorization':'Bearer explicit-opt'},params={'model':OPT})
        self.assertEqual(response.json()['model']['engine_id'],ENGINE);self.assertEqual(response.json()['model']['mtp_drafts'],2)
        for alias in (BASE,OPT):
            response=await self.client.post('/api/chat',headers={'authorization':'Bearer explicit-opt'},json={'model':alias,'messages':[{'role':'user','content':'CPU synthetic'}],'stream':False,'options':{'num_ctx':65536}})
            self.assertEqual(response.status_code,200)
        self.assertEqual([x['model'] for x in self.calls],[BASE,OPT]);self.assertEqual([x['max_tokens'] for x in self.calls],[65536,65536]);self.assertTrue(all('engine_id' not in x and 'num_ctx' not in x for x in self.calls))
    async def test_non_opted_caller_and_runtime_overrides_fail_before_forward(self):
        body={'model':OPT,'messages':[{'role':'user','content':'CPU synthetic'}],'stream':False}
        r=await self.client.post('/api/chat',headers={'authorization':'Bearer restricted'},json=body);self.assertEqual(r.status_code,403)
        for field in ('engine_id','model_path','args','memory','kv_cache','context_tokens'):
            r=await self.client.post('/api/chat',headers={'authorization':'Bearer explicit-opt'},json={**body,field:'CPU override'})
            self.assertEqual(r.status_code,400)
        self.assertEqual(self.calls,[])
    async def test_empty_wildcard_all_router_and_public_inheritance_never_opt_in(self):
        body={'model':OPT,'messages':[{'role':'user','content':'CPU synthetic'}],'stream':False}
        for name in ('empty','wildcard','all-router-models','public'):
            response=await self.client.get('/api/capabilities',headers={'authorization':'Bearer '+name})
            self.assertNotIn(OPT,[p['model'] for p in response.json()['models']])
            response=await self.client.post('/api/chat',headers={'authorization':'Bearer '+name},json=body);self.assertEqual(response.status_code,403)
        self.assertEqual(self.calls,[])
if __name__=='__main__':unittest.main()
