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
