# SPDX-License-Identifier: Apache-2.0
"""Portable owned-tree capture and bounded signals; no launchd/DB/path adoption.

The installation agent must validate its own registry/plist and exclude every
PostgreSQL process before calling terminate_all. GUI busy policy is separate.
"""
from pathlib import Path
import hashlib,os,re,signal,stat,subprocess,time
from common import DistributionError

IDENTITY_FIELDS=('pid','ppid','pgid','uid','started','command_sha256')
def identity(value):
    if (not isinstance(value,dict) or set(value)!=set(IDENTITY_FIELDS)
            or any(type(value[k]) is not int or value[k]<0 for k in ('pid','ppid','pgid','uid'))
            or value['pid']<=1 or value['pgid']<=0 or not isinstance(value['started'],str) or not value['started']
            or not isinstance(value['command_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['command_sha256'])):
        raise DistributionError('owned process identity is malformed')
    return value
def _probe(pid):
    if type(pid) is not int or pid<=1:raise DistributionError('unsafe owned process PID')
    try:
        result=subprocess.run(['/bin/ps','-p',str(pid),'-o','pid=,ppid=,pgid=,uid=,lstart=,command='],
                              capture_output=True,text=True,timeout=5,check=False,
                              env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})
    except (OSError,subprocess.SubprocessError):raise DistributionError('owned process probe is unknown') from None
    if result.returncode==1 and not result.stdout.strip() and not result.stderr.strip():return None
    lines=result.stdout.strip().splitlines()
    if result.returncode!=0 or result.stderr.strip() or len(lines)!=1:raise DistributionError('owned process probe is unknown or ambiguous')
    fields=lines[0].split(None,9)
    if len(fields)!=10:raise DistributionError('owned process probe is malformed')
    try:values=dict(zip(('pid','ppid','pgid','uid'),map(int,fields[:4])))
    except ValueError:raise DistributionError('owned process probe numeric identity is malformed') from None
    values.update(started=' '.join(fields[4:9]),command_sha256=hashlib.sha256(fields[9].encode()).hexdigest())
    identity(values)
    if values['pid']!=pid:raise DistributionError('owned process probe PID differs')
    return values,fields[9]
def inspect(pid):
    row=_probe(pid);return row[0] if row is not None else None
def exact(value):
    value=identity(value);now=inspect(value['pid'])
    if now is None:return False
    if now!=value:raise DistributionError('owned process PID/PPID/PGID/UID/start/command changed')
    return True
def command(value):
    value=identity(value);row=_probe(value['pid'])
    if row is None or row[0]!=value:raise DistributionError('owned command identity changed')
    return row[1]

def _descendant_rows():
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,uid=,ruid=,stat=,ucomm=,lstart="],
            capture_output=True, text=True, check=True, timeout=5,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError,subprocess.SubprocessError):raise DistributionError('project kernel snapshot is unknown') from None
    if result.returncode or result.stderr.strip():raise DistributionError('project kernel snapshot has errors')
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 6)
        if len(fields) != 7:
            raise DistributionError("project process snapshot is malformed")
        try:pid, ppid, uid, ruid = map(int, fields[:4])
        except ValueError:raise DistributionError('project kernel snapshot numeric identity is malformed') from None
        rows.append({"pid": pid, "ppid": ppid, "uid": uid, "ruid": ruid,
                     "stat": fields[4], "ucomm": fields[5], "started": " ".join(fields[6].split())})
    if len({row["pid"] for row in rows}) != len(rows):
        raise DistributionError("project process snapshot has duplicate PIDs")
    return rows


def _suid_ps_identity():
    info = Path("/bin/ps").lstat()
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and bool(info.st_mode & stat.S_ISUID)


def _known_ps(row):
    # This is ONLY permission to wait for exit, never permission to signal UID0.
    return row["uid"] == 0 and row["ruid"] == os.getuid() and row["ucomm"] == "ps" and _suid_ps_identity()


def descendants(parent):
    if type(parent) is not int or parent<=1:
        raise DistributionError('unsafe owned-tree parent PID')
    # A snapshot can overlap the supervisor/guard's SUID ps inspection helper.
    # Keep live foreign UID rejection; recapture at most 0.5s for known ps.
    deadline = time.monotonic() + .5
    while True:
        rows = _descendant_rows()
        parents = {parent}
        found = []
        retry = False
        rechecked = None
        while True:
            before = len(parents)
            for row in rows:
                pid, ppid, uid = row["pid"], row["ppid"], row["uid"]
                if ppid not in parents or pid in parents:
                    continue
                if uid != os.getuid():
                    if not _known_ps(row):
                        raise DistributionError("project child UID changed; refusing forced shutdown")
                    if not row["stat"].startswith("Z"):
                        retry = True
                        break
                    # Frozen parents cannot reap a zombie. Positive kernel Z
                    # means exited, not a root process we are allowed to kill.
                    # Require unchanged PID/start/PPID/UID and no subtree twice.
                    if rechecked is None:
                        rechecked = _descendant_rows()
                    current = next((item for item in rechecked if item["pid"] == pid), None)
                    if current is None:
                        retry = True
                        break  # Reaped by an unfrozen parent: fresh entire tree.
                    if (current != row or any(item["ppid"] == pid for item in rows)
                            or any(item["ppid"] == pid for item in rechecked)):
                        raise DistributionError("exited ps identity or descendant tree changed during capture")
                    parents.add(pid)
                    continue  # No identity is returned, so no signal can target it.
                if row["stat"].startswith("Z"):
                    # Same-UID zombies are also exited and cannot be terminated
                    # while their parent is frozen. No subtree may be hidden.
                    if rechecked is None:
                        rechecked = _descendant_rows()
                    current_row = next((item for item in rechecked if item["pid"] == pid), None)
                    if current_row is None:
                        retry = True
                        break
                    if (current_row != row or any(item["ppid"] == pid for item in rows)
                            or any(item["ppid"] == pid for item in rechecked)):
                        raise DistributionError("exited child identity or descendant tree changed during capture")
                    parents.add(pid)
                    continue
                current = inspect(pid)
                if current is None:
                    if any(item["ppid"] == pid for item in rows):
                        raise DistributionError("project child exited with an unverified descendant")
                    parents.add(pid)
                    continue
                ident = current
                if ident["ppid"] != ppid or ident["uid"] != uid or ident["started"] != row["started"]:
                    raise DistributionError("project child lineage changed during capture")
                parents.add(pid)
                found.append(ident)
            if retry or len(parents) == before:
                break
        if not retry:
            return found
        if time.monotonic() >= deadline:
            raise DistributionError("project child UID changed; live ps did not settle within capture bound")
        time.sleep(min(.02, max(0, deadline - time.monotonic())))


def send(ident, signum):
    if ident['pid'] in {os.getpid(), os.getppid()} or ident['uid'] != os.getuid():
        raise DistributionError("refusing signal to caller or another owner")
    identity(ident)
    if not exact(ident):
        return False
    try:
        os.kill(ident['pid'], signum)
    except ProcessLookupError:
        return False
    return True


def exited(ident):
    """Observe absence or an exited zombie; this never authorizes a signal."""
    ident=identity(ident)
    if ident['uid']!=os.getuid():raise DistributionError('exit proof belongs to another owner')
    now=inspect(ident['pid'])
    if now is None:return True
    # A zombie's command changes, and its PPID may change after its parent exits.
    # PID birth, group and owner must still match; live changes always refuse.
    birth=('pid','pgid','uid','started')
    if any(now[k]!=ident[k] for k in birth):raise DistributionError('exit proof PID birth/group/owner changed')
    rows=_descendant_rows();row=next((x for x in rows if x['pid']==ident['pid']),None)
    if row is None:
        if inspect(ident['pid']) is None:return True
        raise DistributionError('exit proof kernel snapshot is unknown')
    if (row['uid']!=ident['uid'] or row['ruid']!=os.getuid()
            or row['started']!=ident['started'] or row['ppid']!=now['ppid']):
        raise DistributionError('exit proof kernel identity differs')
    if not row['stat'].startswith('Z'):
        if now!=ident:raise DistributionError('live owned process identity changed')
        return False
    again=_descendant_rows();second=next((x for x in again if x['pid']==ident['pid']),None)
    if any(x['ppid']==ident['pid'] for x in rows+again):
        raise DistributionError('exited zombie identity or subtree changed')
    final=inspect(ident['pid'])
    if second is None and final is None:return True
    if second!=row or (final is not None and any(final[k]!=now[k] for k in ('pid','ppid','pgid','uid','started'))):
        raise DistributionError('exited zombie identity or subtree changed')
    return True


def _send_alive(ident, signum):
    try:return send(ident,signum)
    except DistributionError:
        if exited(ident):return False
        raise


def terminate_all(identities, *, grace=10, resume=False):
    # Leaves exit before their parents receive TERM. Share one grace/KILL
    # bound across the tree; frozen parents need not reap before exit proof.
    pending={identity(x)['pid']:x for x in identities}
    if len(pending)!=len(identities):raise DistributionError('duplicate owned shutdown PID')
    deadline=time.monotonic()+grace;kill_deadline=deadline+5
    while pending:
        parents={x['ppid'] for x in pending.values()}
        leaves=[x for pid,x in pending.items() if pid not in parents]
        if not leaves:raise DistributionError('owned shutdown lineage contains a cycle')
        for ident in leaves:
            if not exited(ident):
                _send_alive(ident,signal.SIGTERM)
                if resume:_send_alive(ident,signal.SIGCONT)
        while any(not exited(x) for x in leaves) and time.monotonic()<deadline:time.sleep(.02)
        for ident in leaves:
            if not exited(ident):_send_alive(ident,signal.SIGKILL)
        while any(not exited(x) for x in leaves) and time.monotonic()<kill_deadline:time.sleep(.02)
        if any(not exited(x) for x in leaves):raise DistributionError('verified owned processes remain after bounded shutdown')
        for ident in leaves:pending.pop(ident['pid'])
