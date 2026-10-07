#!/usr/bin/env python3
"""Prepare a new metadata-only empty restore target from verified own runtime code, never data."""
import argparse,copy,json,os,shutil,subprocess
from pathlib import Path
from common import DistributionError,no_symlinks,private_directory,write_json_new
from trust import validate,sha256,atomic_private_json
from liliuxflow import setup


def prepare(origin,target):
    origin=no_symlinks(origin);target=no_symlinks(target)
    if target.exists():raise DistributionError('restore target directory must be new')
    trusted=validate(origin,require_checkpoint='controlplane');cfg=trusted['config']
    if (origin/'run/agent.json').exists() or subprocess.run(['/bin/launchctl','print','gui/'+str(os.getuid())+'/'+cfg['launchd_label']],capture_output=True).returncode==0:
        raise DistributionError('stop verified own source before preparing a restore target')
    source=Path(cfg.get('package_source_root',cfg['source_root']))
    setup(argparse.Namespace(data_root=target,model_dir=Path(cfg['model_dir']) if trusted['model_configured'] else None,
                             without_model=not trusted['model_configured'],port=list(cfg['ports'].items())))
    target_cfg=json.loads((target/'install.json').read_text());target_cfg['package_source_root']=str(source);target_cfg['source_root']=str(source)
    runtime=private_directory(target/'runtime')
    for name in ('bin','compat','litellm','toolchains','notices'):
        original=origin/'runtime'/name
        if original.exists():
            result=subprocess.run(['/bin/cp','-cR',str(original),str(runtime/name)],capture_output=True)
            if result.returncode:raise DistributionError('private runtime code copy failed')
    # uv console scripts embed the venv location. Rebind only textual launchers in the new own venv.
    for service in ('compat','litellm'):
        for file in (runtime/service/'bin').iterdir():
            if file.is_symlink() or not file.is_file() or file.stat().st_size>1024**2:continue
            raw=file.read_bytes()
            if raw.startswith(b'#!') and str(origin).encode() in raw:
                file.write_bytes(raw.replace(str(origin).encode(),str(target).encode()))
    trust=copy.deepcopy(trusted['trust']);trust['installation_id']=target_cfg['installation_id']
    for group in ('binaries','build_tools'):
        for item in trust[group].values():
            if sha256(target/item['path'])!=item['sha256']:raise DistributionError('copied runtime executable identity differs')
    write_json_new(runtime/'release-trust.json',trust)
    if trusted['checkpoint'] is not None:
        metadata=copy.deepcopy(trusted['checkpoint']);metadata['payload_verified']=False;metadata['origin']='inherited metadata-only fingerprints; no tensor/header reread'
        write_json_new(runtime/'checkpoint-metadata.json',metadata)
    target_cfg['runtime_state']='BUILT';atomic_private_json(target/'install.json',target_cfg)
    return {'state':'prepared_empty_restore_target','installation_id':target_cfg['installation_id'],'database_initialized':False,
            'data_credentials_copied':False,'runtime_code_only_copied':True,'weights_copied_read_loaded':False,'payload_verified':False,
            'source_view_rebind_required':True}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--origin-data-root',type=Path,required=True);p.add_argument('--new-data-root',type=Path,required=True);a=p.parse_args();os.umask(0o077)
    print(json.dumps(prepare(a.origin_data_root,a.new_data_root),sort_keys=True))
