#!/usr/bin/env python3
"""At most two same-helper readiness observations; no listeners/PG/inference or response-body logs."""
import argparse,json,time
from pathlib import Path
from common import DistributionError,no_symlinks,write_json_new
from trust import validate
from agent import http_json


def probe(data,service,output,*,timeout=3):
    if timeout>5 or timeout<=0:raise DistributionError('probe timeout must be at most five seconds')
    trusted=validate(no_symlinks(data),require_checkpoint=False)
    if service not in {'litellm','manager','guard'}:raise DistributionError('readiness probe service is not permitted')
    path='/health/readiness' if service=='litellm' else '/health'
    url='http://127.0.0.1:'+str(trusted['config']['ports'][service])+path
    token=trusted['secrets']['MANAGER_BACKEND_TOKEN'] if service in {'manager','guard'} else None
    events=[];started=time.monotonic()
    for attempt in range(2):
        def record(event):events.append({'attempt':attempt+1,'service':service,**event})
        status,_=http_json(url,token=token,timeout=timeout,diagnostics=record)
        events.append({'attempt':attempt+1,'service':service,'stage':'helper_result','helper_status':status})
    result={'scope':'same helper and same Content-Type/auth selection as wait_http; no native acceptance',
            'events':events,'elapsed_seconds':time.monotonic()-started,'model_requested':False,'response_body_recorded':False}
    write_json_new(output,result)
    print(json.dumps({'scope':'safe_readiness_probe','service':service,'elapsed_seconds':result['elapsed_seconds'],'response_body_recorded':False}))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--service',choices=('litellm','manager','guard'),default='litellm');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--timeout',type=float,default=3);args=parser.parse_args()
    probe(args.data_root,args.service,args.output,timeout=args.timeout)
