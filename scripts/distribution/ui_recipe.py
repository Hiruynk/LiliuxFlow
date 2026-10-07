"""Portable UI patch/asset recipes: validate all paths and hashes before any replay."""
import json
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from common import DistributionError,no_symlinks,read_object,relative_path
from trust import verified_file,sha256

UI_ROOTS={'litellm':'ui/litellm-dashboard','llama-swap':'ui'}

def git_environment(target):
    # Do not discover an ancestor checkout or inherit caller GIT_DIR/WORK_TREE/config.
    return {'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(Path.home()),'LANG':'en_US.UTF-8','GIT_CEILING_DIRECTORIES':str(target.parent),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull}

def source_tree_digest(target,name):
    target=no_symlinks(target);entries=[];base=no_symlinks(target/UI_ROOTS[name])
    if not base.is_dir():raise DistributionError('UI replay source root is missing')
    for file in sorted(base.rglob('*'),key=lambda file:file.relative_to(target).as_posix()):
        rel=file.relative_to(target).as_posix();parts=file.relative_to(base).parts
        if any(part in {'.git','node_modules','.next','out','__pycache__'} for part in parts) or file.name=='tsconfig.tsbuildinfo':continue
        if file.is_symlink():raise DistributionError('UI replay tree contains a link')
        if file.is_file():entries.append({'path':rel,'sha256':sha256(file)})
    return hashlib.sha256(json.dumps(entries,separators=(',',':')).encode()).hexdigest()

def ui_path(name,value):
    relative_path(value)
    prefix=UI_ROOTS[name]+'/'
    if not value.startswith(prefix):raise DistributionError('UI recipe target escapes its upstream UI root')
    parts=Path(value).parts
    if any(p in {'.git','node_modules','.next','out','var','secrets','__pycache__'} for p in parts):
        raise DistributionError('UI recipe includes cache, secret or generated build input')
    return value


def patch_paths(patch,strip):
    content=patch.read_bytes()
    for line in content.splitlines():
        if line.startswith((b'new file mode ',b'new mode ',b'old mode ',b'deleted file mode ')):
            if line.rsplit(b' ',1)[-1] not in (b'100644',b'100755'):
                raise DistributionError('UI source patch contains a link or special mode')
    # Inventory must not inherit an unrelated ancestor repo prefix or caller Git config.
    # Git ignores patch paths outside the current subdirectory when it discovers a repo.
    with tempfile.TemporaryDirectory(prefix='liliuxflow-patch-inventory-') as folder:
        task=Path(folder)
        result=subprocess.run(['git','apply','--numstat','-z','-p'+str(strip),str(patch.resolve())],cwd=task,capture_output=True,env=git_environment(task))
    if result.returncode:raise DistributionError('UI patch path inventory failed')
    files=[]
    for row in result.stdout.split(b'\0'):
        if not row:continue
        fields=row.split(b'\t',2)
        if len(fields)!=3 or not fields[2] or fields[0]==b'-' or fields[1]==b'-':
            raise DistributionError('UI patch contains binary/rename or malformed paths')
        files.append(fields[2].decode('utf-8'))
    if not files:raise DistributionError('UI patch has no source changes')
    return files


def recipe_source_file(source,item):
    from package import eligible
    policy=read_object(source/'manifests/distribution/source-allowlist.json')
    if not eligible(item['path'],policy):
        raise DistributionError('UI recipe source is outside the release allowlist')
    return verified_file(source,item)

def validate_recipe(source,roots,manifest):
    source=no_symlinks(source);manifest=no_symlinks(manifest);recipe=read_object(manifest)
    if recipe.get('schema_version')!=1 or set(recipe.get('apps',{}))!=set(UI_ROOTS):
        raise DistributionError('portable UI recipe schema/apps differ')
    locks=read_object(source/'manifests/distribution/native-sources.json');plan=[]
    for name,record in recipe['apps'].items():
        pin=locks['litellm' if name=='litellm' else 'llama_swap']
        if record.get('upstream_commit')!=pin['commit'] or record.get('archive_sha256')!=pin['archive_sha256']:
            raise DistributionError('portable UI recipe upstream commit/archive identity differs')
        target=no_symlinks(roots[name]);patches=[];extras=[];outputs=[]
        tree_hash=record.get('source_tree_sha256')
        if tree_hash is not None and (not isinstance(tree_hash,str) or len(tree_hash)!=64 or any(c not in '0123456789abcdef' for c in tree_hash)):raise DistributionError('UI source tree hash is malformed')
        for item in record.get('patches',[]):
            file=recipe_source_file(source,item);strip=item.get('strip',1)
            if type(strip) is not int or not 0<=strip<=5:raise DistributionError('invalid UI patch strip level')
            paths=[ui_path(name,p) for p in patch_paths(file,strip)]
            for path in paths:no_symlinks(target/path)
            patches.append((file,strip))
        for item in record.get('extra_files',[]):
            file=recipe_source_file(source,{'path':item['source'],'sha256':item['sha256']})
            dest=no_symlinks(target/ui_path(name,item['target']))
            if dest.exists():
                if not item.get('before_sha256') or sha256(dest)!=item['before_sha256']:
                    raise DistributionError('UI extra-file preimage differs; refusing overwrite')
            elif item.get('before_sha256') is not None:
                raise DistributionError('UI extra-file preimage is missing')
            extras.append((file,dest))
        for item in record.get('lockfiles',[]):
            ui_path(name,item['path'])
            if not item['path'].endswith('/package-lock.json'):
                raise DistributionError('UI recipe lockfile path is not package-lock.json')
            if not isinstance(item.get('sha256'),str) or len(item['sha256'])!=64:
                raise DistributionError('UI recipe lockfile hash is missing')
            outputs.append(item)
        if not outputs:raise DistributionError('UI recipe must bind each final package lock')
        plan.append((name,target,patches,extras,outputs))
    return recipe,plan


def replay(source,roots,manifest):
    import shutil
    recipe,plan=validate_recipe(source,roots,manifest)
    for name,target,patches,extras,locks in plan:
        for patch,strip in patches:
            for mode in ('--check',None):
                argv=['git','apply','-p'+str(strip)]+([mode] if mode else [])+[str(patch)]
                result=subprocess.run(argv,cwd=target,capture_output=True,env=git_environment(target))
                if result.returncode:raise DistributionError('UI patch failed to replay against pinned source')
        for file,dest in extras:
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest)
        for item in locks:verified_file(target,item)
        expected_tree=recipe['apps'][name].get('source_tree_sha256')
        if expected_tree is not None and source_tree_digest(target,name)!=expected_tree:raise DistributionError('materialized UI source tree differs from frozen candidate')
    return {'recipe_sha256':sha256(manifest),'apps':list(UI_ROOTS),'replay':'PASS','ui_locale_acceptance':False,
            'coverage_state':recipe.get('coverage_state','NOT_RUN'),'source_authoring_state':recipe.get('state','IN_PROGRESS')}
