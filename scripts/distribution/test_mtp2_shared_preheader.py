"""Pinned SDK/native signed CPU fixtures; no DB, keys, socket or models."""
import os
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
import asyncio,copy,json,sys,unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path[:0]=[str(HERE),str(ROOT/'services/compat/src')]
import mtp2_shared_usage_accounting as a
import mtp2_shared_gateway as gateway
import litellm
from fastapi import HTTPException
from litellm.proxy._types import UserAPIKeyAuth
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.proxy.common_request_processing import _bill_partial_streamed_spend_on_disconnect as official,_CLIENT_DISCONNECT_DETAIL
import test_mtp2_shared_usage as native_fixtures

def fixture():
    owner=object();auth=UserAPIKeyAuth(api_key='CPU-keyhash',models=['qwen3.8-flash-next-lily-q4-mtp2-64k'],via_virtual_key=True)
    auth.via_virtual_key=True # Explicit CPU auth fixture, same server-only post-construction marker.
    log=object.__new__(Logging);log.model=auth.models[0];log.model_call_details={};log.stream=True
    log.call_type='acompletion'
    log._response_cost_calculator=lambda **_:0.0;seen=[]
    async def success(response,**kwargs):seen.append(response)
    log.async_success_handler=success;log._should_run_sync_callbacks_for_async_calls=lambda:False
    log._is_sync_litellm_request=lambda _:False
    ticket=a.UsageTicket('b'*32,'c'*64,log.model,'ctx64k-mtp2',owner,'d'*64,log,auth)
    usage={'epoch':'a'*64,'nonce':ticket.nonce,'tenant':ticket.tenant,'alias':ticket.alias,'profile':ticket.profile,
        'response_id':'chatcmpl-native-CPU','prompt_tokens':159,'cached_tokens':0,'completion_tokens':0,
        'reasoning_tokens':0,'drafted_tokens':0,'accepted_tokens':0,'finish_reason':'cancelled','cancelled':True}
    data={'model':ticket.alias,'litellm_call_id':'CPU-caller-selected-ID','litellm_logging_obj':log}
    return owner,auth,log,ticket,usage,data,seen

class PreheaderCPU(unittest.IsolatedAsyncioTestCase):
    async def test_instance_interception_before_stock_failure_pop_no_duplicate_slot_owner_MTP0_delegates(self):
        from litellm.proxy.utils import ProxyLogging
        from litellm.integrations.custom_logger import CustomLogger
        import litellm.proxy.common_request_processing as cleanup
        import httpx
        owner,auth,log,ticket,usage,data,seen=fixture();stock=[];statuses=[]
        class FailureSpy(CustomLogger):
            async def async_post_call_failure_hook(self,request_data,**kwargs):stock.append('litellm_logging_obj' in request_data)
        proxy=object.__new__(ProxyLogging);proxy.alert_types=[];proxy._is_proxy_only_llm_api_error=lambda **_:False
        async def update(**kwargs):statuses.append(kwargs)
        proxy.update_request_status=update
        boundary=object.__new__(gateway._PrivateRouteBoundary);boundary.server=SimpleNamespace(proxy_logging_obj=proxy)
        boundary._callback=gateway.shared_callback;boundary.guard_port=29090
        boundary.assert_live_routes=lambda:None
        original_hook=cleanup._bill_partial_streamed_spend_on_disconnect;original_config=gateway._callback_config
        self.addCleanup(setattr,cleanup,'_bill_partial_streamed_spend_on_disconnect',original_hook)
        self.addCleanup(setattr,gateway,'_callback_config',original_config)
        boundary.install_callback_boundary();gateway._callback_config=(None,b'CPU-backend-secret-0123456789',owner,boundary)
        other=object.__new__(ProxyLogging)
        self.assertIs(other.post_call_failure_hook.__func__,ProxyLogging.post_call_failure_hook)
        self.assertIs(proxy.post_call_failure_hook.__self__,proxy)
        async def provider(request):
            query=json.loads(request.content);self.assertIsNone(query['epoch']);self.assertEqual(query['claim_sha256'],ticket.claim_sha256)
            return httpx.Response(200,json=usage)
        actual_client=httpx.AsyncClient
        def client(*args,**kwargs):return actual_client(transport=httpx.MockTransport(provider))
        capture=gateway._RawCapture(ticket.alias,b'{}',(),owner);capture.accounting.append(ticket)
        token=gateway._raw_context.set(capture)
        try:
            from litellm.proxy.common_request_processing import _await_llm_call_cancelling_on_disconnect
            async def provider_pending():await asyncio.Event().wait()
            async def disconnected():return {'type':'http.disconnect'}
            response=None;pending=asyncio.create_task(provider_pending())
            with self.assertRaises(HTTPException) as caught:
                response=await _await_llm_call_cancelling_on_disconnect(SimpleNamespace(receive=disconnected),pending)
            self.assertIsNone(response);self.assertTrue(pending.cancelled());self.assertEqual(caught.exception.status_code,499)
            with patch.object(httpx,'AsyncClient',side_effect=client),patch.object(litellm,'callbacks',[FailureSpy()]),patch.object(litellm,'disable_streaming_logging',False):
                for _ in range(2):
                    self.assertIsNone(await proxy.post_call_failure_hook(data,caught.exception,auth))
            self.assertEqual(stock,[]);self.assertEqual(statuses,[]);self.assertEqual(len(seen),1)
            self.assertIn('litellm_logging_obj',data)
        finally:gateway._raw_context.reset(token)
        # Unselected/MTP0 delegates to the actual pinned stock failure method.
        legacy={'model':'CPU-MTP0','litellm_logging_obj':log,'litellm_call_id':'CPU-legacy'}
        with patch.object(litellm,'callbacks',[FailureSpy()]):
            await proxy.post_call_failure_hook(legacy,HTTPException(499,_CLIENT_DISCONNECT_DETAIL),auth)
        self.assertEqual(stock,[False]);self.assertEqual(len(statuses),1);self.assertNotIn('litellm_logging_obj',legacy)

    async def test_authoritative_prompt_only_before_headers_original_dispatch_once(self):
        owner,auth,log,ticket,usage,data,seen=fixture();calls=[]
        async def resolve(*args):calls.append(args);return usage
        exception=HTTPException(499,_CLIENT_DISCONNECT_DETAIL)
        with patch.object(litellm,'disable_streaming_logging',False):
            answers=await asyncio.gather(*(a.bill_native_preheader(ticket,owner,data,auth,exception,resolve=resolve,official_bill=official)for _ in range(2)))
        self.assertEqual(answers,[True,True]);self.assertEqual(len(calls),1);self.assertEqual(calls[0][1:],(None,None))
        self.assertEqual(len(seen),1);self.assertEqual(seen[0].id,usage['response_id'])
        self.assertEqual((seen[0].usage.prompt_tokens,seen[0].usage.completion_tokens,seen[0].usage.total_tokens),(159,0,159))
        self.assertEqual(data['litellm_call_id'],'CPU-caller-selected-ID')
    async def test_exact_types_owners_logger_auth_alias_499_and_detail_required(self):
        owner,auth,log,ticket,usage,data,seen=fixture()
        async def forbidden(*_):raise AssertionError('NO_AUTHORITY_QUERY')
        variants=[(None,owner,data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,object(),data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,owner,{**data,'litellm_logging_obj':object()},auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,owner,data,auth.model_copy(),HTTPException(499,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,owner,{**data,'model':'foreign'},auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,owner,data,auth,HTTPException(503,_CLIENT_DISCONNECT_DETAIL)),
            (ticket,owner,data,auth,HTTPException(499,'caller-controlled-detail')),
            (replace(ticket,claim_sha256=None),owner,data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL))]
        for args in variants:
            with self.subTest(args=args[-1].status_code):
                self.assertIsNone(await a.bill_native_preheader(*args,resolve=forbidden,official_bill=official))
        self.assertEqual(seen,[])
    async def test_unknown_drift_or_no_real_native_ID_never_dispatches_zero_or_refunds(self):
        faults=[None,{},'timeout',{'tenant':'f'*64},{'nonce':'f'*32},{'alias':'foreign'},
            {'profile':'foreign'},{'response_id':None},{'cancelled':'unknown'},{'completion_tokens':True}]
        for fault in faults:
            owner,auth,log,ticket,usage,data,seen=fixture()
            async def resolve(*_):
                if fault=='timeout':raise asyncio.TimeoutError()
                return ({**usage,**fault}if type(fault)is dict and fault else fault)
            with self.subTest(fault=fault):
                self.assertFalse(await a.bill_native_preheader(ticket,owner,data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL),resolve=resolve,official_bill=official))
                self.assertEqual(seen,[]);self.assertFalse(log.model_call_details.get('has_dispatched_final_stream_success',False))
                self.assertEqual(log.model_call_details['litellm_params']['metadata']['liliuxflow_mtp2_native_usage']['status'],'UNVERIFIED_NATIVE_PREHEADER_USAGE')
        # Native completion may beat the disconnect while SDK headers are still pending.
        owner,auth,log,ticket,usage,data,seen=fixture();usage.update(cancelled=False,finish_reason='length',completion_tokens=200,reasoning_tokens=187)
        async def resolve(*_):return usage
        self.assertTrue(await a.bill_native_preheader(ticket,owner,data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL),resolve=resolve,official_bill=official))
        self.assertEqual(seen[0].usage.completion_tokens,200)
    async def test_signed_original_handle_epoch_after_release_guard_claim_lookup_and_billing(self):
        from compat_api.llama_guard import create_app
        import httpx
        native=native_fixtures.UsageTests();native.setUp();self.addCleanup(native.doCleanups)
        handle=native.infer(key='sk-CPU-own').handles[0]
        row=native.final(handle,completion=0,reasoning=0);row['usage']['finish_reason']='cancelled'
        record=native.status((handle,),(row,)).records[0];usage=native.actor.verified_usage(handle,record)
        native.actor.forget_terminal(handle,record)
        owner,auth,log,ticket,_,data,seen=fixture()
        ticket=replace(ticket,nonce=handle.nonce,tenant=handle.tenant,alias=handle.alias,profile=handle.profile)
        secret='CPU-preheader-backend-credential-0123456789'
        probe=SimpleNamespace(current_owner=lambda:None,matches=lambda _:False,exited=lambda _:False)
        async def proof(*_):return True
        app=create_app(manager_url='http://127.0.0.1:19090',backend_token=secret,control_token='CPU-control',registry=SimpleNamespace(pending_limit=8),resource_probe=proof,
            shared_guard_factory=lambda **kw:gateway.make_shared_guard(owner_probe=probe,**kw))
        guard=app.state.lifecycle_guard;guard.native_binding=SimpleNamespace(epoch='f'*64) # Later/latest epoch must never select this request.
        guard.terminal_usage[handle.nonce]=(asyncio.get_running_loop().time()+60,usage,ticket.claim_sha256)
        query={'nonce':ticket.nonce,'tenant':ticket.tenant,'alias':ticket.alias,'profile':ticket.profile,
            'epoch':None,'response_id':None,'claim_sha256':ticket.claim_sha256}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://guard.test')as client:
            response=await client.post(a.USAGE_PATH,json=query);self.assertEqual(response.status_code,403)
            for bad in ({'tenant':'f'*64},{'nonce':'f'*32},{'claim_sha256':'f'*64},{'epoch':'f'*64},{'profile':'foreign'}):
                response=await client.post(a.USAGE_PATH,json={**query,**bad},headers={'Authorization':'Bearer '+secret})
                self.assertEqual(response.status_code,503)
            async def resolve(*_):
                response=await client.post(a.USAGE_PATH,json=query,headers={'Authorization':'Bearer '+secret})
                self.assertEqual(response.status_code,200);return response.json()
            settled=asyncio.get_running_loop().create_future()
            guard.shared_rows[handle.nonce]=SimpleNamespace(handle=handle,claim_sha256=ticket.claim_sha256,settled=settled)
            pending=asyncio.create_task(client.post(a.USAGE_PATH,json=query,headers={'Authorization':'Bearer '+secret}))
            await asyncio.sleep(.01);self.assertFalse(pending.done())
            settled.set_result(False);self.assertEqual((await pending).status_code,503)
            # Same nonce with a new admission claim cannot expose old epoch usage.
            guard.shared_rows[handle.nonce]=SimpleNamespace(handle=handle,claim_sha256='f'*64,settled=settled)
            response=await client.post(a.USAGE_PATH,json=query,headers={'Authorization':'Bearer '+secret})
            self.assertEqual(response.status_code,503)
            guard.shared_rows.pop(handle.nonce)
            self.assertTrue(await a.bill_native_preheader(ticket,owner,data,auth,HTTPException(499,_CLIENT_DISCONNECT_DETAIL),resolve=resolve,official_bill=official))
        self.assertEqual(len(seen),1);self.assertEqual(seen[0].id,usage.response_id);self.assertEqual(seen[0].usage.prompt_tokens,4096)
        self.assertEqual(seen[0].usage.completion_tokens,0)

if __name__=='__main__':unittest.main(verbosity=2)
