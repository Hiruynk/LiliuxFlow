# SPDX-License-Identifier: Apache-2.0
"""MTP2-only product ingress/guard entry points; explicit opt-in, no second lease.

The default agent/factories retain their legacy paths. Enabling this candidate
requires generated shared installation trust, runner and exact owner probe.
"""
from __future__ import annotations

import contextvars
import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from types import MethodType

from common import DistributionError
from mtp2_shared_config import PUBLIC_ALIASES, PHYSICAL_MODEL_ID
from mtp2_shared_ingress import CAPABILITY_HEADER, issue_capability, validate_raw_ingress

FEATURE_ENV = "LILIUXFLOW_MTP2_SHARED_GATEWAY"
VALIDATION_ENV = "LILIUXFLOW_MTP2_SHARED_VALIDATION"
_raw_context = contextvars.ContextVar("liliuxflow_mtp2_original_request", default=None)
_callback_config = None
_final_callback_context = contextvars.ContextVar("liliuxflow_mtp2_final_callback", default=None)


def enabled():
    value = os.environ.get(FEATURE_ENV)
    if value is None:
        return False
    if value != "1":
        raise DistributionError("shared MTP2 feature flag is invalid")
    return True


class _PrivateRouteBoundary:
    """Final finite route/callback gate for normal and validation shared modes.

    This helper grants no installation or validation selection. Its expected
    hash comes from the verified normal generator or typed validation binding,
    never an environment value, approval JSON or current config self-hash.
    Only this ProxyConfig instance's model/config readers are restricted;
    virtual-key/user/team/budget/spend database operations are untouched.
    """
    def __init__(self, server, config_path, expected_sha256, registry, guard_port):
        from trust import private_file
        self.server = server
        self._callback = None
        self._logging = self._final_pre_call = None
        self.path = private_file(Path(config_path))
        if type(guard_port) is not int or not 1024 <= guard_port <= 65535:
            raise DistributionError("private candidate guard port is invalid")
        if (type(expected_sha256) is not str or len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256)):
            raise DistributionError("private candidate config expectation is unavailable")
        self.expected_sha256 = expected_sha256
        document = self._source()
        allowed = {row.public_alias for row in registry.profiles}
        rows = document.get("model_list")
        if type(rows) is not list or len(rows) != 6 or len(allowed) != 6:
            raise DistributionError("private candidate requires its six finite routes")
        self.projections = {}
        for row in rows:
            if (type(row) is not dict or type(row.get("model_name")) is not str
                    or row["model_name"] not in allowed or row["model_name"] in self.projections):
                raise DistributionError("private candidate model route is invalid")
            alias = row["model_name"]
            params, info = row.get("litellm_params"), row.get("model_info")
            if (type(params) is not dict or type(info) is not dict or params.get("model") != "openai/" + alias
                    or params.get("api_base") != "http://127.0.0.1:" + str(guard_port) + "/v1"):
                raise DistributionError("private candidate route differs from its guard")
            profile = registry.resolve(alias)
            expected_info = {"id": "liliuxflow-" + profile.profile_id, "mode": "chat",
                             "context_profile_id": profile.profile_id, "max_tokens": profile.context_tokens,
                             "max_input_tokens": profile.context_tokens, "max_output_tokens": min(profile.context_tokens, 65536),
                             "default_output_tokens": profile.default_output_tokens}
            if any(type(info.get(key)) is not type(value) or info[key] != value for key, value in expected_info.items()):
                raise DistributionError("private candidate context differs from its profile")
            self.projections[alias] = copy.deepcopy(row)
        if set(self.projections) != allowed or "environment_variables" in document:
            raise DistributionError("private candidate route/config boundary is invalid")
        settings = document.get("general_settings")
        if type(settings) is not dict or settings.get("store_model_in_db") is not False or settings.get("supported_db_objects") != []:
            raise DistributionError("private candidate DB route isolation is unavailable")

    def _source(self):
        from trust import private_file
        path = private_file(self.path)
        with path.open("rb") as source:
            raw = source.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise DistributionError("private candidate config exceeds its bound")
        if hashlib.sha256(raw).hexdigest() != self.expected_sha256:
            raise DistributionError("private candidate config changed")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise DistributionError("private candidate config is invalid") from None
        if type(value) is not dict:
            raise DistributionError("private candidate config is invalid")
        return value

    def install(self):
        config = self.server.proxy_config
        original_general = config._update_general_settings
        boundary = self
        async def local_file(_self, config_file_path=None):
            if config_file_path is not None and Path(config_file_path) != boundary.path:
                raise DistributionError("private candidate config source changed")
            return boundary._source()
        async def local_merge(_self, *, config, **_kwargs):
            boundary._source()
            return config
        async def no_models(_self, prisma_client):
            boundary._source()
            return []
        async def no_config_override(_self, prisma_client):
            boundary._source()
        async def limited_general(_self, db_general_settings):
            boundary._source()
            if db_general_settings is not None:
                if type(db_general_settings) is not dict:
                    raise DistributionError("private candidate DB settings are invalid")
                filtered = {key: value for key, value in db_general_settings.items()
                            if key not in {"store_model_in_db", "supported_db_objects"}}
                await original_general(filtered)
            boundary.server.general_settings["store_model_in_db"] = False
            boundary.server.general_settings["supported_db_objects"] = []
            boundary.server.store_model_in_db = False
        for name, function in (("_get_config_from_file", local_file), ("_update_config_from_db", local_merge),
                               ("_get_models_from_db", no_models), ("_update_general_settings", limited_general),
                               ("_apply_safe_litellm_settings_overrides_from_db", no_config_override)):
            setattr(config, name, MethodType(function, config))
        return self

    def install_callback_boundary(self):
        """Finalize after the pinned complete hook, only on this proxy instance."""
        from fastapi import HTTPException
        from litellm.proxy.utils import ProxyLogging
        from litellm.integrations.custom_logger import CustomLogger
        logging = self.server.proxy_logging_obj
        original = logging.pre_call_hook
        if (type(logging) is not ProxyLogging
                or getattr(original, "__func__", None) is not ProxyLogging.pre_call_hook):
            raise DistributionError("private candidate pre-call boundary is unavailable")
        self._during_call = ProxyLogging.during_call_hook
        self._shared_pre_call = type(self._callback).async_pre_call_hook
        self._late_baselines = {name: getattr(CustomLogger, name) for name in (
            "async_pre_call_deployment_hook", "async_pre_call_check", "pre_call_check", "async_moderation_hook")}
        boundary = self
        async def final_pre_call(_self, user_api_key_dict, data, call_type, guardrails_only=False):
            capture = _raw_context.get()
            shared = capture is not None or isinstance(data, dict) and data.get("model") in (*PUBLIC_ALIASES, PHYSICAL_MODEL_ID)
            if not shared:
                return await original(user_api_key_dict=user_api_key_dict, data=data,
                                      call_type=call_type, guardrails_only=guardrails_only)
            try:
                boundary.assert_live_routes()
                boundary.assert_no_late_mutators()
            except DistributionError:
                raise HTTPException(503, "private candidate callback boundary changed") from None
            import litellm
            callback_registry = litellm.callbacks
            callback_members = tuple(callback_registry)
            result = await original(user_api_key_dict=user_api_key_dict, data=data,
                                    call_type=call_type, guardrails_only=guardrails_only)
            try:
                if (litellm.callbacks is not callback_registry
                        or len(litellm.callbacks) != len(callback_members)
                        or any(current is not previous for current, previous in zip(litellm.callbacks, callback_members))):
                    raise DistributionError("private candidate callback registry changed during pre-call")
                boundary.assert_live_routes()
                boundary.assert_no_late_mutators()
            except DistributionError:
                raise HTTPException(503, "private candidate callback boundary changed") from None
            if type(result) is not dict or guardrails_only:
                raise HTTPException(403, "MTP2 requires the complete authenticated pre-call path")
            token = _final_callback_context.set(boundary)
            try:
                return await boundary._callback.async_pre_call_hook(
                    user_api_key_dict=user_api_key_dict,
                    cache=logging.call_details["user_api_key_cache"], data=result, call_type=call_type)
            finally:
                _final_callback_context.reset(token)
        logging.pre_call_hook = MethodType(final_pre_call, logging)
        self._logging, self._final_pre_call = logging, logging.pre_call_hook
        self._final_pre_call_function = final_pre_call

    def assert_no_late_mutators(self):
        """Pre-call processing finishes before issuance; later hooks must be inert.

        The pinned proxy runs during-call guardrails concurrently with dispatch,
        and deployment hooks after routing. Checking class names or just the
        final list member cannot protect either path. MRO and instance overrides
        are checked against the actual pinned CustomLogger baseline methods.
        """
        import litellm
        from litellm.integrations.custom_logger import CustomLogger
        from litellm.integrations.custom_guardrail import CustomGuardrail
        from litellm.proxy.utils import ProxyLogging
        if (type(litellm.callbacks) is not list
                or "during_call_hook" in vars(self._logging)
                or type(self._logging).during_call_hook is not self._during_call
                or any(getattr(CustomLogger, name) is not method for name, method in self._late_baselines.items())):
            raise DistributionError("private candidate during-call boundary changed")
        # The deployment loop reads the raw list; reject unresolved entries too.
        for callback in litellm.callbacks:
            if not isinstance(callback, CustomLogger) or isinstance(callback, CustomGuardrail):
                raise DistributionError("private candidate late request processor is unsupported")
            for name, method in self._late_baselines.items():
                if (name in vars(callback)
                        or getattr(type(callback), name, None) is not method):
                    raise DistributionError("private candidate late request processor changed")

    def assert_live_routes(self):
        self._source()
        if self._callback is not None:
            import litellm
            from litellm.proxy.utils import ProxyLogging
            if type(litellm.callbacks) is not list:
                raise DistributionError("private candidate callback registry changed")
            callbacks = ProxyLogging._callback_capabilities().resolved_callbacks
            if (len(callbacks) != len(litellm.callbacks)
                    or any(resolved is not registered for resolved, registered in zip(callbacks, litellm.callbacks))
                    or sum(callback is self._callback for callback in callbacks) != 1
                    or self.server.proxy_logging_obj is not self._logging
                    or self._logging.pre_call_hook is not self._final_pre_call
                    or getattr(self._final_pre_call, "__self__", None) is not self._logging
                    or getattr(self._final_pre_call, "__func__", None) is not self._final_pre_call_function
                    or "async_pre_call_hook" in vars(self._callback)
                    or type(self._callback).async_pre_call_hook is not self._shared_pre_call):
                raise DistributionError("private candidate final callback changed")
        router = self.server.llm_router
        if router is None:
            raise DistributionError("private candidate router is not initialized")
        rows = router.get_model_list()
        if type(rows) is not list or len(rows) != 6:
            raise DistributionError("private candidate router changed")
        seen = set()
        for row in rows:
            if (type(row) is not dict or type(row.get("model_name")) is not str
                    or row["model_name"] not in self.projections or row["model_name"] in seen):
                raise DistributionError("private candidate router changed")
            alias = row["model_name"]
            seen.add(alias)
            expected = self.projections[alias]
            params, info = row.get("litellm_params"), row.get("model_info")
            if (type(params) is not dict or type(info) is not dict
                    or any(params.get(key) != expected["litellm_params"].get(key) for key in ("model", "api_base", "timeout", "stream_timeout"))
                    or any(type(info.get(key)) is not type(value) or info[key] != value for key, value in expected["model_info"].items())):
                raise DistributionError("private candidate route or context changed")
        if seen != set(self.projections):
            raise DistributionError("private candidate router changed")


class _PrivateRouteApp:
    """Do not announce ASGI startup before the candidate router is verified."""
    def __init__(self, app, boundary):
        if type(boundary) is not _PrivateRouteBoundary:
            raise DistributionError("private candidate startup boundary is unavailable")
        self.app, self.boundary = app, boundary

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "lifespan":
            if (scope.get("type") == "http" and scope.get("method") == "POST"
                    and scope.get("path") in {"/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions"}):
                try:
                    self.boundary.assert_live_routes()
                except DistributionError:
                    from starlette.responses import JSONResponse
                    return await JSONResponse({"error": {"type": "service_unavailable", "message": "private candidate route configuration changed"}},status_code=503)(scope,receive,send)
            return await self.app(scope, receive, send)
        async def checked_send(message):
            if message.get("type") == "lifespan.startup.complete":
                self.boundary.assert_live_routes()
            return await send(message)
        return await self.app(scope, receive, checked_send)


@dataclass(frozen=True, slots=True)
class _RawCapture:
    alias: str
    body: bytes = field(repr=False)
    headers: tuple = field(repr=False)
    owner: object = field(repr=False)


class _Pairs(list):
    pass


def _reject_provider_overrides(value, *, depth=0, routing=True):
    """MTP2 caller data cannot choose provider endpoints or credentials."""
    if depth > 64:
        raise DistributionError("MTP2 request nesting exceeds its bound")
    if isinstance(value, dict):
        for key, item in value.items():
            name = key.lower() if isinstance(key, str) else ""
            if routing and (name in {"api_base", "api_key", "base_url", "api_version", "custom_llm_provider",
                                     "litellm_credentials_name", "litellm_credential_name", "credential_name",
                                     "authorization", "api-key", "x-api-key"} or depth > 0 and name == "model"):
                raise DistributionError("caller cannot override MTP2 provider routing or credentials")
            _reject_provider_overrides(item, depth=depth + 1,
                                       routing=routing and (depth > 0 or key in {"options", "extra_body", "chat_template_kwargs", "metadata", "litellm_metadata", "litellm_params", "model_config", "extra_headers", "headers"}))
    elif isinstance(value, list):
        for item in value:
            _reject_provider_overrides(item, depth=depth + 1, routing=routing)


def _mtp2_request(body):
    """Inspect root model occurrences without normalizing duplicate fields."""
    try:
        parsed = json.loads(body, object_pairs_hook=_Pairs)
    except (ValueError, UnicodeError, RecursionError):
        return False
    return type(parsed) is _Pairs and any(key == "model" and value in (*PUBLIC_ALIASES, PHYSICAL_MODEL_ID)
                                        for key, value in parsed)


class RawMtp2Ingress:
    """Outer ASGI wrapper, before official auth dependency/provider parsing."""
    def __init__(self, app, registry, *, capture=False, owner=None):
        self.app, self.registry = app, registry
        self.capture, self.owner = capture, owner

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or scope.get("path") not in {"/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/chat", "/api/chat", "/api/generate"}):
            return await self.app(scope, receive, send)
        messages, size, disconnected = [], 0, False
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                messages.append(message)
                disconnected = True
                break
            if message.get("type") != "http.request":
                messages.append(message)
                break
            size += len(message.get("body", b""))
            if size > 8 * 1024**2:
                from starlette.responses import JSONResponse
                response = JSONResponse({"error": {"type": "request_too_large", "message": "request exceeds the installation body limit"}},
                                        status_code=413, headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        body = b"".join(message.get("body", b"") for message in messages)
        token = None
        if not disconnected and _mtp2_request(body):
            try:
                header_names = tuple(key.decode("ascii") for key, _value in scope.get("headers", ()))
                value, profile = validate_raw_ingress(body, self.registry, headers=header_names)
                _reject_provider_overrides(value)
                if self.capture:
                    token = _raw_context.set(_RawCapture(value["model"], body, header_names, self.owner))
            except (DistributionError, UnicodeError):
                from starlette.responses import JSONResponse
                response = JSONResponse({"error": {"type": "invalid_request_error", "message": "invalid MTP2 request policy or JSON"}},
                                        status_code=400, headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
        index = 0
        async def replay():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()
        try:
            return await self.app(scope, replay, send)
        finally:
            if token is not None:
                _raw_context.reset(token)


def configure_callback(registry, backend_secret, *, private_boundary=None):
    global _callback_config
    if type(backend_secret) is not bytes or not 24 <= len(backend_secret) <= 4096:
        raise DistributionError("shared MTP2 backend credential is unavailable")
    if private_boundary is not None and type(private_boundary) is not _PrivateRouteBoundary:
        raise DistributionError("private candidate route boundary is unavailable")
    if private_boundary is not None:
        private_boundary._callback = shared_callback
        private_boundary.install_callback_boundary()
    owner = object()
    _callback_config = (registry, backend_secret, owner, private_boundary)
    return owner


def create_callback():
    from litellm.integrations.custom_logger import CustomLogger
    from fastapi import HTTPException

    class SharedMtp2Callback(CustomLogger):
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
            if _callback_config is not None and _callback_config[3] is not None:
                try:
                    _callback_config[3].assert_live_routes()
                except DistributionError:
                    raise HTTPException(503, "private candidate route configuration changed") from None
            capture = _raw_context.get()
            model = data.get("model")
            if capture is None:
                if model in PUBLIC_ALIASES or model == PHYSICAL_MODEL_ID:
                    raise HTTPException(403, "MTP2 requires the original raw authenticated ingress")
                return None
            if _callback_config is None or capture.owner is not _callback_config[2]:
                raise HTTPException(503, "MTP2 authenticated ingress is unavailable")
            registry, backend_secret, _owner, _private_boundary = _callback_config
            if _private_boundary is not None and _final_callback_context.get() is not _private_boundary:
                # Stock proxy hooks, deferred reroutes and parallel guardrails
                # finish inside ProxyLogging before the final gate issues once.
                return None
            try:
                _reject_provider_overrides(data)
            except DistributionError:
                raise HTTPException(400, "caller cannot override MTP2 provider routing or credentials") from None
            try:
                capability = issue_capability(capture.body, model, user_api_key_dict, registry,
                                              backend_secret=backend_secret, headers=capture.headers)
            except DistributionError:
                raise HTTPException(403, "MTP2 requires its exact original virtual-key model grant") from None
            headers = data.get("extra_headers", {})
            if type(headers) is not dict or any(type(key) is not str or key.lower().startswith("x-liliuxflow-") for key in headers):
                raise HTTPException(400, "caller cannot supply internal MTP2 headers")
            data["extra_headers"] = {**headers, CAPABILITY_HEADER: capability}
            return data

    return SharedMtp2Callback()


try:
    shared_callback = create_callback()
except ImportError:
    shared_callback = None


def require_shared_installation(trusted, *, for_configuration=False):
    if type(for_configuration) is not bool:
        raise DistributionError("shared MTP2 configuration purpose is invalid")
    if "_mtp2_validation_binding" in trusted:
        from mtp2_shared_upgrade import require_validation_binding
        return require_validation_binding(trusted).installation
    if "_mtp2_config_binding" in trusted:
        if not for_configuration:
            raise DistributionError("shared MTP2 config preparation cannot launch a runtime")
        from mtp2_shared_upgrade import configuration_binding
        return configuration_binding(trusted).installation
    from mtp2_shared_runtime import validate_shared_extension, normal_shared_view
    from mtp2_shared_runner import RELEASE_EXPECTATION
    if RELEASE_EXPECTATION is None:
        raise DistributionError("shared MTP2 frozen release recipe is unavailable")
    selected = normal_shared_view(trusted)
    if selected['registry'] != trusted['registry']:
        raise DistributionError("shared MTP2 normal source view was not explicitly selected")
    installation = validate_shared_extension(selected, RELEASE_EXPECTATION)
    if installation is None or not (trusted["source_root"] / "scripts/distribution/mtp2_shared_runner.py").is_file():
        raise DistributionError("shared MTP2 runner and trusted recipe are unavailable")
    return installation


def create_litellm_app():
    if not enabled():
        raise DistributionError("shared MTP2 LiteLLM entry point is disabled")
    from compat_api.portable import _validated
    trusted = _validated()
    require_shared_installation(trusted)
    from litellm.proxy import proxy_server
    if "_mtp2_validation_binding" in trusted:
        from mtp2_shared_upgrade import require_validation_binding
        binding = require_validation_binding(trusted)
        config_sha256 = binding.lp_config_sha256
    else:
        from agent import normal_shared_configuration_sha256
        config_sha256 = normal_shared_configuration_sha256(trusted)
    boundary = _PrivateRouteBoundary(proxy_server, trusted["data_root"] / "run/litellm.json",
                                     config_sha256, trusted["registry"], trusted["config"]["ports"]["guard"]).install()
    os.environ["STORE_MODEL_IN_DB"] = "False"
    # Reuse the pinned CLI's supported initialization mechanism. No key is
    # copied into this config; existing os.environ references remain in LP.
    proxy_server.save_worker_config(config=str(trusted["data_root"] / "run/litellm.json"),
                                   request_timeout=None, drop_params=False,
                                   add_function_to_prompt=False, telemetry=False)
    secret = trusted["secrets"]["MANAGER_BACKEND_TOKEN"]
    if type(secret) is not str:
        raise DistributionError("shared MTP2 backend credential is unavailable")
    owner = configure_callback(trusted["registry"], secret.encode("utf-8"), private_boundary=boundary)
    from mtp2_shared_ui import SharedModelInfo
    async def ui_snapshot():
        import httpx
        async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
            async with client.stream("GET","http://127.0.0.1:"+str(trusted["config"]["ports"]["guard"])+"/api/context-profiles",headers={"Authorization":"Bearer "+trusted["secrets"]["MANAGER_BACKEND_TOKEN"]}) as response:
                if response.status_code!=200:raise DistributionError("shared UI snapshot is unavailable")
                raw=bytearray()
                async for part in response.aiter_bytes():
                    if len(raw)+len(part)>32768:raise DistributionError("shared UI snapshot exceeds its bound")
                    raw.extend(part)
                return json.loads(raw)
    app = RawMtp2Ingress(SharedModelInfo(proxy_server.app,ui_snapshot), trusted["registry"], capture=True, owner=owner)
    return _PrivateRouteApp(app, boundary) if boundary is not None else app


def shared_guard_factory(trusted):
    """Return a trusted guard constructor; no construction from HTTP dictionaries."""
    installation = require_shared_installation(trusted)
    from mtp2_shared_owner import Mtp2SharedOwnerProbe
    probe = Mtp2SharedOwnerProbe(trusted, installation)
    def factory(**kwargs):
        return make_shared_guard(owner_probe=probe, shared_ui_litellm_url="http://127.0.0.1:"+str(trusted["config"]["ports"]["litellm"]), **kwargs)
    return factory


def make_shared_guard(*, owner_probe, **kwargs):
    # Actual implementation below; unavailable prerequisites are never routed
    # to a separate model/runner or cleared using HTTP completion.
    return _make_shared_guard(owner_probe=owner_probe, **kwargs)


def _instance_free():
    """Probe the original Lily flock without creating/truncating/removing it."""
    import fcntl
    import stat
    from common import no_symlinks
    path = no_symlinks(Path.home() / "Library/Caches/lily/instance.lock")
    if not path.exists():
        return True
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


def _make_shared_guard(*, owner_probe, **kwargs):
    import asyncio
    import hmac
    import httpx
    from types import SimpleNamespace
    from compat_api.llama_guard import (
        _ContextLifecycleGuard, _GuardError, _CallerGone, _ClientGoneResponse,
        _ProfileStreamingResponse, _wait_request_disconnect, _stage,
        _filter_headers, _control_body)
    from mtp2_shared_coordinator import Mtp2SharedCoordinator, RuntimeBinding
    from mtp2_shared_ingress import VerifiedIngress
    from mtp2_shared_transport import NativeTransport, RawReply, MAX_RESPONSE_BYTES

    for name in ("current_owner", "matches", "exited"):
        if not callable(getattr(owner_probe, name, None)):
            raise DistributionError("shared MTP2 exact owner provider is unavailable")

    class SharedContextGuard(_ContextLifecycleGuard):
        def __init__(self, **options):
            self.ui_litellm_url=options.pop("shared_ui_litellm_url",None)
            super().__init__(**options)
            if type(self.backend_token) is not str or not self.backend_token:
                raise DistributionError("shared MTP2 backend credential is unavailable")
            self.native_actor = NativeTransport(self.backend_token.encode("utf-8"))
            self.owner_probe = owner_probe
            self.shared_owner = self.native_binding = None
            self.shared_ticket = self.physical_wait = self.poll_task = None
            self.idle_task = None
            self.last_shared_work = None
            self.shared_rows = {}
            self.shared_resident = SimpleNamespace(profile_id="mtp2-shared", public_alias=PHYSICAL_MODEL_ID,
                                                   engine_id="latest13f-defer-pc123-mtp2-shared-context-v1")
            self.shared = Mtp2SharedCoordinator(prepare_runtime=self._shared_ready,
                                               retire_runtime=self._retire_shared,
                                               pending_limit=self.registry.pending_limit)

        @asynccontextmanager
        async def lifespan(self):
            async with super().lifespan():
                self.idle_task = asyncio.create_task(self._idle_watch())
                try:
                    yield
                finally:
                    self.idle_task.cancel()
                    await asyncio.gather(self.idle_task, return_exceptions=True)

        async def _idle_watch(self):
            while True:
                await asyncio.sleep(1)
                await self._idle_once()

        async def _idle_once(self):
            now = asyncio.get_running_loop().time()
            async with self._lock:
                if (self.shared_owner is None or self.shared_rows or self._ticket is not None or self._pending
                        or self._admission_paused or self.last_shared_work is None or now - self.last_shared_work < 1800):
                    return False
                self._admission_paused, self._pause_reason = True, "ui_lifecycle_mutation"
            try:
                await self._verify_shared_ready()
                await self.shared.unload()
            except Exception:
                async with self._lock:
                    self._pause_reason = "resource_release_unverified"
                return False
            await self.end_lifecycle_mutation()
            return True

        async def _verify_shared_ready(self):
            binding = self.shared._binding
            if binding is None or binding.physical_owner is not self.shared_owner:
                raise DistributionError("shared MTP2 resident binding is unavailable")
            raw = None
            async def send(wire):
                nonlocal raw
                raw = await self._native_send(wire)
                return raw
            reply = await self.native_actor.exchange(self.native_actor.state_request(), send)
            if reply.binding is None or reply.binding.epoch != binding.epoch:
                raise DistributionError("shared MTP2 resident epoch changed")
            # Read counts only after the actor authenticated the raw response
            # and validated its exact schema/types. A ready state can be busy.
            state = json.loads(raw.body)
            if state["active"] != 0 or state["pending"] != 0:
                raise DistributionError("shared MTP2 resident is not idle")

        async def begin_lifecycle_mutation(self):
            async with self._lock:
                if self.shared_rows or self._ticket is not None or self._pending:
                    return False, "busy"
                if self._admission_paused:
                    return False, "maintenance"
                self._admission_paused, self._pause_reason = True, "ui_lifecycle_mutation"
                return True, ""

        async def maintenance(self, *, token, unlock):
            if not self.control_token or not hmac.compare_digest(token, self.control_token):
                return 403, {"error": "forbidden"}
            async with self._lock:
                if self.shared_rows or self._ticket is not None or self._pending:
                    return 409, {"error": "inference_busy", **await self._status_locked()}
                if unlock:
                    if self._pause_reason != "session_maintenance":
                        return 409, {"error": "not_in_session_maintenance"}
                    self._admission_paused, self._pause_reason = False, None
                else:
                    if self._admission_paused:
                        return 409, {"error": "admission_already_paused"}
                    self._admission_paused, self._pause_reason = True, "session_maintenance"
                return 200, await self._status_locked()

        async def _manager_running(self, deadline):
            value = await self._manager_json("GET", "/running", deadline=deadline)
            rows = value.get("running") if type(value) is dict else None
            if type(rows) is not list or len(rows) > 1:
                raise _GuardError(503, "ownership_unverified", "manager running inventory differs")
            return rows

        async def _running(self, deadline):
            rows = await self._manager_running(deadline)
            if rows and rows[0].get("model") == PHYSICAL_MODEL_ID:
                if self.shared_owner is None or not await asyncio.to_thread(self.owner_probe.matches, self.shared_owner):
                    raise _GuardError(503, "ownership_unverified", "shared native owner is unverified")
                return self.shared_resident
            return await super()._running(deadline)

        async def _native_send(self, wire):
            owner = self.shared_owner
            if owner is None or not await asyncio.to_thread(self.owner_probe.matches, owner):
                raise DistributionError("shared MTP2 native owner changed")
            if self.client is None:
                raise DistributionError("shared MTP2 private transport is unavailable")
            request = self.client.build_request(wire.method, "http://127.0.0.1:" + str(owner.port) + wire.target,
                                                headers=(*wire.headers, ("Content-Type", "application/json")), content=wire.body)
            response = await self.client.send(request, stream=True)
            body = bytearray()
            try:
                async for part in response.aiter_raw():
                    if len(body) + len(part) > MAX_RESPONSE_BYTES:
                        raise DistributionError("shared MTP2 control response exceeds its bound")
                    body.extend(part)
                headers = tuple((name.decode("ascii"), value.decode("ascii")) for name, value in response.headers.raw)
                if not await asyncio.to_thread(self.owner_probe.matches, owner):
                    raise DistributionError("shared MTP2 native owner changed")
                return RawReply(response.status_code, headers, bytes(body))
            finally:
                await response.aclose()

        async def _physical_acquire(self, profile):
            # The established context FIFO is the sole physical barrier. No
            # new global lease and no expansion of its MTP0 _ticket semantics.
            ticket = await super()._acquire(profile, asyncio.get_running_loop().time() + profile.total_deadline_seconds, None)
            self.shared_ticket = ticket
            return ticket

        async def _join_physical(self, profile, deadline, disconnect):
            async with self._lock:
                if self.physical_wait is None:
                    self.physical_wait = asyncio.create_task(self._physical_acquire(profile))
                    self.physical_wait.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                waiting = self.physical_wait
            return await _stage(asyncio.shield(waiting), deadline=deadline, disconnect=disconnect)

        async def _shared_ready(self):
            deadline = asyncio.get_running_loop().time() + 300
            rows = await self._manager_running(deadline)
            if rows and rows[0].get("model") != PHYSICAL_MODEL_ID:
                current = self.registry.resolve(rows[0]["model"])
                await super()._stop_and_prove(current, deadline)
            if not rows or rows[0].get("model") != PHYSICAL_MODEL_ID:
                await self._proof(self.registry.default, "before_forward", deadline=deadline)
                await self._manager_json("GET", "/upstream/" + PHYSICAL_MODEL_ID + "/health", deadline=deadline)
            while True:
                try:
                    self.shared_owner = await asyncio.to_thread(self.owner_probe.current_owner)
                    reply = await self.native_actor.exchange(self.native_actor.state_request(), self._native_send)
                    if reply.binding is not None:
                        self.native_binding = reply.binding
                        self._resident_profile = self.shared_resident
                        self.last_shared_work = asyncio.get_running_loop().time()
                        asyncio.get_running_loop().call_soon(lambda: asyncio.create_task(self._release_physical_if_idle()))
                        return RuntimeBinding(reply.binding.epoch, self.native_actor, self.shared_owner)
                except (DistributionError, OSError):
                    if asyncio.get_running_loop().time() >= deadline:
                        raise DistributionError("shared MTP2 owned native readiness is unavailable") from None
                await _stage(asyncio.sleep(.025), deadline=deadline)

        async def _retire_shared(self, binding):
            owner = binding.physical_owner
            if owner is not self.shared_owner or not await asyncio.to_thread(self.owner_probe.matches, owner):
                raise DistributionError("shared MTP2 retirement ownership differs")
            deadline = asyncio.get_running_loop().time() + 60
            await self._manager_json("POST", "/api/models/unload", deadline=deadline)
            while True:
                rows = await self._manager_running(deadline)
                exited = await asyncio.to_thread(self.owner_probe.exited, owner)
                if not rows and exited and await asyncio.to_thread(_instance_free):
                    self.shared_owner = self.native_binding = None
                    self._resident_profile = None
                    return
                await _stage(asyncio.sleep(.025), deadline=deadline)

        async def _prepare(self, ticket, disconnect):
            if self.shared_owner is not None:
                await self.shared.unload()
            return await super()._prepare(ticket, disconnect)

        async def _stop_and_prove(self, profile, deadline):
            if getattr(profile, "profile_id", None) == "mtp2-shared":
                await self.shared.unload()
                return
            return await super()._stop_and_prove(profile, deadline)

        async def _release_physical_if_idle(self):
            status = await self.shared.status()
            async with self._lock:
                if self.shared_rows or status["busy"] or status["paused"]:
                    return
                if self.shared_ticket is not None and self._ticket is self.shared_ticket:
                    self.shared_ticket = None
                    self.physical_wait = None
                    self._last_release_method = "authenticated_shared_native_rows"
                    self._advance_locked()
                elif self.physical_wait is not None and not self.physical_wait.done():
                    self.physical_wait.cancel()
                    self.physical_wait = None

        async def _poll(self):
            try:
                while True:
                    rows = tuple(row for row in self.shared_rows.values() if row.handle is not None and not row.native_done)
                    if not rows:
                        return
                    if self.native_binding is None:
                        raise DistributionError("shared MTP2 runtime binding is unavailable")
                    try:
                        reply = await self.native_actor.exchange(self.native_actor.status_request(self.native_binding, tuple(row.handle for row in rows)), self._native_send)
                    except DistributionError as error:
                        # Status can beat the inference socket to registration.
                        # Keep all permits; only new/unobserved IDs get a bounded
                        # visibility wait. Unknown never means released.
                        if any(self.native_actor.is_initial_unknown(error, row.handle)
                               and asyncio.get_running_loop().time() < row.visibility_until for row in rows):
                            await asyncio.sleep(.025)
                            continue
                        raise
                    for record in reply.records:
                        row = self.shared_rows.get(record.nonce)
                        if row is None:
                            raise DistributionError("shared MTP2 receipt has no admitted caller")
                        if not await asyncio.to_thread(self.owner_probe.matches, self.shared_owner):
                            raise DistributionError("shared MTP2 receipt owner changed")
                        terminal = await self.shared.apply_verified_record(self.native_actor, row.handle, record,
                                                                         owned_runtime=self.shared_owner)
                        if terminal:
                            self.native_actor.forget_terminal(row.handle, record)
                            row.native_done = True
                            row.stage = "cleanup"
                            if not row.settled.done():
                                row.settled.set_result(True)
                            if row.reader_done:
                                async with self._lock:
                                    self.shared_rows.pop(record.nonce, None)
                    for nonce in await self.shared.tick():
                        row = self.shared_rows.get(nonce)
                        if row is not None and row.handle is not None and not row.native_done:
                            await self.native_actor.exchange(self.native_actor.cancel_request(self.native_binding, row.handle), self._native_send)
                    await self._release_physical_if_idle()
                    await asyncio.sleep(.1)
            except (Exception, asyncio.CancelledError):
                await self.shared.event_channel_lost()
                async with self._lock:
                    self._admission_paused, self._pause_reason = True, "resource_release_unverified"
                for row in tuple(self.shared_rows.values()):
                    if not row.settled.done():
                        row.settled.set_exception(_GuardError(503, "resource_release_unverified", "shared native cleanup is unverified"))
            finally:
                self.poll_task = None

        async def _shared_finish(self, row, response, complete):
            if response is not None:
                await response.aclose()
            row.reader_done = True
            self.last_shared_work = asyncio.get_running_loop().time()
            if row.handle is None:
                await self.shared.cancel(row.admitted.nonce)
                self.native_actor.discard_unforwarded(row.admitted)
                async with self._lock:
                    self.shared_rows.pop(row.admitted.nonce, None)
                await self._release_physical_if_idle()
                return
            terminal = row.settled.done() and not row.settled.cancelled() and row.settled.exception() is None and row.settled.result() is True
            if not terminal and (row.caller_cancelled or row.generation_failed or not complete):
                row.stage = "cleanup"
                await self.shared.cancel(row.handle.nonce)
                try:
                    await self.native_actor.exchange(self.native_actor.cancel_request(self.native_binding, row.handle), self._native_send)
                except Exception:
                    await self.shared.event_channel_lost()
            await _stage(asyncio.shield(row.settled), deadline=asyncio.get_running_loop().time() + 60)
            async with self._lock:
                self.shared_rows.pop(row.admitted.nonce, None)
            await self._release_physical_if_idle()

        async def _shared_inference(self, request):
            if self.client is None:
                return _control_body(503, "manager_unavailable", "manager proxy is starting")
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + self.backend_token):
                return _control_body(401, "unauthorized", "inference requires the authenticated LiteLLM route")
            disconnect = row = response = None
            delegated = complete = False
            try:
                body, value = await self._body(request)
                _reject_provider_overrides(value)
                profile = await self._profile(value)
                deadline = asyncio.get_running_loop().time() + profile.total_deadline_seconds
                capability = request.headers.get(CAPABILITY_HEADER, "")
                admitted = self.native_actor.consume_verified_ingress(capability, body, self.registry, deadline=deadline,
                                                                       completion=request.url.path == "/v1/completions")
                row = SimpleNamespace(admitted=admitted, profile=profile, handle=None,
                                      settled=asyncio.get_running_loop().create_future(), caller_cancelled=False, generation_failed=False,
                                      stage="queued", native_done=False, reader_done=False)
                async with self._lock:
                    if self._admission_paused:
                        raise _GuardError(503, "admission_paused", "manager admission is paused")
                    # Do not let a later shared family request overtake an
                    # earlier MTP0 waiter in the established physical FIFO.
                    if any(ticket.profile.public_alias not in PUBLIC_ALIASES for ticket in self._pending):
                        raise _GuardError(429, "queue_full", "another model family is already queued")
                    if (self.shared_owner is not None and self._ticket is not None
                            and self._ticket.profile.public_alias not in PUBLIC_ALIASES):
                        raise _GuardError(429, "queue_full", "another model family is retiring the shared runtime")
                    self.shared_rows[admitted.nonce] = row
                ingress = VerifiedIngress(admitted.nonce, admitted.tenant, admitted.alias, admitted.profile, int(__import__('time').time()) + 30)
                disconnect = asyncio.create_task(_wait_request_disconnect(request))
                await _stage(self.shared.reserve(ingress, deadline=deadline), deadline=deadline, disconnect=disconnect)
                row.stage = "loading"
                await self._join_physical(profile, deadline, disconnect)
                await _stage(self.shared.ensure_ready(admitted.nonce), deadline=deadline, disconnect=disconnect)
                if not await asyncio.to_thread(self.owner_probe.matches, self.shared_owner):
                    raise DistributionError("shared MTP2 owner changed before forwarding")
                wire = self.native_actor.inference_request(admitted, self.native_binding)
                row.handle = wire.handles[0]
                row.stage = "forwarded"
                row.visibility_until = asyncio.get_running_loop().time() + 2
                await self.shared.mark_forwarded(admitted.nonce)
                headers = _filter_headers(request.headers, request_side=True)
                for key in tuple(headers):
                    if key.lower().startswith("x-liliuxflow-"):
                        del headers[key]
                headers.update(wire.headers)
                headers["Authorization"] = "Bearer " + self.backend_token
                built = self.client.build_request(wire.method, self.manager_url + wire.target, headers=headers, content=wire.body)
                if self.poll_task is None:
                    self.poll_task = asyncio.create_task(self._poll())
                response = await _stage(self.client.send(built, stream=True), deadline=deadline, disconnect=disconnect)
                public_headers = _filter_headers(response.headers, request_side=False)
                for key in tuple(public_headers):
                    if key.lower().startswith("x-liliuxflow-control-"):
                        del public_headers[key]
                public_headers["X-LiliuxFlow-Context-Tokens"] = str(profile.context_tokens)
                public_headers["X-LiliuxFlow-Profile"] = profile.profile_id
                async def chunks():
                    nonlocal complete
                    iterator = response.aiter_raw().__aiter__()
                    while True:
                        try:
                            part = await _stage(iterator.__anext__(), deadline=deadline, disconnect=disconnect)
                        except StopAsyncIteration:
                            break
                        yield part
                    complete = response.is_success
                async def cleanup():
                    await self._shared_finish(row, response, complete)
                delegated = True
                return _ProfileStreamingResponse(chunks(), disconnect=disconnect, cleanup=cleanup, ticket=row,
                                                 status_code=response.status_code, headers=public_headers, media_type=None)
            except _CallerGone:
                if row is not None:
                    row.caller_cancelled = True
                return _ClientGoneResponse()
            except _GuardError as error:
                return _control_body(error.status, error.code, error.detail)
            except (DistributionError, ValueError, httpx.HTTPError):
                return _control_body(503, "shared_mtp2_unavailable", "shared MTP2 authenticated serving is unavailable")
            finally:
                if not delegated:
                    if disconnect is not None:
                        disconnect.cancel()
                        await asyncio.gather(disconnect, return_exceptions=True)
                    if row is not None:
                        await self._shared_finish(row, response, False)

        async def _bounded_request_bytes(self,request):
            body=bytearray()
            async for chunk in request.stream():
                if len(body)+len(chunk)>self.registry.maximum_body_bytes:
                    raise _GuardError(413,"request_too_large","request body exceeds the bounded body limit")
                body.extend(chunk)
            request._body=bytes(body)
            return request._body

        async def _ui_relay(self, request):
            # Target is the validated installation's fixed loopback LP port.
            # The UI's own key goes to actual LP auth exactly once; no new cap,
            # physical ticket, admission, fallback or GPU-release claim here.
            from urllib.parse import urlsplit
            target=urlsplit(self.ui_litellm_url or "")
            if target.scheme!="http" or target.hostname!="127.0.0.1" or target.port is None or target.path or target.query or target.fragment or target.username is not None:
                return _control_body(503,"shared_ui_unavailable","shared UI LiteLLM route is unavailable")
            origin=request.headers.get("origin")
            if origin and origin.rstrip("/")!=str(request.url).split(request.url.path,1)[0]:
                return _control_body(403,"origin_not_allowed","UI request origin differs")
            auth=request.headers.get("authorization","")
            if (len(request.headers.getlist("authorization"))!=1 or not auth.startswith("Bearer ")
                    or not 0<len(auth[7:])<=4096 or not auth.isascii() or "\r" in auth or "\n" in auth):
                return _control_body(401,"virtual_key_required","use your LiteLLM virtual key")
            if any(key.lower().startswith("x-liliuxflow-") for key in request.headers):
                return _control_body(400,"internal_header_forbidden","caller cannot supply internal headers")
            body=b""
            if request.method=="GET":
                async for chunk in request.stream():
                    if chunk:return _control_body(400,"unexpected_body","model catalog GET does not accept a body")
            if request.method=="POST":
                try:
                    body=await self._bounded_request_bytes(request)
                    value,_=validate_raw_ingress(body,self.registry)
                    _reject_provider_overrides(value)
                except _GuardError as error:
                    return _control_body(error.status,error.code,error.detail)
                except DistributionError:
                    return _control_body(400,"invalid_request","invalid shared MTP2 request")
            if self.client is None:return _control_body(503,"shared_ui_unavailable","shared UI transport is unavailable")
            response=await self.client.send(self.client.build_request(request.method,self.ui_litellm_url+request.url.path,
                headers=_filter_headers(request.headers,request_side=True),content=body),stream=True)
            disconnect=asyncio.create_task(_wait_request_disconnect(request))
            async def chunks():
                async for chunk in response.aiter_raw():yield chunk
            async def cleanup():
                await response.aclose();disconnect.cancel()
                await asyncio.gather(disconnect,return_exceptions=True)
            return _ProfileStreamingResponse(chunks(),disconnect=disconnect,cleanup=cleanup,
                status_code=response.status_code,headers=dict(_filter_headers(response.headers,request_side=False)))

        async def proxy_http(self, request, *, inference):
            if (request.method=="GET" and request.url.path=="/v1/models"
                    and request.headers.get("authorization","")!="Bearer "+self.backend_token):
                if request.headers.get("authorization"):return await self._ui_relay(request)
            if request.url.path.startswith("/_liliuxflow/"):
                return _control_body(403, "route_not_allowed", "native control is private")
            if inference:
                try:body=await self._bounded_request_bytes(request)
                except _GuardError as error:return _control_body(error.status,error.code,error.detail)
                if _mtp2_request(body):
                    if request.url.path not in ("/v1/chat/completions", "/v1/completions"):
                        return _control_body(400, "unsupported_route", "shared MTP2 serves chat and completions")
                    if request.headers.get("authorization","")!="Bearer "+self.backend_token:
                        return await self._ui_relay(request)
                    return await self._shared_inference(request)
            return await super().proxy_http(request, inference=inference)

        async def catalog(self):
            value = await super().catalog()
            if self._resident_profile is self.shared_resident:
                for profile in value["profiles"]:
                    if profile["model"] in PUBLIC_ALIASES:
                        profile["loaded"] = True
                        matching = [row for row in self.shared_rows.values() if row.admitted.alias == profile["model"]]
                        profile["state"] = matching[0].stage if matching else "ready"
                        profile["queued"] = sum(row.handle is None for row in matching)
            from mtp2_shared_ui import catalog_projection
            async with self._lock:
                rows=tuple(self.shared_rows.values())
                loading=bool(self.physical_wait is not None and not self.physical_wait.done() or self.shared._ready_task is not None and not self.shared._ready_task.done())
                state=("unavailable" if self.shared._mutating else "ready" if self._resident_profile is self.shared_resident else "loading" if loading else "unavailable" if self.shared_owner is not None else "unloaded")
                busy=bool(rows or self._ticket is not None or self._pending or loading or self._admission_paused)
            return catalog_projection(value,rows,state=state,busy=busy)

        async def _profile_lifecycle(self, request):
            path = request.url.path
            load_alias = path.split("/load/", 1)[1] if "/load/" in path else None
            unload_alias = path.split("/unload/", 1)[1] if "/unload/" in path else None
            if load_alias not in PUBLIC_ALIASES and unload_alias not in PUBLIC_ALIASES:
                return await super()._profile_lifecycle(request)
            allowed, reason = await self.begin_lifecycle_mutation()
            if not allowed:
                return _control_body(409, "inference_busy" if reason == "busy" else "maintenance", "model generation or cleanup is active")
            safe = False
            try:
                self.registry.resolve(load_alias or unload_alias)
                if unload_alias is not None:
                    if self.shared_owner is not None:
                        await self.shared.unload()
                    safe = True
                    from starlette.responses import JSONResponse
                    return JSONResponse({"unloaded": unload_alias}, headers={"Cache-Control": "no-store"})
                # An explicit UI Load owns the same lifecycle hold. It creates
                # no inference request, virtual key, nonce or dummy GPU job.
                async with self.shared._condition:
                    if self.shared._paused or self.shared._busy_locked() or self.shared._mutating:
                        raise DistributionError("shared MTP2 lifecycle is busy")
                    binding = self.shared._binding
                    if binding is None:
                        if self.shared._ready_task is None:
                            self.shared._ready_task = asyncio.create_task(self.shared._start())
                        ready = self.shared._ready_task
                    else:
                        ready = None
                if ready is not None:
                    await asyncio.shield(ready)
                else:
                    await self._verify_shared_ready()
                self.last_shared_work = asyncio.get_running_loop().time()
                safe = True
                from starlette.responses import JSONResponse
                return JSONResponse({"status": "ok", "state": "ready", "model": load_alias}, headers={"Cache-Control": "no-store"})
            except (DistributionError, ValueError, _GuardError):
                return _control_body(503, "shared_mtp2_unavailable", "shared MTP2 lifecycle is unverified")
            finally:
                if safe:
                    await self.end_lifecycle_mutation()
                else:
                    async with self._lock:
                        self._pause_reason = "resource_release_unverified"

        async def _status_locked(self):
            value = await super()._status_locked()
            if not self.shared_rows and self.shared_ticket is None and self.shared_owner is None and self.physical_wait is None:
                return value
            if self._ticket is not None and self._ticket.profile.public_alias in PUBLIC_ALIASES:
                value["active_inferences"] -= 1
            value["pending_inferences"] -= sum(ticket.profile.public_alias in PUBLIC_ALIASES for ticket in self._pending)
            value["active_inferences"] += sum(row.handle is not None for row in self.shared_rows.values())
            value["pending_inferences"] += sum(row.handle is None for row in self.shared_rows.values())
            value["shared_requests"] = tuple({"request_id": nonce, "model": row.admitted.alias,
                                               "stage": row.stage, "native_settled": row.native_done}
                                              for nonce, row in self.shared_rows.items())
            value["shared_physical_loading"] = bool(self.shared_ticket is not None and not self.shared_rows)
            return value

    return SharedContextGuard(**kwargs)
