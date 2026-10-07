#!/usr/bin/env python3
"""Bind an unstarted own runtime to an exact source snapshot; never read tensor payloads."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
from common import DistributionError,no_symlinks,read_object,write_json_new
from trust import installation,private_file,verified_file,sha256,atomic_private_json,within,validate,model_configured
from build_native import install_source_view


def prepare(data,commit):
    data=no_symlinks(data);cfg=installation(data);source=no_symlinks(Path(cfg.get('package_source_root',cfg['source_root'])))
    if (data/'run/agent.json').exists() or (data/'run/stack.plist').exists():
        raise DistributionError('stop and verify this installation before preparing its source view')
    label='gui/'+str(os.getuid())+'/'+cfg['launchd_label']
    if subprocess.run(['/bin/launchctl','print',label],capture_output=True).returncode==0:
        raise DistributionError('own LaunchAgent is still registered')
    result=subprocess.run(['git','-C',str(source),'rev-parse','--verify',commit+'^{commit}'],capture_output=True,text=True)
    if result.returncode:raise DistributionError('exact source commit is unavailable')
    commit=result.stdout.strip();trust_path=data/'runtime/release-trust.json';trust=read_object(private_file(trust_path));before=copy.deepcopy(trust)
    if trust.get('installation_id')!=cfg['installation_id']:raise DistributionError('runtime trust installation identity differs')
    for item in [*trust['binaries'].values(),*trust['build_tools'].values()]:verified_file(data,item)
    present={item['path'] for item in trust['source_files']}
    support_paths = ('scripts/distribution/backup_native.py','scripts/distribution/liliuxflow.py',
                     'scripts/distribution/model_catalog.py','scripts/distribution/model_installer.py',
                     'services/model-installer/pyproject.toml','services/model-installer/uv.lock',
                     'manifests/distribution/native-sources.json','manifests/distribution/checkpoint-files.json')
    from model_catalog import load_catalog
    load_catalog(source)
    for path in support_paths:
        if path not in present:trust['source_files'].append({'path':path,'sha256':'0'*64})
    # Every necessary serving file and public runtime manifest must be in the exact source commit.
    for item in trust['source_files']:
        blob=subprocess.run(['git','-C',str(source),'show',commit+':'+item['path']],capture_output=True)
        if blob.returncode or hashlib.sha256(blob.stdout).hexdigest()!=sha256(source/item['path']):
            raise DistributionError('serving source differs from the selected commit')
        item['sha256']=hashlib.sha256(blob.stdout).hexdigest()
    for prefix in ('manifests/distribution','profiles/distribution'):
        for file in (source/prefix).glob('*.json'):
            rel=file.relative_to(source).as_posix();blob=subprocess.run(['git','-C',str(source),'show',commit+':'+rel],capture_output=True)
            if blob.returncode or hashlib.sha256(blob.stdout).hexdigest()!=sha256(file):
                raise DistributionError('runtime public manifest differs from the selected commit')
    configured=model_configured(cfg);metadata_path=data/'runtime/checkpoint-metadata.json';metadata=None;metadata_before=None
    if configured:
        metadata=read_object(private_file(metadata_path));metadata_before=copy.deepcopy(metadata)
        expected=read_object(source/'manifests/distribution/checkpoint-files.json');model=no_symlinks(Path(cfg['model_dir']))
        if metadata.get('validation')!='metadata_only' or metadata.get('payload_verified') is not False or metadata.get('manifest_sha256')!=sha256(source/'manifests/distribution/checkpoint-files.json') or len(metadata['files'])!=len(expected['files']):
            raise DistributionError('previous metadata-only receipt is unavailable or differs')
        for record,item in zip(metadata['files'],expected['files']):
            file=within(model,item['path']);info=file.stat()
            if record['path']!=item['path'] or record['inode']!=info.st_ino or record['device']!=info.st_dev or record['mtime_ns']!=info.st_mtime_ns or record['size_bytes']!=info.st_size or info.st_size!=item['size_bytes']:
                raise DistributionError('checkpoint fingerprint changed; no tensor reads permitted in source preparation')
            if file.suffix!='.safetensors' and sha256(file)!=item['sha256']:
                raise DistributionError('runtime model metadata changed')
            record['ctime_ns']=info.st_ctime_ns
        # Header evidence is inherited transparently; payload verification stays false.
        metadata['current_check']='stat_fingerprints_and_non_tensor_hashes_only';metadata['tensor_headers_revalidated']=False
        metadata['tensor_payload_bytes_read']=0;metadata['payload_verified']=False
    view,files=install_source_view(source,data,commit,destination_name='source-'+commit[:12])
    trust['source_commit']=commit;trust['runtime_source_copy']=files;trust['optional_model_support']={'schema_version':1}
    trust['launcher_refresh']={'previous_source_commit':before['source_commit'],'new_source_commit':commit,'native_binary_hashes_unchanged':True,'source_files_match_exact_git_commit':True,'scope':'unstarted own source-view candidate; no production or tensor mutation'}
    cfg['package_source_root']=str(source);cfg['source_root']=str(view)
    write_json_new(data/('runtime/source-prepare-before.'+commit[:12]+'.json'),{'install':installation(data),'trust':before,'metadata':metadata_before})
    # Refresh only the existing hash-bound policy helper; the proxy patch and
    # native binaries retain their identities. Persist its previous source.
    policy=trust.get('litellm_profile_policy')
    if policy is not None:
        module=verified_file(data,policy['module']);verified_file(data,policy['proxy'])
        helper=source/'scripts/distribution/litellm_profile_policy.py'
        if sha256(module)!=sha256(helper):
            from common import write_new
            from patch_litellm_profiles import _atomic
            previous=data/('runtime/profile-policy-before.'+commit[:12]+'.py')
            write_new(previous,module.read_bytes());_atomic(module,helper.read_bytes())
            policy['module']['sha256']=sha256(module)
    atomic_private_json(trust_path,trust)
    if metadata is not None:atomic_private_json(metadata_path,metadata)
    atomic_private_json(data/'install.json',cfg)
    validate(data,require_checkpoint='controlplane')
    return {'state':'source_view_prepared','commit':commit,'source_files':len(files),'tensor_payload_bytes_read':0,
            'payload_verified':False,'model_configured':configured,'header_validation':'reused previous receipt; not re-read' if configured else 'not applicable; no model', 'native_binaries_changed':False,'credentials_changed':False,'database_changed':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--commit',required=True);args=parser.parse_args();os.umask(0o077)
    try:print(json.dumps(prepare(args.data_root,args.commit),sort_keys=True))
    except (DistributionError,OSError,ValueError,KeyError) as error:
        print(json.dumps({'state':'error','error':str(error) if isinstance(error,DistributionError) else type(error).__name__}));raise SystemExit(2)
