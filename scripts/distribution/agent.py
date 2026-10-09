#!/usr/bin/env python3
"""One native user launchd supervisor for an isolated LiliuxFlow installation."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import signal
import socket
import subprocess
import time
import stat
import uuid
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.parse import quote,urlsplit
from common import DistributionError,private_directory,no_symlinks,read_object,write_new,write_json_new
from ownership import capture,unchanged,terminate,descendants
from trust import validate,installation,private_file,sha256,atomic_private_json


class _NoControlRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        return None


def _content_type(headers):
    import re
    raw=headers.get('Content-Type','') if headers is not None else ''
    media=raw.split(';',1)[0].strip().lower()
    return media if media in {'application/json','application/problem+json','text/plain','text/html','application/octet-stream'} else 'other' if media else None


def http_json(url,*,method='GET',payload=None,token=None,control=None,timeout=10,diagnostics=None,health_contract=None):
    # Transport HTTP status and decode outcome are independent; ordinary API callers
    # retain strict JSON success semantics. The diagnostics never contain body/URL/headers.
    try:parsed=urlsplit(url);port=parsed.port
    except ValueError:raise DistributionError('runtime control endpoint syntax is invalid')
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or type(port) is not int or not 1<=port<=65535 or parsed.fragment:
        raise DistributionError('runtime HTTP endpoint must stay on credential-free loopback')
    if health_contract not in (None,'llama_swap_health') or health_contract=='llama_swap_health' and (method!='GET' or parsed.path!='/health' or payload is not None):
        raise DistributionError('health contract is not permitted for this API path')
    diag={'stage':'request_build','http_status':None,'content_type':None,'body_length':None,
          'json_decode_success':False,'exception_class':None,'errno':None}
    def note(stage,error=None):
        diag['stage']=stage
        if error is not None:
            cause=getattr(error,'reason',None)
            diag['exception_class']=type(error).__name__
            value=getattr(error,'errno',None)
            if not isinstance(value,int):value=getattr(cause,'errno',None)
            diag['errno']=value if isinstance(value,int) else None
        if diagnostics is not None:
            record=dict(diag)
            diagnostics(record) if callable(diagnostics) else diagnostics.append(record)
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    if control:headers['X-Recovery-Control-Token']=control
    data=json.dumps(payload).encode() if payload is not None else None
    request=Request(url,data=data,headers=headers,method=method);note('request_build')
    try:
        note('opener_build')
        opener=build_opener(ProxyHandler({}),_NoControlRedirect())
        note('connect')
        with opener.open(request,timeout=timeout) as response:
            diag['http_status']=response.status;diag['content_type']=_content_type(response.headers);note('http_headers')
            note('body_read');body=response.read(1024**2+1);diag['body_length']=len(body)
            if len(body)>1024**2:
                note('response_limit');raise DistributionError('runtime control response exceeds cap')
            if health_contract=='llama_swap_health':
                if response.status==200 and body==b'OK' and diag['content_type']=='text/plain':
                    note('health_contract_match');return 200,{'health_contract':'llama_swap_v260_OK'}
                note('health_contract_mismatch');return 0,{}
            note('json_decode')
            try:decoded=json.loads(body)
            except (ValueError,UnicodeError) as error:
                note('json_decode_failed',error);return 0,{}
            diag['json_decode_success']=True;note('complete')
            return response.status,decoded
    except HTTPError as error:
        diag['http_status']=error.code;diag['content_type']=_content_type(error.headers)
        note('http_error',error)
        return error.code,{}
    except (URLError,TimeoutError,OSError) as error:
        note('network_error',error);return 0,{}


def startup_diagnostic(data,service,event):
    # Only schema-defined safe scalar fields are persisted; no exception str or request data.
    permitted={'stage','http_status','content_type','body_length','json_decode_success','exception_class','errno'}
    record={key:event.get(key) for key in permitted};record['service']=service
    file=no_symlinks(data/'logs/startup-http.jsonl')
    descriptor=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'a') as output:output.write(json.dumps(record)+'\n')


def preflight_ports(ports):
    for port in ports.values():
        with socket.socket() as sock:
            sock.settimeout(.2)
            if sock.connect_ex(('127.0.0.1',port))==0:
                raise DistributionError('configured loopback port is occupied; listener owner left untouched')


def safe_environment(trusted):
    data=trusted['data_root'];config=trusted['config'];secret=trusted['secrets']
    node_bin=data/'runtime/toolchains/node/bin'
    if not node_bin.is_dir():raise DistributionError('installation-owned Node toolchain is unavailable')
    common={'PATH':str(node_bin)+':/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(Path.home()),'LANG':'en_US.UTF-8',
            'TMPDIR':str(private_directory(data/'run/tmp')),'PYTHONUNBUFFERED':'1',
            'LILIUXFLOW_DATA_ROOT':str(data),'LILIUXFLOW_PRIVATE_LOG':str(data/'logs/database-steps.jsonl'),'PYTHONPATH':str(trusted['source_root']/'services/compat/src')}
    if trusted.get('controlplane_only'):
        common['LILIUXFLOW_CONTROLPLANE_ONLY']='1'
    litellm={**common,'PATH':str(data/'runtime/litellm/bin')+':'+common['PATH'],**{k:secret[k] for k in ('LITELLM_MASTER_KEY','LITELLM_SALT_KEY','UI_USERNAME','UI_PASSWORD')},
             'DATABASE_URL':'postgresql://litellm_app:'+quote(secret['DB_PASSWORD'],safe='')+'@127.0.0.1:'+str(config['ports']['postgresql'])+'/litellm',
             'MANAGER_BACKEND_TOKEN':secret['MANAGER_BACKEND_TOKEN'],'STORE_MODEL_IN_DB':'True','LITELLM_LOG':'ERROR',
             'PRISMA_BINARY_CACHE_DIR':str(private_directory(data/'cache/prisma/binaries')),
             'PRISMA_HOME_DIR':str(private_directory(data/'cache/prisma')),
             'PRISMA_NODEENV_CACHE_DIR':str(private_directory(data/'cache/prisma/nodeenv')),
             'PRISMA_USE_GLOBAL_NODE':'true','PRISMA_USE_NODEJS_BIN':'false',
             'ENFORCE_PRISMA_MIGRATION_CHECK':'true','LANG':'en_US.UTF-8'}
    policy=data/'run/context-profile-policy.json'
    if policy.exists():
        private_file(policy)
        litellm['LILIUXFLOW_PROFILE_POLICY_PATH']=str(policy)
        litellm['LILIUXFLOW_PROFILE_POLICY_SHA256']=sha256(policy)
    return common,litellm


def validation_routes(trusted, profile_ids=()):
    """Resolve explicit private test scope without production enablement."""
    if not isinstance(profile_ids,tuple) or len(profile_ids)>2 or any(pid not in ('ctx128k','ctx262k') for pid in profile_ids) or len(set(profile_ids))!=len(profile_ids):
        raise DistributionError('validation profile selection must be a finite immutable tuple')
    if not profile_ids:return (),{}
    if trusted.get('model_configured') is False:
        raise DistributionError('no-model installation cannot enable validation routes')
    from runtime_proof import validation_authorized
    from litellm_profile_policy import parse_policy
    registry=trusted['registry'];selected=[]
    for pid in profile_ids:
        profile=registry.by_id(pid,allow_validation=True)
        if profile.production_enabled or not validation_authorized(trusted,pid):
            raise DistributionError('candidate validation scope differs from the private owner record')
        selected.append(profile)
    record=read_object(private_file(trusted['data_root']/'run/context-validation.json'))
    keys=record.get('validation_keys')
    if not isinstance(keys,dict) or set(keys)!={p.public_alias for p in selected}:
        raise DistributionError('candidate validation virtual key allowlist differs')
    parse_policy({'schema_version':1,'enabled_models':[p.public_alias for p in registry.enabled_profiles],'validation_keys':keys})
    return tuple(selected),keys


def configs(trusted, *, validation_profile_ids=()):
    data=trusted['data_root'];cfg=trusted['config'];secret=trusted['secrets'];source=trusted['source_root'];ports=cfg['ports']
    registry=trusted['registry'];validation,validation_keys=validation_routes(trusted,validation_profile_ids)
    configured = trusted.get('model_configured') is not False
    profiles=(*registry.enabled_profiles,*validation) if configured else ()
    if not profiles and configured:raise DistributionError('no enabled context profile routes')
    manager={'healthCheckTimeout':180,'globalTTL':1800,'unloadTimeout':60,'logLevel':'warn','captureBuffer':0,
             'apiKeys':[secret['MANAGER_BACKEND_TOKEN']], 'models':{}}
    routes=[]
    for profile in profiles:
        alias=profile.public_alias
        lily_cmd=shlex.join([str(trusted['binaries']['compat_python']),str(source/'scripts/distribution/model_runner.py'),
                            '--data-root',str(data),'--profile',profile.profile_id]+(['--validation'] if not profile.production_enabled else [])+['--port'])+' ${PORT}'
        manager['models'][alias]={'cmd':lily_cmd,'name':'Qwen3.8-Flash-Next Lily Q4 · '+str(profile.context_tokens),
            'useModelName':profile.runtime_model_id,'proxy':'http://127.0.0.1:${PORT}','checkEndpoint':'/health','ttl':profile.idle_ttl_seconds,
            'unloadTimeout':60,'capabilities':{'in':['text','image'],'out':['text'],'context':profile.context_tokens,'disableAuto':True}}
        routes.append({'model_name':alias,'litellm_params':{'model':'openai/'+alias,'api_base':'http://127.0.0.1:'+str(ports['guard'])+'/v1',
            'api_key':'os.environ/MANAGER_BACKEND_TOKEN','timeout':profile.total_deadline_seconds+30,'stream_timeout':profile.total_deadline_seconds+30},
            'model_info':{'id':'liliuxflow-'+profile.profile_id,'mode':'chat','max_tokens':profile.context_tokens,
                          'max_input_tokens':profile.context_tokens,'max_output_tokens':min(profile.context_tokens,65536),
                          'default_output_tokens':profile.default_output_tokens,'context_profile_id':profile.profile_id}})
        if profile.profile_id in ('ctx64k-mtp2','ctx128k-mtp2','ctx262k-mtp2'):manager['models'][alias]['name']+=' · MTP2 opt-in'
        metadata=profile.as_dict()
        for name in ('engine_id','mtp_drafts','kv_cache','max_batch','qsa_route','qsa_scores'):
            if name in metadata:routes[-1]['model_info'][name]=metadata[name]
    lp={'model_list':routes,
        'general_settings':{'master_key':'os.environ/LITELLM_MASTER_KEY','database_url':'os.environ/DATABASE_URL',
         'allow_client_side_credentials':False,'store_prompts_in_spend_logs':False,'background_health_checks':False,'cancel_on_disconnect':True,
         'disable_prisma_schema_update':True},'litellm_settings':{'turn_off_message_logging':True,'ui_access_mode':'admin_only',
           'callbacks':['litellm.proxy._liliuxflow_context_profiles.context_profile_policy']},
        'router_settings':{'num_retries':0,'max_fallbacks':0,'fallbacks':[],'context_window_fallbacks':[],'content_policy_fallbacks':[],
                           'timeout':max((p.total_deadline_seconds for p in profiles),default=30)+30}}
    atomic_private_json(data/'run/llama-swap.json',manager)
    atomic_private_json(data/'run/litellm.json',lp)
    atomic_private_json(data/'run/context-profile-policy.json',{'schema_version':1,
        'enabled_models':[p.public_alias for p in profiles if p.production_enabled],'validation_keys':validation_keys})
    return data/'run/llama-swap.json',data/'run/litellm.json'


def postgres_tools(trusted):
    pg=Path(trusted['trust']['postgresql_bin'])
    records=trusted['trust'].get('postgresql_tools',{})
    if set(records)!={'postgres','initdb','pg_ctl','psql','pg_dump','pg_restore'}:
        raise DistributionError('PostgreSQL tool trust inventory is incomplete')
    if any(sha256(pg/name)!=digest for name,digest in records.items()):
        raise DistributionError('PostgreSQL executable changed since native build')
    return pg


def pg_environment(trusted,*,owner=True):
    return {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','PGSSLMODE':'disable',
            'PGPASSWORD':trusted['secrets']['PG_OWNER_PASSWORD' if owner else 'DB_PASSWORD'],'LILIUXFLOW_PRIVATE_LOG':str(trusted['data_root']/'logs/database-steps.jsonl')}


def private_run(argv,*,env,input_text=None,timeout=60):
    result=subprocess.run([str(x) for x in argv],env=env,input=input_text,text=True,capture_output=True,timeout=timeout)
    if result.returncode:
        log=env.get('LILIUXFLOW_PRIVATE_LOG')
        if log:
            import re
            details=(result.stdout+'\n'+result.stderr)[-8000:]
            for name,value in sorted(env.items(),key=lambda pair:len(pair[1]),reverse=True):
                if len(value)>=12 and any(word in name for word in ('PASSWORD','KEY','TOKEN','DATABASE_URL')):
                    details=details.replace(value,'[REDACTED]')
            details=re.sub(r"(?i)(PASSWORD\s+')([^']+)(')",r"\1[REDACTED]\3",details)
            details=re.sub(r'(postgres(?:ql)?://[^:/@]+:)[^@]+@',r'\1[REDACTED]@',details)
            descriptor=os.open(no_symlinks(Path(log)),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(descriptor,'a') as output:output.write(json.dumps({'tool':Path(argv[0]).name,'exit_code':result.returncode,'details':details})+'\n')
        raise DistributionError('native database step failed; inspect private sanitized database-steps.jsonl')
    return result.stdout


def pg_start(trusted):
    data=trusted['data_root'];pg=postgres_tools(trusted);pgdata=data/'postgres/data';port=trusted['config']['ports']['postgresql']
    if (pgdata/'postmaster.pid').exists():
        raise DistributionError('PostgreSQL PID file already exists; refusing to adopt an unverified process')
    private_directory(data/'postgres')
    logfile=data/'logs/postgres.log'
    if not logfile.exists():write_new(logfile,b'')
    # Socket directory omitted: loopback TCP only, works for long Unicode paths without AF_UNIX limits.
    private_run([pg/'pg_ctl','-D',pgdata,'-l',logfile,'-o','-h 127.0.0.1 -p '+str(port)+' -k ""','-w','-t','30','start'],env=pg_environment(trusted),timeout=40)
    pid=int((pgdata/'postmaster.pid').read_text().splitlines()[0])
    ident=capture(pid,executable=pg/'postgres')
    # Strong PGDATA binding in postmaster.pid plus live executable hash; PID/start/UID is retained.
    lines=(pgdata/'postmaster.pid').read_text().splitlines()
    if Path(lines[1])!=pgdata or int(lines[3])!=port:
        raise DistributionError('isolated postmaster data directory or port differs')
    return ident


def pg_stop(trusted,identity):
    if not unchanged(identity):return
    private_run([postgres_tools(trusted)/'pg_ctl','-D',trusted['data_root']/'postgres/data','-m','fast','-w','-t','30','stop'],env=pg_environment(trusted),timeout=40)
    if unchanged(identity):raise DistributionError('owned PostgreSQL remained after stop')


@contextlib.contextmanager
def mutation(data):
    data=no_symlinks(data);lock=data/'run/host-mutation.lease'
    try:lock.mkdir(mode=0o700)
    except FileExistsError:raise DistributionError('installation host mutation lease is busy')
    holder=capture(os.getpid());write_json_new(lock/'lease.json',holder)
    try:yield
    finally:
        if read_object(lock/'lease.json')==holder:
            (lock/'lease.json').unlink();lock.rmdir()


def initialize(data,*,controlplane_only=False):
    trusted=validate(data,require_checkpoint='controlplane' if controlplane_only else 'available');cfg=trusted['config'];pg=postgres_tools(trusted);pgdata=data/'postgres/data'
    with mutation(data):
        if cfg['database_initialized']:
            return {'state':'already_initialized','credentials_preserved':True}
        marker=data/'postgres/initialization.json'
        preflight_ports(cfg['ports']);private_directory(data/'postgres')
        if pgdata.exists():
            if not marker.is_file() or read_object(private_file(marker)).get('installation_id')!=cfg['installation_id']:
                raise DistributionError('existing database directory is preserved; explicit recovery required')
        else:
            password=data/'run/initdb-password';write_new(password,(trusted['secrets']['PG_OWNER_PASSWORD']+'\n').encode())
            try:
                private_run([pg/'initdb','-D',pgdata,'-U','liliuxflow_owner','--encoding=UTF8','--locale=C','--auth-local=scram-sha-256','--auth-host=scram-sha-256','--pwfile='+str(password)],env=pg_environment(trusted),timeout=120)
            finally:password.unlink(missing_ok=True)
            write_json_new(marker,{'schema_version':1,'installation_id':cfg['installation_id'],'fresh_own_cluster':True})
        identity=pg_start(trusted)
        try:
            base=[pg/'psql','-h','127.0.0.1','-p',cfg['ports']['postgresql'],'-U','liliuxflow_owner','-d','postgres','-At','-v','ON_ERROR_STOP=1']
            role=private_run(base,env=pg_environment(trusted),input_text="SELECT count(*) FROM pg_roles WHERE rolname='litellm_app';\n").strip()
            if role=='0':
                sql="CREATE ROLE litellm_app LOGIN PASSWORD '"+trusted['secrets']['DB_PASSWORD'].replace("'","''")+"';\n"
                private_run(base,env=pg_environment(trusted),input_text=sql)
            database=private_run(base,env=pg_environment(trusted),input_text="SELECT count(*) FROM pg_database WHERE datname='litellm';\n").strip()
            if database=='0':private_run(base,env=pg_environment(trusted),input_text='CREATE DATABASE litellm OWNER litellm_app;\n')
            _,lp_env=safe_environment(trusted)
            schema=data/'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/schema.prisma'
            python=trusted['binaries']['litellm_python']
            for action in (['generate'],['db','push']):
                private_run([python,'-m','prisma',*action,'--schema',schema],env=lp_env,timeout=300)
            cfg['database_initialized']=True;atomic_private_json(data/'install.json',cfg)
        finally:pg_stop(trusted,identity)
    return {'state':'database_initialized','database':'own empty installation','credentials_preserved':True,'model_loaded':False}


def wait_http(url,token=None,seconds=60,abort=None,diagnostics=None,health_contract=None):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if abort and abort():raise DistributionError('owned startup cancelled before readiness')
        status,body=http_json(url,token=token,diagnostics=diagnostics,health_contract=health_contract)
        ready=status==200
        if urlsplit(url).path=='/health/readiness':ready=ready and isinstance(body,dict) and body.get('status')=='healthy' and body.get('db')=='connected'
        if ready:return
        time.sleep(.5)
    raise DistributionError('owned service did not become ready before its timeout')


def profile_route_inventory(yaml_models, db_models, registry):
    """Classify duplicate owned deployments without changing any database row."""
    expected={p.public_alias:'liliuxflow-'+p.profile_id for p in registry.enabled_profiles}
    rows=[]
    for source,models in (('yaml',yaml_models),('database',db_models)):
        for item in models:
            if not isinstance(item,dict):raise DistributionError('deployment inventory is malformed')
            name=item.get('model_name');info=item.get('model_info') or {};identifier=info.get('id')
            rows.append({'source':source,'model_name':name,'deployment_id':identifier,
                         'owned':name in expected and identifier==expected[name]})
    conflicts=[]
    for name in expected:
        matches=[r for r in rows if r['model_name']==name]
        if len(matches)>1 or any(not r['owned'] for r in matches):
            conflicts.append({'model_name':name,'deployments':matches})
    return {'rows':rows,'conflicts':conflicts,'requires_review':bool(conflicts)}


OPTIN_ALIAS='qwen3.8-flash-next-lily-q4-mtp2-64k'
OPTIN_ALIASES=frozenset('qwen3.8-flash-next-lily-q4-mtp2-'+size for size in ('64k','128k','262k'))
def _owner_grant_models(enabled,authorized_opt_in_aliases=()):
    if not isinstance(authorized_opt_in_aliases,tuple) or len(set(authorized_opt_in_aliases))!=len(authorized_opt_in_aliases) or any(alias not in OPTIN_ALIASES for alias in authorized_opt_in_aliases):raise DistributionError('opt-in caller grant must name the exact finite alias')
    if any(alias not in enabled for alias in authorized_opt_in_aliases):raise DistributionError('opt-in profile is not enabled in the trusted registry')
    return [alias for alias in enabled if alias not in OPTIN_ALIASES or alias in authorized_opt_in_aliases]

def _expanded_owner_models(current, enabled, authorized_opt_in_aliases=()):
    # LiteLLM treats [] as unrestricted. Refuse to infer a restricted grant from
    # wildcard/unrestricted ACLs, and never change another user's key here.
    if not isinstance(current,list) or not current or any(not isinstance(x,str) for x in current):
        raise DistributionError('designated owner model ACL must be explicit')
    if any('*' in x or x in ('all-proxy-models','all-team-models','all-router-models') for x in current):
        raise DistributionError('designated owner wildcard ACL requires explicit review')
    return list(dict.fromkeys([*current,*_owner_grant_models(enabled,authorized_opt_in_aliases)]))


def reconcile_designated_owner_caller(*, caller_path, expected_user_id, api_base, master_key, registry, backup_root, dry_run=False,authorized_opt_in_aliases=()):
    """Add enabled profiles to only the installation's designated existing key.

    Official models-only update endpoints preserve key identity, budget, expiry
    and limits. Caller secrets and a private rollback snapshot stay local.
    """
    destination=private_file(Path(caller_path))
    caller=read_object(destination);user=expected_user_id;key=caller.get('api_key')
    if not isinstance(user,str) or not user:raise DistributionError('designated caller user is required')
    if caller.get('user_id')!=user or not isinstance(key,str) or not key.startswith('sk-'):
        raise DistributionError('designated caller ownership differs')
    parsed=urlsplit(api_base)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path!='/v1' or parsed.query or parsed.fragment:
        raise DistributionError('designated caller API must be the owned loopback v1 endpoint')
    base=api_base.removesuffix('/v1');master=master_key
    if caller.get('api_base')!=api_base or caller.get('alias',registry.default.public_alias)!=registry.default.public_alias:
        raise DistributionError('designated caller default or endpoint differs')
    # Management-only inventory: caller keys can be limited to llm_api_routes.
    # The exact existing key is sent in the POST body, never a URL.
    key_status,key_body=http_json(base+'/v2/key/info',method='POST',payload={'keys':[key]},token=master)
    user_status,user_body=http_json(base+'/user/info?user_id='+quote(user,safe=''),token=master)
    rows=key_body.get('info')
    key_info=rows[0] if key_body.get('key')==[key] and isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],dict) else None
    user_info=user_body.get('user_info')
    if key_status!=200 or user_status!=200 or not isinstance(key_info,dict) or not isinstance(user_info,dict):
        raise DistributionError('designated caller ACL inventory unavailable')
    if key_info.get('user_id')!=user or user_info.get('user_id')!=user:
        raise DistributionError('designated caller API ownership differs')
    enabled=[p.public_alias for p in registry.enabled_profiles]
    key_models=_expanded_owner_models(key_info.get('models'),enabled,authorized_opt_in_aliases)
    user_models=_expanded_owner_models(user_info.get('models'),enabled,authorized_opt_in_aliases)
    changes={'key_models':key_models,'user_models':user_models,'enabled_profiles':enabled,
             'key_changed':key_models!=key_info['models'],'user_changed':user_models!=user_info['models']}
    if dry_run:return changes
    if changes['key_changed'] or changes['user_changed']:
        backup=private_directory(Path(backup_root)/str(time.time_ns()))
        write_json_new(backup/'before.json',{'caller':caller,'key_info':key_info,'user_info':user_info})
        # Restrict changes to models, and retain this same virtual key.
        if changes['user_changed']:
            status,_=http_json(base+'/user/update',method='POST',payload={'user_id':user,'models':user_models},token=master)
            if status!=200:raise DistributionError('designated owner user ACL update failed; private rollback snapshot retained')
        if changes['key_changed']:
            status,_=http_json(base+'/key/update',method='POST',payload={'key':key,'models':key_models},token=master)
            if status!=200:
                if changes['user_changed']:
                    http_json(base+'/user/update',method='POST',payload={'user_id':user,'models':user_info['models']},token=master)
                raise DistributionError('designated owner key ACL update failed; private rollback snapshot retained')
        status,after=http_json(base+'/v2/key/info',method='POST',payload={'keys':[key]},token=master)
        after_rows=after.get('info')
        after_info=after_rows[0] if after.get('key')==[key] and isinstance(after_rows,list) and len(after_rows)==1 and isinstance(after_rows[0],dict) else None
        user_status,after_user=http_json(base+'/user/info?user_id='+quote(user,safe=''),token=master)
        after_user_info=after_user.get('user_info')
        stable=('user_id','team_id','organization_id','max_budget','budget_duration','expires','rpm_limit','tpm_limit','key_alias','blocked','allowed_routes','permissions','object_permission_id')
        user_stable=('user_id','user_role','max_budget','budget_duration','tpm_limit','rpm_limit','user_email')
        valid_key=status==200 and isinstance(after_info,dict) and after_info.get('models')==key_models and all(after_info.get(f)==key_info.get(f) for f in stable)
        valid_user=user_status==200 and isinstance(after_user_info,dict) and after_user_info.get('models')==user_models and all(after_user_info.get(f)==user_info.get(f) for f in user_stable)
        if not valid_key or not valid_user:
            if changes['key_changed']:
                http_json(base+'/key/update',method='POST',payload={'key':key,'models':key_info['models']},token=master)
            if changes['user_changed']:
                http_json(base+'/user/update',method='POST',payload={'user_id':user,'models':user_info['models']},token=master)
            raise DistributionError('designated owner ACL verification failed; private rollback snapshot retained')
    caller['models']=key_models
    atomic_private_json(destination,caller)
    return changes



UI_USER_STABLE=('user_id','user_role','max_budget','budget_duration','tpm_limit','rpm_limit','user_email',
                'team_id','organization_id','max_parallel_requests','model_max_budget','model_rpm_limit',
                'model_tpm_limit','permissions','allowed_routes','blocked','metadata','sso_user_id')
def reconcile_designated_ui_user(*,user_id,api_base,master_key,registry,backup_root,dry_run=True,authorized_opt_in_aliases=()):
    """Append explicit enabled models to one named UI user; never inspect session keys."""
    if not isinstance(user_id,str) or not 1<=len(user_id)<=256 or any(ord(c)<32 for c in user_id) or type(dry_run) is not bool:
        raise DistributionError('UI user selection must be explicit and bounded')
    parsed=urlsplit(api_base)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path!='/v1' or parsed.query or parsed.fragment:
        raise DistributionError('UI user API must be the owned loopback v1 endpoint')
    base=api_base.removesuffix('/v1');enabled=[p.public_alias for p in registry.enabled_profiles]
    _owner_grant_models(enabled,authorized_opt_in_aliases)  # Reject disabled/unknown opt-ins before any API operation.
    def inventory():
        status,body=http_json(base+'/user/info?user_id='+quote(user_id,safe=''),token=master_key)
        user=body.get('user_info') if isinstance(body,dict) else None
        if status!=200 or not isinstance(user,dict) or user.get('user_id')!=user_id:
            raise DistributionError('selected UI user identity could not be verified')
        return user
    before=inventory();old=before.get('models');models=_expanded_owner_models(old,[alias for alias in enabled if alias in authorized_opt_in_aliases],authorized_opt_in_aliases)
    result={'user_id':user_id,'models_before':old,'models_after':models,'changed':models!=old,'writes':False,
            'roles_or_limits_changed':False,'other_users_changed':False,'session_keys_read_or_updated':False,
            'existing_sessions':'sign out and sign in again to inherit the updated user models'}
    if dry_run or models==old:return result
    archive=private_directory(Path(backup_root)/str(time.time_ns()));write_json_new(archive/'before.json',{'schema_version':1,'user_info':before})
    def stable(user):return all(user.get(k)==before.get(k) for k in UI_USER_STABLE)
    def update(values):
        status,_=http_json(base+'/user/update',method='POST',payload={'user_id':user_id,'models':values},token=master_key)
        if status!=200:raise DistributionError('selected UI user models update was refused')
    try:
        update(models);after=inventory()
        if after.get('models')!=models or not stable(after):raise DistributionError('selected UI user models verification failed')
    except Exception:
        # A timeout or refused response may follow a committed update. Restore
        # only a proven old/expected state; concurrent identity/limit drift refuses.
        try:
            current=inventory()
            if not stable(current) or current.get('models') not in (old,models):raise DistributionError('UI user ACL rollback refuses concurrent drift')
            if current['models']==models:update(old)
            restored=inventory()
            if restored.get('models')!=old or not stable(restored):raise DistributionError('UI user ACL rollback is unproven')
        except Exception:raise DistributionError('UI user ACL update failed; private rollback retained for explicit review') from None
        raise DistributionError('UI user ACL update failed; original models restored') from None
    return {**result,'writes':True,'rollback_snapshot':str(archive/'before.json')}


def reconcile_ui_user(trusted,user_id,*,dry_run=True,authorized_opt_in_aliases=()):
    if trusted.get('model_configured') is False:raise DistributionError('UI user model grant requires a configured model')
    return reconcile_designated_ui_user(user_id=user_id,api_base='http://127.0.0.1:'+str(trusted['config']['ports']['litellm'])+'/v1',
        master_key=trusted['secrets']['LITELLM_MASTER_KEY'],registry=trusted['registry'],backup_root=trusted['data_root']/'backups/ui-user-acl',
        dry_run=dry_run,authorized_opt_in_aliases=authorized_opt_in_aliases)

def reconcile_owner_caller(trusted, *, dry_run=False,authorized_opt_in_aliases=()):
    data=trusted['data_root'];cfg=trusted['config']
    return reconcile_designated_owner_caller(caller_path=data/'secrets/caller.json',
        expected_user_id='local-'+cfg['installation_id'],api_base='http://127.0.0.1:'+str(cfg['ports']['litellm'])+'/v1',
        master_key=trusted['secrets']['LITELLM_MASTER_KEY'],registry=trusted['registry'],backup_root=data/'backups/context-acl',dry_run=dry_run,authorized_opt_in_aliases=authorized_opt_in_aliases)


def make_caller(trusted):
    data=trusted['data_root'];destination=data/'secrets/caller.json'
    if destination.exists():
        if trusted.get('model_configured') is not False:
            reconcile_owner_caller(trusted)
        return
    cfg=trusted['config'];alias=trusted['registry'].default.public_alias;models=_owner_grant_models([p.public_alias for p in trusted['registry'].enabled_profiles]) if trusted.get('model_configured') is not False else ['liliuxflow:model-not-configured'];base='http://127.0.0.1:'+str(cfg['ports']['litellm']);master=trusted['secrets']['LITELLM_MASTER_KEY']
    # LiteLLM [] means unrestricted. A management-only caller receives an
    # explicit unavailable grant; the empty installed catalog hides it.
    user='local-'+cfg['installation_id']
    status,_=http_json(base+'/user/new',method='POST',payload={'user_id':user,'user_role':'internal_user','models':models},token=master)
    if status not in (200,201):raise DistributionError('own caller identity creation failed')
    status,response=http_json(base+'/key/generate',method='POST',payload={'user_id':user,'models':models,'key_alias':'local-caller','rpm_limit':60,'tpm_limit':1000000},token=master)
    key=response.get('key')
    if status!=200 or not isinstance(key,str) or not key.startswith('sk-'):
        raise DistributionError('own caller key creation failed')
    write_json_new(destination,{'schema_version':1,'user_id':user,'alias':alias,'models':models,'api_key':key,'api_base':base+'/v1','compat_base':'http://127.0.0.1:'+str(cfg['ports']['compat'])})
    cfg['caller_key_created']=True;atomic_private_json(data/'install.json',cfg)


def guard_admission_status(trusted):
    guard='http://127.0.0.1:'+str(trusted['config']['ports']['guard'])
    status,body=http_json(guard+'/__recovery/status')
    if status!=200 or not isinstance(body,dict) or type(body.get('admission_paused')) is not bool:
        raise DistributionError('owned guard admission state is unavailable')
    return body


def recover_closed_cleanup(trusted, state):
    # A retained cleanup permit may still count active=1. Only the authenticated
    # guard endpoint can prove closed owner/owned native exit and clear it.
    if (state.get('admission_paused') is not True or state.get('pause_reason')!='resource_release_unverified'
        or state.get('active_stage')!='cleanup' or type(state.get('pending_inferences')) is not int
        or state['pending_inferences']!=0):
        raise DistributionError('guard cleanup is not eligible for recovery; stop refused without signaling')
    guard='http://127.0.0.1:'+str(trusted['config']['ports']['guard'])
    status,body=http_json(guard+'/__recovery/maintenance/recover',method='POST',payload={},
                          control=trusted['secrets']['GUARD_CONTROL_TOKEN'],timeout=65)
    if (status!=200 or not isinstance(body,dict) or body.get('recovered') is not True
        or body.get('admission_paused') is not False
        or body.get('inference_completed') is not False or body.get('native_lane_counters_modified') is not False
        or type(body.get('active_inferences')) is not int or body['active_inferences']!=0
        or type(body.get('pending_inferences')) is not int or body['pending_inferences']!=0):
        raise DistributionError('owned cleanup recovery was not proven; stop refused without signaling')
    after=guard_admission_status(trusted)
    if after['admission_paused'] or after.get('active_inferences')!=0 or after.get('pending_inferences')!=0:
        raise DistributionError('guard is busy or remained paused after recovery; stop refused without signaling')
    return after


def drain(trusted):
    cfg=trusted['config'];secret=trusted['secrets'];guard='http://127.0.0.1:'+str(cfg['ports']['guard']);manager='http://127.0.0.1:'+str(cfg['ports']['manager'])
    state=guard_admission_status(trusted)
    if state['admission_paused'] and state.get('pause_reason')=='resource_release_unverified':
        recover_closed_cleanup(trusted,state)
    status,_=http_json(guard+'/__recovery/maintenance/lock',method='POST',payload={},control=secret['GUARD_CONTROL_TOKEN'])
    if status!=200:raise DistributionError('stack is busy or guard unavailable; stop refused without signaling')
    success=False
    try:
        status,_=http_json(manager+'/api/models/unload',method='POST',payload={},token=secret['MANAGER_BACKEND_TOKEN'],timeout=60)
        if status not in (200,202):raise DistributionError('owned model unload failed; stop refused')
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            status,response=http_json(manager+'/running',token=secret['MANAGER_BACKEND_TOKEN'])
            running=response if isinstance(response,list) else response.get('running') if isinstance(response,dict) else None
            if status==200 and running in ([],0):
                record=trusted['data_root']/'run/model.json'
                if not record.exists():success=True;return
            time.sleep(.25)
        raise DistributionError('owned model did not drain before stop timeout')
    finally:
        if not success:http_json(guard+'/__recovery/maintenance/unlock',method='POST',payload={},control=secret['GUARD_CONTROL_TOKEN'])


def run(data,*,controlplane_only=False):
    print('LILIUXFLOW_AGENT_STAGE validate',flush=True)
    def trust_trace(event):
        file=no_symlinks(data/'logs/trust-stage.jsonl')
        descriptor=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(descriptor,'a') as output:output.write(json.dumps(event)+'\n')
    trusted=validate(data,require_checkpoint='controlplane' if controlplane_only else 'available',trace=trust_trace);trusted['controlplane_only']=controlplane_only or not trusted['model_configured'];cfg=trusted['config'];ports=cfg['ports'];source=trusted['source_root'];children={};postgres=None;requested_stop=False
    def stop_signal(_signal,_frame):
        nonlocal requested_stop
        requested_stop=True
    signal.signal(signal.SIGTERM,stop_signal);signal.signal(signal.SIGINT,stop_signal)
    if not cfg['database_initialized']:raise DistributionError('initialize this installation database before start')
    state=data/'run/agent.json'
    if state.exists():raise DistributionError('prior process registry exists; explicit ownership audit required')
    print('LILIUXFLOW_AGENT_STAGE port_preflight',flush=True)
    preflight_ports(ports)
    with mutation(data):
        print('LILIUXFLOW_AGENT_STAGE configs',flush=True)
        manager,lp=configs(trusted)
        print('LILIUXFLOW_AGENT_STAGE environment',flush=True)
        common,lp_env=safe_environment(trusted)
        print('LILIUXFLOW_AGENT_STAGE registry',flush=True)
        me=capture(os.getpid());record={'schema_version':1,'installation_id':cfg['installation_id'],'agent':me,'children':{},'postgres':None,'ready':False,'controlplane_only':trusted['controlplane_only'],'model_configured':trusted['model_configured']}
        write_json_new(state,record)
        try:
            postgres=pg_start(trusted);record['postgres']=postgres;atomic_private_json(state,record)
            cp=trusted['binaries']['compat_python'];lp_python=trusted['binaries']['litellm_python']
            commands={'litellm':[lp_python,'-m','litellm.proxy.proxy_cli','--config',lp,'--host','127.0.0.1','--port',str(ports['litellm'])],
                      'manager':[trusted['binaries']['llama_swap'],'-config',manager,'-listen','127.0.0.1:'+str(ports['manager'])],
                      'guard':[cp,'-m','uvicorn','compat_api.portable:create_guard_app','--factory','--host','127.0.0.1','--port',str(ports['guard']),'--no-access-log','--log-level','warning','--timeout-graceful-shutdown','10'],
                      'compat':[cp,'-m','uvicorn','compat_api.portable:create_compat_app','--factory','--host','127.0.0.1','--port',str(ports['compat']),'--no-access-log','--log-level','warning','--timeout-graceful-shutdown','5']}
            urls={'litellm':'/health/readiness','manager':'/health','guard':'/health','compat':'/api/capabilities'}
            for name in ('litellm','manager','guard','compat'):
                log=data/'logs'/(name+'.log');descriptor=os.open(log,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
                with os.fdopen(descriptor,'ab') as output:
                    proc=subprocess.Popen([str(x) for x in commands[name]],cwd=source,env=lp_env if name=='litellm' else common,stdout=output,stderr=subprocess.STDOUT)
                ident=capture(proc.pid,parent=os.getpid(),executable=commands[name][0]);children[name]=(proc,ident);record['children'][name]=ident;atomic_private_json(state,record)
                token=trusted['secrets']['MANAGER_BACKEND_TOKEN'] if name in ('manager','guard') else read_object(private_file(data/'secrets/caller.json'))['api_key'] if name=='compat' else None
                wait_http('http://127.0.0.1:'+str(ports[name])+urls[name],token=token,seconds=180 if name=='litellm' else 60,abort=lambda:requested_stop,
                          diagnostics=lambda event:startup_diagnostic(data,name,event),
                          health_contract='llama_swap_health' if name in ('manager','guard') else None)
                if name=='litellm':make_caller(trusted)
            make_caller(trusted);record['ready']=True;atomic_private_json(state,record)
            while not requested_stop:
                if any(proc.poll() is not None for proc,_ in children.values()):
                    raise DistributionError('owned control-plane child exited unexpectedly')
                time.sleep(.5)
            # CLI stop drained first. An external launchd signal must also pass the guard.
            status,body=http_json('http://127.0.0.1:'+str(ports['guard'])+'/__recovery/status')
            if status!=200 or not body.get('admission_paused'):
                drain(trusted)
        finally:
            # Unload lazy models before allowing launchd to reload the manager.
            manager_child=children.get('manager')
            model=data/'run/model.json'
            if model.exists():
                model_record=read_object(model)
                runner=model_record.get('runner');child=model_record.get('child')
                if runner and child and unchanged(runner) and unchanged(child):
                    lineage=descendants(me['pid'])
                    if child['pid'] not in {x['pid'] for x in lineage} or runner['pid'] not in {x['pid'] for x in lineage}:
                        raise DistributionError('model lineage differs; cleanup refused')
                    if not terminate(runner,grace=30):raise DistributionError('owned model runner did not stop')
            for name in ('compat','guard','manager','litellm'):
                if name in children and not terminate(children[name][1],grace=20):raise DistributionError('owned child did not exit; ownership registry retained')
            if postgres:pg_stop(trusted,postgres)
            if read_object(state).get('agent')==me:state.unlink()
    return 0


def user_launchd_log(trusted,*,prepare=False):
    cfg=trusted['config'];value=cfg['installation_id']
    try:identity=str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError):raise DistributionError('installation log UUID is invalid')
    if identity!=value or cfg.get('owner_uid')!=os.getuid():raise DistributionError('installation log identity/owner differs')
    base=no_symlinks(Path.home()/'Library/Logs/LiliuxFlow'/identity)
    log=no_symlinks(base/'agent.log')
    if prepare:
        private_directory(base)
        fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.getuid()or info.st_mode&0o777!=0o600 or info.st_nlink!=1:raise DistributionError('installation user log owner/mode/link identity differs')
        finally:os.close(fd)
    return log


def launchd_plist(trusted):
    data=trusted['data_root'];source=trusted['source_root'];cfg=trusted['config']
    return {'Label':cfg['launchd_label'],'ProgramArguments':[str(trusted['binaries']['compat_python']),str(source/'scripts/distribution/agent.py'),'--data-root',str(data)]+(['--controlplane-only'] if trusted.get('controlplane_only') else []),
            'WorkingDirectory':str(source),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':10,
            'ExitTimeOut':90,'AbandonProcessGroup':False,'Umask':63,'ProcessType':'Interactive',
            'StandardOutPath':str(user_launchd_log(trusted)),'StandardErrorPath':str(user_launchd_log(trusted))}


def start(data,*,dry_run=False,controlplane_only=False,readiness_seconds=180):
    if type(readiness_seconds) is not int or not 1<=readiness_seconds<=180:raise DistributionError('startup readiness bound is invalid')
    trusted=validate(data,require_checkpoint='controlplane' if controlplane_only else 'available')
    trusted['controlplane_only']=controlplane_only or not trusted['model_configured']
    if not trusted['config']['database_initialized']:raise DistributionError('database initialization is required')
    if dry_run:return {'state':'validated_start_plan','launchd_label':trusted['config']['launchd_label'],'loopback_ports':trusted['config']['ports'],'model_load':'lazy' if trusted['model_configured'] else 'not_configured','inference_ready':False,'credentials_printed':False}
    preflight_ports(trusted['config']['ports'])
    log=user_launchd_log(trusted,prepare=True)
    # Stop/uninstall retain user logs; database/credential backups do not export them.
    plist=data/'run/stack.plist'
    if plist.exists():private_file(plist)
    else:write_new(plist,plistlib.dumps(launchd_plist(trusted)))
    result=subprocess.run(['/bin/launchctl','bootstrap','gui/'+str(os.getuid()),str(plist)],capture_output=True)
    if result.returncode:raise DistributionError('own user LaunchAgent bootstrap failed')
    kick=subprocess.run(['/bin/launchctl','kickstart','gui/'+str(os.getuid())+'/'+trusted['config']['launchd_label']],capture_output=True)
    if kick.returncode:raise DistributionError('own user LaunchAgent scheduling failed')
    deadline=time.monotonic()+readiness_seconds
    while time.monotonic()<deadline:
        state=data/'run/agent.json'
        if state.exists() and read_object(state).get('ready') is True:
            return {'state':'running','launchd_label':trusted['config']['launchd_label'],'caller_credentials':'private secrets/caller.json','model_load':'lazy' if trusted['model_configured'] else 'not_configured','controlplane_ready':True,'inference_ready':not trusted['controlplane_only']}
        time.sleep(.5)
    raise DistributionError('own LaunchAgent started but readiness failed; inspect private logs and ownership registry')


def _force_pg_identity(trusted,registry,fs):
    recorded=registry.get('postgres');pgdata=no_symlinks(trusted['data_root']/'postgres/data');pidfile=no_symlinks(pgdata/'postmaster.pid')
    if recorded is None:
        if pidfile.exists():raise DistributionError('unregistered PostgreSQL PID file; force stop refused')
        return None,None
    fs.identity(recorded)
    if recorded['uid']!=os.getuid():raise DistributionError('force stop PostgreSQL owner differs')
    if not fs.exact(recorded):raise DistributionError('registered PostgreSQL already absent; explicit record review required')
    private_directory(pgdata);content=private_file(pidfile).read_bytes();lines=content.decode().splitlines()
    if len(lines)<4 or int(lines[0])!=recorded['pid'] or Path(lines[1])!=pgdata or int(lines[3])!=trusted['config']['ports']['postgresql']:
        raise DistributionError('force stop PostgreSQL PID/PGDATA/port differs')
    pg=postgres_tools(trusted);command=fs.command(recorded);prefix=str(pg/'postgres')+' -D '+str(pgdata)
    import datetime
    started=datetime.datetime.strptime(recorded['started'],'%a %b %d %H:%M:%S %Y').timestamp()
    if not (command==prefix or command.startswith(prefix+' ')) or abs(started-int(lines[2]))>5:
        raise DistributionError('force stop PostgreSQL binary/data/start differs')
    return recorded,content

def _wait_launchd_removed(target, released, *, seconds=5, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """A read-only bounded settle for the already validated installation label."""
    deadline=clock()+seconds
    while True:
        if released() is not True:raise DistributionError('owned force shutdown incomplete; registry retained')
        remaining=deadline-clock()
        if remaining<=0:raise DistributionError('own launchd label remains registered')
        status=runner(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=max(.01,min(1,remaining)))
        if status.returncode:
            diagnostic=(status.stderr or '').lower()
            if 'could not find service' not in diagnostic and 'no such process' not in diagnostic:
                raise DistributionError('own launchd removal status is unknown')
            if released() is not True:raise DistributionError('owned resources changed during launchd removal')
            return
        sleep(min(.05,max(0,deadline-clock())))


def _force_stop(trusted,*,dry_run=False):
    import forced_stop as fs
    cfg=trusted['config'];data=trusted['data_root'];source=trusted['source_root'];label=cfg['launchd_label']
    if cfg.get('owner_uid')!=os.getuid() or label!='com.diurnoctra.liliuxflow.'+str(uuid.UUID(cfg['installation_id'])):
        raise DistributionError('force stop installation label/owner differs')
    state=no_symlinks(data/'run/agent.json');plist=no_symlinks(data/'run/stack.plist')
    if not state.exists():raise DistributionError('force stop needs an exact owned agent registry; use normal stop for an unstarted installation')
    record=read_object(private_file(state));decoded=plistlib.loads(private_file(plist).read_bytes())
    arguments=decoded.get('ProgramArguments');prefix=[str(trusted['binaries']['compat_python']),str(source/'scripts/distribution/agent.py'),'--data-root',str(data)]
    if (type(record.get('schema_version')) is not int or record['schema_version']!=1 or record.get('installation_id')!=cfg['installation_id']
            or not isinstance(record.get('children'),dict)
            or decoded.get('Label')!=label or decoded.get('WorkingDirectory')!=str(source)
            or not isinstance(arguments,list) or arguments[:4]!=prefix or arguments[4:] not in ([],['--controlplane-only'])):
        raise DistributionError('force stop owned registry/plist installation differs')
    owner=fs.identity(record.get('agent'))
    if not fs.exact(owner) or fs.command(owner)!=' '.join(arguments):raise DistributionError('force stop agent identity/command differs')
    target='gui/'+str(os.getuid())+'/'+label
    printed=subprocess.run(['/bin/launchctl','print',target],capture_output=True,text=True,timeout=5)
    import re
    match=re.search(r'^\s*pid\s*=\s*(\d+)\s*$',printed.stdout,re.M)
    if printed.returncode or not match or int(match.group(1))!=owner['pid']:raise DistributionError('force stop exact launchd PID differs')
    pg,pgcontent=_force_pg_identity(trusted,record,fs)
    pgfamily={pg['pid'],*(x['pid'] for x in fs.descendants(pg['pid']))} if pg is not None else set()
    def non_database_tree(values):
        excluded=set(pgfamily)
        while True:
            new=excluded|{x['pid'] for x in values if x['ppid'] in excluded}
            if new==excluded:break
            excluded=new
        return [x for x in values if x['pid'] not in excluded]
    if dry_run:return {'state':'validated_force_stop_plan','force':True,'requires_idle_guard':False,'data_preserved':True}
    frozen=[];bootout=None;captured=[]
    try:
        fs.freeze_tree_for_stop(owner,captured,frozen,non_database_tree)
        bypid={x['pid']:x for x in captured}
        for name,value in record.get('children',{}).items():
            fs.identity(value)
            if fs.exact(value) and bypid.get(value['pid'])!=value:raise DistributionError('registered owned child outside frozen agent tree')
        model=data/'run/model.json';model_record=read_object(private_file(model)) if model.exists() else None
        if model_record is not None:
            if type(model_record.get('schema_version')) is not int or model_record['schema_version']!=1 or model_record.get('installation_id')!=cfg['installation_id'] or model_record.get('binary_sha256')!=trusted['trust']['binaries']['lily']['sha256']:
                raise DistributionError('force stop model installation/binary differs')
            for key in ('runner','child'):
                value=fs.identity(model_record.get(key))
                if fs.exact(value) and bypid.get(value['pid'])!=value:raise DistributionError('owned model lies outside verified agent tree')
        bootout=subprocess.Popen(['/bin/launchctl','bootout',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        fs.terminate_all(captured,resume=True)
        if pg is not None:
            if fs.exact(pg):
                if private_file(data/'postgres/data/postmaster.pid').read_bytes()!=pgcontent:raise DistributionError('owned PGDATA PID file changed before fast stop')
                private_run([postgres_tools(trusted)/'pg_ctl','-D',data/'postgres/data','-m','fast','-w','-t','30','stop'],env=pg_environment(trusted),timeout=40)
            if not fs.exited(pg):raise DistributionError('owned PostgreSQL remains after normal fast stop')
        fs.terminate_all([owner],grace=5,resume=True)
        bootout.wait(timeout=15)
        if bootout.returncode or any(not fs.exited(x) for x in captured+[owner]):raise DistributionError('owned force shutdown incomplete; registry retained')
        _wait_launchd_removed(target,lambda:all(fs.exited(x) for x in captured+[owner]+([pg] if pg is not None else [])),runner=subprocess.run)
        _force_clear_model_records(trusted,captured,fs)
        if state.exists():
            if read_object(private_file(state))!=record:raise DistributionError('force stop agent registry changed; retained for review')
            state.unlink()
        plist.unlink(missing_ok=True)
        return {'state':'stopped','force':True,'data_preserved':True,'model_preserved':True,'postgres_fast_stop':pg is not None}
    finally:
        for value in reversed(frozen):
            try:fs.send(value,signal.SIGCONT)
            except (DistributionError,OSError):pass

def _force_clear_model_records(trusted,captured,fs):
    # No event counters or identity fields are rewritten. Only exact exited
    # installation-owned stale records may be removed after physical teardown.
    data=trusted['data_root'];bypid={x['pid']:x for x in captured};lease=no_symlinks(Path.home()/'Library/Application Support/LiliuxFlow-runtime-leases/gpu.lease')
    path=lease/'lease.json'
    if path.exists():
        record=read_object(private_file(path))
        if record.get('installation_id')==trusted['config']['installation_id']:
            if type(record.get('schema_version')) is not int or record['schema_version']!=1:raise DistributionError('owned runtime lease schema differs')
            private_directory(lease);before=lease.stat()
            if record.get('binary_sha256')!=trusted['trust']['binaries']['lily']['sha256']:raise DistributionError('owned runtime lease binary differs')
            for key in ('runner','child'):
                value=record.get(key)
                if value is not None:
                    fs.identity(value)
                    if bypid.get(value['pid'])!=value or not fs.exited(value):raise DistributionError('runtime lease owner is not an exact exited captured child')
            if {x.name for x in lease.iterdir()}!={'lease.json'} or read_object(private_file(path))!=record:raise DistributionError('owned runtime lease changed')
            moved=lease.with_name('gpu.lease.portable-stopped-'+uuid.uuid4().hex);os.rename(lease,moved)
            if (moved.stat().st_dev,moved.stat().st_ino)!=(before.st_dev,before.st_ino) or read_object(private_file(moved/'lease.json'))!=record:
                if not lease.exists():os.rename(moved,lease)
                raise DistributionError('runtime lease quarantine identity differs')
            (moved/'lease.json').unlink();moved.rmdir()
    model=data/'run/model.json'
    if model.exists():
        record=read_object(private_file(model))
        if record.get('installation_id')!=trusted['config']['installation_id']:raise DistributionError('retained model record belongs to another installation')
        for key in ('runner','child'):
            value=record.get(key)
            if value is not None and (bypid.get(fs.identity(value)['pid'])!=value or not fs.exited(value)):raise DistributionError('retained model record owner is unverified')
        model.unlink()

def stop(data,*,dry_run=False,controlplane_only=False,force=False):
    trusted=validate(data,require_checkpoint=False);state=data/'run/agent.json'
    if force:return _force_stop(trusted,dry_run=dry_run)
    if not state.exists():
        plist=data/'run/stack.plist'
        if not plist.exists():return {'state':'already_stopped','data_preserved':True}
        decoded=plistlib.loads(private_file(plist).read_bytes())
        arguments=decoded.get('ProgramArguments',[])
        expected_prefix=[str(trusted['binaries']['compat_python']),str(trusted['source_root']/'scripts/distribution/agent.py'),'--data-root',str(data)]
        if decoded.get('Label')!=trusted['config']['launchd_label'] or arguments[:4]!=expected_prefix or arguments[4:] not in ([],['--controlplane-only']):
            raise DistributionError('pre-start agent plist identity differs; no job signaled')
        label='gui/'+str(os.getuid())+'/'+trusted['config']['launchd_label']
        status=subprocess.run(['/bin/launchctl','print',label],capture_output=True,text=True)
        if status.returncode:
            if dry_run:return {'state':'validated_stop_plan','launchd_loaded':False,'data_preserved':True}
            plist.unlink();return {'state':'already_stopped','data_preserved':True}
        import re
        match=re.search(r'^\s*pid = (\d+)$',status.stdout,re.M)
        identity=capture(int(match.group(1)),executable=trusted['binaries']['compat_python']) if match else None
        if identity and descendants(identity['pid']):
            raise DistributionError('unregistered agent has descendants; explicit owned review required')
        if dry_run:return {'state':'validated_stop_plan','launchd_loaded':True,'data_preserved':True}
        removed=subprocess.run(['/bin/launchctl','bootout',label],capture_output=True)
        if removed.returncode:raise DistributionError('pre-start own agent removal failed')
        if identity:
            from ownership import wait_gone
            if not wait_gone(identity,30):raise DistributionError('pre-start own agent remains; plist retained')
        if state.exists():raise DistributionError('startup registry appeared during stop; inspect exact owned records')
        plist.unlink();return {'state':'stopped_before_initialization','data_preserved':True}
    registry=read_object(private_file(state))
    if registry.get('installation_id')!=trusted['config']['installation_id'] or not unchanged(registry['agent']):
        raise DistributionError('owned agent identity differs; no process signaled')
    if dry_run:return {'state':'validated_stop_plan','requires_idle_guard':True,'data_preserved':True}
    drain(trusted)
    held=guard_admission_status(trusted)
    if (held.get('admission_paused') is not True or held.get('pause_reason')!='session_maintenance'
        or type(held.get('active_inferences')) is not int or held['active_inferences']!=0
        or type(held.get('pending_inferences')) is not int or held['pending_inferences']!=0):
        raise DistributionError('normal stop atomic idle hold was not proven')
    from runtime_proof import RunnerResourceProbe
    probe=RunnerResourceProbe(trusted);native=probe._state()
    profile=trusted['registry'].default if native is None else probe.profiles.get(native.get('profile_id'))
    if profile is None or not probe._probe(profile,'before_unload') or not probe._probe(profile,'unloaded'):
        raise DistributionError('normal stop native ownership/exit is unverified')
    # Reuse existing exact owned shutdown/normal PG fast stop after idle drain.
    result=_force_stop(trusted,dry_run=False)
    if result.get('state')!='stopped' or result.get('data_preserved') is not True:
        raise DistributionError('normal stop owned shutdown did not complete')
    if trusted['config'].get('database_initialized') is True and result.get('postgres_fast_stop') is not True:
        raise DistributionError('normal stop initialized PostgreSQL exit is unverified')
    return {**result,'force':False,'normal_idle_drain':True,'native_exit_proven':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--controlplane-only',action='store_true');args=parser.parse_args();os.umask(0o077)
    try:raise SystemExit(run(args.data_root,controlplane_only=args.controlplane_only))
    except (DistributionError,OSError,ValueError,KeyError) as error:
        reason=str(error) if isinstance(error,DistributionError) else type(error).__name__
        print('LILIUXFLOW_AGENT_STOPPED reason='+reason,flush=True)
        raise SystemExit(2)
