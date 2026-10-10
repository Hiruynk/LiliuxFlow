"""Portable serving factories. Existing production app/profile/guard files stay unchanged."""
import os
from pathlib import Path
import sys


def _validated():
    source = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(source / 'scripts/distribution'))
    from trust import validate
    from common import DistributionError
    value = os.environ.get('LILIUXFLOW_DATA_ROOT', '')
    if not value:
        raise DistributionError('portable data root is required')
    mode = 'controlplane' if os.environ.get('LILIUXFLOW_CONTROLPLANE_ONLY') == '1' else 'available'
    shared = os.environ.get('LILIUXFLOW_MTP2_SHARED_GATEWAY')
    if shared is not None and shared != '1':
        raise DistributionError('shared MTP2 feature flag is invalid')
    validation = os.environ.get('LILIUXFLOW_MTP2_SHARED_VALIDATION')
    if validation is not None:
        if validation != '1':
            raise DistributionError('shared validation factory mode is invalid')
        trusted = validate(Path(value), require_checkpoint=mode)
        from mtp2_shared_upgrade import resolve_validation_selection, validation_view
        binding = resolve_validation_selection(trusted, explicit_validation=True)
        if binding.source_root != source:
            raise DistributionError('shared validation factory source differs from generated bundle')
        return validation_view(trusted, binding)
    selection = {'shared_context': True} if shared == '1' else {}
    trusted = validate(Path(value), require_checkpoint=mode, **selection)
    if trusted['source_root'] != source:
        raise DistributionError('portable factory source differs from release trust')
    return trusted


def create_compat_app():
    trusted = _validated()
    from .app import CompatModelProfile, Settings, create_app
    checkpoint = trusted['checkpoint']
    profile = trusted['profile']
    from common import read_object
    from agent import validation_routes
    validation_ids = _validation_profile_ids()
    validation, _keys = validation_routes(trusted, validation_ids)
    manifest = read_object(trusted['source_root'] / 'manifests/distribution/checkpoint-files.json')
    settings = Settings(litellm_base_url='http://127.0.0.1:' + str(trusted['config']['ports']['litellm']),
                        public_alias=profile['public_alias'], runtime_model_id=profile['runtime_model_id'],
                        context_limit_tokens=profile['context_tokens'], checkpoint_revision=manifest['revision'] if checkpoint is not None else None,
                        checkpoint_manifest_sha256=manifest['checkpoint_manifest_sha256'] if checkpoint is not None else None, checkpoint_file_count=len(checkpoint['files']) if checkpoint is not None else 0,
                        profile_id=profile.get('profile_id'),
                        model_configured=trusted.get('model_configured') is not False,
                        profiles=tuple(CompatModelProfile(p.public_alias, p.context_tokens, p.total_deadline_seconds, p.default_output_tokens,
                                       profile_id=p.profile_id,engine_id=p.as_dict().get('engine_id'),mtp_drafts=p.as_dict().get('mtp_drafts',0),kv_cache=p.as_dict().get('kv_cache'),max_batch=p.as_dict().get('max_batch'))
                                       for p in (*trusted['registry'].enabled_profiles, *validation)) if trusted.get('model_configured') is not False else ())
    app = create_app(settings=settings)
    flag = os.environ.get('LILIUXFLOW_MTP2_SHARED_GATEWAY')
    if flag is not None:
        if flag != '1':
            raise ValueError('shared MTP2 feature flag is invalid')
        from mtp2_shared_gateway import require_shared_installation, RawMtp2Ingress
        require_shared_installation(trusted)
        return RawMtp2Ingress(app, trusted['registry'])
    return app


def create_guard_app():
    trusted = _validated()
    from .llama_guard import create_app
    from runtime_proof import portable_guard_kwargs
    from agent import validation_routes
    validation_ids = _validation_profile_ids()
    validation_routes(trusted, validation_ids)
    flag = os.environ.get('LILIUXFLOW_MTP2_SHARED_GATEWAY')
    shared = None
    if flag is not None:
        if flag != '1':
            raise ValueError('shared MTP2 feature flag is invalid')
        from mtp2_shared_gateway import shared_guard_factory
        shared = shared_guard_factory(trusted)
    app = create_app(manager_url='http://127.0.0.1:' + str(trusted['config']['ports']['manager']),
                      control_token=trusted['secrets']['GUARD_CONTROL_TOKEN'], backend_token=trusted['secrets']['MANAGER_BACKEND_TOKEN'],
                      shared_guard_factory=shared,
                      **portable_guard_kwargs(trusted, validation_profile_ids=validation_ids))
    if shared is not None:
        from mtp2_shared_ui import SharedCatalogAccess
        return SharedCatalogAccess(app,lp_url='http://127.0.0.1:'+str(trusted['config']['ports']['litellm']),backend_token=trusted['secrets']['MANAGER_BACKEND_TOKEN'],registry=trusted['registry'])
    return app


def _validation_profile_ids():
    """Only an explicit operator environment enables private candidate factories."""
    import json
    value = os.environ.get('LILIUXFLOW_CONTEXT_VALIDATION_PROFILES')
    if value is None:
        return ()
    try:
        profiles = json.loads(value)
    except ValueError as error:
        raise ValueError('validation profile selection is invalid') from error
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 2 or any(profile not in ('ctx128k', 'ctx262k') for profile in profiles) or len(set(profiles)) != len(profiles):
        raise ValueError('validation profile selection is outside the finite allowlist')
    return tuple(profiles)
