# SPDX-License-Identifier: Apache-2.0
"""Portable owned-tree capture and bounded signals; no launchd/DB/path adoption.

The installation agent must validate its own registry/plist and exclude every
PostgreSQL process before calling terminate_all. GUI busy policy is separate.
"""
from pathlib import Path
import hashlib,os,re,signal,stat,subprocess,time
from common import DistributionError

IDENTITY_FIELDS=('pid','ppid','pgid','uid','started','command_sha256')

def shared_model_record(trusted,record):
    """Classify one finite shared record with owned immutable source/binary proof.

    This does not signal or infer native request release. It verifies exact
    process commands/identity; the caller must separately verify its captured
    agent descendants and retain the existing non-DB filtering.
    """
    import shlex
    from mtp2_shared_runtime import SharedInstallation,shared_argv,runtime_identity_fields
    if (type(record) is not dict or type(record.get('schema_version')) is not int or record['schema_version']!=1
        or record.get('installation_id')!=trusted['config']['installation_id']
        or record.get('profile_id')!='mtp2-shared' or record.get('context_tokens')!=262144
        or type(record.get('context_tokens')) is not int):
        raise DistributionError('shared model record installation/profile differs')
    generation=record.get('validation_bundle_id')
    if generation is not None:
        from mtp2_shared_upgrade import _verify_bundle
        m,source,engine,binary=_verify_bundle(trusted,generation,record.get('validation_bundle_manifest_sha256'))
        installation=SharedInstallation(engine,binary,Path(trusted['config']['model_dir']),trusted['data_root'],(),
            tuple(trusted['config']['ports'].values()),generation)
    else:
        from mtp2_shared_runner import RELEASE_EXPECTATION
        from mtp2_shared_runtime import validate_shared_extension, normal_shared_view
        if RELEASE_EXPECTATION is None:raise DistributionError('shared production stop recipe is unavailable')
        # Public stop validates the base without a checkpoint. Reuse the
        # existing verified receipt/stat/metadata gate for normal shared stop;
        # this does not rehash safetensors, load a model or authorize inference.
        from trust import validate
        verified=validate(trusted['data_root'],require_checkpoint=True)
        if (verified['config']!=trusted['config'] or verified['trust']!=trusted['trust']
                or verified['source_root']!=trusted['source_root'] or verified['data_root']!=trusted['data_root']):
            raise DistributionError('shared shutdown installation changed during revalidation')
        selected=normal_shared_view(verified)
        installation=validate_shared_extension(selected,RELEASE_EXPECTATION);source=selected['source_root']
        if installation is None:raise DistributionError('shared model executable is not installed')
    if record.get('binary_sha256')!=installation.engine.binary_sha256 or any(record.get(k)!=v for k,v in runtime_identity_fields(installation.engine).items()):
        raise DistributionError('shared model record binary/source identity differs')
    runner=identity(record.get('runner'));child=identity(record.get('child'))
    if runner['uid']!=trusted['config']['owner_uid'] or child['uid']!=runner['uid'] or child['ppid']!=runner['pid']:
        raise DistributionError('shared model process owner/parent differs')
    port=record.get('backend_port')
    argv=shared_argv(installation,port)
    if exact(child):
        actual=shlex.split(command(child))
        if (actual[:-3]!=argv or actual[-3:-1]!=['--liliuxflow-mtp2-control','--liliuxflow-mtp2-control-key-fd']
            or not actual[-1].isascii() or not actual[-1].isdigit() or int(actual[-1])<3):
            raise DistributionError('shared native command differs from its finite recipe')
    if exact(runner):
        actual=shlex.split(command(runner));expected=[str(trusted['binaries']['compat_python']),
            str(source/'scripts/distribution/mtp2_shared_runner.py'),'--data-root',str(trusted['data_root']),
            '--port',str(port)]
        if generation is not None:expected+=['--candidate-validation']
        # Manager command places the finite flag before --port; direct CLI may
        # place it last. Both lists carry exactly the same fixed operands.
        alternate=expected[:-3]+['--candidate-validation','--port',str(port)] if generation is not None else expected
        if actual not in (expected,alternate):raise DistributionError('shared runner command/source differs')
    return runner,child
def identity(value):
    if (not isinstance(value,dict) or set(value)!=set(IDENTITY_FIELDS)
            or any(type(value[k]) is not int or value[k]<0 for k in ('pid','ppid','pgid','uid'))
            or value['pid']<=1 or value['pgid']<=0 or not isinstance(value['started'],str) or not value['started']
            or not isinstance(value['command_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['command_sha256'])):
        raise DistributionError('owned process identity is malformed')
    return value
def _probe(pid, *, timeout=5):
    if type(pid) is not int or pid<=1:raise DistributionError('unsafe owned process PID')
    try:
        result=subprocess.run(['/bin/ps','-p',str(pid),'-o','pid=,ppid=,pgid=,uid=,lstart=,command='],
                              capture_output=True,text=True,timeout=timeout,check=False,
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

def _descendant_rows(*, timeout=5):
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,uid=,ruid=,stat=,ucomm=,lstart="],
            capture_output=True, text=True, check=True, timeout=timeout,
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


class KnownLivePsPending(DistributionError):
    """Only two matching kernel identities of live SUID ps authorize reader retry."""
    pass


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
        pending_live_ps = None
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
                        pending_live_ps = row
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
            if pending_live_ps is not None:
                second = _descendant_rows()
                current = next((item for item in second if item['pid'] == pending_live_ps['pid']), None)
                keys = ('pid','ppid','uid','ruid','ucomm','started')
                if (current is not None and _known_ps(current) and not current['stat'].startswith('Z')
                        and all(current[key] == pending_live_ps[key] for key in keys)
                        and not any(item['ppid'] == current['pid'] for item in rows + second)):
                    raise KnownLivePsPending("project child UID changed; live ps did not settle within capture bound")
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


def shared_exit_observer(identities, diagnostic):
    """Observe only captured shared owners; never authorize a changed signal.

    A live-drift snapshot may only settle into two fresh absences or the
    existing two-kernel-snapshot zombie proof. It never returns a live identity.
    """
    owned={identity(x)['pid']:dict(x) for x in identities}
    if len(owned)!=len(identities) or any(x['uid']!=os.getuid() or x['pid'] in (os.getpid(),os.getppid()) for x in owned.values()):
        raise DistributionError('shared exit observer needs finite captured owned identities')
    emitted=False;ended={}
    def observe(value):
        nonlocal emitted
        ident=identity(value)
        if owned.get(ident['pid'])!=ident:raise DistributionError('shared exit observer identity was not captured')
        deadline=time.monotonic()+.5;first=None;outcome='REFUSED';current=row=None;absences=0;drift=False
        def timeout():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise DistributionError('shared exit observation deadline expired')
            return min(.1,remaining)
        def inspect_now():
            value=_probe(ident['pid'],timeout=timeout());return None if value is None else value[0]
        def evidence(enum):
            def scalar(value):
                if value is None:return None
                return {k:value[k] for k in ('pid','ppid','pgid','uid')}|{'started_sha256':hashlib.sha256(value['started'].encode()).hexdigest()}
            return {'schema_version':1,'enum':enum,'pid':ident['pid'],
                'changed_fields':[k for k in IDENTITY_FIELDS if current is None or current[k]!=ident[k]],
                'captured':scalar(ident),'observed':scalar(current),
                'kernel':None if row is None else {k:row[k] for k in ('pid','ppid','uid','ruid')}|
                    {'stat_ascii':[ord(ch) for ch in row['stat']]},
                'signal_authorized':False,'cause':'UNKNOWN'}
        def sample():
            nonlocal current,row
            current=row=None;current=inspect_now()
            if current is None:return 'ABSENT'
            if any(current[k]!=ident[k] for k in ('pid','pgid','uid','started')):
                raise DistributionError('exit proof PID birth/group/owner changed')
            rows=_descendant_rows(timeout=timeout());row=next((x for x in rows if x['pid']==ident['pid']),None)
            if row is None:
                if inspect_now() is None:return 'ABSENT'
                raise DistributionError('exit proof kernel snapshot is unknown')
            if (row['uid']!=ident['uid'] or row['ruid']!=os.getuid()
                    or row['started']!=ident['started'] or row['ppid']!=current['ppid']):
                raise DistributionError('exit proof kernel identity differs')
            if not row['stat'].startswith('Z'):return 'DRIFT' if current!=ident else 'LIVE'
            again=_descendant_rows(timeout=timeout());second=next((x for x in again if x['pid']==ident['pid']),None)
            if any(x['ppid']==ident['pid'] for x in rows+again):
                raise DistributionError('exited zombie identity or subtree changed')
            final=inspect_now()
            if second is None and final is None:return 'ABSENT'
            if second!=row or (final is not None and any(final[k]!=current[k] for k in ('pid','ppid','pgid','uid','started'))):
                raise DistributionError('exited zombie identity or subtree changed')
            return 'ZOMBIE'
        try:
            for attempt in range(4):
                state=sample()
                if (absences or ended.get(ident['pid'])=='ABSENT') and state!='ABSENT':raise DistributionError('shared exit PID reappeared after absence')
                if ended.get(ident['pid'])=='ZOMBIE' and state in ('LIVE','DRIFT'):
                    raise DistributionError('shared exited zombie changed to a live identity')
                if state=='LIVE' and not drift:return False
                if state=='ZOMBIE':ended[ident['pid']]='ZOMBIE';outcome='TWO_KERNEL_ZOMBIE_SNAPSHOTS';return True
                if state=='ABSENT':
                    absences+=1
                    if absences==2:ended[ident['pid']]='ABSENT';outcome='ABSENT_TWICE';return True
                else:
                    absences=0
                    if state=='DRIFT':
                        drift=True
                        if first is None:first=evidence('live owned process identity changed')
                time.sleep(min(.01,timeout()))
            raise DistributionError('live owned process identity changed' if drift else 'shared exit absence is unproven')
        except DistributionError as error:
            if first is None:first=evidence(str(error))
            raise
        finally:
            if first is not None and not emitted:
                emitted=True;first['reobservation_outcome']=outcome;diagnostic(first)
    return observe


def _send_alive(ident, signum, *, observer=None):
    try:return send(ident,signum)
    except DistributionError:
        if (exited if observer is None else observer)(ident):return False
        raise


def terminate_all(identities, *, grace=10, resume=False, observer=None):
    # Leaves exit before their parents receive TERM. Share one grace/KILL
    # bound across the tree; frozen parents need not reap before exit proof.
    pending={identity(x)['pid']:x for x in identities}
    if len(pending)!=len(identities):raise DistributionError('duplicate owned shutdown PID')
    observe=exited if observer is None else observer
    deadline=time.monotonic()+grace;kill_deadline=deadline+5
    while pending:
        parents={x['ppid'] for x in pending.values()}
        leaves=[x for pid,x in pending.items() if pid not in parents]
        if not leaves:raise DistributionError('owned shutdown lineage contains a cycle')
        for ident in leaves:
            if not observe(ident):
                _send_alive(ident,signal.SIGTERM,observer=observer)
                if resume:_send_alive(ident,signal.SIGCONT,observer=observer)
        while any(not observe(x) for x in leaves) and time.monotonic()<deadline:time.sleep(.02)
        for ident in leaves:
            if not observe(ident):_send_alive(ident,signal.SIGKILL,observer=observer)
        while any(not observe(x) for x in leaves) and time.monotonic()<kill_deadline:time.sleep(.02)
        if any(not observe(x) for x in leaves):raise DistributionError('verified owned processes remain after bounded shutdown')
        for ident in leaves:pending.pop(ident['pid'])


def freeze_tree_for_stop(owner, captured, frozen, filter_tree, *, capture=None, signaler=None, clock=None):
    """Typed known-ps retry resumes only exact own readers, then freezes a new tree."""
    capture=capture or descendants;signaler=signaler or send;clock=clock or time.monotonic;deadline=clock()+2
    def remember(values):
        merged={value['pid']:value for value in captured}
        for value in values:
            if value['pid'] in merged and merged[value['pid']]!=value:raise DistributionError('captured PID identity changed during refreeze')
            merged[value['pid']]=value
        captured[:]=merged.values()
    def freeze(value):
        if signaler(value,signal.SIGSTOP) and value not in frozen:frozen.append(value)
    for attempt in range(3):
        if not signaler(owner,signal.SIGSTOP):raise DistributionError('owned agent exited before force capture')
        if owner not in frozen:frozen.append(owner)
        try:
            remember(filter_tree(capture(owner['pid'])))
            for value in captured:freeze(value)
            remember(filter_tree(capture(owner['pid'])))
            for value in captured:freeze(value)
            return
        except KnownLivePsPending:
            for value in reversed(frozen):
                current=inspect(value['pid'])
                if current is None:continue
                if current!=value:raise DistributionError('paused reader identity changed during known ps retry')
                signaler(value,signal.SIGCONT)
            if attempt==2 or clock()>=deadline:raise
            capture(owner['pid'])
    raise DistributionError('known ps reader retry exhausted')
