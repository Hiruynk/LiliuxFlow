#!/usr/bin/env python3
"""Bounded native control-plane exercise on an isolated installation. It never sends inference."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time
from common import DistributionError,read_object,no_symlinks,write_json_new
from trust import validate,private_file
import agent
from backup_native import backup
from ownership import unchanged,terminate


def emergency_startup_cleanup(trusted):
    data=trusted['data_root'];state=data/'run/agent.json';plist=data/'run/stack.plist'
    if not state.exists():
        if plist.exists():
            decoded=plistlib.loads(private_file(plist).read_bytes())
            if decoded.get('Label')!=trusted['config']['launchd_label'] or '--controlplane-only' not in decoded.get('ProgramArguments',[]):
                raise DistributionError('startup plist identity differs')
            subprocess.run(['/bin/launchctl','bootout','gui/'+str(os.getuid())+'/'+decoded['Label']],capture_output=True)
            plist.unlink()
        return {'state':'no_owned_process_registry'}
    registry=read_object(private_file(state))
    if registry.get('installation_id')!=trusted['config']['installation_id'] or (data/'run/model.json').exists() or (data/'runtime/checkpoint-verified.json').exists():
        raise DistributionError('emergency startup cleanup cannot establish control-plane-only scope')
    decoded=plistlib.loads(private_file(plist).read_bytes())
    if decoded.get('Label')!=trusted['config']['launchd_label'] or '--controlplane-only' not in decoded.get('ProgramArguments',[]):
        raise DistributionError('startup plist identity differs')
    subprocess.run(['/bin/launchctl','bootout','gui/'+str(os.getuid())+'/'+decoded['Label']],capture_output=True)
    if unchanged(registry['agent']) and not terminate(registry['agent'],grace=30):
        raise DistributionError('owned startup agent remained; ownership registry and plist retained')
    for identity in reversed(list(registry['children'].values())):
        if unchanged(identity) and not terminate(identity,grace=20):raise DistributionError('owned startup child remained')
    if registry.get('postgres') and unchanged(registry['postgres']):agent.pg_stop(trusted,registry['postgres'])
    if state.exists():
        latest=read_object(private_file(state))
        if latest.get('agent')==registry['agent']:state.unlink()
    lease=data/'run/host-mutation.lease'
    if lease.exists():
        record=read_object(private_file(lease/'lease.json'))
        if record==registry['agent'] and not unchanged(record):
            (lease/'lease.json').unlink();lease.rmdir()
    plist.unlink(missing_ok=True)
    return {'state':'owned_startup_cleanup_complete','model_was_loaded':False}


def check(data,evidence):
    data=no_symlinks(data);trusted=validate(data,require_checkpoint='controlplane');started=time.monotonic()
    if (data/'runtime/checkpoint-verified.json').exists():raise DistributionError('control-plane exercise requires an installation without a payload verification receipt')
    # Read-only lsof preflight. Only our explicit five named ports are inspected.
    preflight=[]
    for name,port in trusted['config']['ports'].items():
        result=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True)
        if result.returncode!=1:raise DistributionError('test port is occupied or occupancy is unknown')
        preflight.append({'service':name,'port':port,'listener_before':False})
    report={'schema_version':1,'scope':'own native control plane with inference disabled','status':'RUNNING','installation_id':trusted['config']['installation_id'],
            'model_configured':trusted.get('model_configured',True),'inference_ready':False,
            'preflight':preflight,'model_loaded':False,'model_inference_requests':0,'checkpoint_payload_verified':False,'operations':[],'owned_processes':[]}
    bootstrap=(data/'secrets/bootstrap.json').read_bytes();stage='initialize'
    try:
        report['operations'].append({'command':'initialize --controlplane-only --execute','result':agent.initialize(data,controlplane_only=True)})
        for cycle in (1,2):
            if time.monotonic()-started>240:raise DistributionError('bounded exercise time budget exhausted')
            stage='start_cycle_'+str(cycle)
            report['operations'].append({'command':'start --controlplane-only','cycle':cycle,'result':agent.start(data,controlplane_only=True)})
            registry=read_object(private_file(data/'run/agent.json'));report['owned_processes'].append({'cycle':cycle,**registry})
            owncaller=(data/'secrets/caller.json').read_bytes();caller=json.loads(owncaller);master=trusted['secrets']['LITELLM_MASTER_KEY'];ports=trusted['config']['ports']
            # Only metadata endpoints, all synthetic identity data stay private.
            results={}
            for name,url,token in [('litellm','http://127.0.0.1:'+str(ports['litellm'])+'/health/readiness',None),
                                   ('manager','http://127.0.0.1:'+str(ports['manager'])+'/running',trusted['secrets']['MANAGER_BACKEND_TOKEN']),
                                   ('guard','http://127.0.0.1:'+str(ports['guard'])+'/__recovery/status',None),
                                   ('compat','http://127.0.0.1:'+str(ports['compat'])+'/api/capabilities',caller['api_key'])]:
                status,body=agent.http_json(url,token=token);results[name]=status
                if status!=200:raise DistributionError('owned metadata endpoint is unhealthy')
                if name=='manager':
                    running=body if isinstance(body,list) else body.get('running')
                    if running not in ([],0):raise DistributionError('unexpected model residency in control-plane-only exercise')
            report['operations'].append({'command':'metadata health only','cycle':cycle,'statuses':results,'caller_credentials_printed':False})
            stage='stop_cycle_'+str(cycle)
            report['operations'].append({'command':'stop --controlplane-only','cycle':cycle,'result':agent.stop(data,controlplane_only=True)})
            stage='ownership_readback_cycle_'+str(cycle)
            readback={'command':'ownership readback','cycle':cycle,'all_recorded_pids_gone':all(not unchanged(x) for x in [registry['agent'],registry['postgres'],*registry['children'].values()]),'bootstrap_preserved':bootstrap==(data/'secrets/bootstrap.json').read_bytes(),'caller_preserved':owncaller==(data/'secrets/caller.json').read_bytes()}
            report['operations'].append(readback)
            if not all(readback[name] for name in ('all_recorded_pids_gone','bootstrap_preserved','caller_preserved')):
                raise DistributionError('owned stop identity or credential preservation readback differs')
            if cycle==1:
                stage='backup';report['operations'].append({'command':'backup stopped own database','result':backup(data,data/'backups/controlplane-before-restart.tar.gz')})
        report['status']='PASS'
    except (DistributionError,OSError,ValueError,KeyError,TypeError) as error:
        report['status']='FAIL';report['first_failure_stage']=stage;report['error']=str(error) if isinstance(error,DistributionError) else type(error).__name__
        try:report['cleanup']=emergency_startup_cleanup(trusted)
        except (DistributionError,OSError,ValueError,KeyError) as cleanup:report['cleanup']={'state':'NEEDS_OWNED_REVIEW','error':str(cleanup) if isinstance(cleanup,DistributionError) else type(cleanup).__name__}
    report['elapsed_seconds']=time.monotonic()-started
    report['listener_returncodes']={name:subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:'+str(port),'-sTCP:LISTEN'],capture_output=True).returncode for name,port in trusted['config']['ports'].items()}
    report['remaining_listeners']={name:False if code==1 else True if code==0 else None for name,code in report['listener_returncodes'].items()}
    if any(code!=1 for code in report['listener_returncodes'].values()):
        report['status']='FAIL'
        report.setdefault('first_failure_stage','final_listener_readback')
        report.setdefault('error','owned listener remained or final listener occupancy is unknown')
    write_json_new(evidence,report)
    print(json.dumps({k:report[k] for k in ('status','elapsed_seconds','model_loaded','checkpoint_payload_verified','remaining_listeners')}))
    return 0 if report['status']=='PASS' and all(code==1 for code in report['listener_returncodes'].values()) else 2

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args();os.umask(0o077)
    raise SystemExit(check(args.data_root,args.evidence))
