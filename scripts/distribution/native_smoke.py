#!/usr/bin/env python3
"""Bounded own-key Q4 smoke; only run after reviewed archive build/full verification with exclusive model access."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.request import Request,build_opener,ProxyHandler
from common import DistributionError,no_symlinks,read_object,write_json_new
from trust import validate,private_file
from ownership import unchanged,descendants
from client_example import request_parts
import agent

DEFAULT_PORTS={4000,8001,8080,8081,15432}
def model_lease_path():return Path.home()/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease'

def preflight(trusted,source_commit,recipe_sha256):
    if trusted['checkpoint'].get('payload_verified') is not True or trusted['checkpoint'].get('validation')=='metadata_only':raise DistributionError('metadata-only checkpoint cannot count as real Q4')
    if trusted['trust'].get('source_commit')!=source_commit or trusted['trust'].get('ui_recipe_sha256')!=recipe_sha256:raise DistributionError('actual source/recipe trust differs from reviewed archive')
    caller=read_object(private_file(trusted['data_root']/'secrets/caller.json')) if (trusted['data_root']/'secrets/caller.json').exists() else None
    if caller is not None and caller.get('user_id')!='local-'+trusted['config']['installation_id']:raise DistributionError('caller is outside own installation identity')
    if set(trusted['config']['ports'].values())&DEFAULT_PORTS:raise DistributionError('acceptance smoke refuses original/default ports')
    if model_lease_path().exists():raise DistributionError('portable model instance lease is occupied; no second model allowed')
    for port in trusted['config']['ports'].values():
        result=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True)
        if result.returncode!=1:raise DistributionError('owned smoke port is occupied or occupancy is unknown')

def inference_parts(trusted,api,stream):
    caller=read_object(private_file(trusted['data_root']/'secrets/caller.json'))
    if caller.get('user_id')!='local-'+trusted['config']['installation_id']:raise DistributionError('caller is outside own installation identity')
    url,body,key=request_parts(trusted['data_root'],api,stream,'Reply with the single word OK.')
    if key==trusted['secrets']['LITELLM_MASTER_KEY']:raise DistributionError('master credentials cannot serve inference')
    return url,body,key

def openai(trusted):
    url,body,key=inference_parts(trusted,'openai',False)
    if key==trusted['secrets']['LITELLM_MASTER_KEY']:raise DistributionError('master credentials cannot serve inference')
    body['max_tokens']=4096
    status,response=agent.http_json(url,method='POST',payload=body,token=key,timeout=600)
    if status!=200 or not isinstance(response,dict):raise DistributionError('own LiteLLM inference failed')
    tokens=response.get('usage',{}).get('completion_tokens')
    choices=response.get('choices',[])
    if type(tokens)is not int or tokens<=0 or not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict) or choices[0].get('finish_reason') not in ('stop','length'):raise DistributionError('actual completed Q4 token evidence is missing')
    message=choices[0].get('message',{})
    if not isinstance(message,dict) or not isinstance(message.get('content'),str) or not message['content'].strip():raise DistributionError('MISSING_EVIDENCE: own LiteLLM visible content is missing')
    return {'http_status':status,'visible_content_characters':len(message['content']),'completion_tokens':tokens,'finish_reason':choices[0]['finish_reason'],'caller_key_used':True,'response_content_recorded':False}

def parse_legacy_sse(raw):
    events=[]
    for block in raw.replace(b'\r\n',b'\n').split(b'\n\n'):
        if not block.strip():continue
        fields=[line[5:].lstrip() for line in block.split(b'\n') if line.startswith(b'data:')]
        if not fields:continue
        try:event=json.loads(b'\n'.join(fields).decode('utf-8'))
        except (ValueError,UnicodeError):raise DistributionError('legacy smoke returned non-JSON custom SSE')
        if not isinstance(event,dict) or type(event.get('done'))is not bool or 'error' in event:raise DistributionError('legacy smoke returned malformed/error SSE')
        message=event.get('message',{})
        if not isinstance(message,dict) or any(not isinstance(message.get(key,''),str) for key in ('thinking','content')):raise DistributionError('legacy smoke message field types differ')
        events.append(event)
    if not events or sum(x['done'] for x in events)!=1 or not events[-1]['done'] or events[-1].get('done_reason') not in ('stop','length'):raise DistributionError('legacy SSE terminal contract differs')
    count=sum(len(str(x.get('message',{}).get('thinking','')))+len(str(x.get('message',{}).get('content',''))) for x in events[:-1])
    visible=sum(len(x.get('message',{}).get('content','')) for x in events[:-1])
    if count<=0 or visible<=0:raise DistributionError('MISSING_EVIDENCE: legacy SSE visible content is missing')
    return {'visible_content_characters':visible,'events':len(events),'terminal_count':1,'thinking_content_characters':count,'response_content_recorded':False}

def legacy(trusted):
    url,body,key=inference_parts(trusted,'legacy',True)
    if key==trusted['secrets']['LITELLM_MASTER_KEY']:raise DistributionError('master credentials cannot serve inference')
    body['options']['num_predict']=4096
    req=Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    with build_opener(ProxyHandler({}),agent._NoControlRedirect()).open(req,timeout=600) as response:
        if response.status!=200 or response.headers.get_content_type()!='text/event-stream':raise DistributionError('legacy SSE HTTP contract differs')
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise DistributionError('legacy smoke body exceeds output cap')
    return {'http_status':200,'caller_key_used':True,**parse_legacy_sse(raw)}


def nonstream_contract(content_type,raw):
    if content_type!='text/plain' or not raw or len(raw)>1024*1024:raise DistributionError('legacy nonstream must be nonempty bounded text/plain')
    try:text=raw.decode('utf-8')
    except UnicodeError:raise DistributionError('legacy nonstream UTF-8 contract differs')
    if not text.strip():raise DistributionError('MISSING_EVIDENCE: legacy nonstream visible content is missing')
    return {'visible_content_characters':len(text),'response_content_recorded':False}

def legacy_nonstream(trusted):
    url,body,key=inference_parts(trusted,'legacy',False);body['options']['num_predict']=4096
    req=Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    with build_opener(ProxyHandler({}),agent._NoControlRedirect()).open(req,timeout=600) as response:
        if response.status!=200:raise DistributionError('legacy nonstream HTTP contract differs')
        result=nonstream_contract(response.headers.get_content_type(),response.read(1024*1024+1))
    return {'http_status':200,'caller_key_used':True,**result}

def check(data,*,source_commit,recipe_sha256,evidence):
    data=no_symlinks(data);trusted=validate(data);preflight(trusted,source_commit,recipe_sha256)
    report={'schema_version':1,'status':'RUNNING','scope':'same-archive native Q4 smoke; not full UI/browser/soak acceptance','installation_id':trusted['config']['installation_id'],'source_commit':source_commit,'ui_recipe_sha256':recipe_sha256,'checkpoint_payload_verified':True,'model_load_route':'own caller -> LiteLLM -> guard -> manager -> Lily','operations':[],'final_UI_browser_acceptance':False,'new_30min_soak':'NOT_RUN'}
    stage='initialize';started=time.monotonic();registry=None;model=None;old_handlers={}
    def interrupted(_signum,_frame):raise InterruptedError('owned smoke interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):old_handlers[sig]=signal.signal(sig,interrupted)
    def timed_out(_signum,_frame):raise TimeoutError('owned smoke wall-clock budget exhausted')
    old_handlers[signal.SIGALRM]=signal.signal(signal.SIGALRM,timed_out);old_timer=signal.setitimer(signal.ITIMER_REAL,900)
    try:
        report['operations'].append({'stage':'initialize','result':agent.initialize(data)})
        stage='start';report['operations'].append({'stage':'start','result':agent.start(data,readiness_seconds=30)})
        registry=read_object(private_file(data/'run/agent.json'));stage='own_litellm_inference';report['operations'].append({'stage':stage,'result':openai(trusted)})
        model=read_object(private_file(data/'run/model.json'))
        if model.get('installation_id')!=trusted['config']['installation_id'] or not model.get('child') or not unchanged(model['runner']) or not unchanged(model['child']):raise DistributionError('actual owned Lily identity is missing')
        manager_descendants=descendants(registry['children']['manager']['pid'])
        if model['runner']['pid'] not in {x['pid'] for x in manager_descendants}:raise DistributionError('owned Lily runner is outside manager lineage')
        report['model_identity']=model;report['owned_processes']=registry
        stage='own_compat_inference';report['operations'].append({'stage':stage,'result':legacy(trusted)})
        stage='own_compat_nonstream_inference';report['operations'].append({'stage':stage,'result':legacy_nonstream(trusted)})
        report['status']='PASS_NATIVE_Q4_SMOKE'
    except (DistributionError,OSError,ValueError,KeyError,TypeError) as error:
        report.update(status='FAIL',first_failure_stage=stage,error=str(error) if isinstance(error,DistributionError) else type(error).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        try:report['cleanup']=agent.stop(data)
        except (DistributionError,OSError,ValueError,KeyError,TypeError) as error:
            report['cleanup']={'state':'NEEDS_OWNED_REVIEW','error':str(error) if isinstance(error,DistributionError) else type(error).__name__};report['status']='FAIL'
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
        signal.setitimer(signal.ITIMER_REAL,*old_timer)
    identities=([registry['agent'],registry['postgres'],*registry['children'].values()] if registry else [])+([model['runner'],model['child']] if model else [])
    report['all_recorded_pids_gone']=all(not unchanged(x) for x in identities)
    report['remaining_listener_checks']={name:subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True).returncode for name,port in trusted['config']['ports'].items()}
    report['owned_registry_removed']=not (data/'run/agent.json').exists() and not (data/'run/model.json').exists();report['owned_plist_removed']=not (data/'run/stack.plist').exists()
    if not report['all_recorded_pids_gone'] or not report['owned_registry_removed'] or not report['owned_plist_removed'] or any(x!=1 for x in report['remaining_listener_checks'].values()):report['status']='FAIL'
    report['elapsed_seconds']=time.monotonic()-started;write_json_new(evidence,report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-root',type=Path,required=True);p.add_argument('--source-commit',required=True);p.add_argument('--recipe-sha256',required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();os.umask(0o077)
    try:
        r=check(a.data_root,source_commit=a.source_commit,recipe_sha256=a.recipe_sha256,evidence=a.evidence);print(json.dumps({'status':r['status'],'checkpoint_payload_verified':True,'elapsed_seconds':r['elapsed_seconds'],'all_recorded_pids_gone':r['all_recorded_pids_gone']}));raise SystemExit(0 if r['status']=='PASS_NATIVE_Q4_SMOKE' else 2)
    except (DistributionError,OSError,ValueError,KeyError,TypeError) as error:print(json.dumps({'status':'FAIL','error':str(error) if isinstance(error,DistributionError) else type(error).__name__}));raise SystemExit(2)
