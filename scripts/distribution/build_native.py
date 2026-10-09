#!/usr/bin/env python3
"""Build pinned native components into this installation; never touch development runtimes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.request
from common import DistributionError, no_symlinks, private_directory, read_object, relative_path, write_json_new
from trust import installation, sha256, atomic_private_json, verified_file


SOURCE_IDENTITY_GUIDANCE = ('Build from git clone or an official LiliuxFlow source release asset '
                            'containing SOURCE_COMMIT.json; GitHub Source code ZIP/tar.gz lacks that record.')


def _source_commit(value):
    # Git object IDs are complete SHA-1 or SHA-256 values, never short IDs/placeholders.
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) or not value.strip('0'):
        raise DistributionError('source commit must be a complete Git object ID; ' + SOURCE_IDENTITY_GUIDANCE)
    return value


def resolve_source_commit(source):
    """Resolve once before build writes; an archive record takes precedence over Git."""
    source = no_symlinks(source)
    try:
        receipt = no_symlinks(source / 'SOURCE_COMMIT.json')
        try:
            info = receipt.stat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode):
                raise DistributionError('source identity record must be a regular file')
            record = read_object(receipt)
            if (type(record.get('schema_version')) is not int or record['schema_version'] != 1
                or not {'schema_version', 'commit'}.issubset(record)
                or not set(record).issubset({'schema_version', 'commit', 'allowlist_sha256', 'runtime_source_view'})
                or ('allowlist_sha256' not in record and record.get('runtime_source_view') is not True)
                or ('allowlist_sha256' in record and (not isinstance(record['allowlist_sha256'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', record['allowlist_sha256'])))
                or ('runtime_source_view' in record and record['runtime_source_view'] is not True)):
                raise DistributionError('source identity record format or schema is unsupported')
            if 'allowlist_sha256' in record:
                allowlist = no_symlinks(source / 'manifests/distribution/source-allowlist.json')
                if not allowlist.is_file() or sha256(allowlist) != record['allowlist_sha256']:
                    raise DistributionError('source identity allowlist hash differs')
            return _source_commit(record['commit'])
    except (DistributionError, OSError, ValueError, UnicodeError):
        raise DistributionError('source identity record is invalid or unreadable; ' + SOURCE_IDENTITY_GUIDANCE) from None
    # Reuse the existing isolated Git environment. It blocks ancestor discovery
    # and inherited GIT_DIR/WORK_TREE overrides without rejecting linked worktrees.
    from ui_recipe import git_environment
    try:
        env = git_environment(source)
        root = subprocess.run(['git', '-C', str(source), 'rev-parse', '--show-toplevel'],
                              capture_output=True, text=True, env=env, timeout=10)
        root_name = root.stdout.rstrip('\n')
        if root.returncode or not root_name or not Path(root_name).is_absolute() or no_symlinks(Path(root_name)) != source:
            raise DistributionError('Git checkout root must equal the build source root')
        commit = subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', 'HEAD^{commit}'],
                                capture_output=True, text=True, env=env, timeout=10)
        if commit.returncode:
            raise DistributionError('Git checkout must have a readable commit')
        return _source_commit(commit.stdout.strip())
    except (DistributionError, OSError, subprocess.SubprocessError, UnicodeError):
        raise DistributionError('build source needs its own Git checkout root and commit; ' + SOURCE_IDENTITY_GUIDANCE) from None


def download(url, target, expected, cap=512*1024**2):
    target = no_symlinks(target)
    if not target.exists():
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'LiliuxFlow-native-builder'}),timeout=60) as response,target.open('xb') as out:
            while chunk := response.read(1024**2):
                out.write(chunk)
                if out.tell() > cap:
                    raise DistributionError('upstream archive exceeds download cap')
    if sha256(target) != expected:
        raise DistributionError('pinned upstream archive checksum differs')
    return target


def extract(archive,destination,*,select=None,prefix=None,node_links=False):
    destination = no_symlinks(destination)
    if destination.exists():
        raise DistributionError('source destination already exists')
    destination.mkdir(parents=True,mode=0o700)
    total = 0
    files = []
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar:
            path = relative_path(member.name.rstrip('/'))
            if prefix and path.parts[0] != prefix:
                raise DistributionError('toolchain archive prefix differs')
            rel = Path(*path.parts[1:])
            if not rel.parts or member.isdir():
                continue
            if select is not None and not select(rel.as_posix()):
                continue
            if member.issym() and node_links and rel.as_posix() in ('bin/npm','bin/npx','bin/corepack'):
                # Node build always calls the regular npm-cli JS file; these known launch links are not extracted.
                if member.linkname not in ('../lib/node_modules/npm/bin/npm-cli.js','../lib/node_modules/npm/bin/npx-cli.js','../lib/node_modules/corepack/dist/corepack.js'):
                    raise DistributionError('Node launcher link target differs')
                continue
            if not member.isfile() or member.size > 128 * 1024**2:
                raise DistributionError('selected upstream source contains a link/special/oversized file')
            total += member.size
            if total > 1024**3 or len(files) >= 50000:
                raise DistributionError('selected upstream source exceeds extraction cap')
            target = no_symlinks(destination / rel)
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target.open('xb') as out:
                shutil.copyfileobj(tar.extractfile(member),out)
            target.chmod(0o700 if member.mode & 0o111 else 0o600)
            files.append({'path':rel.as_posix(),'sha256':sha256(target),'bytes':member.size})
    return files


def command(argv,*,cwd,env,log,timeout=1800,resource_roots=None,runtime_root=None,immutable_source=None):
    from ownership import capture,descendants,terminate
    work=log.parent.parent
    from build_budget import snapshot,CAP,FLOOR
    resources=list(resource_roots) if resource_roots is not None else [work]
    def measure():
        try:
            value=snapshot(resources,runtime=runtime_root,immutable_source=immutable_source)
            failed=False
        except DistributionError as exc:
            value={'measurement_status':'COLLECTOR_ERROR','over_transient_cap':None,'below_disk_floor':None,
                'transient_build_payload_cache_bytes':None,'free_bytes':None,'transient_cap_bytes':CAP,
                'disk_floor_bytes':FLOOR,'collector_error':getattr(exc,'evidence',{
                    'category':'UNCLASSIFIED_INVENTORY_ERROR','stage':'snapshot',
                    'cause_exception_class':type(exc).__name__,'errno':getattr(exc,'errno',None)})}
            value_private=getattr(exc,'private_stderr',[]);failed=True
        else:value_private=getattr(value,'private_stderr',[])
        if value_private:
            import secrets
            names=[]
            for raw in value_private:
                file=log.with_name(log.name+'.inventory-'+secrets.token_hex(4)+'.stderr')
                fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as output:output.write(raw)
                names.append(file.name)
            value['private_collector_stderr_files']=names
        return value,failed
    before,before_error=measure()
    if before_error:
        write_json_new(log.with_suffix(log.suffix+'.resource-stop.json'),{'resource_snapshot':before,
            'stop_reason':'RESOURCE_COLLECTOR_ERROR','phase':'preflight','elapsed_seconds':0,
            'timeout_seconds':timeout,'automatic_deletion':False})
        raise DistributionError('native build refused because resource inventory is unavailable')
    if before['below_disk_floor']:raise DistributionError('native build disk floor is below64GiB')
    if before['over_transient_cap']:raise DistributionError('native transient build payload/cache exceeds8GiB')
    started=time.monotonic()
    with log.open('xb') as output:
        proc=subprocess.Popen([str(x) for x in argv],cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT)
        identity=capture(proc.pid,parent=os.getpid())
        while proc.poll() is None:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
            measured,collector_error=measure()
            timed_out=time.monotonic()-started > timeout
            if collector_error or measured['below_disk_floor'] or measured['over_transient_cap'] or timed_out:
                reason='RESOURCE_COLLECTOR_ERROR' if collector_error else ('TIME_BOUND' if timed_out else 'RESOURCE_BOUND')
                write_json_new(log.with_suffix(log.suffix+'.resource-stop.json'),{'resource_snapshot':measured,
                    'stop_reason':reason,'phase':'running','elapsed_seconds':time.monotonic()-started,
                    'timeout_seconds':timeout,'automatic_deletion':False})
                for child in reversed(descendants(proc.pid)):
                    terminate(child,grace=10)
                terminate(identity,grace=10)
                raise DistributionError('native build stopped because resource inventory is unavailable' if collector_error else 'native build stopped at disk/cache/time resource bound')
    if proc.returncode:
        raise DistributionError('native build step failed; inspect its private build log')
    return {'step':log.stem,'exit_code':proc.returncode,'elapsed_seconds':time.monotonic()-started}

def apply_ui(source_root,upstream_roots,manifest):
    from ui_recipe import replay
    return replay(source_root,upstream_roots,manifest)['recipe_sha256']

def install_source_view(source, data, commit, *, destination_name='source'):
    relative_path(destination_name)
    if '/' in destination_name:raise DistributionError('source-view name must be one directory component')
    optional_inputs = ('scripts/distribution/model_installer.py', 'services/model-installer/pyproject.toml',
                       'services/model-installer/uv.lock', 'docs/productization/MODEL_LICENSE.txt')
    if (source/'scripts/distribution/model_catalog.py').is_file():
        for value in optional_inputs:
            if not no_symlinks(source/value).is_file():
                raise DistributionError('runtime source omits an optional model helper, lock or original license')
    target=data/'runtime'/destination_name
    if target.exists():raise DistributionError('runtime source view already exists')
    private_directory(target)
    selected=[]
    for prefix in ('scripts/distribution','services/compat/src/compat_api','manifests/distribution','profiles/distribution'):
        base=source/prefix
        for file in sorted(base.rglob('*')):
            if file.is_symlink():raise DistributionError('runtime source contains a link')
            if not file.is_file() or '__pycache__' in file.parts or file.suffix not in ('.py','.json','.patch'):
                continue
            rel=file.relative_to(source);dest=target/rel;private_directory(dest.parent);shutil.copyfile(file,dest);dest.chmod(0o600)
            if sha256(dest)!=sha256(file):raise DistributionError('runtime source copy hash differs')
            selected.append({'path':rel.as_posix(),'sha256':sha256(dest)})
    # Static Welcome inputs are finite source files, never private installation data.
    for value in ('services/model-installer/pyproject.toml', 'services/model-installer/uv.lock',
                  'docs/productization/MODEL_LICENSE.txt'):
        file = source / value
        if file.is_file():
            no_symlinks(file); dest = target / value
            private_directory(dest.parent); shutil.copyfile(file, dest); dest.chmod(0o600)
            if sha256(dest) != sha256(file):raise DistributionError('optional model source copy hash differs')
            selected.append({'path': value, 'sha256': sha256(dest)})
    if (source/'manifests/distribution/welcome-assets.json').is_file():
        manifest=read_object(source/'manifests/distribution/welcome-assets.json')
        for item in [manifest['helper'],*manifest['assets']]:
            file=verified_file(source,item);rel=Path(item['path']);dest=target/rel
            private_directory(dest.parent);shutil.copyfile(file,dest);dest.chmod(0o600)
            selected.append({'path':rel.as_posix(),'sha256':sha256(dest)})
    write_json_new(target/'SOURCE_COMMIT.json',{'schema_version':1,'commit':commit,'runtime_source_view':True})
    return target,selected

NODE_INTERNAL_BIN_LINKS={
    'bin/nodejs':'bin/node',
    'bin/npm':'lib/node_modules/npm/bin/npm-cli.js',
    'bin/npx':'lib/node_modules/npm/bin/npx-cli.js',
    'bin/corepack':'lib/node_modules/corepack/dist/corepack.js',
}

def node_regular_target(origin,file):
    seen=set();cursor=file
    while cursor.is_symlink():
        if cursor in seen or len(seen)>=16:raise DistributionError('project Node link cycle')
        seen.add(cursor);link=os.readlink(cursor)
        if Path(link).is_absolute():raise DistributionError('project Node link is absolute')
        cursor=Path(os.path.abspath(cursor.parent/link))
        try:cursor.relative_to(origin)
        except ValueError:raise DistributionError('project Node link escapes origin')
        for parent in cursor.parents:
            if parent==origin:break
            if parent.is_symlink():raise DistributionError('project Node directory link refused')
    info=cursor.lstat()
    if not stat.S_ISREG(info.st_mode):raise DistributionError('project Node link target is not regular')
    return cursor,info

def copy_project_node(origin,target):
    # Only Node's known internal bin aliases may be materialized as regular files.
    # Archive/UI/source extraction remains strictly link-free and unchanged.
    origin=no_symlinks(origin);records=[];total=0
    for folder,dirs,files in os.walk(origin,followlinks=False):
        for name in dirs:
            if (Path(folder)/name).is_symlink():raise DistributionError('project Node directory link refused')
        for name in files:
            file=Path(folder)/name;rel=file.relative_to(origin).as_posix()
            if file.is_symlink():
                if rel not in NODE_INTERNAL_BIN_LINKS:raise DistributionError('project Node unknown bin link')
                resolved,info=node_regular_target(origin,file)
                if resolved.relative_to(origin).as_posix()!=NODE_INTERNAL_BIN_LINKS[rel]:raise DistributionError('project Node bin link canonical target differs')
            else:resolved,info=node_regular_target(origin,file)
            if info.st_uid not in (0,os.getuid()) or info.st_mode&0o002:raise DistributionError('project Node file owner/mode differs')
            total+=info.st_size
            if info.st_size>192*1024**2 or total>512*1024**2 or len(records)>=10000:raise DistributionError('project Node copy range exceeds cap')
            records.append((rel,resolved,info,sha256(resolved)))
    target.mkdir(mode=0o700)
    for rel,resolved,info,expected in records:
        dest=target/rel;dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb')as stream:
            actual=os.fstat(stream.fileno())
            if (actual.st_dev,actual.st_ino,actual.st_size,actual.st_mtime_ns)!=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns):raise DistributionError('project Node input changed before copy')
            with dest.open('xb')as output:shutil.copyfileobj(stream,output)
        dest.chmod(0o700 if info.st_mode&0o111 else 0o600)
        if sha256(dest)!=expected:raise DistributionError('project Node copied file bytes differ')
    return records

def install_project_node(data, node):
    origin=Path(node).resolve().parents[1]
    target=data/'runtime/toolchains/node'
    private_directory(target.parent)
    if target.exists():
        raise DistributionError('installation Node toolchain already exists; verify its receipt before reuse')
    copy_project_node(origin,target)
    launcher=target/'bin/npm'
    text='#!/bin/sh\nNODE_BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\nexec "$NODE_BASE/bin/node" "$NODE_BASE/lib/node_modules/npm/bin/npm-cli.js" "$@"\n'
    launcher.write_text(text);launcher.chmod(0o700)
    return {name:{'path':str(file.relative_to(data)),'sha256':sha256(file)} for name,file in {
        'node':target/'bin/node','npm':launcher,'npm_cli':target/'lib/node_modules/npm/bin/npm-cli.js'}.items()}

def build(data,*,cargo=None,go=None,node=None,npm_cli=None,pg_bin=None,ui_manifest=None,execute=False,optin_engine=None):
    data=no_symlinks(data)
    config=installation(data)
    source=no_symlinks(Path(config['source_root']))
    source_commit=resolve_source_commit(source)
    lock=read_object(source/'manifests/distribution/native-sources.json')
    from native_recipe import LEGACY_ENGINE, OPT64_ENGINE, select_lily_recipe
    select_lily_recipe(lock)
    if optin_engine not in (None, OPT64_ENGINE):
        raise DistributionError('native opt-in engine selector is outside the finite allowlist')
    opt_pin, opt_baseline = select_lily_recipe(lock, OPT64_ENGINE) if optin_engine else (None, None)
    plan={'state':'plan','source_commit':source_commit,'source_commits':{k:lock[k]['commit'] for k in ('lily','litellm','llama_swap')},
          'steps':['fetch SHA-pinned sources/toolchains',f"replay {len(lock['lily']['patches'])} canonical Lily patches and verify {len(read_object(source/'manifests/distribution/lily-source-baseline.json')['files'])} source hashes",
                   'uv sync --locked to installation runtime venvs','install finite LiteLLM context policy and catalog filters',
                   'npm ci and two static UI builds','cargo locked Lily native build','Go embed_ui llama-swap build','write release trust'],
          'optin_engine':optin_engine, 'existing_default_engine':LEGACY_ENGINE,
          'optin_source_commit':opt_pin['commit'] if opt_pin else None,
          'optin_archive_sha256':opt_pin['archive_sha256'] if opt_pin else None,
          'ui':'localized recipe supplied' if ui_manifest else 'upstream UI only; final locale acceptance false',
          'live_services_started':False,'weights_downloaded_or_loaded':False,'database_initialized':False}
    if not execute:
        return plan
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise DistributionError('native serving build requires Darwin arm64')
    if config['runtime_state'] != 'NOT_BUILT' or (data/'runtime/release-trust.json').exists():
        raise DistributionError('runtime already exists; build an explicit upgrade candidate instead of overwriting')
    runtime=private_directory(data/'runtime')
    import secrets as rng
    build_root=private_directory(data/'build')
    work=private_directory(build_root/('attempt-'+rng.token_hex(6)))
    logs=private_directory(work/'logs')
    archives=private_directory(work/'archives')
    native=private_directory(runtime/'bin')
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'), 'HOME':str(Path.home()),'TMPDIR':str(private_directory(work/'tmp')),
         'LANG':'en_US.UTF-8','CI':'1','NEXT_TELEMETRY_DISABLED':'1','CARGO_BUILD_JOBS':'2','RUSTUP_TOOLCHAIN':'1.97.0',
         'UV_CACHE_DIR':str(private_directory(work/'uv-cache')),'PRISMA_HOME_DIR':str(private_directory(data/'cache/prisma')),
         'PRISMA_BINARY_CACHE_DIR':str(private_directory(data/'cache/prisma/binaries')),
         'PRISMA_NODEENV_CACHE_DIR':str(private_directory(data/'cache/prisma/nodeenv')),
         'GOMODCACHE':str(private_directory(work/'go-mod-cache')),'GOCACHE':str(private_directory(work/'go-cache')),
         'GOTOOLCHAIN':'local','GOMAXPROCS':'2'}
    for name,explicit in (('go',go),('node',node)):
        if explicit:
            continue
        item=lock['project_toolchains'][name]
        archive=download(item['url'],archives/(name+'.tar.gz'),item['sha256'])
        dest=work/('toolchain-'+name)
        extract(archive,dest,prefix=item['archive_prefix'],node_links=name=='node')
        if name=='go':go=dest/item['executable']
        else:
            node=dest/item['executable'];npm_cli=dest/item['npm_cli']
    node=Path(node).resolve();go=Path(go).resolve()
    if npm_cli is None:
        raise DistributionError('explicit Node requires an explicit npm-cli path')
    npm_cli=Path(npm_cli).resolve()
    runtime_node=install_project_node(data,node)
    env['PATH']=str(data/'runtime/toolchains/node/bin')+':'+str(go.parent)+':'+env['PATH']
    env['PRISMA_USE_GLOBAL_NODE']='true'
    env['PRISMA_USE_NODEJS_BIN']='false'
    cargo=Path(cargo or shutil.which('cargo') or Path.home()/'.cargo/bin/cargo')
    rust=subprocess.run([str(cargo),'--version'],capture_output=True,text=True,env=env)
    gv=subprocess.run([str(go),'version'],capture_output=True,text=True)
    nv=subprocess.run([str(node),'--version'],capture_output=True,text=True)
    if rust.returncode or '1.97.0' not in rust.stdout or gv.returncode or 'go1.27.1' not in gv.stdout or nv.returncode or tuple(map(int,nv.stdout.strip().lstrip('v').split('.'))) < (24,14,1):
        raise DistributionError('native toolchain version differs from the build contract')
    uv=shutil.which('uv')
    if not uv:raise DistributionError('uv is required for locked native Python dependencies')
    if pg_bin is None:
        prefix=subprocess.run(['brew','--prefix','postgresql@17'],capture_output=True,text=True)
        if prefix.returncode:raise DistributionError('native PostgreSQL17 tools are required')
        pg_bin=Path(prefix.stdout.strip())/'bin'
    pg_bin=Path(pg_bin).resolve()
    pv=subprocess.run([str(pg_bin/'postgres'),'--version'],capture_output=True,text=True)
    if pv.returncode or 'PostgreSQL) 17.' not in pv.stdout:
        raise DistributionError('native PostgreSQL17 executable differs')
    upstream={}
    fetch_receipts={}
    for name in ('lily','llama_swap','litellm'):
        item=lock[name]
        archive=download('https://codeload.github.com/'+item['repository']+'/tar.gz/'+item['commit'],archives/(name+'.tar.gz'),item['archive_sha256'])
        target=work/name
        select=(lambda p:p.startswith('ui/litellm-dashboard/') or p in ('LICENSE','LICENSE.md','NOTICE')) if name=='litellm' else None
        files=extract(archive,target,select=select)
        upstream['llama-swap' if name=='llama_swap' else name]=target
        fetch_receipts[name]={'commit':item['commit'],'archive_sha256':item['archive_sha256'],'file_count':len(files)}
    from native_recipe import apply_lily_source_patches
    lily=upstream['lily']
    apply_lily_source_patches(source,lily,lock)
    opt_recipe=None
    if opt_pin:
        archive=download('https://codeload.github.com/'+opt_pin['repository']+'/tar.gz/'+opt_pin['commit'],
                         archives/'lily-opt64.tar.gz',opt_pin['archive_sha256'],cap=32*1024**2)
        opt_source=work/'lily-opt64'
        upstream['lily_opt64']=opt_source
        files=extract(archive,opt_source)
        opt_recipe=apply_lily_source_patches(source,opt_source,lock,OPT64_ENGINE)
        fetch_receipts['lily_opt64']={'commit':opt_pin['commit'],'archive_sha256':opt_pin['archive_sha256'],'file_count':len(files)}
    from native_recipe import apply_manager_source_patches
    manager_recipe=apply_manager_source_patches(source,upstream['llama-swap'],lock['llama_swap'])
    ui_recipe=None
    if ui_manifest:
        ui_recipe=apply_ui(source,upstream,ui_manifest)
    steps=[]
    for service in ('compat','litellm'):
        step_env={**env,'UV_PROJECT_ENVIRONMENT':str(runtime/service)}
        steps.append(command([uv,'sync','--locked','--no-dev','--python','3.12','--project',source/'services'/service],cwd=source,env=step_env,log=logs/(service+'-uv.txt')))
    version=subprocess.run([str(runtime/'litellm/bin/python'),'-c','from importlib.metadata import version; print(version("litellm"))'],capture_output=True,text=True,env=env)
    if version.returncode or version.stdout.strip()!='1.102.1':
        raise DistributionError('context policy requires the pinned LiteLLM1.102.1 runtime')
    from welcome_native import install_welcome
    welcome_receipt=install_welcome(source,data)
    from patch_litellm_profiles import install_runtime_policy
    lp_profile_policy=install_runtime_policy(source,data)
    # No Node listener: both upstream applications produce static files.
    for name,target in (('litellm',upstream['litellm']/'ui/litellm-dashboard'),('llama-swap',upstream['llama-swap']/'ui')):
        steps.append(command([node,npm_cli,'ci','--cache',work/'npm-cache','--no-fund','--no-audit'],cwd=target,env=env,log=logs/(name+'-npm.txt')))
        steps.append(command([node,npm_cli,'run','build'],cwd=target,env=env,log=logs/(name+'-ui.txt')))
    steps.append(command([cargo,'build','--release','--locked','--bin','lily','--target-dir',work/'cargo-target'],cwd=lily,env=env,log=logs/'lily-build.txt'))
    shutil.copyfile(work/'cargo-target/release/lily',native/'lily');(native/'lily').chmod(0o700)
    if opt_pin:
        steps.append(command([cargo,'build','--release','--locked','--bin','lily','--target-dir',work/'cargo-opt64-target'],cwd=opt_source,env=env,log=logs/'lily-opt64-build.txt'))
        shutil.copyfile(work/'cargo-opt64-target/release/lily',native/'lily-opt64');(native/'lily-opt64').chmod(0o700)
    steps.append(command([go,'build','-mod=readonly','-trimpath','-tags','embed_ui','-ldflags','-X main.version=v260 -X main.commit='+lock['llama_swap']['commit']+' -X main.date=2026-10-01T00:00:00Z','-o',native/'llama-swap','.'],cwd=upstream['llama-swap'],env=env,log=logs/'llama-swap-build.txt'))
    (native/'llama-swap').chmod(0o700)
    ui=runtime/'litellm/lib/python3.12/site-packages/litellm/proxy/_experimental/out'
    if not ui.parent.exists():
        raise DistributionError('pinned LiteLLM static asset destination differs')
    if ui.exists():shutil.rmtree(ui)
    shutil.copytree(upstream['litellm']/'ui/litellm-dashboard/out',ui)
    notices=private_directory(runtime/'notices')
    for name,target in upstream.items():
        for filename in ('LICENSE','LICENSE.md','LICENSE.txt','NOTICE'):
            file=target/filename
            if file.is_file():shutil.copyfile(file,notices/(name+'-'+filename))
    binaries={'lily':native/'lily','llama_swap':native/'llama-swap'}
    if opt_pin:binaries['lily_opt64']=native/'lily-opt64'
    for name,service in (('compat_python','compat'),('litellm_python','litellm')):
        interpreter=runtime/service/'bin/python'
        actual=interpreter.resolve()
        if interpreter.is_symlink():
            interpreter.unlink()
            shutil.copyfile(actual,interpreter)
            interpreter.chmod(0o700)
        binaries[name]=interpreter
    source_paths=['services/compat/src/compat_api/'+p for p in ('app.py','profile.py','llama_guard.py','portable.py','__init__.py')]+['scripts/distribution/'+p for p in ('trust.py','agent.py','model_runner.py','ownership.py','forced_stop.py','common.py','backup_native.py','liliuxflow.py','profile_registry.py','runtime_proof.py','litellm_profile_policy.py','patch_litellm_profiles.py','welcome_native.py','welcome_profiles.py','model_catalog.py','model_installer.py')]+['patches/litellm-welcome/runtime.py','services/model-installer/pyproject.toml','services/model-installer/uv.lock','manifests/distribution/native-sources.json','manifests/distribution/checkpoint-files.json']
    source_paths += ['scripts/distribution/native_recipe.py','scripts/distribution/optin_engine.py','scripts/distribution/native_metadata.py','scripts/distribution/build_native.py']
    future_opt, future_baseline=select_lily_recipe(lock,OPT64_ENGINE)
    source_paths += [future_baseline]+[row['path'] for row in future_opt['patches']]
    from model_catalog import load_catalog
    load_catalog(source)
    from profile_registry import load_registry
    load_registry(source)
    welcome_sources=read_object(source/'manifests/distribution/welcome-assets.json')
    source_paths+=['manifests/distribution/welcome-assets.json']+[item['path'] for item in welcome_sources['assets']]
    trust={'schema_version':1,'installation_id':config['installation_id'],'source_commit':source_commit,
           'engine_contract_support':{'schema_version':1},
           'lily_source_commit':lock['lily']['commit'],'llama_swap_source_commit':lock['llama_swap']['commit'],
           'lily_patch_sha256':[x['sha256'] for x in lock['lily']['patches']],
           'llama_swap_patch_sha256':[x['sha256'] for x in lock['llama_swap'].get('patches',[])],
           'llama_swap_source_recipe':manager_recipe,
           'profile':{'path':'profiles/distribution/safe64k.json','sha256':sha256(source/'profiles/distribution/safe64k.json')},
           'profile_registry':{'path':'profiles/distribution/context-registry.json','sha256':sha256(source/'profiles/distribution/context-registry.json')},
           'litellm_profile_policy':lp_profile_policy,'welcome':welcome_receipt,'optional_model_support':{'schema_version':1},
           'source_files':[{'path':p,'sha256':sha256(source/p)} for p in source_paths],
           'binaries':{name:{'path':str(path.relative_to(data)),'sha256':sha256(path)} for name,path in binaries.items()},'build_tools':runtime_node,
           'postgresql_bin':str(pg_bin),'postgresql_sha256':sha256(pg_bin/'postgres'),'postgresql_tools':{name:sha256(pg_bin/name) for name in ('postgres','initdb','pg_ctl','psql','pg_dump','pg_restore')},'ui_recipe_sha256':ui_recipe,'ui_locale_acceptance':False,
           'build_steps':steps,'fetches':fetch_receipts,'toolchains':{'rust':rust.stdout.strip(),'go':gv.stdout.strip(),'node':nv.stdout.strip(),'postgresql':pv.stdout.strip()}}
    if opt_pin:
        from optin_engine import generated_record
        trust['optin_engines']={OPT64_ENGINE:generated_record(opt_recipe,sha256(native/'lily-opt64'))}
        trust['enabled_optin_profiles']=['ctx64k-mtp2']
    runtime_source,source_copy=install_source_view(source,data,trust['source_commit'])
    trust['runtime_source_copy']=source_copy
    dependency_paths=[work/'lily/Cargo.toml',work/'lily/Cargo.lock',work/'llama_swap/go.mod',work/'llama_swap/go.sum',work/'litellm/ui/litellm-dashboard/package.json',work/'litellm/ui/litellm-dashboard/package-lock.json',work/'llama_swap/ui/package.json',work/'llama_swap/ui/package-lock.json']
    if opt_pin:dependency_paths += [opt_source/'Cargo.toml',opt_source/'Cargo.lock']
    dependency_manifest={'schema_version':1,'installation_id':config['installation_id'],'source_commit':trust['source_commit'],'build_root':str(work.relative_to(data)),'inputs':[{'path':str(file.relative_to(data)),'sha256':sha256(file)} for file in dependency_paths],'scope':'actual successful build lock inputs; retain until native inventory collected'}
    write_json_new(runtime/'dependency-inputs.json',dependency_manifest);trust['dependency_inputs']={'path':'runtime/dependency-inputs.json','sha256':sha256(runtime/'dependency-inputs.json')}
    config['package_source_root']=str(source)
    config['source_root']=str(runtime_source)
    write_json_new(runtime/'release-trust.json',trust)
    config['runtime_state']='BUILT';atomic_private_json(data/'install.json',config)
    return {'state':'built','source_commit':trust['source_commit'],'steps':steps,'ui_locale_acceptance':False,'live_services_started':False}


def build_optin_candidate(data, *, cargo=None, execute=False):
    """Add one generated opt64 engine to a versioned installation; no UI/DB/model operation.

    This only adds the fixed native executable and trust record. Applying the
    resulting catalog/manager configuration is an explicit later lifecycle step.
    Existing binaries, profiles, credentials and cached payloads stay bound.
    """
    from trust import validate, private_file
    from native_recipe import OPT64_ENGINE, select_lily_recipe, apply_lily_source_patches
    from optin_engine import generated_record
    import copy
    data=no_symlinks(data);trusted=validate(data,require_checkpoint=False)
    old=copy.deepcopy(trusted['trust']);source=trusted['source_root']
    if old.get('engine_contract_support')!={'schema_version':1} or trusted['engines']:
        raise DistributionError('native-only opt-in build requires the versioned disabled engine contract')
    lock=read_object(source/'manifests/distribution/native-sources.json')
    pin,baseline=select_lily_recipe(lock,OPT64_ENGINE)
    # The default build binds all recipe inputs even before this engine is enabled.
    source_records={row['path']:row for row in old['source_files']}
    for path in [baseline,*[row['path'] for row in pin['patches']]]:
        if path not in source_records:raise DistributionError('native-only engine recipe input is not trusted')
        verified_file(source,source_records[path])
    from profile_registry import parse_registry
    registry=parse_registry(read_object(verified_file(source,old['profile_registry'])),enabled_optin_profiles=['ctx64k-mtp2'])
    if registry.default.profile_id!='ctx64k':raise DistributionError('native-only engine changed the default profile')
    destination=no_symlinks(data/'runtime/bin/lily-opt64')
    if destination.exists():raise DistributionError('opt-in executable already exists; owner left untouched')
    plan={'state':'plan','engine_id':OPT64_ENGINE,'source_commit':old['source_commit'],
          'native_source_commit':pin['commit'],'archive_sha256':pin['archive_sha256'],
          'patch_sha256':[row['sha256'] for row in pin['patches']],
          'source_inventory_sha256':pin['source_inventory_sha256'],'enabled_optin_profiles':['ctx64k-mtp2'],
          'existing_default_engine':'legacy-db3-mtp0','frontend_build':False,'model_loaded':False,
          'live_services_started':False,'keys_or_database_changed':False}
    if not execute:return plan
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise DistributionError('native serving build requires Darwin arm64')
    import secrets as rng
    work=private_directory(data/'build'/('opt64-'+rng.token_hex(6)));logs=private_directory(work/'logs')
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(Path.home()),
         'TMPDIR':str(private_directory(work/'tmp')),'LANG':'en_US.UTF-8','CARGO_BUILD_JOBS':'2','RUSTUP_TOOLCHAIN':'1.97.0'}
    cargo=Path(cargo or shutil.which('cargo') or Path.home()/'.cargo/bin/cargo')
    version=subprocess.run([str(cargo),'--version'],capture_output=True,text=True,env=env)
    if version.returncode or '1.97.0' not in version.stdout:
        raise DistributionError('native-only Rust toolchain differs from the build contract')
    archive=download('https://codeload.github.com/'+pin['repository']+'/tar.gz/'+pin['commit'],work/'lily-opt64.tar.gz',
                     pin['archive_sha256'],cap=32*1024**2)
    target=work/'source';extract(archive,target)
    replay=apply_lily_source_patches(source,target,lock,OPT64_ENGINE)
    step=command([cargo,'build','--release','--locked','--bin','lily','--target-dir',work/'cargo-target'],
                 cwd=target,env=env,log=logs/'lily-opt64-build.txt',resource_roots=[work])
    candidate=work/'cargo-target/release/lily';binary_sha=sha256(candidate)
    # Revalidate before publishing. Never overwrite an installed trust changed by another owner.
    current=validate(data,require_checkpoint=False)
    if current['trust']!=old or current['config']!=trusted['config']:
        raise DistributionError('installation changed during native-only build')
    receipt=generated_record(replay,binary_sha)
    trust_path=private_file(data/'runtime/release-trust.json')
    stamp=trust_path.stat()
    write_json_new(work/'previous-release-trust.json',old)
    with destination.open('xb') as output, candidate.open('rb') as input:shutil.copyfileobj(input,output)
    destination.chmod(0o700)
    new=copy.deepcopy(old);new['binaries']['lily_opt64']={'path':'runtime/bin/lily-opt64','sha256':binary_sha}
    new['optin_engines']={OPT64_ENGINE:receipt};new['enabled_optin_profiles']=['ctx64k-mtp2']
    new['optin_native_build']={'source_commit':pin['commit'],'archive_sha256':pin['archive_sha256'],
                             'source_inventory_sha256':replay['source_inventory_sha256'],'step':step}
    now=trust_path.stat()
    if (now.st_dev,now.st_ino,now.st_size,now.st_mtime_ns)!=(stamp.st_dev,stamp.st_ino,stamp.st_size,stamp.st_mtime_ns) or read_object(trust_path)!=old:
        raise DistributionError('installation trust changed before opt-in publication; candidate retained')
    atomic_private_json(trust_path,new)
    validate(data,require_checkpoint=False)
    return {**plan,'state':'built','binary_sha256':binary_sha,'source_replay':'PASS',
            'services_reconfigured':False,'runtime_acceptance':False}


def build_optin_source_candidate(data, *, cargo=None, execute=False):
    """Compile only a fixed native candidate from a fresh normal installation.

    A build-only receipt cannot stand in for an installed manager/UI trust or
    enable an alias. Source identity comes from the existing normal resolver.
    """
    from native_recipe import OPT64_ENGINE, select_lily_recipe, apply_lily_source_patches
    from optin_engine import generated_record
    data=no_symlinks(data);config=installation(data);source=no_symlinks(Path(config['source_root']))
    source_commit=resolve_source_commit(source)
    if config.get('runtime_state')!='NOT_BUILT' or (data/'runtime/release-trust.json').exists():
        raise DistributionError('build-only candidate requires a fresh not-built installation')
    lock=read_object(source/'manifests/distribution/native-sources.json');pin,baseline=select_lily_recipe(lock,OPT64_ENGINE)
    for row in pin['patches']:verified_file(source,row)
    from profile_registry import load_registry
    registry=load_registry(source)
    if not any(p.profile_id=='ctx64k-mtp2' and not p.production_enabled for p in registry.profiles):
        raise DistributionError('build-only candidate requires the finite disabled opt64 source profile')
    plan={'state':'plan','engine_id':OPT64_ENGINE,'source_commit':source_commit,'native_source_commit':pin['commit'],
          'archive_sha256':pin['archive_sha256'],'patch_sha256':[r['sha256'] for r in pin['patches']],
          'source_inventory_sha256':pin['source_inventory_sha256'],'frontend_build':False,'model_loaded':False,
          'production_enabled':False,'default':False,'installed_runtime_ready':False,'runtime_acceptance':False}
    if not execute:return plan
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise DistributionError('native serving build requires Darwin arm64')
    import secrets as rng
    work=private_directory(data/'build'/('native-opt64-'+rng.token_hex(6)));logs=private_directory(work/'logs')
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(Path.home()),'TMPDIR':str(private_directory(work/'tmp')),
         'LANG':'en_US.UTF-8','CARGO_BUILD_JOBS':'2','RUSTUP_TOOLCHAIN':'1.97.0'}
    cargo=Path(cargo or shutil.which('cargo') or Path.home()/'.cargo/bin/cargo')
    version=subprocess.run([str(cargo),'--version'],capture_output=True,text=True,env=env)
    if version.returncode or '1.97.0' not in version.stdout:
        raise DistributionError('build-only Rust toolchain differs')
    archive=download('https://codeload.github.com/'+pin['repository']+'/tar.gz/'+pin['commit'],work/'lily-opt64.tar.gz',
                     pin['archive_sha256'],cap=32*1024**2)
    target=work/'source';extract(archive,target);replay=apply_lily_source_patches(source,target,lock,OPT64_ENGINE)
    controls=['scripts/distribution/'+name for name in ('build_native.py','native_recipe.py','trust.py','common.py','optin_engine.py','profile_registry.py')]
    inputs={path:sha256(source/path) for path in controls+[baseline,'manifests/distribution/native-sources.json','profiles/distribution/context-registry.json']}
    step=command([cargo,'build','--release','--locked','--bin','lily','--target-dir',work/'cargo-target'],
                 cwd=target,env=env,log=logs/'lily-opt64-build.txt',resource_roots=[work])
    if installation(data)!=config or any(sha256(source/path)!=digest for path,digest in inputs.items()):
        raise DistributionError('candidate installation or recipe source changed during compilation')
    binary=work/'cargo-target/release/lily';record=generated_record(replay,sha256(binary))
    receipt={**plan,'state':'built','engine':record,'binary':{'path':str(binary.relative_to(data)),'sha256':sha256(binary)},
             'source_files_sha256':inputs,'step':step,'source_replay':'PASS','native_build_only':True}
    write_json_new(work/'native-candidate.json',receipt)
    return {**receipt,'receipt_path':str((work/'native-candidate.json').relative_to(data))}
