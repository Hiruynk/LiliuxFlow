# SPDX-License-Identifier: Apache-2.0
"""Native-only disconnected MTP2 accounting, before the official single dispatch."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from common import DistributionError

EPOCH_HEADER = 'x-liliuxflow-mtp2-usage-epoch'
USAGE_PATH = '/_liliuxflow/shared-usage'
_FIELDS = frozenset(('epoch','nonce','tenant','alias','profile','response_id','prompt_tokens','cached_tokens','completion_tokens','reasoning_tokens','drafted_tokens','accepted_tokens','finish_reason','cancelled'))

@dataclass(frozen=True, slots=True)
class UsageTicket:
    nonce: str
    tenant: str = field(repr=False)
    alias: str
    profile: str
    owner: object = field(repr=False, compare=False)


def native_response_id(response):
    ids={getattr(chunk,'id',None) for chunk in getattr(response,'chunks',())}
    ids.discard(None)
    if len(ids)>1 or any(type(value) is not str or not value or len(value)>256 for value in ids):
        raise DistributionError('shared_usage_response_identity_changed')
    return next(iter(ids),None)


def exact_usage(ticket, value, *, epoch, response_id):
    if type(ticket) is not UsageTicket or type(value) is not dict or set(value)!=_FIELDS:
        raise DistributionError('shared_usage_unknown_schema')
    expected={'epoch':epoch,'nonce':ticket.nonce,'tenant':ticket.tenant,'alias':ticket.alias,'profile':ticket.profile}
    if (type(epoch) is not str or len(epoch)!=64 or any(c not in '0123456789abcdef' for c in epoch)
            or any(type(value.get(k)) is not str or value[k]!=v for k,v in expected.items())):
        raise DistributionError('shared_usage_request_binding_changed')
    actual=value['response_id']
    # Final producer creates the SAME real native ID after exact session facts.
    # Historical pre-ID summaries cannot select a spend row through the caller's
    # x-litellm-call-id fallback; keep them explicitly unverified for billing.
    if (type(actual) is not str or not 0<len(actual)<=256
            or response_id is not None and actual!=response_id):
        raise DistributionError('shared_usage_response_identity_changed')
    for key in ('prompt_tokens','cached_tokens','completion_tokens','drafted_tokens','accepted_tokens'):
        if type(value[key]) is not int or not 0<=value[key]<2**53:
            raise DistributionError('shared_usage_token_count_unverified')
    reasoning=value['reasoning_tokens']
    if reasoning is not None and (type(reasoning) is not int or not 0<=reasoning<=value['completion_tokens']):
        raise DistributionError('shared_usage_reasoning_unverified')
    if value['cached_tokens']>value['prompt_tokens'] or value['accepted_tokens']>value['drafted_tokens'] or type(value['cancelled']) is not bool:
        raise DistributionError('shared_usage_counts_inconsistent')
    if type(value['finish_reason']) is not str or value['finish_reason'] not in ('stop','length','tool_calls','cancelled'):
        raise DistributionError('shared_usage_finish_unverified')
    return value


async def bill_native_partial(ticket, owner, request_data, response, *, resolve, official_bill):
    """No ticket -> exactly original MTP0 behavior. Unknown -> no estimated spend event.

    The official dispatcher still owns its call-ID dedup and successful slot
    release. Returning False on missing authority asks its cleanup caller to
    release the slot explicitly; no fabricated zero-token DB row is dispatched.
    """
    if ticket is None:
        return await official_bill(request_data,response)
    from litellm.litellm_core_utils.litellm_logging import Logging
    logging=request_data.get('litellm_logging_obj')
    if not isinstance(logging,Logging):return False
    if type(ticket) is not UsageTicket or ticket.owner is not owner:
        logging.model_call_details.setdefault('litellm_params',{}).setdefault('metadata',{})['liliuxflow_mtp2_native_usage']={'status':'UNVERIFIED_NATIVE_PARTIAL_USAGE'}
        return False
    if logging.model_call_details.get('has_dispatched_final_stream_success'):
        return await official_bill(request_data,response)
    try:
        requested=request_data.get('_litellm_client_requested_model',request_data.get('model'))
        if requested!=ticket.alias:raise DistributionError('shared_usage_logical_model_changed')
        response_id=native_response_id(response)
        headers=getattr(response,'_response_headers',None)
        epoch=headers.get(EPOCH_HEADER) if headers is not None else None
        if hasattr(response,'aclose'):
            await asyncio.wait_for(response.aclose(),timeout=5)
        value=await asyncio.wait_for(resolve(ticket,epoch,response_id),timeout=65)
        value=exact_usage(ticket,value,epoch=epoch,response_id=response_id)
    except (Exception,asyncio.CancelledError):
        metadata=logging.model_call_details.setdefault('litellm_params',{}).setdefault('metadata',{})
        metadata['liliuxflow_mtp2_native_usage']={'status':'UNVERIFIED_NATIVE_PARTIAL_USAGE'}
        return False
    from litellm.types.utils import ModelResponseStream, Usage
    details={} if value['reasoning_tokens'] is None else {'reasoning_tokens':value['reasoning_tokens']}
    usage=Usage(prompt_tokens=value['prompt_tokens'],completion_tokens=value['completion_tokens'],
        total_tokens=value['prompt_tokens']+value['completion_tokens'],
        prompt_tokens_details={'cached_tokens':value['cached_tokens']},completion_tokens_details=details)
    # Native final counts include all generated HIGH thinking; never drafts or
    # delivered-frame counts. Use the real native response ID, never LPcall_id.
    native_chunk=ModelResponseStream(id=value['response_id'],model=response.model,choices=[],usage=usage)
    native_chunk.id=value['response_id']  # Always the authenticated real native response ID.
    response.chunks.append(native_chunk)
    logging.model_call_details.setdefault('litellm_params',{}).setdefault('metadata',{})['liliuxflow_mtp2_native_usage']={'status':'VERIFIED_NATIVE_TERMINAL_USAGE','cancelled':value['cancelled']}
    # The builder estimates unknown reasoning and treats zero provider fields
    # as absent. Override only exact native usage immediately before its
    # original once-only dispatcher; caller metadata/cost semantics stay intact.
    absent=object()
    previous=vars(logging).get('dispatch_success_handlers',absent)
    dispatch=logging.dispatch_success_handlers
    async def native_dispatch(partial_response,**kwargs):
        partial_response.usage=usage
        partial_response.id=value['response_id']
        return await dispatch(partial_response,**kwargs)
    logging.dispatch_success_handlers=native_dispatch
    try:
        return await official_bill(request_data,response)
    finally:
        if previous is absent:del logging.dispatch_success_handlers
        else:logging.dispatch_success_handlers=previous
