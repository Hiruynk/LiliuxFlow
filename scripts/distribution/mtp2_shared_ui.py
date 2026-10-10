# SPDX-License-Identifier: Apache-2.0
"""Shared MTP2 UI projections: public aliases/counts only, no request identities."""
from __future__ import annotations
import copy,json
from common import DistributionError
from mtp2_shared_config import PUBLIC_ALIASES, RESIDENT_CONTEXT_TOKENS

CONTEXTS=dict(zip(PUBLIC_ALIASES,(65536,131072,262144)))

def catalog_projection(value,rows,*,state,busy):
    if state not in {'ready','loading','unloaded','unavailable'} or type(busy) is not bool:
        raise DistributionError('shared UI state is unavailable')
    result=copy.deepcopy(value)
    enabled={row['model'] for row in result['profiles'] if row['model'] in CONTEXTS and row['enabled'] is True}
    result['profiles']=[row for row in result['profiles'] if row['model'] not in CONTEXTS or row['model'] in enabled]
    if not enabled:return result
    totals={'active':0,'pending':0,'cleanup':0}
    for profile in result['profiles']:
        alias=profile['model']
        if alias not in enabled:continue
        counts={'active':0,'pending':0,'cleanup':0}
        for row in rows:
            if row.admitted.alias!=alias:continue
            if row.handle is None:counts['pending']+=1
            elif row.native_done or row.reader_done or row.caller_cancelled or row.generation_failed:counts['cleanup']+=1
            else:counts['active']+=1
        profile.update(counts)
        profile['queued']=counts['pending']
        profile['state']=('cleanup' if counts['cleanup'] else 'active' if counts['active'] else 'loading' if counts['pending'] else state)
        for name in totals:totals[name]+=counts[name]
    if not enabled:return result
    result['shared_engine']={'state':state,'resident_context_tokens':RESIDENT_CONTEXT_TOKENS,
        'ram_bytes':None,'busy':busy,**totals}
    scope=catalog_scope.get()
    if scope is not None:
        result['profiles']=[row for row in result['profiles'] if row['model'] not in CONTEXTS or row['model'] in scope]
        if result.get('active_model') in CONTEXTS and result['active_model'] not in scope:result['active_model']=None
        result['queued_models']=[alias for alias in result.get('queued_models',[]) if alias not in CONTEXTS or alias in scope]
        if not scope:
            result.pop('shared_engine',None);result['pending_count']=len(result['queued_models']);result['shared_available']=True
    return result

def summary_projection(value,allowed):
    """Only enrich names already returned by the authenticated LP catalog."""
    if type(value) is not dict or type(value.get('profiles')) is not list:return None
    selected=[row for row in value['profiles'] if type(row) is dict and row.get('model') in CONTEXTS and row['model'] in allowed]
    group=value.get('shared_engine')
    if not selected or type(group) is not dict:return None
    if (group.get('state') not in {'ready','loading','unloaded','unavailable'}
        or group.get('resident_context_tokens')!=RESIDENT_CONTEXT_TOKENS or group.get('ram_bytes') is not None
        or type(group.get('busy')) is not bool):raise DistributionError('shared UI summary differs')
    limits={'active':2,'pending':4,'cleanup':6}
    for name,maximum in limits.items():
        if type(group.get(name)) is not int or not 0<=group[name]<=maximum:raise DistributionError('shared UI count differs')
    profiles=[]
    for row in selected:
        if row.get('context_tokens')!=CONTEXTS[row['model']]:raise DistributionError('shared UI context differs')
        counts={name:row.get(name) for name in limits}
        if any(type(counts[name]) is not int or not 0<=counts[name]<=maximum for name,maximum in limits.items()):raise DistributionError('shared UI count differs')
        profiles.append({'model':row['model'],'context_tokens':row['context_tokens'],'state':row['state'],**counts})
    return {'engine':{key:group[key] for key in ('state','resident_context_tokens','ram_bytes','busy',*limits)},'profiles':profiles}

class SharedModelInfo:
    """Enrich an existing authenticated handler's successful JSON; no new route.

    Snapshot lookup is deferred until that handler returned allowed MTP2 names.
    All existing auth, response errors and non-MTP2 bytes pass through unchanged.
    """
    def __init__(self,app,snapshot):self.app,self.snapshot=app,snapshot
    async def __call__(self,scope,receive,send):
        if scope.get('type')!='http' or scope.get('method')!='GET' or scope.get('path')!='/v2/model/info':
            return await self.app(scope,receive,send)
        frames=[];total=0;start=None;passthrough=False
        async def capture(message):
            nonlocal total,start,passthrough
            if passthrough:return await send(message)
            if message['type']=='http.response.start':start=message
            if message['type']=='http.response.body':total+=len(message.get('body',b''))
            frames.append(message)
            if total>512*1024:
                passthrough=True
                for frame in frames:await send(frame)
                frames.clear();return
            if message['type']!='http.response.body' or message.get('more_body',False):return
            headers={key.lower():value for key,value in start.get('headers',())} if start else {}
            if start and start['status']==200 and b'content-encoding' not in headers:
                try:
                    raw=b''.join(frame.get('body',b'') for frame in frames if frame['type']=='http.response.body')
                    value=json.loads(raw);allowed={row['model_name'] for row in value.get('data',[]) if type(row) is dict and row.get('model_name') in CONTEXTS}
                    if allowed:
                        try:value['liliuxflow_shared']=summary_projection(await self.snapshot(),allowed)
                        except Exception:value['liliuxflow_shared']=None
                        body=json.dumps(value,separators=(',',':')).encode()
                        safe=[(key,val) for key,val in start.get('headers',()) if key.lower() not in {b'content-length',b'etag',b'cache-control'}]
                        await send({**start,'headers':[*safe,(b'content-length',str(len(body)).encode()),(b'cache-control',b'no-store')]})
                        await send({'type':'http.response.body','body':body});frames.clear();return
                except (ValueError,KeyError,TypeError,AttributeError):pass
            for frame in frames:await send(frame)
            frames.clear()
        await self.app(scope,receive,capture)

import contextvars,hmac
catalog_scope=contextvars.ContextVar('shared_ui_catalog_scope',default=None)

class SharedCatalogAccess:
    """Existing shared catalog: reveal aliases/counts only after real LP auth."""
    def __init__(self,app,*,lp_url,backend_token,registry):
        from urllib.parse import urlsplit
        target=urlsplit(lp_url)
        if target.scheme!='http' or target.hostname!='127.0.0.1' or not target.port or target.path or target.query or target.fragment or target.username:
            raise DistributionError('shared UI configured LP target differs')
        self.app,self.lp_url,self.backend_token=app,lp_url,backend_token
        self.aliases=frozenset(p.public_alias for p in registry.enabled_profiles if p.public_alias in CONTEXTS)
    async def __call__(self,scope,receive,send):
        if scope.get('type')!='http' or scope.get('method')!='GET' or scope.get('path')!='/api/context-profiles':return await self.app(scope,receive,send)
        from starlette.responses import JSONResponse
        try:auth=[value.decode('ascii') for key,value in scope.get('headers',()) if key.lower()==b'authorization']
        except UnicodeError:return await JSONResponse({'error':'virtual_key_required'},status_code=401)(scope,receive,send)
        allowed=frozenset()
        if auth:
            from starlette.responses import JSONResponse
            if len(auth)!=1 or not auth[0].startswith('Bearer ') or not 0<len(auth[0][7:])<=4096:return await JSONResponse({'error':'virtual_key_required'},status_code=401)(scope,receive,send)
            if hmac.compare_digest(auth[0],'Bearer '+self.backend_token):allowed=self.aliases
            else:
                import httpx
                try:
                    async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
                        async with client.stream('GET',self.lp_url+'/v1/models',headers={'Authorization':auth[0]}) as response:
                            if response.status_code!=200:return await JSONResponse({'error':'virtual_key_not_authorized'},status_code=response.status_code if response.status_code in (401,403) else 503)(scope,receive,send)
                            raw=bytearray()
                            async for part in response.aiter_bytes():
                                if len(raw)+len(part)>32768:raise DistributionError('shared UI catalog exceeds its bound')
                                raw.extend(part)
                            data=json.loads(raw)
                    if type(data) is not dict or type(data.get('data')) is not list:raise DistributionError('shared UI catalog differs')
                    allowed=frozenset(row['id'] for row in data['data'] if type(row) is dict and type(row.get('id')) is str and row['id'] in self.aliases)
                except (DistributionError,httpx.HTTPError,ValueError):return await JSONResponse({'error':'shared_ui_unavailable'},status_code=503)(scope,receive,send)
        token=catalog_scope.set(allowed)
        try:return await self.app(scope,receive,send)
        finally:catalog_scope.reset(token)
