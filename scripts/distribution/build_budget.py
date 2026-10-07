"""Read-only byte categories and bounded collection; never delete build dependencies."""
import errno
import hashlib
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from common import DistributionError,no_symlinks
CAP=8*1024**3
FLOOR=64*1024**3
DU_TIMEOUT_SECONDS=5
INVENTORY_TIMEOUT_SECONDS=12
MAX_DU_ATTEMPTS=3

class BudgetInventoryError(DistributionError):
    def __init__(self,category,*,stage,attempt=0,cause=None,result=None,events=(),private_stderr=()):
        super().__init__('resource inventory failed; inspect private collector diagnostics')
        self.evidence={'category':category,'stage':stage,'attempts_used':attempt,
            'cause_exception_class':type(cause).__name__ if cause is not None else None,
            'errno':getattr(cause,'errno',errno.ENOENT if category=='MISSING_DESCENDANT_RETRY_EXHAUSTED' else None),'du_exit_code':getattr(result,'returncode',None),
            'du_stderr_bytes':len(getattr(result,'stderr',b'') or b''),
            'du_stderr_sha256':hashlib.sha256(getattr(result,'stderr',b'') or b'').hexdigest(),
            'maximum_du_attempts':MAX_DU_ATTEMPTS,'per_du_timeout_seconds':DU_TIMEOUT_SECONDS,
            'total_inventory_timeout_seconds':INVENTORY_TIMEOUT_SECONDS,'retry_events':list(events)}
        self.private_stderr=list(private_stderr)
        if result is not None and result.stderr:self.private_stderr.append(result.stderr)

class BudgetSnapshot(dict):
    def __init__(self,value,private_stderr):
        super().__init__(value);self.private_stderr=private_stderr

def _root_identity(path):
    try:
        no_symlinks(path)
        info=path.lstat()
    except FileNotFoundError:return None
    except (OSError,DistributionError) as exc:
        raise BudgetInventoryError('ROOT_VALIDATION_ERROR',stage='root.validation',cause=exc) from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():
        raise BudgetInventoryError('ROOT_NOT_OWNED_DIRECTORY',stage='root.validation')
    return info.st_dev,info.st_ino,info.st_uid

def _missing_descendants(stderr,path):
    """Retry only exact C-locale ENOENT diagnostics strictly below this fixed root."""
    lines=stderr.splitlines()
    if not lines:return False
    prefix=b'du: ';suffix=b': '+os.strerror(errno.ENOENT).encode()
    for line in lines:
        if not line.startswith(prefix) or not line.endswith(suffix):return False
        try:
            reported=Path(os.fsdecode(line[len(prefix):-len(suffix)]))
            rel=reported.relative_to(path)
        except (ValueError,UnicodeError):return False
        if not rel.parts or '..' in rel.parts:return False
    return True

def directory_bytes(path,*,deadline=None,events=None,private_stderr=None,role='directory'):
    path=Path(os.path.abspath(path));identity=_root_identity(path)
    if identity is None:return 0
    deadline=deadline if deadline is not None else time.monotonic()+INVENTORY_TIMEOUT_SECONDS
    events=events if events is not None else []
    private_stderr=private_stderr if private_stderr is not None else []
    for attempt in range(1,MAX_DU_ATTEMPTS+1):
        if _root_identity(path)!=identity:
            raise BudgetInventoryError('ROOT_CHANGED',stage=role,attempt=attempt,events=events,private_stderr=private_stderr)
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise BudgetInventoryError('INVENTORY_TIMEOUT',stage=role,attempt=attempt-1,events=events,private_stderr=private_stderr)
        try:
            result=subprocess.run(['/usr/bin/du','-sk',str(path)],capture_output=True,
                env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','LC_ALL':'en_US.UTF-8'},
                timeout=min(DU_TIMEOUT_SECONDS,remaining))
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise BudgetInventoryError('DU_EXECUTION_ERROR',stage=role,attempt=attempt,cause=exc,events=events,private_stderr=private_stderr) from exc
        if _root_identity(path)!=identity:
            raise BudgetInventoryError('ROOT_CHANGED',stage=role,attempt=attempt,result=result,events=events,private_stderr=private_stderr)
        if result.returncode:
            retry=_missing_descendants(result.stderr,path)
            event={'stage':role,'attempt':attempt,'du_exit_code':result.returncode,
                'errno':errno.ENOENT if retry else None,'du_stderr_bytes':len(result.stderr),
                'du_stderr_sha256':hashlib.sha256(result.stderr).hexdigest(),'verified_missing_descendant':retry}
            if retry and attempt<MAX_DU_ATTEMPTS:
                if time.monotonic()>=deadline:
                    raise BudgetInventoryError('INVENTORY_TIMEOUT',stage=role,attempt=attempt,
                        result=result,events=events,private_stderr=private_stderr)
                events.append(event);private_stderr.append(result.stderr);continue
            raise BudgetInventoryError('MISSING_DESCENDANT_RETRY_EXHAUSTED' if retry else 'DU_NONZERO_EXIT',
                stage=role,attempt=attempt,result=result,events=events,private_stderr=private_stderr)
        fields=result.stdout.split(None,1)
        if not fields or not fields[0].isdigit():
            raise BudgetInventoryError('DU_OUTPUT_MALFORMED',stage=role,attempt=attempt,result=result,events=events,private_stderr=private_stderr)
        return int(fields[0])*1024

def snapshot(roots,*,runtime=None,immutable_source=None):
    roots=[Path(os.path.abspath(p)) for p in roots]
    if not roots:raise BudgetInventoryError('MISSING_ROOTS',stage='roots.validation')
    for i,path in enumerate(roots):
        _root_identity(path)
        if any(path==other or path in other.parents or other in path.parents for other in roots[:i]):
            raise BudgetInventoryError('ROOTS_OVERLAP',stage='roots.validation')
    deadline=time.monotonic()+INVENTORY_TIMEOUT_SECONDS;events=[];private=[]
    total=sum(directory_bytes(path,deadline=deadline,events=events,private_stderr=private,role='transient['+str(i)+']') for i,path in enumerate(roots))
    observed_runtime=directory_bytes(runtime,deadline=deadline,events=events,private_stderr=private,role='runtime') if runtime is not None else None
    observed_source=directory_bytes(immutable_source,deadline=deadline,events=events,private_stderr=private,role='immutable_source') if immutable_source is not None else None
    filesystem=next((path for path in roots if _root_identity(path) is not None),roots[0].parent)
    while not filesystem.exists():filesystem=filesystem.parent
    try:free=shutil.disk_usage(filesystem).free
    except OSError as exc:raise BudgetInventoryError('DISK_USAGE_ERROR',stage='disk.free',cause=exc,events=events,private_stderr=private) from exc
    return BudgetSnapshot({'measurement_status':'MEASURED','transient_build_payload_cache_bytes':total,
        'transient_cap_bytes':CAP,'runtime_bytes_observed_not_evicted':observed_runtime,
        'immutable_source_bytes_observed_not_evicted':observed_source,'free_bytes':free,
        'disk_floor_bytes':FLOOR,'over_transient_cap':total>CAP,'below_disk_floor':free<FLOOR,
        'inventory_retry_events':events,'total_inventory_timeout_seconds':INVENTORY_TIMEOUT_SECONDS,
        'automatic_deletion':False},private)
