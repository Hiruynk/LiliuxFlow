# SPDX-License-Identifier: Apache-2.0
"""Signed native terminal usage with synthetic authenticated callers, CPU only."""
import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import unittest

from common import DistributionError
import mtp2_shared_transport as transport
import test_mtp2_shared_transport as fixture


class UsageTests(unittest.TestCase):
    setUp=fixture.TransportTests.setUp
    payload=fixture.TransportTests.payload
    state=fixture.TransportTests.state
    reply=fixture.TransportTests.reply
    ready=fixture.TransportTests.ready
    infer=fixture.TransportTests.infer
    row=fixture.TransportTests.row
    status=fixture.TransportTests.status

    def test_exact_native_tenant_binding_vector(self):
        frame=transport._lp(transport.USAGE_DOMAIN,('a'*64,'00000000000000000000000000000001',
            'a'*64,transport.PUBLIC_ALIASES[0],transport.PROFILE_IDS[0]))
        self.assertEqual(hashlib.sha256(frame).hexdigest(),'47ea6109fd61f4720d2e327d00e1efab0d2e56d97d7996a024705ddf642c9cae')

    def final(self,handle,*,chat=True,response_id='chatcmpl-1000-1',completion=200,reasoning=187):
        row=self.row(handle,'released')
        value={'prompt_tokens':4096,'completion_tokens':completion,'total_tokens':4096+completion,
               'prompt_tokens_details':{'cached_tokens':4095},
               'completion_tokens_details':{'accepted_prediction_tokens':123,'rejected_prediction_tokens':177}}
        if chat:value['completion_tokens_details']['reasoning_tokens']=reasoning
        binding=hashlib.sha256(transport._lp(transport.USAGE_DOMAIN,(handle.epoch,handle.nonce,handle.tenant,handle.alias,handle.profile))).hexdigest()
        row['usage']={'v':1,'tenant_binding':binding,'response_id':response_id,
                      'finish_reason':'stop','cancelled':True,'usage':value}
        return row

    def test_high_counts_are_typed_frozen_native_scalars_and_survive_forget(self):
        h=self.infer().handles[0];row=self.final(h)
        record=self.status((h,),(row,)).records[0]
        usage=self.actor.verified_usage(h,record,'chatcmpl-1000-1')
        self.assertEqual((usage.prompt_tokens,usage.cached_tokens,usage.completion_tokens,usage.reasoning_tokens,
                          usage.drafted_tokens,usage.accepted_tokens),(4096,4095,200,187,300,123))
        self.assertEqual((usage.epoch,usage.nonce,usage.tenant,usage.alias,usage.profile),
                         (h.epoch,h.nonce,h.tenant,h.alias,h.profile))
        self.assertTrue(usage.cancelled)
        with self.assertRaises(FrozenInstanceError):usage.completion_tokens=0
        self.assertNotIn(h.tenant,repr(usage))
        self.actor.forget_terminal(h,record)
        self.assertEqual(usage.completion_tokens,200)
        with self.assertRaises(DistributionError):self.actor.verified_usage(h,record)

    def test_actual_zero_prefill_may_have_no_id_or_created_role_id(self):
        for response_id in (None,'chatcmpl-1000-1'):
            h=self.infer().handles[0];row=self.final(h,response_id=response_id,completion=0,reasoning=0)
            row['usage']['finish_reason']='cancelled'
            row['usage']['usage']['completion_tokens_details']={'reasoning_tokens':0}
            record=self.status((h,),(row,)).records[0]
            usage=self.actor.verified_usage(h,record,response_id)
            self.assertEqual(usage.completion_tokens,0);self.assertEqual(usage.response_id,response_id)

    def test_completion_preserves_native_unknown_reasoning_without_text_estimator(self):
        h=self.infer(completion=True).handles[0];row=self.final(h,chat=False,response_id='cmpl-1000-1')
        record=self.status((h,),(row,)).records[0]
        self.assertIsNone(self.actor.verified_usage(h,record).reasoning_tokens)

    def test_missing_old_abi_bad_mac_unknown_or_nonterminal_never_yields_usage(self):
        h=self.infer().handles[0]
        for phase in ('preparing','queued','acquired','cleanup','rejected_without_native_state'):
            row=self.row(h,phase)
            with self.subTest(phase=phase):
                # Use a fresh handle for every phase to preserve lifecycle order.
                handle=self.infer().handles[0];row=self.row(handle,phase)
                record=self.status((handle,),(row,)).records[0]
                self.assertIsNone(self.actor.verified_usage(handle,record))
                row['usage']=self.final(handle)['usage']
                with self.assertRaises(DistributionError):self.status((handle,),(row,))
        row=self.final(h);old=copy.deepcopy(row);del old['usage']
        with self.assertRaises(DistributionError):self.status((h,),(old,))
        wire=self.actor.status_request(self.binding,(h,))
        reply=self.reply(wire,{'v':1,'epoch':h.epoch,'records':[row]})
        headers=tuple((k,'0'*64 if k.endswith('-mac') else v) for k,v in reply.headers)
        with self.assertRaises(DistributionError):self.actor.verify_response(wire,replace(reply,headers=headers))
        with self.assertRaises(transport.UnknownNativeRecord):self.status((h,),({'nonce':h.nonce,'phase':'unknown'},))

    def test_peer_tenant_epoch_nonce_alias_and_response_id_binding_refuse(self):
        a=self.infer(key='sk-synthetic-A').handles[0];b=self.infer(1,key='sk-synthetic-B').handles[0]
        for change in ({'tenant':b.tenant},{'epoch':'b'*64},{'nonce':b.nonce},{'alias':b.alias,'profile':b.profile}):
            row=self.final(a);fake=replace(a,**change)
            row['usage']['tenant_binding']=self.final(fake)['usage']['tenant_binding']
            with self.subTest(change=tuple(change)),self.assertRaises(DistributionError):self.status((a,),(row,))
        record=self.status((a,),(self.final(a),)).records[0]
        with self.assertRaises(DistributionError):self.actor.verified_usage(a,record,'chatcmpl-1000-2')
        with self.assertRaises(DistributionError):self.actor.verified_usage(b,record)
        with self.assertRaises(DistributionError):self.actor.verified_usage(a,replace(record))

    def test_scalar_schema_unknown_or_estimated_fields_boolean_and_limits_refuse(self):
        h=self.infer().handles[0]
        bad=[]
        for key,value in [('prompt_tokens',97),('completion_tokens',True),('completion_tokens',65537),
                          ('total_tokens',0),('prompt_tokens_details',{'cached_tokens':4097}),
                          ('completion_tokens_details',None),('completion_tokens_details',{'reasoning_tokens':201}),
                          ('completion_tokens_details',{'reasoning_tokens':187,'accepted_prediction_tokens':1}),
                          ('completion_tokens_details',{'reasoning_tokens':187,'raw_text':'forbidden'})]:
            row=self.final(h);row['usage']['usage'][key]=value;bad.append(row)
        for key,value in [('v',True),('cancelled',1),('finish_reason','unknown'),('response_id',None),
                          ('response_id','cmpl-1000-1'),('response_id','chatcmpl-01-1'),('response_id','chatcmpl-1-18446744073709551616')]:
            row=self.final(h);row['usage'][key]=value;bad.append(row)
        row=self.final(h);row['usage']['estimated_tokens']=200;bad.append(row)
        for index,row in enumerate(bad):
            with self.subTest(index=index),self.assertRaises(DistributionError):self.status((h,),(row,))

    def test_terminal_counts_are_immutable_and_latest_actor_snapshot_required(self):
        h=self.infer().handles[0];row=self.final(h)
        original=self.status((h,),(row,)).records[0]
        latest=self.status((h,),(row,)).records[0]
        with self.assertRaises(DistributionError):self.actor.verified_usage(h,original)
        self.assertEqual(self.actor.verified_usage(h,latest).completion_tokens,200)
        for revision in (1,2):
            changed=copy.deepcopy(row);changed['revision']=revision;changed['usage']['usage']['completion_tokens']=201
            changed['usage']['usage']['total_tokens']=4297
            with self.assertRaises(DistributionError):self.status((h,),(changed,))

    def test_peer_batch_failure_commits_no_usage_or_lifecycle_snapshot(self):
        a=self.infer().handles[0];b=self.infer(1).handles[0]
        bad=self.final(b);bad['usage']['tenant_binding']='0'*64
        with self.assertRaises(DistributionError):self.status((a,b),(self.final(a),bad))
        self.assertNotIn((a.epoch,a.nonce),self.actor._verified_records)
        self.assertNotIn((b.epoch,b.nonce),self.actor._verified_records)


if __name__=='__main__':unittest.main(verbosity=2)
