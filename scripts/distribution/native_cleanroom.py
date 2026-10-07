#!/usr/bin/env python3
"""Same-archive native continuation. Build, payload verification and smoke are separate explicit windows."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from common import DistributionError,no_symlinks,private_directory,read_object,relative_path,write_json_new
from cleanroom import verify_archive
from trust import verified_file,private_file,sha256
from ui_recipe import ui_path,patch_paths
from package import eligible
from liliuxflow import DEFAULT_PORTS,validate_ports

PORTS={'litellm':14000,'compat':18001,'guard':18080,'manager':18081,'postgresql':25432}
FROZEN_STATES={'FROZEN','FROZEN_FOR_REVIEW','READY_FOR_REVIEW'}
COMPLETE_SOURCE={'COMPLETE','SOURCE_COMPLETE_BROWSER_PENDING'}
REQUIRED_UI_LOCALES=['en','zh-Hant','zh-Hans','ja']

def digest(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise DistributionError('reviewed SHA-256 is required')
    return value

def isolated_ports(ports):
    validate_ports(ports)
    if set(ports.values())&set(DEFAULT_PORTS.values()):raise DistributionError('acceptance runner refuses original/default ports')
    return ports

def reviewed_recipe(source,recipe_path,expected,*,source_candidate=False):
    if type(source_candidate)is not bool:raise DistributionError('source-candidate recipe option must be boolean')
    relative_path(recipe_path);policy=read_object(source/'manifests/distribution/source-allowlist.json')
    if not eligible(recipe_path,policy):raise DistributionError('frozen recipe is not archive allowlisted')
    file=verified_file(source,{'path':recipe_path,'sha256':digest(expected)});recipe=read_object(file)
    if recipe.get('schema_version')!=1 or recipe.get('state') not in FROZEN_STATES or recipe.get('coverage_state') not in COMPLETE_SOURCE or set(recipe.get('apps',{}))!={'litellm','llama-swap'}:
        raise DistributionError('frozen complete source UI recipe is required; browser acceptance remains separate')
    if recipe.get('supported_locales')!=REQUIRED_UI_LOCALES:
        raise DistributionError('English/Hant/Hans/Japanese source locales are required')
    if not source_candidate and (recipe.get('browser_locales')!=REQUIRED_UI_LOCALES or recipe.get('four_locale_browser_accepted') is not True):
        raise DistributionError('reviewed English/Hant/Hans/Japanese browser scope is required; old three-locale/source-only approval refused')
    pins=read_object(source/'manifests/distribution/native-sources.json')
    for name,record in recipe['apps'].items():
        pin=pins['litellm' if name=='litellm' else 'llama_swap']
        if record.get('upstream_commit')!=pin['commit'] or record.get('archive_sha256')!=pin['archive_sha256']:raise DistributionError('frozen UI upstream identity differs')
        if not record.get('patches') and not record.get('extra_files'):raise DistributionError('stock UI cannot stand in for frozen localized source')
        for item in record.get('patches',[]):
            if not eligible(item['path'],policy):raise DistributionError('UI patch is not archive allowlisted')
            patch=verified_file(source,item);strip=item.get('strip',1)
            if type(strip)is not int or not 0<=strip<=5:raise DistributionError('UI strip level differs')
            for target in patch_paths(patch,strip):ui_path(name,target)
        for item in record.get('extra_files',[]):
            if not eligible(item['source'],policy):raise DistributionError('UI asset is not archive allowlisted')
            verified_file(source,{'path':item['source'],'sha256':digest(item['sha256'])});ui_path(name,item['target'])
        if not record.get('lockfiles'):raise DistributionError('final UI lock identity is required')
        for item in record['lockfiles']:
            ui_path(name,item['path']);digest(item['sha256'])
            if not item['path'].endswith('/package-lock.json'):raise DistributionError('final UI lock path differs')
    return file

def match_review(receipt,args):
    if receipt.get('schema_version')!=1 or receipt.get('source_commit')!=args.expected_source_commit or receipt.get('archive_sha256')!=digest(args.expected_archive_sha256) or receipt.get('ui_recipe_sha256')!=digest(args.expected_ui_recipe_sha256) or receipt.get('run_id')!=args.run_id:
        raise DistributionError('old/different candidate or run receipt cannot be reused')
    mode=receipt.get('source_candidate',False)
    requested=getattr(args,'source_candidate',False)
    if type(mode)is not bool or type(requested)is not bool or mode!=requested:
        raise DistributionError('source-candidate mode cannot change between build/verify/smoke')
    uuid.UUID(receipt['run_id']);isolated_ports(receipt['ports'])

def verify_staged(task,args):
    receipt=read_object(private_file(task/'candidate.json'));match_review(receipt,args)
    source=no_symlinks(task/'unpacked/LiliuxFlow');manifest=read_object(private_file(task/'archive-manifest.json'))
    if sha256(private_file(task/'source.tar.gz'))!=args.expected_archive_sha256 or manifest.get('commit')!=receipt['source_commit']:raise DistributionError('staged archive provenance differs')
    with tempfile.TemporaryDirectory(prefix='receipt-readback-',dir=task) as scratch:
        verify_archive(task/'source.tar.gz',task/'archive-manifest.json',Path(scratch)/'source')
    for item in manifest['files']:verified_file(source,item)
    if read_object(source/'SOURCE_COMMIT.json').get('commit')!=receipt['source_commit']:raise DistributionError('source provenance differs')
    if sha256(source/'scripts/distribution/native_cleanroom.py')!=sha256(Path(__file__)):raise DistributionError('driver must be the same archived code')
    recipe=reviewed_recipe(source,receipt['recipe_path'],receipt['ui_recipe_sha256'],source_candidate=receipt.get('source_candidate',False))
    return receipt,source,recipe

def run_cli(source,data,arguments,task,stage,timeout):
    from build_native import command
    logs=private_directory(task/'logs');log=logs/(stage+'-'+uuid.uuid4().hex+'.txt')
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(Path.home()),'LANG':'en_US.UTF-8','TMPDIR':str(private_directory(task/'tmp')),'PYTHONDONTWRITEBYTECODE':'1'}
    return command([sys.executable,'-B',source/'scripts/distribution/liliuxflow.py','--data-root',data,*arguments],cwd=source,env=env,log=log,timeout=timeout,resource_roots=[data/'build',data/'cache'],runtime_root=data/'runtime',immutable_source=source)

def stage(args):
    source_candidate=getattr(args,'source_candidate',False)
    if type(source_candidate)is not bool:raise DistributionError('source-candidate option must be boolean')
    if not args.execute:return {'state':'plan','action':args.action,'native_or_model_actions':False,'required':['exact reviewed archive/source/recipe hashes','frozen localized source recipe','new own UUID/Unicode-space data root','separate controlled build/verify/smoke runs'],'full_Q4_acceptance':False,'source_candidate':source_candidate,'final_UI_browser_acceptance':False,'full_GUI_functional_acceptance':False}
    if not isinstance(args.expected_source_commit,str) or len(args.expected_source_commit)!=40 or any(c not in '0123456789abcdef' for c in args.expected_source_commit):raise DistributionError('exact reviewed source commit is required')
    uuid.UUID(args.run_id);digest(args.expected_archive_sha256);digest(args.expected_ui_recipe_sha256)
    if args.action=='build':
        if any(getattr(args,name,None) is None for name in ('archive','manifest','work_parent','model_dir')):raise DistributionError('archive/manifest/work parent/external model directory are required')
        parent=private_directory(args.work_parent);task=parent/('LiliuxFlow native 安裝 space-'+args.run_id)
        if task.exists():raise DistributionError('native clean-room task must be new; no old installation reused')
        if sha256(no_symlinks(args.archive))!=args.expected_archive_sha256:raise DistributionError('archive differs from independently reviewed hash')
        if read_object(args.manifest).get('commit')!=args.expected_source_commit:raise DistributionError('archive differs from reviewed source commit')
        private_directory(task);shutil.copyfile(args.archive,task/'source.tar.gz');(task/'source.tar.gz').chmod(0o600)
        source,manifest=verify_archive(args.archive,args.manifest,task/'unpacked')
        if sha256(source/'scripts/distribution/native_cleanroom.py')!=sha256(Path(__file__)):raise DistributionError('driver must be the same archived code')
        recipe=reviewed_recipe(source,args.recipe_path,args.expected_ui_recipe_sha256,source_candidate=source_candidate)
        model=no_symlinks(args.model_dir)
        if model==task or task in model.parents:raise DistributionError('weights must remain external to the clean-room task')
        ports=isolated_ports(PORTS);data=task/'own data 資料'
        receipt={'schema_version':1,'run_id':args.run_id,'source_commit':manifest['commit'],'archive_sha256':manifest['archive_sha256'],'ui_recipe_sha256':args.expected_ui_recipe_sha256,'recipe_path':args.recipe_path,'ports':ports,'data_root':str(data),'source_root':str(source),'state':'PREPARED','source_candidate':source_candidate,'final_UI_browser_acceptance':False,'full_GUI_functional_acceptance':False,'full_Q4_acceptance':False}
        write_json_new(task/'candidate.json',receipt);write_json_new(task/'archive-manifest.json',manifest)
        run_cli(source,data,['setup','--model-dir',no_symlinks(args.model_dir),*[x for name,p in ports.items() for x in ['--port',name+'='+str(p)]]],task,'setup',30)
        tool_flags=[x for name in ('cargo','go','node','npm_cli','pg_bin') if getattr(args,name,None) is not None for x in ['--'+name.replace('_','-'),getattr(args,name)]]
        result=run_cli(source,data,['build','--execute','--ui-manifest',recipe,*tool_flags],task,'build',3600)
        from trust import installation,validate,atomic_private_json
        trusted=validate(data,require_checkpoint=False)
        if trusted['trust'].get('source_commit')!=receipt['source_commit'] or trusted['trust'].get('ui_recipe_sha256')!=receipt['ui_recipe_sha256']:raise DistributionError('actual built source/recipe trust differs')
        receipt.update(state='BUILT',installation_id=installation(data)['installation_id']);atomic_private_json(task/'candidate.json',receipt)
        return {'state':'BUILT','task_root':str(task),'source_commit':receipt['source_commit'],'full_Q4_acceptance':False,'source_candidate':source_candidate,'final_UI_browser_acceptance':False,'full_GUI_functional_acceptance':False,'native_listeners_started':False,'build':result}
    task=private_directory(args.task_root);receipt,source,recipe=verify_staged(task,args);data=no_symlinks(Path(receipt['data_root']))
    if data!=task/'own data 資料':raise DistributionError('installation escaped its private task')
    from trust import validate,installation,atomic_private_json
    config=installation(data)
    if config['installation_id']!=receipt.get('installation_id') or config['ports']!=receipt['ports'] or config.get('package_source_root')!=str(source):raise DistributionError('actual own installation/source identity differs')
    if args.action=='verify':
        if receipt['state']!='BUILT':raise DistributionError('fresh build receipt is required before full verification')
        result=run_cli(source,data,['verify-model'],task,'full-payload-verification',1800)
        trusted=validate(data)
        if trusted['checkpoint'].get('payload_verified') is not True:raise DistributionError('metadata-only receipt cannot grant real Q4 smoke')
        receipt.update(state='PAYLOAD_VERIFIED');atomic_private_json(task/'candidate.json',receipt)
        return {'state':'PAYLOAD_VERIFIED','model_loaded':False,'source_candidate':source_candidate,'final_UI_browser_acceptance':False,'full_GUI_functional_acceptance':False,'full_Q4_acceptance':False,'verification':result}
    if receipt['state']!='PAYLOAD_VERIFIED':raise DistributionError('fresh full payload verification is required before Q4 smoke')
    from build_native import command
    evidence=task/('smoke-'+uuid.uuid4().hex+'.json');logs=private_directory(task/'logs')
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(Path.home()),'LANG':'en_US.UTF-8','TMPDIR':str(private_directory(task/'tmp')),'PYTHONDONTWRITEBYTECODE':'1'}
    try:
        command([sys.executable,'-B',source/'scripts/distribution/native_smoke.py','--data-root',data,'--source-commit',receipt['source_commit'],'--recipe-sha256',receipt['ui_recipe_sha256'],'--evidence',evidence],cwd=source,env=env,log=logs/('smoke-'+uuid.uuid4().hex+'.txt'),timeout=1200,resource_roots=[data/'build',data/'cache'],runtime_root=data/'runtime',immutable_source=source)
    except DistributionError:
        receipt['state']='SMOKE_FAILED';atomic_private_json(task/'candidate.json',receipt);raise
    result=read_object(private_file(evidence))
    receipt['state']='SMOKE_PASS' if result['status']=='PASS_NATIVE_Q4_SMOKE' else 'SMOKE_FAILED';atomic_private_json(task/'candidate.json',receipt)
    if source_candidate:
        return dict(result,source_candidate=True,final_UI_browser_acceptance=False,full_GUI_functional_acceptance=False,full_Q4_acceptance=False)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['build','verify','smoke']);p.add_argument('--execute',action='store_true');p.add_argument('--source-candidate',action='store_true',help='Build/verify/smoke exact frozen source with browser/GUI acceptance pending; never grants GUI acceptance');p.add_argument('--run-id');p.add_argument('--archive',type=Path);p.add_argument('--manifest',type=Path);p.add_argument('--work-parent',type=Path);p.add_argument('--task-root',type=Path);p.add_argument('--model-dir',type=Path);
    for name in ('cargo','go','node','npm-cli','pg-bin'):p.add_argument('--'+name,type=Path)
    p.add_argument('--recipe-path',default='manifests/distribution/ui-recipe-context-profiles.json');p.add_argument('--expected-archive-sha256');p.add_argument('--expected-source-commit');p.add_argument('--expected-ui-recipe-sha256');args=p.parse_args();os.umask(0o077)
    try:
        result=stage(args);print(json.dumps(result));raise SystemExit(0 if result.get('status')!='FAIL' else 2)
    except (DistributionError,OSError,ValueError,KeyError,TypeError) as error:print(json.dumps({'status':'FAIL','error':str(error) if isinstance(error,DistributionError) else type(error).__name__,'full_Q4_acceptance':False}));raise SystemExit(2)
