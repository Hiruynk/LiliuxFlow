# SPDX-License-Identifier: Apache-2.0
"""Release provenance and local installation validation shared by serving entry points."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import stat
from common import DistributionError, no_symlinks, read_object, relative_path
from profile_registry import REGISTRY_PATH, SHARED, legacy_registry, parse_registry

ALIAS = 'qwen3.8-flash-next-lily-q4-64k'
PROFILE = {'public_alias': ALIAS, 'runtime_model_id': 'Qwen3.8-Flash-Next', 'context_tokens': 65536,
           'thinking': 'checkpoint default high', 'qsa_route': 'split', 'mtp_drafts': 0,
           'pin_weights': 'off', 'ngram_lock': 'off', 'long128k_enabled': False}
SHA = re.compile(r'^[0-9a-f]{64}$')

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(8 * 1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()

def private_file(path):
    path = no_symlinks(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise DistributionError('runtime private file owner or permissions differ')
    return path

def within(base, value):
    relative_path(value)
    file = no_symlinks(base / value)
    file.relative_to(base)
    return file

def verified_file(base, item):
    if not isinstance(item, dict) or not SHA.fullmatch(str(item.get('sha256', ''))):
        raise DistributionError('release trust file record is invalid')
    file = within(base, item['path'])
    if not file.is_file() or sha256(file) != item['sha256']:
        raise DistributionError('release trust file hash differs')
    return file

def installation(data):
    data = no_symlinks(data)
    config = read_object(private_file(data / 'install.json'))
    if type(config.get('schema_version')) is not int or config['schema_version'] not in (1, 2) or config.get('data_root') != str(data) or config.get('owner_uid') != os.getuid() or config.get('bind_host') != '127.0.0.1':
        raise DistributionError('portable installation identity or loopback bind differs')
    ports = config.get('ports', {})
    if set(ports) != {'litellm', 'compat', 'guard', 'manager', 'postgresql'} or any(type(x) is not int or not 1024 <= x <= 65535 for x in ports.values()) or len(set(ports.values())) != 5:
        raise DistributionError('portable installation ports are invalid')
    model_configured(config)  # A schema upgrade never infers or rewrites a model path.
    return config


def model_configured(config):
    """Read legacy installs without mutation; schema 2 represents absence explicitly."""
    if config.get('schema_version') == 1:
        if not isinstance(config.get('model_dir'), str) or not config['model_dir']:
            raise DistributionError('legacy installation checkpoint path is missing')
        return True
    if config.get('schema_version') != 2 or config.get('model_id') not in (None, 'qwen3.8-flash-next-lily-q4'):
        raise DistributionError('optional model installation schema differs')
    if config.get('model_state') == 'not_configured':
        if any(config.get(k) is not None for k in ('model_dir', 'model_id', 'checkpoint')):
            raise DistributionError('no-model installation contains checkpoint configuration')
        return False
    if (config.get('model_state') != 'attached' or config.get('model_id') != 'qwen3.8-flash-next-lily-q4'
        or not isinstance(config.get('model_dir'), str) or not config['model_dir']
        or not isinstance(config.get('checkpoint'), dict)):
        raise DistributionError('attached checkpoint configuration is incomplete')
    return True

def validate(data, *, require_checkpoint=True, trace=None):
    def step(stage,relative=None):
        if trace is not None:trace({'stage':stage,'relative_file':relative})
    data = no_symlinks(data)
    step('install_open','install.json')
    config = installation(data)
    configured = model_configured(config)
    if require_checkpoint not in (True, False, 'metadata', 'available', 'controlplane'):
        raise DistributionError('checkpoint validation mode is invalid')
    if require_checkpoint in ('available', 'controlplane'):
        require_checkpoint = ('metadata' if require_checkpoint == 'controlplane' else True) if configured else False
    if require_checkpoint and not configured:
        raise DistributionError('model is not configured; attach a verified local checkpoint before inference')
    source = no_symlinks(Path(config['source_root']))
    step('trust_open','runtime/release-trust.json')
    trust_path = private_file(data / 'runtime/release-trust.json')
    trust = read_object(trust_path)
    if trust.get('schema_version') != 1 or trust.get('installation_id') != config.get('installation_id') or trust.get('source_commit') is None:
        raise DistributionError('release trust identity is invalid')
    step('source_manifest_open','manifests/distribution/native-sources.json')
    pinned = read_object(source / 'manifests/distribution/native-sources.json')
    if trust.get('lily_source_commit') != pinned['lily']['commit'] or trust.get('llama_swap_source_commit') != pinned['llama_swap']['commit']:
        raise DistributionError('release trust upstream source differs')
    if trust.get('lily_patch_sha256') != [x['sha256'] for x in pinned['lily']['patches']]:
        raise DistributionError('release trust canonical Lily patch series differs')
    step('profile_open',trust['profile']['path'])
    profile = read_object(verified_file(source, trust['profile']))
    if any(profile.get(key) != value or (type(value) in (bool, int) and type(profile.get(key)) is not type(value)) for key, value in PROFILE.items()):
        raise DistributionError('release runtime profile differs from safe64k')
    # Existing single-profile receipts stay readable, and authorize only their
    # established 64K runtime. A new signed source record is required to select
    # either long profile; finding a JSON file on disk never grants admission.
    registry_record = trust.get('profile_registry')
    if registry_record is None:
        registry = legacy_registry(profile)
    else:
        if not isinstance(registry_record, dict) or registry_record.get('path') != REGISTRY_PATH:
            raise DistributionError('release trust context registry path differs')
        step('profile_registry_open', REGISTRY_PATH)
        registry = parse_registry(read_object(verified_file(source, registry_record)))
    for item in trust.get('source_files', []):
        step('source_file_open',item['path'])
        verified_file(source, item)
    required_source = {'services/compat/src/compat_api/app.py', 'services/compat/src/compat_api/profile.py', 'services/compat/src/compat_api/llama_guard.py', 'services/compat/src/compat_api/portable.py', 'services/compat/src/compat_api/__init__.py', 'scripts/distribution/trust.py', 'scripts/distribution/agent.py', 'scripts/distribution/model_runner.py', 'scripts/distribution/ownership.py'}
    if registry_record is not None:
        required_source.update({'scripts/distribution/profile_registry.py', 'scripts/distribution/runtime_proof.py',
                                'scripts/distribution/litellm_profile_policy.py', 'scripts/distribution/patch_litellm_profiles.py'})
    optional_support = trust.get('optional_model_support')
    if config.get('schema_version') == 2 or optional_support is not None:
        if not isinstance(optional_support, dict) or set(optional_support) != {'schema_version'} or type(optional_support['schema_version']) is not int or optional_support['schema_version'] != 1:
            raise DistributionError('release trust omits optional model state support')
        required_source.update({'scripts/distribution/model_catalog.py', 'scripts/distribution/model_installer.py',
                                'services/model-installer/pyproject.toml', 'services/model-installer/uv.lock',
                                'manifests/distribution/native-sources.json', 'manifests/distribution/checkpoint-files.json'})
    if not required_source.issubset({x['path'] for x in trust.get('source_files', [])}):
        raise DistributionError('release trust omits a serving boundary source')
    if optional_support is not None:
        from model_catalog import load_catalog
        load_catalog(source)
    policy = trust.get('litellm_profile_policy')
    if registry_record is not None or policy is not None:
        runtime_prefix = 'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/'
        if not isinstance(policy, dict) or set(policy) != {'module', 'proxy', 'catalog_filters'}:
            raise DistributionError('release trust omits the context catalog policy')
        if type(policy.get('catalog_filters')) is not int or policy['catalog_filters'] != 3:
            raise DistributionError('release context catalog coverage differs')
        module_record, proxy_record = policy.get('module'), policy.get('proxy')
        if (not isinstance(module_record, dict) or module_record.get('path') != runtime_prefix + '_liliuxflow_context_profiles.py'
            or not isinstance(proxy_record, dict) or proxy_record.get('path') != runtime_prefix + 'proxy_server.py'
            or proxy_record.get('before_sha256') not in {
                '8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7',
                '0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8'}):
            raise DistributionError('release context policy target or pinned proxy baseline differs')
        step('context_policy_module_open', module_record['path'])
        module = verified_file(data, module_record)
        if sha256(module) != sha256(source / 'scripts/distribution/litellm_profile_policy.py'):
            raise DistributionError('installed context policy differs from its trusted first-party source')
        step('context_policy_proxy_open', proxy_record['path'])
        proxy = verified_file(data, proxy_record)
        if proxy.read_bytes().count(b'# LILIUXFLOW_CONTEXT_PROFILE_CATALOG_V1') != 3:
            raise DistributionError('installed context catalog filter coverage differs')
    welcome = trust.get('welcome')
    if welcome is not None:
        runtime_prefix = 'runtime/litellm/lib/python3.12/site-packages/litellm/proxy/'
        if (not isinstance(welcome, dict) or set(welcome) != {'helper', 'assets', 'proxy_before_sha256', 'proxy_after_sha256'}
            or welcome.get('proxy_before_sha256') != '8e3a49e253c6ae0a8fc3bb5ceb0c6d69395a9fe8b87575e3ad051d8bf05dbbe7'
            or welcome.get('proxy_after_sha256') != '0da5e7bedc0e79095f3a79bc958c65e6f7523b2a206693da03222b2b5e32c6d8'
            or not isinstance(policy, dict) or policy['proxy']['before_sha256'] != welcome['proxy_after_sha256']):
            raise DistributionError('Welcome source route provenance differs')
        from welcome_native import ASSETS
        from welcome_profiles import render_html
        serving_sources = {item['path']: item for item in trust.get('source_files', [])}
        mandatory = {'scripts/distribution/welcome_native.py', 'scripts/distribution/welcome_profiles.py',
                     'patches/litellm-welcome/runtime.py', 'manifests/distribution/welcome-assets.json'}
        mandatory.update('assets/welcome/' + name for name in ASSETS)
        if not mandatory.issubset(serving_sources):
            raise DistributionError('release trust omits Welcome serving source')
        source_manifest = read_object(verified_file(source, serving_sources['manifests/distribution/welcome-assets.json']))
        if {row['path'] for row in source_manifest['assets']} != {'assets/welcome/' + name for name in ASSETS}:
            raise DistributionError('Welcome source asset inventory differs')
        helper = welcome.get('helper')
        if not isinstance(helper, dict) or helper.get('path') != runtime_prefix + '_liliuxflow_welcome_runtime.py':
            raise DistributionError('Welcome runtime helper target differs')
        if sha256(verified_file(data, helper)) != sha256(source / 'patches/litellm-welcome/runtime.py'):
            raise DistributionError('Welcome helper differs from its trusted source')
        rows = welcome.get('assets')
        if (not isinstance(rows, list) or len(rows) != len(ASSETS)
            or {row.get('path') for row in rows} != {runtime_prefix + '_liliuxflow_welcome/' + name for name in ASSETS}):
            raise DistributionError('Welcome runtime asset paths differ')
        for row in rows:
            name = row['path'].removeprefix(runtime_prefix + '_liliuxflow_welcome/')
            source_item = next(item for item in source_manifest['assets'] if item['path'] == 'assets/welcome/' + name)
            source_file = verified_file(source, source_item)
            expected = source_file.read_bytes()
            if name == 'index.html': expected = render_html(expected.decode(), registry)[0].encode()
            import hashlib as _welcome_hash
            if sha256(verified_file(data, row)) != _welcome_hash.sha256(expected).hexdigest():
                raise DistributionError('Welcome generated asset differs from its trusted source profile')
    binaries={}
    for name,item in trust['binaries'].items():
        step('binary_open',item['path'])
        binaries[name]=verified_file(data,item)
    if set(binaries) != {'lily', 'llama_swap', 'compat_python', 'litellm_python'} or any(not os.access(p, os.X_OK) for p in binaries.values()):
        raise DistributionError('release executable set is incomplete')
    build_tools=trust.get('build_tools',{})
    if set(build_tools)!={'node','npm','npm_cli'}:
        raise DistributionError('installation-owned Node tool trust is incomplete')
    for item in build_tools.values():
        step('tool_open',item['path'])
        verified_file(data,item)
    step('credentials_open','secrets/bootstrap.json')
    secrets = read_object(private_file(data / 'secrets/bootstrap.json'))
    if any(not isinstance(secrets.get(k), str) or len(secrets[k]) < 24 for k in ('LITELLM_MASTER_KEY','LITELLM_SALT_KEY','DB_PASSWORD','PG_OWNER_PASSWORD','UI_PASSWORD','MANAGER_BACKEND_TOKEN','GUARD_CONTROL_TOKEN')):
        raise DistributionError('portable bootstrap credentials are incomplete')
    checkpoint = None
    if require_checkpoint:
        metadata_only = require_checkpoint == 'metadata'
        step('checkpoint_receipt_open','runtime/checkpoint-metadata.json' if metadata_only else 'runtime/checkpoint-verified.json')
        checkpoint = read_object(private_file(data / ('runtime/checkpoint-metadata.json' if metadata_only else 'runtime/checkpoint-verified.json')))
        expected = read_object(source / 'manifests/distribution/checkpoint-files.json')
        if registry_record is not None and expected.get('revision') != SHARED['model_revision']:
            raise DistributionError('context registry checkpoint revision differs from the trusted manifest')
        model = no_symlinks(Path(config['model_dir']))
        if checkpoint.get('model_dir') != str(model) or checkpoint.get('revision') != expected['revision'] or checkpoint.get('manifest_sha256') != sha256(source / 'manifests/distribution/checkpoint-files.json') or (not metadata_only and checkpoint.get('payload_verified') is not True) or (metadata_only and checkpoint.get('validation') != 'metadata_only'):
            raise DistributionError('checkpoint checksum verification receipt is missing or stale')
        records = checkpoint.get('files', [])
        if len(records) != len(expected['files']):
            raise DistributionError('checkpoint verified file inventory differs')
        for actual, item in zip(records, expected['files']):
            file = within(model, item['path'])
            info = file.stat()
            if actual.get('path') != item['path'] or (actual.get('sha256') != item['sha256'] and not (metadata_only and Path(item['path']).suffix == '.safetensors' and actual.get('sha256') is None)) or actual.get('size_bytes') != item['size_bytes'] or info.st_size != item['size_bytes'] or actual.get('mtime_ns') != info.st_mtime_ns or actual.get('inode') != info.st_ino or actual.get('device') != info.st_dev or actual.get('ctime_ns') != info.st_ctime_ns:
                raise DistributionError('checkpoint file fingerprint changed; rerun full verification')
        # Always hash active runtime metadata; payload digest is cached only while inode/size/mtime remain identical.
        for item in expected['files']:
            if Path(item['path']).suffix != '.safetensors' and item['path'] in {'config.json','model.safetensors.index.json','tokenizer.json','tokenizer_config.json','generation_config.json','chat_template.jinja'}:
                step('model_metadata_open',item['path'])
                if sha256(within(model, item['path'])) != item['sha256']:
                    raise DistributionError('checkpoint runtime metadata hash differs')
    step('trust_complete')
    return {'config': config, 'profile': profile, 'registry': registry, 'profiles': registry.enabled_profiles if configured else (),
            'trust': trust, 'binaries': binaries, 'secrets': secrets, 'checkpoint': checkpoint,
            'model_configured': configured,
            'source_root': source, 'data_root': data}

def checkpoint_records(model, manifest):
    """Verify the canonical inventory once, with stable file fingerprints."""
    model = no_symlinks(model)
    records = []
    for item in manifest['files']:
        file = within(model, item['path'])
        before = file.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != item['size_bytes'] or sha256(file) != item['sha256']:
            raise DistributionError('external checkpoint content differs from the pinned revision')
        after = file.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise DistributionError('external checkpoint changed during verification')
        records.append({'path':item['path'],'sha256':item['sha256'],'size_bytes':after.st_size,'mtime_ns':after.st_mtime_ns,'inode':after.st_ino,'device':after.st_dev,'ctime_ns':after.st_ctime_ns})
    return records


def verify_checkpoint(data):
    data = no_symlinks(data)
    config = installation(data)
    if not model_configured(config):
        raise DistributionError('model is not configured; use models attach with a local checkpoint')
    source = no_symlinks(Path(config['source_root']))
    manifest_path = source / 'manifests/distribution/checkpoint-files.json'
    manifest = read_object(manifest_path)
    model = no_symlinks(Path(config['model_dir']))
    records = checkpoint_records(model, manifest)
    result = {'schema_version':1,'model_dir':str(model),'revision':manifest['revision'],'manifest_sha256':sha256(manifest_path),'payload_verified':True,'files':records}
    atomic_private_json(data / 'runtime/checkpoint-verified.json', result)
    return {'state':'checkpoint_verified','revision':manifest['revision'],'file_count':len(records),'payload_verified':True,'weights_copied':False}

def verify_metadata(data):
    config=installation(data)
    if not model_configured(config):
        raise DistributionError('model is not configured; no checkpoint receipt can be created')
    source=no_symlinks(Path(config['source_root']));model=no_symlinks(Path(config['model_dir']))
    manifest_path=source/'manifests/distribution/checkpoint-files.json';manifest=read_object(manifest_path);records=[]
    import struct
    import json
    for item in manifest['files']:
        file=within(model,item['path']);info=file.stat()
        if info.st_size!=item['size_bytes']:
            raise DistributionError('checkpoint file size differs from pinned metadata')
        digest=None
        if file.suffix=='.safetensors':
            with file.open('rb') as handle:
                prefix=handle.read(8)
                if len(prefix)!=8:raise DistributionError('checkpoint tensor header is missing')
                size=struct.unpack('<Q',prefix)[0]
                if not 0<size<16*1024**2 or size+8>info.st_size:raise DistributionError('checkpoint tensor header is unsafe')
                header=json.loads(handle.read(size))
                if not isinstance(header,dict) or not any(isinstance(v,dict) and 'data_offsets' in v for v in header.values()):
                    raise DistributionError('checkpoint tensor metadata format differs')
        else:
            digest=sha256(file)
            if digest!=item['sha256']:raise DistributionError('checkpoint runtime metadata hash differs')
        records.append({'path':item['path'],'sha256':digest,'size_bytes':info.st_size,'mtime_ns':info.st_mtime_ns,'inode':info.st_ino,'device':info.st_dev,'ctime_ns':info.st_ctime_ns})
    result={'schema_version':1,'validation':'metadata_only','model_dir':str(model),'revision':manifest['revision'],
            'manifest_sha256':sha256(manifest_path),'payload_verified':False,'files':records}
    atomic_private_json(data/'runtime/checkpoint-metadata.json',result)
    return {'state':'metadata_verified','file_count':len(records),'payload_verified':False,'revision_payload_verified':False,'model_load_permitted':False}

def atomic_private_json(path, value):
    import json
    import secrets as rng
    from common import write_new
    path = no_symlinks(path)
    temporary = path.with_name(path.name + '.' + rng.token_hex(8) + '.new')
    write_new(temporary, (json.dumps(value,indent=2)+'\n').encode())
    os.replace(temporary, path)
