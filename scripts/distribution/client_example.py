#!/usr/bin/env python3
"""Two local API examples using this caller's private virtual key. Never use the master key for inference."""
import argparse
import codecs
import json
import os
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError,URLError
from common import DistributionError,read_object,no_symlinks
from trust import ALIAS,installation,private_file


def request_parts(data,api,stream,prompt,model=None):
    config=installation(data);caller=read_object(private_file(data/'secrets/caller.json'))
    base='http://127.0.0.1:'+str(config['ports']['litellm']);compat='http://127.0.0.1:'+str(config['ports']['compat'])
    if caller.get('api_base')!=base+'/v1' or caller.get('compat_base')!=compat or caller.get('alias')!=ALIAS:
        raise DistributionError('caller identity or loopback endpoints differ')
    key=caller.get('api_key')
    if not isinstance(key,str) or not key.startswith('sk-'):
        raise DistributionError('caller virtual key is unavailable')
    from profile_registry import load_registry
    profile=load_registry(Path(__file__).resolve().parents[2]).resolve(model or caller['alias'])
    if profile.public_alias not in caller.get('models',[caller['alias']]):
        raise DistributionError('selected model is not enabled for this caller; update designated owner ACL first')
    body={'model':profile.public_alias,'messages':[{'role':'user','content':prompt}],'stream':stream}
    if api=='openai':body['max_tokens']=4096;url=base+'/v1/chat/completions'
    else:body['options']={'num_predict':4096};url=compat+'/api/chat'
    return url,body,key


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,default=Path.home()/'Library/Application Support/LiliuxFlow');parser.add_argument('--api',choices=('openai','legacy'),required=True);parser.add_argument('--model',help='Enabled canonical context profile model name (default: 64K)');parser.add_argument('--stream',action='store_true');parser.add_argument('--prompt',default='請用一句話介紹你自己。');args=parser.parse_args()
    try:
        url,body,key=request_parts(no_symlinks(args.data_root),args.api,args.stream,args.prompt,args.model)
        req=Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        from profile_registry import load_registry
        profile=load_registry(Path(__file__).resolve().parents[2]).resolve(body['model'])
        with build_opener(ProxyHandler({})).open(req,timeout=profile.total_deadline_seconds+50) as response:
            # Preserve each API's own wire format, thinking/content and terminal events.
            decoder=codecs.getincrementaldecoder('utf-8')()
            while chunk:=response.read1(4096):
                print(decoder.decode(chunk),end='',flush=True)
            print(decoder.decode(b'',final=True),end='',flush=True)
        return 0
    except HTTPError as error:
        print(json.dumps({'state':'api_error','status':error.code}));return 2
    except (DistributionError,OSError,ValueError,URLError):
        print(json.dumps({'state':'error','detail':'local API or caller credentials unavailable; values withheld'}));return 2

if __name__=='__main__':raise SystemExit(main())
