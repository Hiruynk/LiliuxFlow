# SPDX-License-Identifier: Apache-2.0
"""Locked official interrupted logging, native scalar fixtures; no DB/keys/GPU."""
import os
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
import asyncio,copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import mtp2_shared_usage_accounting as a
import litellm
from litellm.types.utils import ModelResponseStream
from litellm.proxy.common_request_processing import _bill_partial_streamed_spend_on_disconnect as official
from litellm.litellm_core_utils.litellm_logging import Logging
ALIAS='qwen3.8-flash-next-lily-q4-mtp2-64k';EPOCH='a'*64

def fixture():
 owner=object();ticket=a.UsageTicket('b'*32,'c'*64,ALIAS,'ctx64k-mtp2',owner)
 usage={'epoch':EPOCH,'nonce':ticket.nonce,'tenant':ticket.tenant,'alias':ALIAS,'profile':ticket.profile,
 'response_id':'chatcmpl-native-owned','prompt_tokens':150,'cached_tokens':20,'completion_tokens':1000,
 'reasoning_tokens':900,'drafted_tokens':100,'accepted_tokens':80,'finish_reason':'cancelled','cancelled':True}
 logger=object.__new__(Logging);logger.model_call_details={};logger._response_cost_calculator=lambda **kw:0.0;seen=[]
 async def success(response,**kwargs):seen.append(response);logger.model_call_details['has_dispatched_final_stream_success']=True
 logger.dispatch_success_handlers=success
 response=type('CPUResponse',(),{})();response._response_headers={a.EPOCH_HEADER:EPOCH};response.model=ALIAS;response.messages=[{'role':'user','content':'Synthetic prompt.'}]
 response.chunks=[ModelResponseStream(id=usage['response_id'],model=ALIAS,choices=[{'index':0,'delta':{'role':'assistant','reasoning_content':'Synthetic high thinking. '*80,'content':''},'finish_reason':None}])]
 order=[]
 async def close():order.append('close')
 response.aclose=close
 data={'model':ALIAS,'litellm_call_id':'actual-LP-own-call-id','litellm_logging_obj':logger,'metadata':{'user_api_key':'synthetic-own-authenticated-keyhash'}}
 async def resolve(*args):order.append('resolve');return usage
 return owner,ticket,usage,logger,seen,response,data,resolve,order

class AccountingCPU(unittest.IsolatedAsyncioTestCase):
 async def test_cancelled_high_thinking_actual_official_dispatch_once_identity_unchanged(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();metadata=copy.deepcopy(data['metadata'])
  with patch.object(litellm,'disable_streaming_logging',False):
   self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
   self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
  self.assertEqual(order,['close','resolve']);self.assertEqual(len(seen),1);self.assertEqual(data['litellm_call_id'],'actual-LP-own-call-id');self.assertEqual(data['metadata'],metadata)
  u=seen[0].usage;self.assertEqual((u.prompt_tokens,u.completion_tokens,u.total_tokens),(150,1000,1150));self.assertEqual(u.completion_tokens_details.reasoning_tokens,900);self.assertEqual(u.prompt_tokens_details.cached_tokens,20)
  self.assertEqual(response.chunks[-1].id,'chatcmpl-native-owned')
 async def test_MTP0_delegates_exactly_without_close_or_query(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture()
  async def original(d,r):self.assertIs(d,data);self.assertIs(r,response);return 'unchanged'
  self.assertEqual(await a.bill_native_partial(None,owner,data,response,resolve=resolve,official_bill=original),'unchanged');self.assertEqual(order,[])
 async def test_drift_never_dispatches_estimate_or_zero_and_requests_explicit_slot_release(self):
  faults={'tenant':'d'*64,'nonce':'d'*32,'epoch':'d'*64,'alias':'other','profile':'other','response_id':'different','prompt_tokens':None,'completion_tokens':True,'reasoning_tokens':1001,'cached_tokens':151,'finish_reason':'fault'}
  for key,bad in faults.items():
   owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();usage[key]=bad
   with self.subTest(key=key):self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(seen,[]);self.assertEqual(len(response.chunks),1)
   self.assertEqual(logger.model_call_details['litellm_params']['metadata']['liliuxflow_mtp2_native_usage']['status'],'UNVERIFIED_NATIVE_PARTIAL_USAGE')
 async def test_native_unknown_or_timeout_has_no_success_billing(self):
  for fault in (None,{},'timeout'):
   owner,ticket,usage,logger,seen,response,data,resolve,order=fixture()
   async def unknown(*args):
    if fault=='timeout':raise asyncio.TimeoutError()
    return fault
   self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=unknown,official_bill=official));self.assertEqual(seen,[])
 async def test_conflicting_native_chunk_IDs_refused_before_query(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();response.chunks.append(ModelResponseStream(id='foreign',model=ALIAS,choices=[]))
  self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(order,[]);self.assertEqual(seen,[])
 async def test_native_unknown_reasoning_and_zero_counts_never_estimated_by_builder(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();usage.update(prompt_tokens=0,cached_tokens=0,completion_tokens=0,reasoning_tokens=None)
  with patch.object(litellm,'disable_streaming_logging',False):
   self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
  self.assertEqual((seen[0].usage.prompt_tokens,seen[0].usage.completion_tokens,seen[0].usage.total_tokens),(0,0,0));self.assertTrue(seen[0].usage.completion_tokens_details is None or seen[0].usage.completion_tokens_details.reasoning_tokens is None)
 async def test_dispatch_failure_restores_original_method_and_no_second_dispatch(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();calls=[]
  async def failure(*args,**kw):calls.append(1);raise ValueError('synthetic')
  logger.dispatch_success_handlers=failure
  self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertIs(logger.dispatch_success_handlers,failure);self.assertEqual(calls,[1])
 async def test_historical_prefill_cancel_no_native_ID_is_unverified_without_caller_spend_fallback(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();response.chunks=[];usage.update(response_id=None,completion_tokens=0,reasoning_tokens=None)
  self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(seen,[]);self.assertEqual(response.chunks,[])
  self.assertEqual(logger.model_call_details['litellm_params']['metadata']['liliuxflow_mtp2_native_usage']['status'],'UNVERIFIED_NATIVE_PARTIAL_USAGE')
 async def test_request_alias_drift_and_owner_ticket_forgery_never_bill_peer(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();data['model']='other'
  self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(seen,[])
  from common import DistributionError
  self.assertFalse(await a.bill_native_partial(ticket,object(),data,response,resolve=resolve,official_bill=official));self.assertEqual(seen,[])
 async def test_official_proxy_cancelled_SDK_cleanup_uses_native_bill_and_single_slot_owner(self):
  import litellm.proxy.common_request_processing as proxy
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();releases=[]
  from types import SimpleNamespace
  async def release(auth):releases.append(auth)
  async def native(d,r):return await a.bill_native_partial(ticket,owner,d,r,resolve=resolve,official_bill=official)
  with patch.object(proxy,'_bill_partial_streamed_spend_on_disconnect',native),patch.object(litellm,'disable_streaming_logging',False):
   await proxy.ProxyBaseLLMRequestProcessing._finalize_streaming_generator_cleanup(None,data,response,client_disconnected=True,user_api_key_dict=object(),proxy_logging_obj=SimpleNamespace(_arelease_max_parallel_requests_on_disconnect=release))
  self.assertEqual(len(seen),1);self.assertEqual(releases,[]);self.assertEqual(seen[0].usage.completion_tokens,1000)
 async def test_unknown_native_SDK_cleanup_releases_slot_exactly_once_without_spend(self):
  import litellm.proxy.common_request_processing as proxy
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();releases=[];auth=object()
  from types import SimpleNamespace
  async def release(owned):releases.append(owned)
  async def unknown(*args):return None
  async def native(d,r):return await a.bill_native_partial(ticket,owner,d,r,resolve=unknown,official_bill=official)
  with patch.object(proxy,'_bill_partial_streamed_spend_on_disconnect',native):
   await proxy.ProxyBaseLLMRequestProcessing._finalize_streaming_generator_cleanup(None,data,response,client_disconnected=True,user_api_key_dict=auth,proxy_logging_obj=SimpleNamespace(_arelease_max_parallel_requests_on_disconnect=release))
  self.assertEqual(releases,[auth]);self.assertEqual(seen,[])
 async def test_provider_completed_usage_still_passes_original_dedup(self):
  owner,ticket,usage,logger,seen,response,data,resolve,order=fixture();logger.model_call_details['has_dispatched_final_stream_success']=True
  self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(order,[]);self.assertEqual(seen,[])


class TicketAccountingCPU(unittest.IsolatedAsyncioTestCase):
 async def test_caller_callID_user_metadata_never_selects_native_nonce_or_tenant(self):
  import mtp2_shared_gateway as gateway
  from test_mtp2_shared_ingress import IngressTests,AuthFixture
  import litellm_profile_policy as policy
  existing=IngressTests();existing.setUp();self.addCleanup(existing.doCleanups)
  old=gateway._callback_config;self.addCleanup(setattr,gateway,'_callback_config',old)
  owner=gateway.configure_callback(existing.registry,b'synthetic-backend-credential-0123456789')
  tickets=[]
  for key in ('sk-synthetic-own-A','sk-synthetic-own-B'):
   value={'model':ALIAS,'messages':[],'user':'caller-claimed-peer','metadata':{'user_api_key':'caller-claimed-peer'},'litellm_call_id':'caller-chosen-same-ID'}
   capture=gateway._RawCapture(ALIAS,json.dumps(value).encode(),(),owner);token=gateway._raw_context.set(capture)
   try:
    with patch.object(policy,'load_policy',return_value=existing.policy):
     await gateway.shared_callback.async_pre_call_hook(AuthFixture(key=key),None,value,'completion')
    self.assertEqual(len(capture.accounting),1);tickets.append(capture.accounting[0]);self.assertIs(tickets[-1].owner,owner)
   finally:gateway._raw_context.reset(token)
  self.assertNotEqual(tickets[0].nonce,tickets[1].nonce);self.assertNotEqual(tickets[0].tenant,tickets[1].tenant)
  self.assertNotEqual(tickets[0].nonce,'caller-chosen-same-ID');self.assertNotEqual(tickets[0].tenant,'caller-claimed-peer')

class GatewayAccountingCPU(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  from types import SimpleNamespace
  import mtp2_shared_gateway as gateway
  from compat_api.llama_guard import create_app
  import httpx
  self.gateway=gateway;self.httpx=httpx;self.secret='synthetic-usage-backend-0123456789'
  owner=SimpleNamespace(current_owner=lambda:None,matches=lambda _:False,exited=lambda _:False)
  async def proof(*args):return True
  app=create_app(manager_url='http://127.0.0.1:19090',backend_token=self.secret,control_token='synthetic-control',registry=SimpleNamespace(pending_limit=8),resource_probe=proof,
   shared_guard_factory=lambda **kw:gateway.make_shared_guard(owner_probe=owner,**kw))
  self.guard=app.state.lifecycle_guard;self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://guard.test')
  self.addAsyncCleanup(self.client.aclose)
  _,ticket,usage,*_=fixture();self.usage=usage;self.ticket=ticket
  self.guard.terminal_usage[ticket.nonce]=(asyncio.get_running_loop().time()+60,SimpleNamespace(**usage))
 def query(self):return {k:self.usage[k] for k in ('nonce','tenant','alias','profile','epoch','response_id')}
 async def test_backend_auth_exact_nonce_tenant_epoch_responseID_and_no_public_access(self):
  response=await self.client.post(a.USAGE_PATH,json=self.query());self.assertEqual(response.status_code,403)
  response=await self.client.post(a.USAGE_PATH,json=self.query(),headers={'Authorization':'Bearer '+self.secret});self.assertEqual(response.status_code,200);self.assertEqual(response.json(),self.usage)
  self.assertEqual(response.headers['cache-control'],'no-store')
  for field,bad in (('nonce','d'*32),('tenant','d'*64),('epoch','d'*64),('response_id','different'),('alias','other'),('profile','other')):
   query=self.query();query[field]=bad
   response=await self.client.post(a.USAGE_PATH,json=query,headers={'Authorization':'Bearer '+self.secret});self.assertEqual(response.status_code,503)
 async def test_expired_and_unknown_native_usage_refuse_and_never_forward_upstream(self):
  nonce=self.ticket.nonce
  for entry in ((0,self.guard.terminal_usage[nonce][1]),(asyncio.get_running_loop().time()+60,None)):
   self.guard.terminal_usage[nonce]=entry
   response=await self.client.post(a.USAGE_PATH,json=self.query(),headers={'Authorization':'Bearer '+self.secret});self.assertEqual(response.status_code,503)
  response=await self.client.post(a.USAGE_PATH,content=b'x'*1025,headers={'Authorization':'Bearer '+self.secret});self.assertEqual(response.status_code,400)
 async def test_private_epoch_never_leaks_as_LiteLLM_provider_header(self):
  from types import SimpleNamespace
  sent=[];messages=[{'type':'http.request','body':json.dumps({'model':ALIAS,'messages':[]}).encode(),'more_body':False}]
  async def receive():return messages.pop(0) if messages else {'type':'http.disconnect'}
  async def send(message):sent.append(message)
  async def app(scope,receive,send):
   await receive();await send({'type':'http.response.start','status':200,'headers':[(a.EPOCH_HEADER.encode(),EPOCH.encode()),(('llm_provider-'+a.EPOCH_HEADER).encode(),EPOCH.encode()),(b'content-type',b'text/event-stream')]});await send({'type':'http.response.body','body':b''})
  wrapper=self.gateway.RawMtp2Ingress(app,None,capture=True,owner=object())
  with patch.object(self.gateway,'validate_raw_ingress',return_value=({'model':ALIAS},SimpleNamespace())):
   await wrapper({'type':'http','method':'POST','path':'/v1/chat/completions','headers':[]},receive,send)
  self.assertEqual(sent[0]['headers'],[(b'content-type',b'text/event-stream')])


class SignedActorAccountingCPU(unittest.IsolatedAsyncioTestCase):
 async def test_signed_native_actor_through_private_guard_lookup_to_official_partial_dispatch(self):
  from test_mtp2_shared_usage import UsageTests
  import mtp2_shared_gateway as gateway
  from compat_api.llama_guard import create_app
  from types import SimpleNamespace
  import httpx
  native=UsageTests();native.setUp();self.addCleanup(native.doCleanups)
  handle=native.infer(key='sk-synthetic-own-A').handles[0]
  signed=native.status((handle,),(native.final(handle),)).records[0]
  usage=native.actor.verified_usage(handle,signed,'chatcmpl-1000-1')
  native.actor.forget_terminal(handle,signed)
  owner,_,_,logger,seen,response,data,_,order=fixture()
  ticket=a.UsageTicket(handle.nonce,handle.tenant,handle.alias,handle.profile,owner)
  response.chunks[0].id=usage.response_id;response._response_headers[a.EPOCH_HEADER]=handle.epoch
  secret='synthetic-accounting-backend-0123456789'
  probe=SimpleNamespace(current_owner=lambda:None,matches=lambda _:False,exited=lambda _:False)
  async def proof(*args):return True
  app=create_app(manager_url='http://127.0.0.1:19090',backend_token=secret,control_token='synthetic-control',registry=SimpleNamespace(pending_limit=8),resource_probe=proof,
   shared_guard_factory=lambda **kw:gateway.make_shared_guard(owner_probe=probe,**kw))
  app.state.lifecycle_guard.terminal_usage[handle.nonce]=(asyncio.get_running_loop().time()+60,usage)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://guard.test') as client:
   async def resolve(t,epoch,response_id):
    result=await client.post(a.USAGE_PATH,headers={'Authorization':'Bearer '+secret},json={'epoch':epoch,'nonce':t.nonce,'tenant':t.tenant,'alias':t.alias,'profile':t.profile,'response_id':response_id})
    self.assertEqual(result.status_code,200);return result.json()
   with patch.object(litellm,'disable_streaming_logging',False):
    self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
    self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
  self.assertEqual(len(seen),1);self.assertEqual(seen[0].id,'chatcmpl-1000-1');self.assertEqual(data['litellm_call_id'],'actual-LP-own-call-id')
  u=seen[0].usage;self.assertEqual((u.prompt_tokens,u.completion_tokens,u.total_tokens),(4096,200,4296));self.assertEqual(u.completion_tokens_details.reasoning_tokens,187);self.assertEqual(u.prompt_tokens_details.cached_tokens,4095)
  self.assertNotEqual(u.completion_tokens,usage.drafted_tokens);self.assertEqual(order,['close'])
 async def test_signed_wrong_tenant_commitment_refused_before_billing(self):
  from test_mtp2_shared_usage import UsageTests
  from common import DistributionError
  native=UsageTests();native.setUp();self.addCleanup(native.doCleanups)
  a_handle=native.infer(key='sk-synthetic-A').handles[0];b_handle=native.infer(key='sk-synthetic-B').handles[0]
  row=native.final(a_handle);row['usage']['tenant_binding']=native.final(b_handle)['usage']['tenant_binding']
  with self.assertRaises(DistributionError):native.status((a_handle,),(row,))
 async def test_signed_zero_prefill_and_unknown_reasoning_native_modes_preserved(self):
  from test_mtp2_shared_usage import UsageTests
  native=UsageTests();native.setUp();self.addCleanup(native.doCleanups)
  for chat,response_id in ((True,'chatcmpl-1000-1'),(False,'cmpl-1000-1')):
   handle=native.infer(completion=not chat).handles[0];row=native.final(handle,chat=chat,response_id=response_id,completion=0,reasoning=0)
   row['usage']['finish_reason']='cancelled';row['usage']['usage'].pop('completion_tokens_details')
   if chat:row['usage']['usage']['completion_tokens_details']={'reasoning_tokens':0}
   record=native.status((handle,),(row,)).records[0];usage=native.actor.verified_usage(handle,record,response_id)
   owner,_,_,logger,seen,response,data,_,order=fixture();ticket=a.UsageTicket(handle.nonce,handle.tenant,handle.alias,handle.profile,owner)
   response.chunks=[];response._response_headers[a.EPOCH_HEADER]=handle.epoch
   async def resolve(*args):return {k:getattr(usage,k) for k in a._FIELDS}
   self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(seen[0].usage.completion_tokens,0)
   self.assertEqual(seen[0].id,response_id);self.assertEqual(seen[0].usage.prompt_tokens,4096)
   if not chat:self.assertTrue(seen[0].usage.completion_tokens_details is None or seen[0].usage.completion_tokens_details.reasoning_tokens is None)


class OfficialSpendPayloadCPU(unittest.IsolatedAsyncioTestCase):
 async def test_actual_dispatcher_dedup_and_DB_payload_keep_ownkey_counts_no_fake_native_ID(self):
  from datetime import datetime,timezone
  import hashlib
  from litellm.proxy.spend_tracking.spend_tracking_utils import get_logging_payload
  for native_id,completion,reasoning in (('chatcmpl-1000-1',1000,900),('chatcmpl-1000-2',0,0),('cmpl-1000-1',1000,None)):
   owner,ticket,value,_,_,response,data,_,order=fixture();value.update(response_id=native_id,completion_tokens=completion,reasoning_tokens=reasoning)
   response.chunks=[] if native_id is None else response.chunks
   if response.chunks:response.chunks[0].id=native_id
   ownhash=hashlib.sha256(b'sk-synthetic-owned-A').hexdigest();metadata={'user_api_key':ownhash,'user_api_key_hash':ownhash,'user_api_key_user_id':'CPU-own-A'}
   logger=Logging(model=ALIAS,messages=response.messages,stream=True,call_type='acompletion',start_time=datetime.now(timezone.utc),litellm_call_id=data['litellm_call_id'],function_id='CPU-only',supports_correlation_logging=False,
    kwargs={'metadata':metadata,'custom_llm_provider':'openai'})
   logger._response_cost_calculator=lambda **kw:0.0
   rows=[]
   async def sink(result,**kw):rows.append(get_logging_payload(logger.model_call_details,result,logger.start_time,datetime.now(timezone.utc)))
   logger.async_success_handler=sink;data['litellm_logging_obj']=logger
   async def resolve(*args):return value
   with patch.object(litellm,'success_callback',[]),patch.object(litellm,'disable_streaming_logging',False):
    self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official))
   self.assertEqual(len(rows),1);self.assertTrue(logger.model_call_details['has_dispatched_final_stream_success'])
   self.assertNotIn('dispatch_success_handlers',vars(logger));self.assertEqual(rows[0]['api_key'],ownhash)
   self.assertEqual((rows[0]['prompt_tokens'],rows[0]['completion_tokens'],rows[0]['total_tokens']),(150,completion,150+completion))
   self.assertEqual(rows[0]['request_id'],native_id)
   # The real signed native ID selects the row; caller LPcallID stays unchanged
   # and never becomes native identity or the spend-row fallback.
   self.assertEqual(response.chunks[-1].id,native_id)


class NativeRealIDRemedyCPU(unittest.IsolatedAsyncioTestCase):
 async def test_two_signed_owncallers_same_LPcallID_have_distinct_real_native_spend_rows(self):
  import hashlib
  from test_mtp2_shared_usage import UsageTests
  from litellm.proxy.spend_tracking.spend_tracking_utils import get_logging_payload
  from datetime import datetime,timezone
  native=UsageTests();native.setUp();self.addCleanup(native.doCleanups);rows=[]
  for i,key in enumerate(('sk-synthetic-A','sk-synthetic-B'),1):
   handle=native.infer(key=key).handles[0];native_id='chatcmpl-1000-'+str(i)
   row=native.final(handle,response_id=native_id,completion=0,reasoning=0);row['usage']['finish_reason']='cancelled';row['usage']['usage']['completion_tokens_details']={'reasoning_tokens':0}
   record=native.status((handle,),(row,)).records[0];usage=native.actor.verified_usage(handle,record,native_id)
   owner,_,_,logger,seen,response,data,_,order=fixture();ticket=a.UsageTicket(handle.nonce,handle.tenant,handle.alias,handle.profile,owner)
   response.chunks=[];data['litellm_call_id']='caller-same-ID';fp=hashlib.sha256(key.encode()).hexdigest()
   logger.model_call_details.update(litellm_call_id=data['litellm_call_id'],model=handle.alias,call_type='acompletion',litellm_params={'metadata':{'user_api_key':fp,'user_api_key_hash':fp}})
   async def resolve(*args):return {k:getattr(usage,k) for k in a._FIELDS}
   self.assertTrue(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));now=datetime.now(timezone.utc)
   rows.append(get_logging_payload(logger.model_call_details,seen[0],now,now));self.assertEqual(data['litellm_call_id'],'caller-same-ID')
   self.assertEqual((rows[-1]['prompt_tokens'],rows[-1]['completion_tokens']),(4096,0));self.assertEqual(rows[-1]['request_id'],native_id)
  self.assertNotEqual(rows[0]['request_id'],rows[1]['request_id']);self.assertNotEqual(rows[0]['api_key'],rows[1]['api_key'])
 async def test_historical_signed_NoneID_never_bills_even_with_known_prompt_or_caller_ID(self):
  from test_mtp2_shared_usage import UsageTests
  native=UsageTests();native.setUp();self.addCleanup(native.doCleanups)
  handle=native.infer().handles[0];row=native.final(handle,response_id=None,completion=0,reasoning=0);row['usage']['finish_reason']='cancelled';row['usage']['usage']['completion_tokens_details']={'reasoning_tokens':0}
  record=native.status((handle,),(row,)).records[0];usage=native.actor.verified_usage(handle,record)
  owner,_,_,logger,seen,response,data,_,order=fixture();ticket=a.UsageTicket(handle.nonce,handle.tenant,handle.alias,handle.profile,owner);response.chunks=[]
  async def resolve(*args):return {k:getattr(usage,k) for k in a._FIELDS}
  self.assertFalse(await a.bill_native_partial(ticket,owner,data,response,resolve=resolve,official_bill=official));self.assertEqual(seen,[]);self.assertEqual(response.chunks,[])


class PinnedSDKHeaderCPU(unittest.IsolatedAsyncioTestCase):
 async def test_guard_epoch_reaches_actual_LiteLLM_wrapper_before_any_role_or_token(self):
  import httpx,openai
  gate=asyncio.Event();closed=[];requests=[]
  class NoRoleStream(httpx.AsyncByteStream):
   async def __aiter__(self):
    await gate.wait()
    if False:yield b''
   async def aclose(self):closed.append(True)
  async def backend(request):
   requests.append(request.url.path)
   return httpx.Response(200,headers={'content-type':'text/event-stream',a.EPOCH_HEADER:EPOCH},stream=NoRoleStream())
  async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
   sdk=openai.AsyncOpenAI(api_key='synthetic-CPU-only',base_url='http://127.0.0.1:5402/v1',http_client=http)
   with patch.object(litellm,'telemetry',False),patch.object(litellm,'callbacks',[]),patch.object(litellm,'success_callback',[]),patch.object(litellm,'_async_success_callback',[]):
    response=await asyncio.wait_for(litellm.acompletion(model='openai/'+ALIAS,messages=[{'role':'user','content':'Synthetic prefilling.'}],stream=True,max_tokens=512,client=sdk),timeout=2)
   self.assertEqual(response._response_headers[a.EPOCH_HEADER],EPOCH)
   self.assertEqual(response._hidden_params['additional_headers']['llm_provider-'+a.EPOCH_HEADER],EPOCH)
   self.assertEqual(response.chunks,[]);self.assertEqual(requests,['/v1/chat/completions'])
   await response.aclose();self.assertTrue(closed)

if __name__=='__main__':unittest.main(verbosity=2)
