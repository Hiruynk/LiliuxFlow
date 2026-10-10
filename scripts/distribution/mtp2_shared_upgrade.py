# SPDX-License-Identifier: Apache-2.0
"""Normal generated shared bundles and bounded offline validation selection.

Candidate staging, validation selection and production promotion are separate.
The primary install/trust/source/binary records are never rewritten here. No
private approval, inference header, receipt claim or environment supplies the
code-side recipe. Publication uses one owned selection pointer with rollback.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import copy, fcntl, hashlib, json, os, re, secrets, shutil, stat, time
from pathlib import Path
from common import DistributionError, no_symlinks, private_directory, read_object, relative_path, write_json_new, write_new
from trust import private_file, sha256, verified_file, atomic_private_json
from build_native import resolve_source_commit
from mtp2_shared_recipe import EXPECTED, source_preflight, SERVING_INPUTS, serving_inputs
from mtp2_shared_runtime import candidate_expectation
from mtp2_shared_runtime import (BINARY_PATH, PROFILE_IDS, SharedInstallation,
    from_generated_trust, verify_source_inventory, shared_binary_path, global_gpu_lease)
from profile_registry import SHARED

POINTER = 'runtime/mtp2-shared-validation.json'
BUNDLES = 'runtime/mtp2-shared-bundles'
MAX_TTL = 1200
SOAK_MAX_TTL = 2400
VALIDATION_PURPOSE = 'operator_offline_validation'
SOAK_PURPOSE = 'operator_offline_soak30'
MAX_INT = (1 << 53) - 1
UPGRADE_SOURCE = 'scripts/distribution/mtp2_shared_upgrade.py'

def _same(a, b):
    return type(a) is type(b) and (set(a)==set(b) and all(_same(a[k],b[k]) for k in a) if isinstance(a,dict)
        else len(a)==len(b) and all(_same(x,y) for x,y in zip(a,b)) if isinstance(a,(tuple,list)) else a==b)

def _id(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}',value) is None:
        raise DistributionError('shared bundle identity is malformed')
    return value

def _owned(path, directory=False):
    path=no_symlinks(path);info=path.stat()
    if info.st_uid!=os.getuid() or info.st_mode&0o077 or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise DistributionError('shared bundle owner/mode/type differs')
    return path

def _base(trusted):
    data=_owned(trusted['data_root'],True)
    if trusted['config'].get('data_root')!=str(data) or trusted['config'].get('owner_uid')!=os.getuid():
        raise DistributionError('shared base data identity differs')
    install=private_file(data/'install.json');release=private_file(data/'runtime/release-trust.json')
    if not _same(read_object(install),trusted['config']) or not _same(read_object(release),trusted['trust']):
        raise DistributionError('shared base installation changed')
    base_source=no_symlinks(Path(trusted['config']['source_root']))
    for row in trusted['trust']['source_files']:verified_file(base_source,row)
    for row in trusted['trust']['binaries'].values():verified_file(data,row)
    if trusted['registry'].default.profile_id!='ctx64k':
        raise DistributionError('shared base default differs')
    bootstrap=data/'secrets/bootstrap.json'
    return {'installation_id':trusted['config']['installation_id'],'install_sha256':sha256(install),
            'trust_sha256':sha256(release),'bootstrap_sha256':sha256(private_file(bootstrap))}

def _scope(profiles):
    if (type(profiles) is not tuple or not profiles or any(type(p) is not str for p in profiles)
        or profiles!=tuple(pid for pid in PROFILE_IDS if pid in profiles)):
        raise DistributionError('shared validation profile scope is not finite canonical order')
    return profiles

@dataclass(frozen=True,slots=True)
class FrozenPackage:
    source: Path
    commit: str
    records: tuple[tuple[str,str],...]
    performance_candidate: bool = False

def freeze_package(source, old_source_paths=(), *, performance_candidate=False):
    """Explicit final-package factory, called only after the operator freezes it."""
    source=no_symlinks(source);commit=resolve_source_commit(source);source_preflight(source,performance_candidate=performance_candidate)
    paths=serving_inputs(performance_candidate)|{UPGRADE_SOURCE}|set(old_source_paths)
    records=[]
    for path in sorted(paths):
        relative_path(path);file=no_symlinks(source/path)
        if not file.is_file():raise DistributionError('shared final source boundary is incomplete')
        records.append((path,sha256(file)))
    for path,digest in records:verified_file(source,{'path':path,'sha256':digest})
    return FrozenPackage(source,commit,tuple(records),performance_candidate)

@dataclass(frozen=True,slots=True)
class NativeArtifact:
    binary: Path
    source_tree: Path
    engine_record: dict

@dataclass(frozen=True,slots=True)
class StagedBundle:
    data_root: Path
    generation: str
    manifest_sha256: str
    base: dict

@dataclass(frozen=True,slots=True)
class PublishedSelection:
    data_root: Path
    pointer_sha256: str
    previous: bytes | None
    base: dict

@dataclass(frozen=True,slots=True)
class ValidationBinding:
    data_root: Path
    generation: str
    manifest_sha256: str
    pointer_sha256: str
    profiles: tuple[str,...]
    source_root: Path
    installation: SharedInstallation
    lp_config_sha256: str

@dataclass(frozen=True,slots=True)
class ConfigurationBinding:
    data_root: Path
    generation: str
    manifest_sha256: str
    profiles: tuple[str,...]
    source_root: Path
    installation: SharedInstallation

@dataclass(frozen=True,slots=True)
class GeneratedConfiguration:
    data_root: Path
    generation: str
    profiles: tuple[str,...]
    lp_config_sha256: str
    manifest_sha256: str
    base: dict

def _fault(callback,stage):
    if callback is not None:callback(stage)

def _inventory(root):
    result={}
    for file in sorted(root.rglob('*')):
        _owned(file,file.is_dir())
        if file.is_file():result[file.relative_to(root).as_posix()]=sha256(file)
    return result

def stage_bundle(trusted, *, package_factory, native_artifact, fault=None):
    """Populate an unselected versioned source/native bundle. No service mutation."""
    base=_base(trusted);data=trusted['data_root'];package=package_factory()
    if type(package) is not FrozenPackage or type(native_artifact) is not NativeArtifact:
        raise DistributionError('shared bundle needs normal frozen package/native artifact types')
    expected=candidate_expectation(package.performance_candidate)
    source_preflight(package.source,performance_candidate=package.performance_candidate)
    for path,digest in package.records:verified_file(package.source,{'path':path,'sha256':digest})
    expected_paths=serving_inputs(package.performance_candidate)|{UPGRADE_SOURCE}|{r['path'] for r in trusted['trust']['source_files']}
    if {p for p,_ in package.records}!=expected_paths or len(package.records)!=len(expected_paths):
        raise DistributionError('shared package source closure differs')
    if resolve_source_commit(package.source)!=package.commit:
        raise DistributionError('shared package source identity changed')
    engine=from_generated_trust(native_artifact.engine_record,expected)
    verify_source_inventory(package.source,native_artifact.source_tree,expected)
    binary=no_symlinks(native_artifact.binary)
    if not binary.is_file() or sha256(binary)!=engine.binary_sha256 or not os.access(binary,os.X_OK):
        raise DistributionError('shared actual native artifact differs')
    parent=private_directory(data/BUNDLES);generation=secrets.token_hex(16)
    temporary=parent/('.stage-'+generation);temporary.mkdir(mode=0o700);stamp=(temporary.stat().st_dev,temporary.stat().st_ino)
    final=parent/generation
    try:
        source_view=temporary/'runtime/source';private_directory(source_view)
        for path,digest in package.records:
            dest=source_view/path;private_directory(dest.parent)
            with dest.open('xb') as out,(package.source/path).open('rb') as inp:shutil.copyfileobj(inp,out)
            dest.chmod(0o600);verified_file(source_view,{'path':path,'sha256':digest})
        write_json_new(source_view/'SOURCE_COMMIT.json',{'schema_version':1,'commit':package.commit,'runtime_source_view':True})
        _fault(fault,'source_copied')
        native=temporary/BINARY_PATH;private_directory(native.parent)
        with native.open('xb') as out,binary.open('rb') as inp:shutil.copyfileobj(inp,out)
        native.chmod(0o700)
        if sha256(native)!=engine.binary_sha256:raise DistributionError('shared native copy differs')
        _fault(fault,'binary_copied')
        for path,digest in package.records:verified_file(package.source,{'path':path,'sha256':digest})
        if _base(trusted)!=base or resolve_source_commit(package.source)!=package.commit:
            raise DistributionError('shared base/source changed during staging')
        manifest={'schema_version':1,'generation':generation,'purpose':'candidate_staging','base':base,
            'source_commit':package.commit,'source_files':[{'path':p,'sha256':d} for p,d in package.records],
            'engine':copy.deepcopy(native_artifact.engine_record),'files':_inventory(temporary),
            'default':False,'production_enabled':False,'runtime_acceptance':False}
        if package.performance_candidate:
            manifest['source_variant']='cpu-copy-v1'
        write_json_new(temporary/'bundle.json',manifest);_fault(fault,'manifest_staged')
        os.rename(temporary,final)
        return StagedBundle(data,generation,sha256(final/'bundle.json'),base)
    except BaseException:
        if temporary.exists() and (temporary.stat().st_dev,temporary.stat().st_ino)==stamp:
            shutil.rmtree(temporary)
        raise

def _verify_bundle(trusted,generation,manifest_sha256):
    generation=_id(generation);root=_owned(trusted['data_root']/BUNDLES/generation,True)
    file=private_file(root/'bundle.json')
    if sha256(file)!=manifest_sha256:raise DistributionError('shared bundle manifest differs')
    m=read_object(file)
    fields={'schema_version','generation','purpose','base','source_commit','source_files','engine','files','default','production_enabled','runtime_acceptance'}
    performance_candidate='source_variant' in m
    if (set(m)!=(fields|{'source_variant'} if performance_candidate else fields)
        or performance_candidate and m.get('source_variant')!='cpu-copy-v1'
        or type(m['schema_version']) is not int or m['schema_version']!=1
        or m['generation']!=generation or m['purpose']!='candidate_staging'
        or any(m[key] is not False for key in ('default','production_enabled','runtime_acceptance'))
        or m['base']!=_base(trusted)):
        raise DistributionError('shared candidate bundle identity/purpose differs')
    actual=_inventory(root);actual.pop('bundle.json',None)
    if not _same(actual,m['files']):raise DistributionError('shared bundle complete file inventory differs')
    source=root/'runtime/source';source_preflight(source,performance_candidate=performance_candidate)
    if resolve_source_commit(source)!=m['source_commit']:raise DistributionError('shared bundle source commit differs')
    rows=m['source_files']
    if (type(rows) is not list or any(type(row) is not dict or set(row)!={'path','sha256'} for row in rows)
        or len(rows)!=len({row['path'] for row in rows})
        or {row['path'] for row in rows}!=serving_inputs(performance_candidate)|{UPGRADE_SOURCE}|{r['path'] for r in trusted['trust']['source_files']}):
        raise DistributionError('shared bundle serving closure differs')
    for row in rows:verified_file(source,row)
    engine=from_generated_trust(m['engine'],candidate_expectation(performance_candidate))
    binary=shared_binary_path(trusted['data_root'],generation);_owned(binary)
    if sha256(binary)!=engine.binary_sha256 or not os.access(binary,os.X_OK):
        raise DistributionError('shared bundle executable differs')
    return m,source,engine,binary

@contextmanager
def _publication_lock(data):
    path=no_symlinks(data/'runtime/mtp2-shared-publication.lock')
    fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
    try:
        _owned(path)
        opened=os.fstat(fd);current=path.stat()
        if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
            raise DistributionError('shared publication lock inode differs')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise DistributionError('shared bundle publication is busy') from None
        yield
    finally:os.close(fd)

def _offline(data):
    if global_gpu_lease().exists() or any((data/path).exists() for path in (
            'run/model.json','run/mtp2-shared-model.json','run/agent.json','run/services.json')):
        raise DistributionError('shared selection requires released model/control ownership')

def _pointer_bytes(data):
    path=no_symlinks(data/POINTER)
    return private_file(path).read_bytes() if path.exists() else None

def _restore_pointer(data,expected_sha,previous):
    path=no_symlinks(data/POINTER)
    if not path.exists() or sha256(private_file(path))!=expected_sha:
        raise DistributionError('shared rollback refuses changed selection pointer')
    if previous is None:path.unlink()
    else:
        temporary=path.with_name(path.name+'.'+secrets.token_hex(8)+'.restore')
        write_new(temporary,previous);os.replace(temporary,path)

def _validation_lifetime_limit(purpose):
    if type(purpose) is str and purpose == VALIDATION_PURPOSE:
        return MAX_TTL
    if type(purpose) is str and purpose == SOAK_PURPOSE:
        return SOAK_MAX_TTL
    raise DistributionError('shared validation purpose is invalid')

def publish_validation_selection(trusted,bundle,*,profiles,generated_configuration,explicit_validation=False,lifetime_seconds=300,now=None,fault=None,purpose=VALIDATION_PURPOSE):
    """Explicit offline operator selection; native build success is not this gate."""
    if explicit_validation is not True or type(explicit_validation) is not bool:
        raise DistributionError('shared validation selection requires explicit offline purpose')
    limit=_validation_lifetime_limit(purpose)
    profiles=_scope(profiles);now=int(time.time()) if now is None else now
    if type(now) is not int or not 0<=now<=MAX_INT or type(lifetime_seconds) is not int or not 1<=lifetime_seconds<=limit or now+lifetime_seconds>MAX_INT:
        raise DistributionError('shared validation lifetime is invalid')
    if type(bundle) is not StagedBundle or bundle.data_root!=trusted['data_root']:
        raise DistributionError('shared staged bundle belongs to another data root')
    seal=generated_configuration
    if (type(seal) is not GeneratedConfiguration or seal.data_root!=bundle.data_root
        or seal.generation!=bundle.generation or seal.profiles!=profiles
        or seal.manifest_sha256!=bundle.manifest_sha256 or seal.base!=bundle.base
        or sha256(private_file(bundle.data_root/'run/litellm.json'))!=seal.lp_config_sha256):
        raise DistributionError('shared validation config is not the generated bounded plan')
    data=trusted['data_root']
    with _publication_lock(data):
        _offline(data);base=_base(trusted)
        if base!=bundle.base:raise DistributionError('shared base changed before selection')
        _verify_bundle(trusted,bundle.generation,bundle.manifest_sha256)
        previous=_pointer_bytes(data)
        marker={'schema_version':1,'purpose':purpose,'base':base,
            'generation':bundle.generation,'manifest_sha256':bundle.manifest_sha256,
            'profiles':list(profiles),'issued':now,'expires':now+lifetime_seconds,
            'lp_config_sha256':seal.lp_config_sha256,
            'default':False,'production_enabled':False,'runtime_acceptance':False}
        _fault(fault,'before_pointer')
        if _base(trusted)!=base or _pointer_bytes(data)!=previous:raise DistributionError('shared publication preimage changed')
        atomic_private_json(data/POINTER,marker);written=sha256(data/POINTER)
        try:
            _fault(fault,'after_pointer')
            resolve_validation_selection(trusted,explicit_validation=True,now=now)
            _fault(fault,'selection_verified')
        except BaseException:
            _restore_pointer(data,written,previous)
            raise
        return PublishedSelection(data,written,previous,base)

def rollback_validation_selection(trusted,receipt):
    if type(receipt) is not PublishedSelection or receipt.data_root!=trusted['data_root']:
        raise DistributionError('shared rollback receipt origin differs')
    with _publication_lock(receipt.data_root):
        _offline(receipt.data_root)
        if _base(trusted)!=receipt.base:raise DistributionError('shared rollback base changed')
        _restore_pointer(receipt.data_root,receipt.pointer_sha256,receipt.previous)

def resolve_validation_selection(trusted,*,explicit_validation=False,now=None):
    """Normal production callers cannot inspect or consume this selection."""
    if explicit_validation is not True or type(explicit_validation) is not bool:
        raise DistributionError('shared validation resolver needs explicit offline mode')
    now=int(time.time()) if now is None else now
    path=private_file(trusted['data_root']/POINTER);pointer_sha=sha256(path);m=read_object(path)
    limit=_validation_lifetime_limit(m.get('purpose'))
    fields={'schema_version','purpose','base','generation','manifest_sha256','profiles','issued','expires','lp_config_sha256','default','production_enabled','runtime_acceptance'}
    if (set(m)!=fields or type(m['schema_version']) is not int or m['schema_version']!=1
        or m['base']!=_base(trusted)
        or any(m[key] is not False for key in ('default','production_enabled','runtime_acceptance'))
        or type(now) is not int or type(m['issued']) is not int or type(m['expires']) is not int
        or not 0<=m['issued']<=now<m['expires']<=MAX_INT or not 1<=m['expires']-m['issued']<=limit):
        raise DistributionError('shared validation marker purpose/identity/lifetime differs')
    profiles=_scope(tuple(m['profiles'])) if type(m['profiles']) is list else _scope(None)
    if (type(m['lp_config_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',m['lp_config_sha256']) is None
        or sha256(private_file(trusted['data_root']/'run/litellm.json'))!=m['lp_config_sha256']):
        raise DistributionError('shared validation LP config differs from generated plan')
    manifest,source,engine,binary=_verify_bundle(trusted,m['generation'],m['manifest_sha256'])
    if (trusted.get('model_configured') is not True or trusted.get('checkpoint',{}).get('payload_verified') is not True
        or trusted.get('checkpoint',{}).get('revision')!=SHARED['model_revision']):
        raise DistributionError('shared validation requires the existing fully verified model')
    # Only this fully verified offline selection grants a scoped source view.
    # The original base registry and its disabled opt-ins remain unchanged.
    scoped=_scoped_view(trusted,source,profiles)
    for pid in profiles:scoped['registry'].by_id(pid)
    model=no_symlinks(Path(trusted['config']['model_dir']))
    if not model.is_dir():raise DistributionError('shared model path is unavailable')
    installation=SharedInstallation(engine,binary,model,trusted['data_root'],profiles,
        tuple(trusted['config']['ports'].values()),m['generation'])
    return ValidationBinding(trusted['data_root'],m['generation'],m['manifest_sha256'],pointer_sha,profiles,source,installation,m['lp_config_sha256'])

def validation_view(trusted,binding):
    if type(binding) is not ValidationBinding or binding.data_root!=trusted['data_root']:
        raise DistributionError('shared validation binding origin differs')
    current=resolve_validation_selection(trusted,explicit_validation=True)
    if current.pointer_sha256!=binding.pointer_sha256:raise DistributionError('shared validation selection changed')
    result=_scoped_view(trusted,binding.source_root,binding.profiles)
    result['_mtp2_validation_binding']=binding
    return result

def require_validation_binding(trusted):
    """Gateway/owner revalidation; no dictionary truth or config-only authority."""
    binding=trusted.get('_mtp2_validation_binding')
    if type(binding) is not ValidationBinding or binding.data_root!=trusted['data_root']:
        raise DistributionError('shared runtime validation binding is unavailable')
    current=resolve_validation_selection(trusted,explicit_validation=True)
    if (current.pointer_sha256!=binding.pointer_sha256 or current.source_root!=trusted['source_root']
        or current.lp_config_sha256!=binding.lp_config_sha256):
        raise DistributionError('shared runtime validation binding changed')
    return current

def _scoped_view(trusted,source_root,profiles):
    from profile_registry import parse_registry,REGISTRY_PATH
    result=dict(trusted);result['source_root']=source_root
    result['registry']=parse_registry(read_object(source_root/REGISTRY_PATH),enabled_optin_profiles=list(profiles))
    if result['registry'].default!=trusted['registry'].default:
        raise DistributionError('shared validation cannot change the MTP0 default')
    return result

def prepare_validation_configuration(trusted,bundle,*,profiles):
    """Verified source/native config preparation; it grants no launch authority."""
    profiles=_scope(profiles)
    if type(bundle) is not StagedBundle or bundle.data_root!=trusted['data_root']:
        raise DistributionError('shared config bundle origin differs')
    m,source,engine,binary=_verify_bundle(trusted,bundle.generation,bundle.manifest_sha256)
    model=no_symlinks(Path(trusted['config']['model_dir']))
    installation=SharedInstallation(engine,binary,model,trusted['data_root'],profiles,
        tuple(trusted['config']['ports'].values()),bundle.generation)
    prepared=ConfigurationBinding(trusted['data_root'],bundle.generation,bundle.manifest_sha256,profiles,source,installation)
    result=_scoped_view(trusted,source,profiles);result['_mtp2_config_binding']=prepared
    return result

def configuration_binding(trusted):
    value=trusted.get('_mtp2_config_binding')
    if type(value) is not ConfigurationBinding or value.data_root!=trusted['data_root']:
        raise DistributionError('shared config preparation is not an internal typed binding')
    profiles=_scope(value.profiles)
    m,source,engine,binary=_verify_bundle(trusted,value.generation,value.manifest_sha256)
    model=no_symlinks(Path(trusted['config']['model_dir']))
    installation=SharedInstallation(engine,binary,model,trusted['data_root'],profiles,
        tuple(trusted['config']['ports'].values()),value.generation)
    if (value.source_root!=source or trusted['source_root']!=source or value.installation!=installation
        or tuple(p.profile_id for p in trusted['registry'].enabled_profiles if p.profile_id in PROFILE_IDS)!=profiles
        or trusted['registry'].default.profile_id!='ctx64k'):
        raise DistributionError('shared config typed source/installation/profile binding differs')
    return ConfigurationBinding(trusted['data_root'],value.generation,value.manifest_sha256,profiles,source,installation)

def generated_configuration(trusted,lp_document):
    """Seal a normal generator's in-memory plan, never an unchecked file digest."""
    prepared=configuration_binding(trusted)
    if (type(lp_document) is not dict or lp_document.get('general_settings',{}).get('store_model_in_db') is not False
        or lp_document.get('general_settings',{}).get('supported_db_objects')!=[]
        or 'environment_variables' in lp_document
        or lp_document.get('general_settings',{}).get('allow_client_side_credentials') is not False):
        raise DistributionError('shared validation config must disable DB model/config routing')
    expected={p.public_alias:p for p in trusted['registry'].enabled_profiles}
    rows=lp_document.get('model_list')
    if type(rows) is not list or len(rows)!=len(expected) or any(type(row) is not dict for row in rows) or {row.get('model_name') for row in rows}!=set(expected):
        raise DistributionError('shared validation generated route aliases differ')
    guard='http://127.0.0.1:'+str(trusted['config']['ports']['guard'])+'/v1'
    for row in rows:
        p=expected[row['model_name']];params=row.get('litellm_params',{});info=row.get('model_info',{})
        fixed_info={'context_profile_id':p.profile_id,'max_tokens':p.context_tokens,'max_input_tokens':p.context_tokens,
                    'max_output_tokens':min(p.context_tokens,65536),'default_output_tokens':p.default_output_tokens}
        if (params.get('api_base')!=guard or params.get('model')!='openai/'+p.public_alias
            or params.get('api_key')!='os.environ/MANAGER_BACKEND_TOKEN'
            or any(not _same(info.get(k),v) for k,v in fixed_info.items())):
            raise DistributionError('shared validation route model/context/identity differs')
    router=lp_document.get('router_settings',{})
    if (not _same(router.get('num_retries'),0) or not _same(router.get('max_fallbacks'),0)
        or any(router.get(k)!=[] for k in ('fallbacks','context_window_fallbacks','content_policy_fallbacks'))):
        raise DistributionError('shared validation routing fallback differs')
    digest=hashlib.sha256((json.dumps(lp_document,indent=2)+'\n').encode()).hexdigest()
    if sha256(private_file(trusted['data_root']/'run/litellm.json'))!=digest:
        raise DistributionError('shared validation installed config differs from generator plan')
    return GeneratedConfiguration(prepared.data_root,prepared.generation,prepared.profiles,digest,prepared.manifest_sha256,_base(trusted))
