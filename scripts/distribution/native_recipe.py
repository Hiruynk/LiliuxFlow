"""Hash-bound observer source replay; no compiler, runtime or DB operations."""
from common import DistributionError, no_symlinks
from trust import verified_file
from ui_recipe import patch_paths, git_environment
import subprocess

MANAGER_FILES=frozenset({'internal/server/metrics.go','internal/store/activity.go','internal/store/sqlite/activity.go'})

def apply_manager_source_patches(source,target,pin):
    patches=pin.get('patches',[])
    if not patches:
        return {'replay':'NOT_REQUESTED','files':[]}
    source=no_symlinks(source);target=no_symlinks(target)
    def records(key):
        values=pin.get(key,[])
        if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):
            raise DistributionError('manager source hash records are malformed')
        paths=[v.get('path') for v in values]
        if len(paths)!=len(set(paths)) or set(paths)!=MANAGER_FILES:
            raise DistributionError('manager source hashes must bind the three reviewed observer files')
        return values
    before=records('source_files_before_patch');after=records('patched_source_files')
    # Validate after-record syntax without requiring its future bytes to exist yet.
    import re
    if any(not re.fullmatch('[0-9a-f]{64}',str(v.get('sha256',''))) for v in after):
        raise DistributionError('manager after-source hash is malformed')
    planned=[];touched=set()
    for item in patches:
        file=verified_file(source,item)
        if item.get('strip',1)!=1:
            raise DistributionError('manager source patch requires strip one')
        paths=set(patch_paths(file,1))
        if not paths.issubset(MANAGER_FILES):
            raise DistributionError('manager source patch escapes reviewed observer files')
        touched.update(paths);planned.append(file)
    if touched!=MANAGER_FILES:
        raise DistributionError('manager source patch must cover the three hash-bound observer files')
    for item in before:verified_file(target,item)
    argv=['git','apply','-p1'];env=git_environment(target)
    for mode in ('--check',None):
        result=subprocess.run(argv+([mode] if mode else [])+[str(p) for p in planned],cwd=target,env=env,capture_output=True)
        if result.returncode:
            raise DistributionError('hash-bound manager source patch replay failed')
    for item in after:verified_file(target,item)
    return {'replay':'PASS','files':sorted(touched),'native_build':False,'live_usage_acceptance':False}


LEGACY_ENGINE = 'legacy-db3-mtp0'
OPT64_ENGINE = 'latest13f-defer-pc123-mtp2-opt64k'


def select_lily_recipe(lock, engine_id=LEGACY_ENGINE):
    """Select one fixed source recipe; the default recipe remains unchanged."""
    if engine_id == LEGACY_ENGINE:
        pin = lock['lily']
        if pin.get('commit') != 'db3f8a7cdb33f1e88b68c6889331abd31098c923':
            raise DistributionError('legacy Lily source pin differs')
        return pin, 'manifests/distribution/lily-source-baseline.json'
    if engine_id != OPT64_ENGINE:
        raise DistributionError('native engine is outside the finite allowlist')
    from optin_engine import LATEST_COMMIT, PATCHES, SOURCE_SHA
    pin = lock.get('lily_opt64')
    if (not isinstance(pin, dict) or pin.get('repository') != 'fabiogreter/lily-qwen3.8-flash-next'
        or pin.get('commit') != LATEST_COMMIT
        or pin.get('archive_sha256') != '4406664bdb4df433a660dee85b6338b28e968d92ee5756c2656add17affe8758'
        or pin.get('source_inventory_sha256') != SOURCE_SHA or pin.get('default') is not False
        or pin.get('baseline') != 'manifests/distribution/lily-opt64-source-baseline.json'
        or [v.get('sha256') for v in pin.get('patches', [])] != list(PATCHES)
        or any(v.get('unidiff_zero') is not False or
               not v.get('path', '').startswith('scripts/distribution/patches/lily-opt64/')
               for v in pin.get('patches', []))):
        raise DistributionError('opt-in Lily recipe differs from the reviewed source')
    return pin, pin['baseline']


def apply_lily_source_patches(source, target, lock, engine_id=LEGACY_ENGINE):
    """Normal builder replay and complete hash inventory, without compilation."""
    from common import read_object
    import hashlib, json
    pin, baseline_path = select_lily_recipe(lock, engine_id)
    source=no_symlinks(source);target=no_symlinks(target)
    for item in pin['patches']:
        patch=verified_file(source,item)
        argv=['git','apply']+(['--unidiff-zero'] if item['unidiff_zero'] else [])
        for check in (True,False):
            result=subprocess.run(argv+(['--check'] if check else [])+[str(patch)],
                cwd=target,env=git_environment(target),capture_output=True)
            if result.returncode:raise DistributionError('canonical Lily source patch replay failed')
    baseline=read_object(source/baseline_path)
    for item in baseline['files']:verified_file(target,item)
    if engine_id == OPT64_ENGINE:
        actual={file.relative_to(target).as_posix() for file in target.rglob('*') if file.is_file()}
        expected={row['path']:row['sha256'] for row in baseline['files']}
        if actual!=set(expected):raise DistributionError('opt-in Lily inventory contains unexpected files')
        digest=hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest != pin['source_inventory_sha256']:raise DistributionError('opt-in Lily source inventory differs')
    return {'engine_id':engine_id,'source_commit':pin['commit'],'patch_sha256':[row['sha256'] for row in pin['patches']],
            'source_inventory_sha256':pin.get('source_inventory_sha256'),'replay':'PASS','native_build':False}
