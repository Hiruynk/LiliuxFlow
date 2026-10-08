# SPDX-License-Identifier: Apache-2.0
"""Loopback reverse proxy that makes llama-swap UI unloads busy-safe.

The stock llama-swap process listens on a private loopback port. LiteLLM and
the stock browser UI use this public loopback proxy. Inference admission and
manual lifecycle operations share one condition lock, so a Stop cannot race a
new request between checking busy state and forwarding the unload.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
LIFECYCLE_PREFIXES = ("/api/models/",)
LIFECYCLE_EXACT = {"/api/models/unload", "/api/profiles/active", "/unload"}
LIFECYCLE_RELOAD_PREFIXES = ("/api/config/", "/api/reload", "/api/inflight/")
INFERENCE_PREFIXES = ("/v1/", "/completion", "/infill")
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5


def is_inference_request(method: str, path: str) -> bool:
    upper = method.upper()
    if upper not in {"POST", "PUT", "PATCH"}:
        return False
    return path.startswith(INFERENCE_PREFIXES)


def is_lifecycle_mutation(method: str, path: str) -> bool:
    upper = method.upper()
    if upper == 'GET' and path == '/unload':
        # The pinned manager retains this historical mutating GET operation.
        return True
    if upper not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return (
        path in LIFECYCLE_EXACT
        or any(path.startswith(prefix) for prefix in LIFECYCLE_PREFIXES)
        or any(path.startswith(prefix) for prefix in LIFECYCLE_RELOAD_PREFIXES)
    )


def _filter_headers(headers: Any, *, request_side: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    connection_tokens: set[str] = set()
    for key, value in headers.items():
        if key.lower() == "connection":
            connection_tokens.update(part.strip().lower() for part in value.split(","))
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP or lowered in connection_tokens:
            continue
        if request_side and lowered in {"host", "content-length"}:
            continue
        result[key] = value
    return result


def _control_body(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"type": code, "message": detail}},
        headers={"Cache-Control": "no-store"},
    )


class _GuardError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail


class _CallerGone(Exception):
    pass


async def _stage(awaitable: Any, *, deadline: float, disconnect: asyncio.Task | None = None):
    """Bound one stage by the original total deadline and caller connection."""
    task = asyncio.ensure_future(awaitable)
    waiters = {task} if disconnect is None else {task, disconnect}
    try:
        done, _ = await asyncio.wait(waiters, timeout=max(0, deadline - asyncio.get_running_loop().time()),
                                     return_when=asyncio.FIRST_COMPLETED)
        if disconnect is not None and disconnect in done:
            raise _CallerGone()
        if task not in done:
            raise _GuardError(504, 'request_deadline', 'request exceeded its selected profile deadline')
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
        outcome = await asyncio.gather(task, return_exceptions=True)
        if outcome and isinstance(outcome[0], httpx.Response):
            await outcome[0].aclose()
        raise


@dataclass(eq=False)
class _ProfileTicket:
    profile: Any
    granted: asyncio.Future
    deadline: float
    forwarded: bool = False
    cleanup_profile: Any = None
    stage: str = 'loading'


class _ProfileStreamingResponse(StreamingResponse):
    """One disconnect reader, with cleanup even when ASGI send itself fails."""
    def __init__(self, content: Any, *, disconnect: asyncio.Task, cleanup: Callable, **kwargs):
        super().__init__(content, **kwargs)
        self.disconnect = disconnect
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        streaming = asyncio.create_task(self.stream_response(send))
        try:
            done, _ = await asyncio.wait({streaming, self.disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if self.disconnect in done:
                if not streaming.done():
                    streaming.cancel()
                await asyncio.gather(streaming, return_exceptions=True)
            else:
                await streaming
        finally:
            streaming.cancel()
            self.disconnect.cancel()
            await asyncio.gather(streaming, self.disconnect, return_exceptions=True)
            task = asyncio.create_task(self.cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A disconnected response cannot release another profile while
                # its owned native lane/child is still being cleaned up.
                await task
                raise


class _ClientGoneResponse(Response):
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        return None


async def _wait_request_disconnect(request: Request) -> None:
    while True:
        message = await request.receive()
        if message.get("type") == "http.disconnect":
            return


async def _send_before_disconnect(
    request: Request,
    client: httpx.AsyncClient,
    built: httpx.Request,
) -> httpx.Response | None:
    send_task = asyncio.create_task(client.send(built, stream=True))
    disconnect_task = asyncio.create_task(_wait_request_disconnect(request))
    try:
        done, _pending = await asyncio.wait(
            {send_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            if not send_task.done():
                send_task.cancel()
            outcome = await asyncio.gather(send_task, return_exceptions=True)
            if outcome and isinstance(outcome[0], httpx.Response):
                await outcome[0].aclose()
            return None
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await send_task
    except BaseException:
        disconnect_task.cancel()
        if not send_task.done():
            send_task.cancel()
        outcome = await asyncio.gather(send_task, return_exceptions=True)
        if outcome and isinstance(outcome[0], httpx.Response):
            await outcome[0].aclose()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        raise


class _LifecycleGuard:
    def __init__(self, *, manager_url: str, control_token: str, backend_token: str, transport: httpx.AsyncBaseTransport | None):
        parsed = urlsplit(manager_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
            raise ValueError("manager upstream must use an explicit loopback HTTP endpoint")
        self.manager_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.control_token = control_token
        self.backend_token = backend_token
        self.transport = transport
        self._lock = asyncio.Lock()
        self._active_inferences = 0
        self._admission_paused = False
        self._pause_reason: str | None = None
        self.client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def lifespan(self):
        self.client = httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(connect=3.0, read=None, write=30.0, pool=5.0),
            trust_env=False,
        )
        try:
            yield
        finally:
            await self.client.aclose()
            self.client = None

    async def begin_inference(self) -> bool:
        async with self._lock:
            if self._admission_paused:
                return False
            self._active_inferences += 1
            return True

    async def end_inference(self) -> None:
        async with self._lock:
            self._active_inferences = max(0, self._active_inferences - 1)

    async def begin_lifecycle_mutation(self) -> tuple[bool, str]:
        async with self._lock:
            if self._admission_paused:
                return False, "maintenance"
            self._admission_paused = True
            self._pause_reason = "ui_lifecycle_mutation"
            if self._active_inferences:
                self._admission_paused = False
                self._pause_reason = None
                return False, "busy"
            return True, ""

    async def end_lifecycle_mutation(self) -> None:
        async with self._lock:
            if self._pause_reason == "ui_lifecycle_mutation":
                self._admission_paused = False
                self._pause_reason = None

    async def maintenance(self, *, token: str, unlock: bool) -> tuple[int, dict[str, Any]]:
        if not self.control_token or not hmac.compare_digest(token, self.control_token):
            return 403, {"error": "forbidden"}
        async with self._lock:
            if unlock:
                if self._pause_reason != "session_maintenance":
                    return 409, {"error": "not_in_session_maintenance"}
                self._admission_paused = False
                self._pause_reason = None
                return 200, await self._status_locked()
            if self._admission_paused:
                return 409, {"error": "admission_already_paused"}
            self._admission_paused = True
            self._pause_reason = "session_maintenance"
            if self._active_inferences:
                self._admission_paused = False
                self._pause_reason = None
                return 409, {"error": "inference_busy", "active_inferences": self._active_inferences}
            return 200, await self._status_locked()

    async def _status_locked(self) -> dict[str, Any]:
        return {
            "active_inferences": self._active_inferences,
            "admission_paused": self._admission_paused,
            "pause_reason": self._pause_reason,
        }

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            return await self._status_locked()

    async def proxy_http(self, request: Request, *, inference: bool) -> Response:
        if self.client is None:
            return _control_body(503, "manager_unavailable", "manager proxy is starting")
        if inference:
            bearer = "Bearer " + self.backend_token if self.backend_token else ""
            api_key = request.headers.get("x-api-key", "")
            if not (
                (bearer and hmac.compare_digest(request.headers.get("authorization", ""), bearer))
                or (self.backend_token and hmac.compare_digest(api_key, self.backend_token))
            ):
                return _control_body(401, "unauthorized", "inference must pass through the authenticated LiteLLM model route")
        if inference and not await self.begin_inference():
            return _control_body(503, "admission_paused", "manager is stopping or in maintenance")
        lifecycle = False
        if is_lifecycle_mutation(request.method, request.url.path):
            lifecycle, reason = await self.begin_lifecycle_mutation()
            if not lifecycle:
                if inference:
                    await self.end_inference()
                if reason == "busy":
                    return _control_body(409, "inference_busy", "model is serving a request; retry Stop when idle")
                return _control_body(409, "maintenance", "manager is in controlled maintenance")
        response: httpx.Response | None = None
        delegated = False
        try:
            target = self.manager_url + request.url.path
            if request.url.query:
                target += "?" + request.url.query
            body = await request.body()
            proxy_headers = _filter_headers(request.headers, request_side=True)
            if self.backend_token:
                # The local manager apiKeys setting protects its status and
                # lifecycle APIs as well as inference. The guard supplies the
                # private service credential for stock UI calls; generation
                # requests from outside LiteLLM were rejected above.
                proxy_headers.setdefault("Authorization", "Bearer " + self.backend_token)
            built = self.client.build_request(
                request.method,
                target,
                headers=proxy_headers,
                content=body,
            )
            response = await _send_before_disconnect(request, self.client, built)
            if response is None:
                return _ClientGoneResponse()
            headers = _filter_headers(response.headers, request_side=False)

            async def stream_body() -> AsyncIterator[bytes]:
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                finally:
                    await response.aclose()
                    if inference:
                        await self.end_inference()
                    if lifecycle:
                        await self.end_lifecycle_mutation()

            delegated = True
            return StreamingResponse(
                stream_body(),
                status_code=response.status_code,
                headers=headers,
                media_type=None,
            )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError:
            if response is not None:
                await response.aclose()
            return _control_body(502, "manager_unavailable", "manager proxy request failed")
        finally:
            if not delegated and inference:
                await self.end_inference()
            if not delegated and lifecycle:
                await self.end_lifecycle_mutation()

    async def proxy_websocket(self, websocket: WebSocket) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            await websocket.close(code=1013, reason="websocket proxy unavailable")
            return
        inference = is_inference_request("POST", websocket.url.path)
        if inference:
            bearer = "Bearer " + self.backend_token if self.backend_token else ""
            api_key = websocket.headers.get("x-api-key", "")
            if not (
                (bearer and hmac.compare_digest(websocket.headers.get("authorization", ""), bearer))
                or (self.backend_token and hmac.compare_digest(api_key, self.backend_token))
            ):
                await websocket.close(code=1008, reason="authenticated LiteLLM backend route required")
                return
        if inference and not await self.begin_inference():
            await websocket.close(code=1013, reason="manager admission paused")
            return
        upstream = None
        try:
            scheme = "wss" if self.manager_url.startswith("https:") else "ws"
            ws_target = self.manager_url.replace("http://", scheme + "://", 1) + websocket.url.path
            if websocket.url.query:
                ws_target += "?" + websocket.url.query
            headers = _filter_headers(websocket.headers, request_side=True)
            if self.backend_token:
                headers.setdefault("Authorization", "Bearer " + self.backend_token)
            subprotocols = websocket.scope.get("subprotocols") or None
            upstream = await connect(
                ws_target,
                additional_headers=headers,
                subprotocols=subprotocols,
                open_timeout=5,
                close_timeout=2,
                max_size=16 * 1024 * 1024,
            )
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def browser_to_manager() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def manager_to_browser() -> None:
                while True:
                    message = await upstream.recv()
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(browser_to_manager()),
                asyncio.create_task(manager_to_browser()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, *done, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await websocket.close(code=1013, reason="manager websocket unavailable")
            except RuntimeError:
                pass
        finally:
            if upstream is not None:
                await upstream.close()
            if inference:
                await self.end_inference()


class _ContextLifecycleGuard(_LifecycleGuard):
    """One bounded FIFO across every immutable context profile.

    All network, native-lane and child-exit waits occur outside the state lock.
    The sole permit covers load, generation and cleanup; a closed HTTP reader
    never supplies resource-release proof.
    """
    def __init__(self, *, registry, resource_probe, context_admission=None, validation_aliases=(), **kwargs):
        super().__init__(**kwargs)
        if not callable(resource_probe):
            raise ValueError('context serving requires an owned resource proof callback')
        if not isinstance(validation_aliases, tuple) or any(not isinstance(alias, str) for alias in validation_aliases):
            raise ValueError('validation aliases must be an explicit trusted tuple')
        for alias in validation_aliases:
            registry.resolve(alias, allow_validation=True)
        self.registry = registry
        self.resource_probe = resource_probe
        self.context_admission = context_admission
        self.validation_aliases = validation_aliases
        self._pending: deque[_ProfileTicket] = deque()
        self._ticket: _ProfileTicket | None = None
        self._resident_profile = None
        self.proof_wait_seconds = 10.0
        self.cleanup_seconds = 90.0
        self._last_release_method = None
        self._recovering_cleanup = False
        self._cleanup_recovery_task = None
        self._cleanup_recovery_resumes_queue = False

    def _advance_locked(self):
        while self._pending:
            ticket = self._pending.popleft()
            if ticket.granted.done():
                continue
            if ticket.deadline <= asyncio.get_running_loop().time():
                ticket.granted.set_exception(_GuardError(504, 'request_deadline', 'queued request expired'))
                continue
            self._ticket = ticket
            self._active_inferences = 1
            ticket.granted.set_result(True)
            return
        self._ticket = None
        self._active_inferences = 0

    async def _acquire(self, profile, deadline, disconnect):
        loop = asyncio.get_running_loop()
        ticket = _ProfileTicket(profile, loop.create_future(), deadline)
        async with self._lock:
            if self._admission_paused:
                held = self._ticket
                if (self._pause_reason != 'resource_release_unverified'
                    or (held is not None and held.stage != 'cleanup')
                    or (held is None and self._active_inferences != 0)
                    or (self._recovering_cleanup and not self._cleanup_recovery_resumes_queue)):
                    raise _GuardError(503, 'admission_paused', 'manager admission is paused')
                if len(self._pending) >= self.registry.pending_limit:
                    raise _GuardError(429, 'queue_full', 'the bounded generation queue is full; retry later')
                # Keep admission paused until exact exit proof is revalidated.
                # Recovery never loads a model: only a still-connected queued
                # caller may prepare its profile after that proof succeeds.
                self._pending.append(ticket)
                if not self._recovering_cleanup:
                    self._start_cleanup_recovery_locked(held, resume_queue=True, profile=profile)
            elif self._ticket is None and not self._pending:
                self._ticket = ticket
                self._active_inferences = 1
                ticket.granted.set_result(True)
            else:
                if len(self._pending) >= self.registry.pending_limit:
                    raise _GuardError(429, 'queue_full', 'the bounded generation queue is full; retry later')
                self._pending.append(ticket)
        try:
            await _stage(ticket.granted, deadline=min(deadline, loop.time() + profile.queue_wait_seconds), disconnect=disconnect)
            return ticket
        except BaseException:
            async with self._lock:
                if ticket in self._pending:
                    self._pending.remove(ticket)
                elif self._ticket is ticket and not ticket.forwarded:
                    self._advance_locked()
            raise

    async def begin_lifecycle_mutation(self):
        async with self._lock:
            if self._ticket is not None or self._pending:
                return False, 'busy'
            if self._admission_paused:
                return False, 'maintenance'
            self._admission_paused = True
            self._pause_reason = 'ui_lifecycle_mutation'
            return True, ''

    async def maintenance(self, *, token, unlock):
        if not self.control_token or not hmac.compare_digest(token, self.control_token):
            return 403, {'error': 'forbidden'}
        async with self._lock:
            if self._ticket is not None or self._pending:
                return 409, {'error': 'inference_busy', 'active_inferences': self._active_inferences,
                             'pending_inferences': len(self._pending)}
            if unlock:
                if self._pause_reason != 'session_maintenance':
                    return 409, {'error': 'not_in_session_maintenance'}
                self._admission_paused, self._pause_reason = False, None
            else:
                if self._admission_paused:
                    return 409, {'error': 'admission_already_paused'}
                self._admission_paused, self._pause_reason = True, 'session_maintenance'
            return 200, await self._status_locked()

    async def _status_locked(self):
        return {**await super()._status_locked(), 'pending_inferences': len(self._pending),
                'pending_limit': self.registry.pending_limit,
                'active_model': self._ticket.profile.public_alias if self._ticket else None,
                'queued_models': [ticket.profile.public_alias for ticket in self._pending],
                'resident_model': self._resident_profile.public_alias if self._resident_profile else None,
                'active_stage': self._ticket.stage if self._ticket else None,
                'last_release_method': self._last_release_method}

    def _start_cleanup_recovery_locked(self, ticket, *, resume_queue, profile=None):
        if ticket is not None:
            profile = ticket.cleanup_profile or ticket.profile
        if profile is None:
            raise ValueError('cleanup recovery requires a trusted context profile')
        self._recovering_cleanup = True
        self._cleanup_recovery_resumes_queue = resume_queue
        self._cleanup_recovery_task = asyncio.create_task(self._recover_cleanup_ticket(ticket, profile,
                                                                                     resume_queue=resume_queue))
        return self._cleanup_recovery_task

    async def _recover_cleanup_ticket(self, ticket, profile, *, resume_queue):
        try:
            deadline = asyncio.get_running_loop().time() + 10
            if await self._running(deadline) is not None:
                return 409, {'error': 'owned_model_still_running'}
            if await _stage(self.resource_probe(profile, 'recover_closed'), deadline=deadline) is not True:
                return 409, {'error': 'owned_exit_or_lease_unverified'}
            if await self._running(deadline) is not None:
                return 409, {'error': 'owned_model_still_running'}
            # A newly acquired global lease, reused port or changed owner must
            # still prevent resetting admission after the stale lease was reaped.
            if await _stage(self.resource_probe(profile, 'unloaded'), deadline=deadline) is not True:
                return 409, {'error': 'owned_exit_or_lease_unverified'}
            async with self._lock:
                if (self._ticket is not ticket or (self._pending and not resume_queue)
                    or (ticket is not None and ticket.stage != 'cleanup')
                    or (ticket is None and self._active_inferences != 0)
                    or not self._admission_paused or self._pause_reason != 'resource_release_unverified'):
                    return 409, {'error': 'cleanup_recovery_state_changed'}
                self._ticket = None
                self._active_inferences = 0
                self._resident_profile = None
                self._admission_paused = False
                self._pause_reason = None
                self._last_release_method = 'owned_exit_recovery' if resume_queue else 'privileged_owned_exit_recovery'
                if resume_queue:
                    self._advance_locked()
                return 200, {'recovered': True, **await self._status_locked(),
                             'inference_completed': False, 'native_lane_counters_modified': False}
        except Exception:
            return 503, {'error': 'cleanup_recovery_proof_unavailable'}
        finally:
            async with self._lock:
                if (resume_queue and self._ticket is ticket and self._admission_paused
                    and self._pause_reason == 'resource_release_unverified'):
                    for waiter in self._pending:
                        if not waiter.granted.done():
                            waiter.granted.set_exception(_GuardError(503, 'resource_release_unverified',
                                                                     'owned resource release is not yet verified'))
                    self._pending.clear()
                self._recovering_cleanup = False
                self._cleanup_recovery_resumes_queue = False
                self._cleanup_recovery_task = None

    async def recover_cleanup(self, *, token):
        if not self.control_token or not hmac.compare_digest(token, self.control_token):
            return 403, {'error': 'forbidden'}
        async with self._lock:
            ticket = self._ticket
            if (not self._admission_paused or self._pause_reason != 'resource_release_unverified'
                or ticket is None or ticket.stage != 'cleanup' or self._pending or self._recovering_cleanup):
                return 409, {'error': 'cleanup_recovery_not_available'}
            task = self._start_cleanup_recovery_locked(ticket, resume_queue=False)
        return await asyncio.shield(task)

    async def _manager_json(self, method, path, *, deadline):
        if self.client is None:
            raise _GuardError(503, 'manager_unavailable', 'manager proxy is starting')
        response = await _stage(self.client.request(method, self.manager_url + path,
                              headers={'Authorization': 'Bearer ' + self.backend_token}), deadline=deadline)
        try:
            if not 200 <= response.status_code < 300 or len(response.content) > 1024**2:
                raise _GuardError(503, 'switch_failed', 'manager lifecycle operation failed')
            return response.json()
        except ValueError as error:
            raise _GuardError(503, 'switch_failed', 'manager lifecycle response is invalid') from error
        finally:
            await response.aclose()

    async def _running(self, deadline):
        value = await self._manager_json('GET', '/running', deadline=deadline)
        rows = value.get('running') if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) > 1:
            raise _GuardError(503, 'ownership_unverified', 'manager running model inventory differs')
        if not rows:
            return None
        try:
            return self.registry.resolve(rows[0]['model'], allow_validation=rows[0]['model'] in self.validation_aliases)
        except (KeyError, ValueError, TypeError) as error:
            raise _GuardError(503, 'ownership_unverified', 'manager is running an untrusted profile') from error

    async def _proof(self, profile, phase, *, deadline, disconnect=None):
        while True:
            try:
                proven = await _stage(self.resource_probe(profile, phase), deadline=deadline, disconnect=disconnect)
            except (_CallerGone, asyncio.CancelledError):
                raise
            except Exception as error:
                raise _GuardError(503, 'resource_release_unverified', 'owned resource evidence is unavailable') from error
            if proven is True:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise _GuardError(503, 'resource_release_unverified', 'owned native resource release could not be verified')
            await _stage(asyncio.sleep(.025), deadline=deadline, disconnect=disconnect)

    async def _stop_and_prove(self, profile, deadline):
        await self._proof(profile, 'before_unload', deadline=deadline)
        await self._manager_json('POST', '/api/models/unload', deadline=deadline)
        await self._proof(profile, 'unloaded', deadline=deadline)
        if await self._running(deadline) is not None:
            raise _GuardError(503, 'resource_release_unverified', 'manager retained a model after unload')
        self._resident_profile = None

    async def _prepare(self, ticket, disconnect):
        deadline = ticket.deadline
        current = await _stage(self._running(deadline), deadline=deadline, disconnect=disconnect)
        self._resident_profile = current
        if current is not None and current.profile_id != ticket.profile.profile_id:
            # A cancelled switch still completes owned teardown. The caller's
            # connection only gates subsequent load/forward, never exit proof.
            ticket.cleanup_profile = current
            await _stage(self._stop_and_prove(current, deadline), deadline=deadline, disconnect=disconnect)
            ticket.cleanup_profile = None
            current = None
        ticket.cleanup_profile = ticket.profile
        # Admission must resolve an exact closed predecessor lease before
        # health dispatch can start the requested profile's native runner.
        await self._proof(ticket.profile, 'before_forward', deadline=deadline, disconnect=disconnect)
        # The pinned Lily /health may return HTTP 200 while still loading.
        # Only an explicit granted Load can dispatch upstream health; wait
        # for the native JSON ready state before generation is forwarded.
        while True:
            health = await _stage(self._manager_json('GET', '/upstream/' + ticket.profile.public_alias + '/health',
                                                     deadline=deadline), deadline=deadline, disconnect=disconnect)
            if isinstance(health, dict) and health.get('status') == 'ok' and health.get('state') == 'ready':
                break
            if not isinstance(health, dict) or health.get('state') not in ('loading', 'reloading'):
                raise _GuardError(503, 'profile_not_ready', 'selected native context profile is not ready')
            await _stage(asyncio.sleep(.025), deadline=deadline, disconnect=disconnect)
        await self._proof(ticket.profile, 'before_forward', deadline=deadline, disconnect=disconnect)
        ticket.cleanup_profile = None

    async def _finish(self, ticket, response, *, complete):
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                complete = False
        ticket.stage = 'cleanup'
        safe = not ticket.forwarded and ticket.cleanup_profile is None
        deadline = asyncio.get_running_loop().time() + self.cleanup_seconds
        try:
            if ticket.cleanup_profile is not None:
                await self._stop_and_prove(ticket.cleanup_profile, deadline)
                ticket.cleanup_profile = None
                safe = True
            if ticket.forwarded:
                if complete:
                    try:
                        await self._proof(ticket.profile, 'generation_complete',
                                          deadline=min(deadline, asyncio.get_running_loop().time() + self.proof_wait_seconds))
                        safe = True
                        self._resident_profile = ticket.profile
                        self._last_release_method = 'native_lane'
                    except (Exception, asyncio.CancelledError):
                        await self._stop_and_prove(ticket.profile, deadline)
                        safe = True
                        self._last_release_method = 'owned_child_exit'
                else:
                    await self._stop_and_prove(ticket.profile, deadline)
                    safe = True
                    self._last_release_method = 'owned_child_exit'
        except Exception:
            safe = False
        async with self._lock:
            if self._ticket is not ticket:
                return
            if not safe:
                self._admission_paused, self._pause_reason = True, 'resource_release_unverified'
                for waiter in self._pending:
                    if not waiter.granted.done():
                        waiter.granted.set_exception(_GuardError(503, 'resource_release_unverified', 'model cleanup failed'))
                self._pending.clear()
                # Keep the active permit until owned-resource recovery proves release.
                return
            self._advance_locked()

    async def _body(self, request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > self.registry.maximum_body_bytes:
                raise _GuardError(413, 'request_too_large', 'request body exceeds the bounded body limit')
            body.extend(chunk)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise _GuardError(400, 'invalid_request', 'inference body must contain JSON') from error
        if not isinstance(value, dict):
            raise _GuardError(400, 'invalid_request', 'inference body must be a JSON object')
        return bytes(body), value

    async def _profile(self, value):
        alias = value.get('model')
        try:
            profile = self.registry.resolve(alias, allow_validation=alias in self.validation_aliases)
        except ValueError as error:
            known = any(alias == p.public_alias for p in self.registry.profiles)
            raise _GuardError(503 if known else 404, 'profile_disabled' if known else 'model_not_found',
                              'requested model is disabled' if known else 'model is outside the finite registry') from error
        forbidden = {'num_ctx', 'max_seq', 'context_tokens', 'context_length', 'profile_path',
                     'model_path', 'extra_args', 'truncate', 'truncation'}
        if forbidden.intersection(value):
            raise _GuardError(400, 'invalid_request', 'request cannot override the selected context profile')
        for field in ('options', 'extra_body'):
            nested = value.get(field)
            if isinstance(nested, dict) and forbidden.intersection(nested):
                raise _GuardError(400, 'invalid_request', 'request cannot override the selected context profile')
        budgets = [value[k] for k in ('max_tokens', 'max_completion_tokens') if k in value and value[k] is not None]
        if any(type(budget) is not int or not 0 < budget <= min(profile.context_tokens, 65536) for budget in budgets):
            raise _GuardError(400, 'invalid_request', 'output budget must not exceed 65536 tokens')
        if len(budgets) > 1 and budgets[0] != budgets[1]:
            raise _GuardError(400, 'invalid_request', 'output budgets disagree')
        output = budgets[0] if budgets else profile.default_output_tokens
        if type(output) is not int or not 0 < output <= min(profile.context_tokens, 65536):
            raise _GuardError(400, 'invalid_request', 'output budget must fit the selected profile')
        if self.context_admission is not None:
            try:
                count = await self.context_admission(value, profile)
            except Exception as error:
                raise _GuardError(503, 'context_validation_failed', 'checkpoint context admission failed') from error
            if type(count) is not int or count <= 0:
                raise _GuardError(503, 'context_validation_failed', 'checkpoint context count is unavailable')
            if count >= profile.context_tokens:
                raise _GuardError(400, 'context_length_exceeded', 'prompt leaves no room for output in the selected profile')
            # Native resolve_budget clamps the requested output to the exact
            # remaining window and reports finish_reason=length on exhaustion.
            # Do not reject a nonempty 64K prompt just because its default
            # output budget is now 64K; input itself is never truncated.
        # When no exact callback is supplied, the pinned native checkpoint
        # renderer/tokenizer remains authoritative: it rejects input overflow
        # and signals output clamp with finish_reason=length. Never estimate
        # Qwen tokens by characters or a generic GPT tokenizer.
        return profile

    async def _profile_inference(self, request):
        if self.client is None:
            return _control_body(503, 'manager_unavailable', 'manager proxy is starting')
        bearer = 'Bearer ' + self.backend_token if self.backend_token else ''
        if not ((bearer and hmac.compare_digest(request.headers.get('authorization', ''), bearer))
                or (self.backend_token and hmac.compare_digest(request.headers.get('x-api-key', ''), self.backend_token))):
            return _control_body(401, 'unauthorized', 'inference must use the authenticated LiteLLM route')
        started = asyncio.get_running_loop().time()
        disconnect = ticket = response = None
        delegated = complete = False
        try:
            body, value = await asyncio.wait_for(self._body(request), timeout=30)
            disconnect = asyncio.create_task(_wait_request_disconnect(request))
            profile = await _stage(self._profile(value), deadline=started + 30, disconnect=disconnect)
            if value.get('max_tokens') is None and value.get('max_completion_tokens') is None:
                value['max_tokens'] = profile.default_output_tokens
            body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
            deadline = started + profile.total_deadline_seconds
            ticket = await self._acquire(profile, deadline, disconnect)
            await self._prepare(ticket, disconnect)
            proxy_headers = _filter_headers(request.headers, request_side=True)
            proxy_headers['Authorization'] = 'Bearer ' + self.backend_token
            target = self.manager_url + request.url.path
            built = self.client.build_request(request.method, target, headers=proxy_headers, content=body)
            ticket.forwarded = True
            response = await _stage(self.client.send(built, stream=True), deadline=deadline, disconnect=disconnect)
            ticket.stage = 'active'
            headers = _filter_headers(response.headers, request_side=False)
            headers['X-LiliuxFlow-Context-Tokens'] = str(profile.context_tokens)
            headers['X-LiliuxFlow-Profile'] = profile.profile_id

            async def chunks():
                nonlocal complete
                iterator = response.aiter_raw().__aiter__()
                terminal_seen = False
                tail = b''
                try:
                    while True:
                        try:
                            chunk = await _stage(iterator.__anext__(), deadline=deadline, disconnect=disconnect)
                        except StopAsyncIteration:
                            complete = response.is_success
                            return
                        if 'text/event-stream' in response.headers.get('content-type', ''):
                            examined = tail + chunk
                            terminal_seen = terminal_seen or b'data: [DONE]\n' in examined or b'data: [DONE]\r\n' in examined
                            tail = examined[-32:]
                        yield chunk
                        if terminal_seen:
                            complete = response.is_success
                            return
                except _CallerGone:
                    return
                except _GuardError as error:
                    if not terminal_seen and 'text/event-stream' in response.headers.get('content-type', ''):
                        packet = {'error': {'type': error.code, 'message': error.detail}}
                        yield ('data: ' + json.dumps(packet, separators=(',', ':')) + '\n\ndata: [DONE]\n\n').encode()
                    return
                except httpx.HTTPError:
                    if not terminal_seen and 'text/event-stream' in response.headers.get('content-type', ''):
                        yield b'data: {"error":{"type":"manager_unavailable","message":"upstream stream failed"}}\n\ndata: [DONE]\n\n'
                    return

            async def cleanup():
                await self._finish(ticket, response, complete=complete)

            delegated = True
            return _ProfileStreamingResponse(chunks(), disconnect=disconnect, cleanup=cleanup,
                                             status_code=response.status_code, headers=headers, media_type=None)
        except _CallerGone:
            return _ClientGoneResponse()
        except ClientDisconnect:
            return _ClientGoneResponse()
        except _GuardError as error:
            result = _control_body(error.status, error.code, error.detail)
            if error.status == 429:
                result.headers['Retry-After'] = '1'
            return result
        except TimeoutError:
            return _control_body(408, 'request_body_timeout', 'bounded request body admission timed out')
        except httpx.HTTPError:
            return _control_body(503, 'manager_unavailable', 'manager request failed')
        except (UnicodeError, ValueError):
            return _control_body(400, 'invalid_request', 'request JSON encoding is invalid')
        finally:
            if not delegated:
                if disconnect is not None:
                    disconnect.cancel()
                    await asyncio.gather(disconnect, return_exceptions=True)
                if ticket is not None:
                    task = asyncio.create_task(self._finish(ticket, response, complete=False))
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise

    async def catalog(self):
        try:
            self._resident_profile = await self._running(asyncio.get_running_loop().time() + 3)
        except _GuardError:
            self._resident_profile = None
        status = await self.status()
        queued = status['queued_models']
        active = status['active_model']
        loaded = status['resident_model']
        profiles = []
        for profile in self.registry.profiles:
            enabled = profile.production_enabled
            state = ('disabled' if not enabled else self._ticket.stage if self._ticket and active == profile.public_alias else
                     'queued' if profile.public_alias in queued else
                     'ready' if loaded == profile.public_alias else 'unloaded')
            profiles.append({'model': profile.public_alias, 'profile_id': profile.profile_id,
                             'context_tokens': profile.context_tokens, 'enabled': enabled,
                             'loaded': loaded == profile.public_alias, 'state': state,
                             'queued': queued.count(profile.public_alias)})
        return {'profiles': profiles, 'active_model': active, 'queued_models': queued,
                'pending_count': status['pending_inferences'], 'admission_paused': status['admission_paused'],
                'pause_reason': status['pause_reason']}

    async def _profile_lifecycle(self, request):
        allowed, reason = await self.begin_lifecycle_mutation()
        if not allowed:
            return _control_body(409, 'inference_busy' if reason == 'busy' else 'maintenance',
                                 'model generation or cleanup is active')
        safe = False
        selected = current = None
        deadline = asyncio.get_running_loop().time() + self.cleanup_seconds
        try:
            current = await self._running(deadline)
            path = request.url.path
            if '/load/' in path:
                try:
                    selected = self.registry.resolve(path.split('/load/', 1)[1])
                except ValueError:
                    safe = True
                    return _control_body(503, 'profile_disabled', 'manual Load requires an enabled context profile')
                if current is not None and current.profile_id != selected.profile_id:
                    await self._stop_and_prove(current, deadline)
                await self._proof(selected, 'before_forward', deadline=deadline)
                # fcefa7 has no native Load API. Only this explicit guarded
                # operation may use its supported upstream health dispatch to
                # start a model; ordinary health/catalog polling never does.
                value = await self._manager_json('GET', '/upstream/' + selected.public_alias + '/health', deadline=deadline)
                while isinstance(value, dict) and value.get('state') in ('loading', 'reloading'):
                    await _stage(asyncio.sleep(.025), deadline=deadline)
                    value = await self._manager_json('GET', '/upstream/' + selected.public_alias + '/health', deadline=deadline)
                if not isinstance(value, dict) or value.get('status') != 'ok' or value.get('state') != 'ready':
                    raise _GuardError(503, 'profile_not_ready', 'selected native context profile is not ready')
                loaded = await self._running(deadline)
                if loaded is None or loaded.profile_id != selected.profile_id:
                    raise _GuardError(503, 'switch_failed', 'manual Load did not load the selected profile')
                await self._proof(selected, 'before_forward', deadline=deadline)
                self._resident_profile = selected
                safe = True
                return JSONResponse(value, headers={'Cache-Control': 'no-store'})
            if '/unload' in path:
                if '/unload/' in path:
                    alias = path.split('/unload/', 1)[1]
                    try:
                        target = self.registry.resolve(alias)
                    except ValueError:
                        safe = True
                        return _control_body(404, 'model_not_found', 'unload model is outside the enabled registry')
                    if current is not None and current.profile_id != target.profile_id:
                        safe = True
                        return JSONResponse({'unloaded': alias}, headers={'Cache-Control': 'no-store'})
                await self._stop_and_prove(current or self.registry.default, deadline)
                safe = True
                return JSONResponse({'unloaded': True}, headers={'Cache-Control': 'no-store'})
            safe = True
            return _control_body(403, 'trusted_configuration_required', 'runtime changes require a trusted configuration')
        except _GuardError as error:
            return _control_body(error.status, error.code, error.detail)
        finally:
            if safe:
                await self.end_lifecycle_mutation()
            else:
                # Failed lifecycle/unknown cleanup keeps admission paused.
                async with self._lock:
                    self._pause_reason = 'resource_release_unverified'

    async def proxy_http(self, request, *, inference):
        if request.url.path.startswith(('/upstream/', '/comfyui')):
            return _control_body(403, 'route_not_allowed', 'direct upstream routes cannot bypass profile admission')
        if inference:
            if request.url.path not in ('/v1/chat/completions', '/v1/completions'):
                return _control_body(400, 'unsupported_route', 'this model serves only chat and text completions')
            return await self._profile_inference(request)
        if is_lifecycle_mutation(request.method, request.url.path):
            busy = await self.status()
            if busy['active_inferences'] or busy['pending_inferences']:
                return _control_body(409, 'inference_busy', 'model generation or cleanup is active')
            # Runtime configuration changes need source and trust revalidation; user UI
            # cannot introduce untrusted commands or a fourth model route.
            if not request.url.path.startswith('/api/models/') and request.url.path != '/unload':
                return _control_body(403, 'trusted_configuration_required', 'runtime changes require a trusted configuration')
            return await self._profile_lifecycle(request)
        return await super().proxy_http(request, inference=False)

    async def proxy_websocket(self, websocket):
        if (is_inference_request('POST', websocket.url.path)
            or is_lifecycle_mutation('GET', websocket.url.path)
            or is_lifecycle_mutation('POST', websocket.url.path)
            or websocket.url.path.startswith(('/upstream/', '/comfyui'))):
            await websocket.close(code=1008, reason='use the authenticated HTTP profile route')
            return
        await super().proxy_websocket(websocket)


def create_app(
    manager_url: str | None = None,
    control_token: str | None = None,
    backend_token: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    registry: Any = None,
    resource_probe: Callable | None = None,
    context_admission: Callable | None = None,
    validation_aliases: tuple[str, ...] = (),
) -> FastAPI:
    manager_url = manager_url or os.environ.get("RECOVERY_MANAGER_UPSTREAM", "")
    control_token = control_token if control_token is not None else os.environ.get("RECOVERY_GUARD_CONTROL_TOKEN", "")
    backend_token = backend_token if backend_token is not None else os.environ.get("RECOVERY_GUARD_BACKEND_TOKEN", "")
    options = dict(manager_url=manager_url, control_token=control_token, backend_token=backend_token, transport=transport)
    guard = (_LifecycleGuard(**options) if registry is None else
             _ContextLifecycleGuard(**options, registry=registry, resource_probe=resource_probe,
                                    context_admission=context_admission, validation_aliases=validation_aliases))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with guard.lifespan():
            yield

    app = FastAPI(title="Loopback llama-swap lifecycle guard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.lifecycle_guard = guard

    @app.get("/__recovery/status")
    async def recovery_status():
        return await guard.status()

    @app.get('/api/context-profiles')
    async def context_profiles():
        if registry is None:
            return _control_body(404, 'not_found', 'context registry is unavailable')
        return JSONResponse(await guard.catalog(), headers={'Cache-Control': 'no-store'})

    async def control(request: Request, *, unlock: bool) -> JSONResponse:
        token = request.headers.get("X-Recovery-Control-Token", "")
        status, value = await guard.maintenance(token=token, unlock=unlock)
        return JSONResponse(status_code=status, content=value, headers={"Cache-Control": "no-store"})

    @app.post("/__recovery/maintenance/lock")
    async def maintenance_lock(request: Request):
        return await control(request, unlock=False)

    @app.post("/__recovery/maintenance/unlock")
    async def maintenance_unlock(request: Request):
        return await control(request, unlock=True)

    @app.post('/__recovery/maintenance/recover')
    async def maintenance_recover(request: Request):
        if registry is None:
            return _control_body(409, 'cleanup_recovery_not_available', 'context cleanup recovery is unavailable')
        status, value = await guard.recover_cleanup(token=request.headers.get('X-Recovery-Control-Token', ''))
        return JSONResponse(status_code=status, content=value, headers={'Cache-Control': 'no-store'})

    @app.api_route("/", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy_root(request: Request):
        return await guard.proxy_http(request, inference=is_inference_request(request.method, request.url.path))

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy_path(request: Request, path: str):
        if path.startswith("__recovery/"):
            return _control_body(404, "not_found", "route not found")
        return await guard.proxy_http(request, inference=is_inference_request(request.method, request.url.path))

    @app.websocket("/")
    async def proxy_websocket_root(websocket: WebSocket):
        await guard.proxy_websocket(websocket)

    @app.websocket("/{path:path}")
    async def proxy_websocket_path(websocket: WebSocket, path: str):
        if path.startswith("__recovery/"):
            await websocket.close(code=1008, reason="route not found")
            return
        await guard.proxy_websocket(websocket)

    return app


def main() -> int:
    import uvicorn

    host = os.environ.get("RECOVERY_GUARD_HOST", "127.0.0.1")
    port_value = os.environ.get("RECOVERY_GUARD_PORT", "8080")
    manager_url = os.environ.get("RECOVERY_MANAGER_UPSTREAM", "")
    backend_token = os.environ.get("RECOVERY_GUARD_BACKEND_TOKEN", "")
    if host != "127.0.0.1" or not port_value.isdigit() or not 1 <= int(port_value) <= 65535:
        raise SystemExit("guard must bind to an explicit loopback port")
    if not manager_url or not backend_token:
        raise SystemExit("private manager upstream and backend API token are required")
    app = create_app(manager_url=manager_url, backend_token=backend_token)
    # The normal owner Stop path has already closed inference admission,
    # verified zero active requests, and unloaded the model before signaling
    # this process. Bound lingering stock UI event streams so they cannot keep
    # the guard process alive forever during that otherwise-idle shutdown.
    uvicorn.run(
        app,
        host=host,
        port=int(port_value),
        access_log=False,
        log_level="warning",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
