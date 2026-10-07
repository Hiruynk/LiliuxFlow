"""Exact owned-process identity; no process group assumptions or broad signals."""
import hashlib
import os
import signal
import subprocess
import time
from common import DistributionError


def inspect(pid, *, executable=None):
    if type(pid) is not int or pid <= 1:
        raise DistributionError('unsafe process id')
    result = subprocess.run(['/bin/ps','-p',str(pid),'-o','pid=,ppid=,pgid=,uid=,lstart=,command='],capture_output=True,text=True,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','LC_ALL':'en_US.UTF-8'})
    if result.returncode or not result.stdout.strip():
        return None
    fields = result.stdout.strip().split(None,9)
    if len(fields) != 10:
        raise DistributionError('owned process identity is unavailable')
    if executable is not None and fields[9] != str(executable) and not fields[9].startswith(str(executable)+' '):
        raise DistributionError('owned process executable differs')
    ident = dict(zip(('pid','ppid','pgid','uid'),map(int,fields[:4])))
    ident['started'] = ' '.join(fields[4:9])
    ident['command_sha256'] = hashlib.sha256(fields[9].encode()).hexdigest()
    return ident


def capture(pid, *, parent=None, executable=None):
    identity = inspect(pid, executable=executable)
    if identity is None or identity['uid'] != os.getuid() or parent is not None and identity['ppid'] != parent:
        raise DistributionError('owned process lineage differs')
    return identity


def unchanged(identity):
    now = inspect(identity['pid'])
    return now is not None and all(now[k] == identity[k] for k in ('pid','uid','started','command_sha256'))


def wait_gone(identity, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not unchanged(identity):
            return True
        time.sleep(.1)
    return not unchanged(identity)


def terminate(identity, *, grace=20):
    if identity['pid'] in (os.getpid(),os.getppid()) or identity['uid'] != os.getuid():
        raise DistributionError('refusing signal to caller or another user')
    if not unchanged(identity):
        return True
    os.kill(identity['pid'], signal.SIGTERM)
    return wait_gone(identity, grace)


def descendants(parent):
    # Only PID/PPID/UID metadata. No arbitrary process environment or arguments are printed.
    result = subprocess.run(['/bin/ps','-axo','pid=,ppid=,uid='],capture_output=True,text=True)
    rows = [tuple(map(int,line.split())) for line in result.stdout.splitlines() if len(line.split()) == 3]
    parents = {parent}
    children = []
    changed = True
    while changed:
        changed = False
        for pid,ppid,uid in rows:
            if ppid in parents and pid not in parents and uid == os.getuid():
                parents.add(pid)
                children.append(capture(pid,parent=ppid))
                changed = True
    return children
