#!/usr/bin/env python3
"""Verify and exercise the exact candidate source archive in a fresh private Unicode path.

CPU bootstrap uses only synthetic metadata; native build and full model checks run separately.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from common import DistributionError,no_symlinks,private_directory,read_object,relative_path,write_json_new
from package import safe_extract


def verify_archive(archive,manifest,destination):
    archive=no_symlinks(archive);manifest=no_symlinks(manifest)
    if archive.stat().st_size>512*1024**2:raise DistributionError('candidate archive exceeds source-size cap')
    receipt=read_object(manifest)
    if receipt.get('schema_version')!=1 or not isinstance(receipt.get('commit'),str) or len(receipt['commit'])!=40 or any(c not in '0123456789abcdef' for c in receipt['commit']):
        raise DistributionError('archive receipt lacks an exact source commit')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=receipt.get('archive_sha256'):
        raise DistributionError('candidate archive checksum differs')
    entries=receipt.get('files')
    if not isinstance(entries,list) or not entries:
        raise DistributionError('candidate receipt has no source members')
    for item in entries:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str):
            raise DistributionError('candidate receipt member is invalid')
        relative_path(item['path'])
        if type(item.get('bytes')) is not int or item['bytes']<0 or not isinstance(item.get('sha256'),str) or len(item['sha256'])!=64:
            raise DistributionError('candidate receipt file identity is invalid')
    seen=safe_extract(archive,destination,max_bytes=256*1024**2)
    expected={'LiliuxFlow/'+item['path'] for item in entries}
    if len(expected)!=len(entries) or seen!=expected:
        raise DistributionError('candidate archive membership differs from its receipt')
    source=destination/'LiliuxFlow'
    for item in entries:
        file=no_symlinks(source/item['path'])
        if file.stat().st_size!=item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:
            raise DistributionError('candidate file bytes differ from the exact source receipt')
    if read_object(source/'SOURCE_COMMIT.json').get('commit')!=receipt['commit']:
        raise DistributionError('embedded source commit differs from the archive receipt')
    return source,receipt


def synthetic_checkpoint(root):
    root.mkdir(mode=0o700)
    config={'model_type':'qwen4_exp','lily':{'format':'qwen4_exp-affine-v1','quantization':{'default':{'bits':4,'mode':'affine','group_size':64}}}}
    (root/'config.json').write_text(json.dumps(config))
    for name in ('tokenizer.json','tokenizer_config.json','generation_config.json'):(root/name).write_text('{}')
    for name in ('chat_template.jinja','LICENSE'):(root/name).write_text('synthetic CPU metadata fixture; not model/license evidence')
    (root/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'synthetic':'fixture.safetensors'}}))
    # Deliberately not a tensor header or trained slice; setup checks metadata and file size only.
    (root/'fixture.safetensors').write_bytes(b'fixture!')


def bootstrap(archive,manifest,work_parent,evidence):
    work_parent=private_directory(work_parent)
    if shutil.disk_usage(work_parent).free<64*1024**3:raise DistributionError('clean-room disk floor below64GiB')
    operations=[]
    with tempfile.TemporaryDirectory(prefix='LiliuxFlow source 驗證 space-',dir=work_parent) as task:
        task=Path(task);source,receipt=verify_archive(archive,manifest,task/'unpacked')
        fixture=task/'external synthetic 模型';synthetic_checkpoint(fixture);data=task/'own data 資料'
        cli=source/'scripts/distribution/liliuxflow.py'
        def run(args,expected):
            result=subprocess.run([sys.executable,str(cli),'--data-root',str(data),*args],capture_output=True,text=True,timeout=10,cwd=source)
            parsed=json.loads(result.stdout) if args[0]!='--help' else None
            if result.returncode!=expected:raise DistributionError('same-archive bootstrap command failed')
            operations.append({'command':args[0],'exit':result.returncode,'state':parsed.get('state') if isinstance(parsed,dict) else 'help'})
            return parsed
        run(['--help'],0);run(['setup','--model-dir',str(fixture)],0)
        before=(data/'secrets/bootstrap.json').read_bytes();identity=read_object(data/'install.json')['installation_id']
        run(['setup','--model-dir',str(fixture)],0)
        if before!=(data/'secrets/bootstrap.json').read_bytes() or read_object(data/'install.json')['installation_id']!=identity:
            raise DistributionError('same-archive setup changed identity or credentials')
        run(['status'],0);run(['build'],0);run(['initialize'],0)
        for command in ('start','stop'):run([command,'--dry-run','--controlplane-only'],2)
        run(['backup','--output',str(data/'backups/synthetic-bootstrap.tar.gz')],0)
        run(['uninstall'],0)
        result={'status':'PASS_CPU_BOOTSTRAP_ONLY','archive_sha256':receipt['archive_sha256'],'source_commit':receipt['commit'],
                'candidate_stage':receipt.get('stage'),'operations':operations,'path_spaces_unicode':True,'credential_values_recorded':False,
                'synthetic_metadata_only':True,'real_model_tensor_read_or_copied':False,'native_services_started':False,'full_Q4_install':'NOT_RUN','final_UI_archive_acceptance':False,'temporary_task_data_removed':True}
    write_json_new(evidence,result)
    print(json.dumps({key:result[key] for key in ['status','source_commit','path_spaces_unicode','real_model_tensor_read_or_copied','native_services_started','full_Q4_install','final_UI_archive_acceptance']}))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--work-parent',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();os.umask(0o077)
    try:bootstrap(a.archive,a.manifest,a.work_parent,a.evidence)
    except (DistributionError,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'status':'FAIL','error':str(error) if isinstance(error,DistributionError) else type(error).__name__}));raise SystemExit(2)
