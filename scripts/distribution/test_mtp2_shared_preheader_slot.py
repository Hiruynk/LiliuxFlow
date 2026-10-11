"""Actual pinned logical-slot cleanup/TTL methods, synthetic cache/context only."""
import asyncio,copy,datetime,sys,unittest
from dataclasses import replace
from unittest.mock import patch
import test_mtp2_shared_preheader as t
from litellm.proxy.utils import ProxyLogging
from litellm.proxy.hooks import parallel_request_limiter_v3 as limits

class Cache:
    def __init__(self,value):self.value=value;self.writes=[]
    async def async_get_cache(self,**kwargs):return copy.deepcopy(self.value)
    async def async_set_cache(self,**kwargs):self.value=copy.deepcopy(kwargs['value']);self.writes.append(kwargs)

def bound_fixture(value=None):
    owner,auth,log,ticket,usage,data,seen=t.fixture()
    acquisition=limits.ParallelSlotAcquisition(slot_id='0'*32,counter_keys=['CPU-key-gauge'])
    stash=limits.RequestRateLimiterStash(owner_litellm_call_id=data['litellm_call_id'],parallel_slot=acquisition,
        reserved_tokens=999,itpm_reserved_tokens=777,otpm_reserved_tokens=222,reservation_released=False)
    limiter=object.__new__(limits._PROXY_MaxParallelRequestsHandler_v3);limiter.parallel_release_script=None
    limiter._check_and_increment_lock=asyncio.Lock();limiter.internal_usage_cache=Cache(value if value is not None else {'0'*32:1000.,'1'*32:1000.})
    proxy=object.__new__(ProxyLogging);proxy.proxy_hook_mapping={'parallel_request_limiter':limiter}
    ticket=replace(ticket,rate_stash=stash,slot_acquisition=acquisition,slot_snapshot=('0'*32,('CPU-key-gauge',)),rate_limiter=limiter)
    return owner,auth,log,ticket,usage,data,seen,stash,limiter,proxy

class SlotPolicyCPU(unittest.IsolatedAsyncioTestCase):
    async def test_release_missing_usage_or_ID_noforward_and_GPUunknown_only_logical_slot_once_no_refund(self):
        # All three native situations are deliberately unverified for billing;
        # exact ended logical request owns this cleanup, never a GPU-free claim.
        for case in ('released_no_ID','no_forward','GPU_release_unknown'):
            owner,auth,log,ticket,usage,data,seen,stash,limiter,proxy=bound_fixture()
            token=limits._request_stash.set(stash)
            try:
                async def resolve(*_):
                    if case=='released_no_ID':return {**usage,'response_id':None}
                    if case=='no_forward':return None
                    raise asyncio.TimeoutError()
                result=await t.a.bill_native_preheader(ticket,owner,data,auth,t.HTTPException(499,t._CLIENT_DISCONNECT_DETAIL),resolve=resolve,official_bill=t.official,proxy_logging=proxy)
                self.assertFalse(result);self.assertEqual(seen,[]);self.assertIsNone(stash.parallel_slot)
                self.assertEqual(limiter.internal_usage_cache.value,{'1'*32:1000.})
                self.assertEqual((stash.reserved_tokens,stash.itpm_reserved_tokens,stash.otpm_reserved_tokens,stash.reservation_released),(999,777,222,False))
                meta=log.model_call_details['litellm_params']['metadata']['liliuxflow_mtp2_native_usage']
                self.assertEqual(meta['logical_slot'],'RELEASED_LOGICAL_SLOT_ONLY');self.assertFalse(meta['native_release_claim']);self.assertFalse(meta['TPM_reservation_refunded'])
                await t.a.release_preheader_slot_only(ticket,data,auth,proxy)
                self.assertEqual(len(limiter.internal_usage_cache.writes),1)
            finally:limits._request_stash.reset(token)
    async def test_integer_mirror_slot_cleanup_once_does_not_free_second_request(self):
        owner,auth,log,ticket,usage,data,seen,stash,limiter,proxy=bound_fixture(2)
        token=limits._request_stash.set(stash)
        try:
            self.assertEqual(await t.a.release_preheader_slot_only(ticket,data,auth,proxy),'RELEASED_LOGICAL_SLOT_ONLY')
            self.assertEqual(limiter.internal_usage_cache.value,1)
            self.assertEqual(await t.a.release_preheader_slot_only(ticket,data,auth,proxy),'NOT_ACQUIRED_OR_ALREADY_RELEASED')
            self.assertEqual(limiter.internal_usage_cache.value,1);self.assertEqual(len(limiter.internal_usage_cache.writes),1)
        finally:limits._request_stash.reset(token)
        owner,auth,log,ticket,usage,data,seen,stash,limiter,proxy=bound_fixture(2)
        token=limits._request_stash.set(stash)
        try:
            async def interrupted_release(response,**kwargs):
                await limiter._release_parallel_request_slots(stash.parallel_slot)
                raise asyncio.CancelledError() # interruption before the SDK clears its stash
            log.async_success_handler=interrupted_release
            async def resolve(*_):return usage
            self.assertFalse(await t.a.bill_native_preheader(ticket,owner,data,auth,t.HTTPException(499,t._CLIENT_DISCONNECT_DETAIL),resolve=resolve,official_bill=t.official,proxy_logging=proxy))
            self.assertEqual(limiter.internal_usage_cache.value,1);self.assertEqual(len(limiter.internal_usage_cache.writes),1)
            meta=log.model_call_details['litellm_params']['metadata']['liliuxflow_mtp2_native_usage']
            self.assertEqual(meta['logical_slot'],'OFFICIAL_DISPATCH_RELEASE_UNVERIFIED');self.assertTrue(meta['logical_cleanup_unverified'])
        finally:limits._request_stash.reset(token)
    async def test_changed_stash_acquisition_counter_keys_or_handler_never_releases_peer(self):
        for case in ('stash','call_id','acquisition','keys','handler'):
            owner,auth,log,ticket,usage,data,seen,stash,limiter,proxy=bound_fixture()
            current=stash
            if case=='stash':current=limits.RequestRateLimiterStash(owner_litellm_call_id=data['litellm_call_id'],parallel_slot=stash.parallel_slot)
            if case=='call_id':stash.owner_litellm_call_id='CPU-peer-call'
            if case=='acquisition':stash.parallel_slot=dict(stash.parallel_slot)
            if case=='keys':stash.parallel_slot['counter_keys']=['CPU-peer-key']
            if case=='handler':proxy.proxy_hook_mapping['parallel_request_limiter']=object()
            token=limits._request_stash.set(current)
            try:
                status=await t.a.release_preheader_slot_only(ticket,data,auth,proxy)
                self.assertTrue(status.startswith('UNVERIFIED_'));self.assertEqual(limiter.internal_usage_cache.writes,[])
            finally:limits._request_stash.reset(token)
    async def test_pinned_3600_acquire_timestamp_pruning_not_extended_by_new_traffic(self):
        *_,limiter,proxy=bound_fixture();clock=[1000.]
        limiter._get_current_time=lambda:datetime.datetime.fromtimestamp(clock[0],datetime.timezone.utc)
        gauge={'counter_key':'CPU-key-gauge','limit':100,'descriptor_key':'api_key','descriptor_value':'CPU-key'}
        # Repeated writes renew cache TTL, but cannot rewrite the old slot's acquire time.
        for now in (2000.,3000.,4599.,4600.001):
            clock[0]=now
            await limiter._acquire_parallel_slots_in_memory([gauge],str(int(now)).zfill(32))
        self.assertNotIn('0'*32,limiter.internal_usage_cache.value)
        self.assertEqual(limits.PARALLEL_REQUEST_SLOT_TTL_SECONDS,3600)
        self.assertTrue(all(write['ttl']==3600 for write in limiter.internal_usage_cache.writes))

if __name__=='__main__':unittest.main(verbosity=2)
