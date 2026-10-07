#!/usr/bin/env python3
"""Collect final own native dependency/notice inputs without touching DB, keys, cache or weights."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from common import DistributionError,no_symlinks,private_directory,read_object,relative_path,write_json_new
from trust import validate,verified_file,sha256
from notices import verify_first_party

MAX_STAGE=512*1024**2
MAX_NOTICE=16*1024**2
NOTICE_NAMES=('LICENSE','LICENCE','COPYING','NOTICE','COPYRIGHT')

def contained(root,path):
    root=no_symlinks(root);path=no_symlinks(path)
    try:path.relative_to(root)
    except ValueError:raise DistributionError('dependency input escaped its permitted root')
    return path

class Stager:
    def __init__(self,root):
        self.root=private_directory(root);self.total=0;self.files=[]
    def add_bytes(self,relative,data,*,kind,source_hash=None):
        relative_path(relative);self.total+=len(data)
        if self.total>MAX_STAGE:raise DistributionError('dependency input stage exceeds512MiB cap')
        dest=contained(self.root,self.root/relative);private_directory(dest.parent)
        if dest.exists():raise DistributionError('dependency staging output must be new')
        dest.write_bytes(data);dest.chmod(0o600)
        self.files.append({'path':relative,'bytes':len(data),'sha256':sha256(dest),'kind':kind,'original_source_sha256':source_hash})
    def add_file(self,root,file,relative,*,kind,limit=MAX_NOTICE):
        file=contained(root,file)
        if not file.is_file() or file.stat().st_size>limit:raise DistributionError('dependency file type/size is invalid')
        data=file.read_bytes();self.add_bytes(relative,data,kind=kind,source_hash=hashlib.sha256(data).hexdigest())

def component_id(ecosystem,name,version):
    return ecosystem+'-'+hashlib.sha256((name+'\0'+version).encode()).hexdigest()[:20]

def original_notices(stage,root,component,*,declared=()):
    root=no_symlinks(root);files=[]
    for name in declared:
        relative_path(name);candidate=contained(root,root/name)
        if candidate.is_file():files.append(candidate)
    for file in root.iterdir():
        if file.is_file() and any(file.name.upper().startswith(name) for name in NOTICE_NAMES):files.append(file)
    results=[]
    for file in sorted(set(files)):
        contained(root,file);relative='notices/'+component+'/'+sha256(file)[:16]+'-'+file.name
        existing=stage.root/relative
        if existing.exists():
            if sha256(existing)!=sha256(file):raise DistributionError('notice deduplication identity differs')
        else:stage.add_file(root,file,relative,kind='original-notice')
        results.append(relative)
    return results

def python_installed(stage,site,service):
    site=no_symlinks(site);components=[]
    if not site.is_dir():raise DistributionError('installed Python metadata root missing')
    for directory in sorted(site.glob('*.dist-info')):
        metadata=contained(site,directory/'METADATA')
        if metadata.stat().st_size>MAX_NOTICE:raise DistributionError('Python metadata exceeds cap')
        message=Parser().parsestr(metadata.read_text());name=message.get('Name');version=message.get('Version')
        if not name or not version:raise DistributionError('installed Python package identity missing')
        identity=component_id('python-'+service,name,version);headers=['Metadata-Version: 2.1','Name: '+name,'Version: '+version]
        for key in ('License','License-Expression','Classifier','Requires-Dist'):
            for value in message.get_all(key,[]):headers.append(key+': '+value.replace('\n',' '))
        stage.add_bytes('python/'+service+'/'+directory.name+'/METADATA',('\n'.join(headers)+'\n\n').encode(),kind='derived-installed-metadata',source_hash=sha256(metadata))
        declared=message.get_all('License-File',[]);found=[]
        for value in declared:
            relative_path(value)
            for base in (directory/'licenses',directory):
                if (base/value).is_file():found.extend(original_notices(stage,base,identity,declared=[value]));break
        if not found:found=original_notices(stage,directory,identity)
        components.append({'ecosystem':'python','installation':service,'name':name,'version':version,'metadata_sha256':sha256(metadata),'declared_license':message.get('License-Expression') or message.get('License') or None,'notice_files':found})
    if not components:raise DistributionError('installed Python distribution inventory is empty')
    return components

def npm_installed(stage,root,app):
    root=no_symlinks(root);components=[]
    if not root.is_dir():raise DistributionError('actual npm installation root missing')
    for file in sorted(root.rglob('package.json')):
        file=contained(root,file);package=read_object(file);name=package.get('name');version=package.get('version')
        if not isinstance(name,str) or not isinstance(version,str):continue
        identity=component_id('npm-'+app,name,version);license=package.get('license')
        notices=original_notices(stage,file.parent,identity)
        components.append({'ecosystem':'npm','input_scope':'actual installed UI build dependency; may include dev/unlinked packages','app':app,'name':name,'version':version,'declared_license':license if isinstance(license,str) else None,'package_metadata_sha256':sha256(file),'notice_files':notices})
    if not components:raise DistributionError('actual npm dependency inventory is empty')
    return components

def metadata_command(argv,cwd,env):
    result=subprocess.run([str(x) for x in argv],cwd=cwd,env=env,capture_output=True,timeout=90)
    if result.returncode or len(result.stdout)>32*1024**2:raise DistributionError('offline dependency metadata command failed; raw output withheld')
    return result.stdout

def rust_packages(stage,build,cargo,cargo_home,env):
    cargo_home=no_symlinks(cargo_home);raw=metadata_command([cargo,'metadata','--locked','--offline','--format-version','1','--filter-platform','aarch64-apple-darwin'],build/'lily',env);metadata=json.loads(raw);components=[]
    if not metadata.get('resolve'):raise DistributionError('Cargo resolved dependency graph is absent')
    for package in metadata['packages']:
        manifest=no_symlinks(Path(package['manifest_path']));permitted=[build/'lily',cargo_home/'registry/src',cargo_home/'git/checkouts']
        if not any(manifest==base or base in manifest.parents for base in permitted):raise DistributionError('Cargo metadata refers outside reviewed source/cache roots')
        root=manifest.parent;identity=component_id('cargo',package['name'],package['version']);declared=[package['license_file']] if package.get('license_file') else []
        if declared and Path(declared[0]).is_absolute():declared=[str(contained(root,Path(declared[0])).relative_to(root))]
        files=original_notices(stage,root,identity,declared=declared)
        components.append({'ecosystem':'cargo','input_scope':'resolved target build graph; may include build/optional dependencies','name':package['name'],'version':package['version'],'declared_license':package.get('license'),'manifest_sha256':sha256(manifest),'notice_files':files})
    return components

def go_modules(stage,build,go,binary,env):
    raw=metadata_command([go,'version','-m',binary],build/'llama_swap',env).decode();components=[]
    for line in raw.splitlines():
        fields=line.strip().split()
        if len(fields)>=3 and fields[0] in ('mod','dep'):
            name,version=fields[1:3];identity=component_id('go',name,version);directory=build/'go-mod-cache'/( ''.join('!'+c.lower() if c.isupper() else c for c in name)+'@'+version)
            notices=original_notices(stage,directory,identity) if directory.is_dir() else []
            components.append({'ecosystem':'go','input_scope':'actual binary build-info module','name':name,'version':version,'checksum':fields[3] if len(fields)>3 else None,'notice_files':notices})
    if not components:raise DistributionError('actual Go binary module build-info absent')
    return components

def collect(data,build,output,*,installation_id,source_commit,cargo,go,cargo_home,tools):
    trusted=validate(data,require_checkpoint=False);data=trusted['data_root'];build=contained(data/'build',build)
    first_party=verify_first_party(trusted['source_root'])
    if trusted['config']['installation_id']!=installation_id or trusted['trust']['source_commit']!=source_commit:raise DistributionError('runtime inventory candidate identity differs')
    binding=verified_file(data,trusted['trust'].get('dependency_inputs',{}));bound=read_object(binding)
    if bound.get('installation_id')!=installation_id or bound.get('source_commit')!=source_commit or bound.get('build_root')!=str(build.relative_to(data)):raise DistributionError('dependency build binding differs from actual candidate')
    for item in bound['inputs']:verified_file(data,item)
    output=no_symlinks(output)
    if output==data or data in output.parents:raise DistributionError('inventory output must be outside installation/build source to prevent recursive/private input collection')
    if output.exists():raise DistributionError('runtime inventory output must be new')
    private_directory(output);stage=Stager(output/'inputs');records=[]
    inputs={'cargo/Cargo.lock':build/'lily/Cargo.lock','cargo/Cargo.toml':build/'lily/Cargo.toml','go/go.mod':build/'llama_swap/go.mod','go/go.sum':build/'llama_swap/go.sum','npm/litellm/package.json':build/'litellm/ui/litellm-dashboard/package.json','npm/litellm/package-lock.json':build/'litellm/ui/litellm-dashboard/package-lock.json','npm/llama-swap/package.json':build/'llama_swap/ui/package.json','npm/llama-swap/package-lock.json':build/'llama_swap/ui/package-lock.json'}
    for relative,file in inputs.items():stage.add_file(build,file,relative,kind='actual-build-lock-input')
    for name,file in trusted['binaries'].items():stage.add_file(data,file,'native/'+name,kind='actual-trusted-native-binary',limit=256*1024**2)
    for tool,item in trusted['trust']['build_tools'].items():
        if tool=='node':stage.add_file(data,verified_file(data,item),'native/node',kind='actual-runtime-Prisma-tool',limit=256*1024**2)
    direct_root=data/'runtime/notices'
    if direct_root.is_dir():original_notices(stage,direct_root,'native-direct')
    node_root=data/'runtime/toolchains/node'
    if node_root.is_dir():original_notices(stage,node_root,'runtime-node')
    for service in ('compat','litellm'):records.extend(python_installed(stage,data/('runtime/'+service+'/lib/python3.12/site-packages'),service))
    records.extend(npm_installed(stage,build/'litellm/ui/litellm-dashboard/node_modules','litellm'));records.extend(npm_installed(stage,build/'llama_swap/ui/node_modules','llama-swap'))
    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(Path.home()),'LANG':'en_US.UTF-8','RUSTUP_TOOLCHAIN':'1.97.0','CARGO_HOME':str(no_symlinks(cargo_home)),'CARGO_TARGET_DIR':str(build/'cargo-target'),'GOMODCACHE':str(build/'go-mod-cache'),'GOCACHE':str(build/'go-cache'),'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','TMPDIR':str(private_directory(output/'tmp'))}
    records.extend(rust_packages(stage,build,cargo,cargo_home,env));records.extend(go_modules(stage,build,go,trusted['binaries']['llama_swap'],env))
    from audit import tool_versions,syft
    tool_versions(tools);sbom=syft(tools,stage.root,output,source_commit);write_json_new(output/'sbom.cdx.json',sbom)
    missing=[{'ecosystem':x['ecosystem'],'name':x['name'],'version':x['version']} for x in records if not x['notice_files']]
    result={'status':'COLLECTED_ACTUAL_NATIVE_INPUTS_REVIEW_REQUIRED','installation_id':installation_id,'source_commit':source_commit,'components':records,'missing_original_notice_files':missing,'staged_files':stage.files,'stage_bytes':stage.total,'syft_components':len(sbom.get('components',[])),'scope':'installed Python + actual Go binary modules + resolved Rust target graph + installed UI build dependencies; source/build graphs not identical to linked runtime','transitive_notice_file_coverage':not missing,'runtime_inventory_complete':False,'unresolved_scope':['system/stdlib licenses and actual linkage versus conservative build graphs need final review'],'final_license_acceptance':False,'model_or_DB_or_credential_files_staged':False,'runtime_or_model_executed':False,**first_party}
    write_json_new(output/'inventory.json',result);print(json.dumps({k:result[k] for k in ('status','stage_bytes','syft_components','transitive_notice_file_coverage','runtime_inventory_complete','final_license_acceptance')}));return result
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--data-root',type=Path);p.add_argument('--build-root',type=Path);p.add_argument('--output',type=Path);p.add_argument('--installation-id');p.add_argument('--source-commit');p.add_argument('--cargo',type=Path,default=Path.home()/'.cargo/bin/cargo');p.add_argument('--go',type=Path);p.add_argument('--cargo-home',type=Path,default=Path.home()/'.cargo');p.add_argument('--tools',type=Path);a=p.parse_args();os.umask(0o077)
    if not a.execute:print(json.dumps({'status':'PLAN_ONLY','actual_native_inventory':'NOT_RUN','source141not_runtime_complete':True,'model_DB_credentials_staged':False}));raise SystemExit(0)
    try:collect(a.data_root,a.build_root,a.output,installation_id=a.installation_id,source_commit=a.source_commit,cargo=a.cargo,go=a.go,cargo_home=a.cargo_home,tools=a.tools)
    except (DistributionError,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired):print(json.dumps({'status':'FAIL','error':'native inventory identity/input/metadata boundary failure; private contents withheld'}));raise SystemExit(2)
