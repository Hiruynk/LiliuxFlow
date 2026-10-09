from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import math
from typing import Any, AsyncIterator, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict



MAX_RESPONSE_SCHEMA_BYTES = 32 * 1024
MAX_RESPONSE_SCHEMA_DEPTH = 12
MAX_RESPONSE_SCHEMA_PROPERTIES = 128
MAX_RESPONSE_SCHEMA_ENUM_MEMBERS = 64
MAX_RESPONSE_SCHEMA_ARRAY_ITEMS = 1024
MAX_MEDIA_BYTES = 5 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 5 * 1024 * 1024
MAX_MEDIA_IMAGES = 1
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
MAX_UPSTREAM_SSE_EVENT_BYTES = 1024 * 1024
MAX_NONSTREAM_CONTENT_BYTES = 8 * 1024 * 1024
MAX_INFLIGHT_CHAT_REQUESTS = 4
MEDIA_PREPROCESS_CONCURRENCY = 1
FIXED_CONTEXT_PROFILE_TOKENS = 65_536
MAX_OUTPUT_BUDGET_TOKENS = 65_536
DEFAULT_OUTPUT_BUDGET_TOKENS = MAX_OUTPUT_BUDGET_TOKENS
MAX_TOTAL_REQUEST_SECONDS = 3672
LEGACY_METRIC_KEYS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "eval_duration",
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str
    messages: list[dict[str, Any]]
    stream: bool = True
    options: dict[str, Any] | None = None
    think: bool | str | None = None
    format: dict[str, Any] | Literal["json"] | None = None
    keep_alive: str | int | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(frozen=True)
class CompatModelProfile:
    """A detached immutable selection for one public model request."""

    public_alias: str
    context_tokens: int
    total_deadline_seconds: int
    default_output_tokens: int = DEFAULT_OUTPUT_BUDGET_TOKENS
    profile_id: str | None = None
    engine_id: str | None = None
    mtp_drafts: int = 0
    kv_cache: str | None = None
    max_batch: int | None = None

    def public_info(self):
        value={'model':self.public_alias,'context_tokens':self.context_tokens,'default_output_tokens':self.default_output_tokens,'total_deadline_seconds':self.total_deadline_seconds,'mtp_drafts':self.mtp_drafts}
        for key in ('profile_id','engine_id','kv_cache','max_batch'):
            item=getattr(self,key)
            if item is not None:value[key]=item
        return value

@dataclass(frozen=True)
class Settings:
    litellm_base_url: str
    public_alias: str
    runtime_model_id: str
    context_limit_tokens: int
    checkpoint_revision: str | None
    checkpoint_manifest_sha256: str | None
    checkpoint_file_count: int
    stream_test_interval_seconds: float = 0.25
    staging_profile_path: str | None = None
    staging_profile_sha256: str | None = None
    profile_id: str | None = None
    profiles: tuple[CompatModelProfile, ...] = ()
    model_configured: bool = True

    def __post_init__(self) -> None:
        if type(self.model_configured) is not bool or not self.model_configured and (self.profiles or self.checkpoint_revision is not None or self.checkpoint_manifest_sha256 is not None or self.checkpoint_file_count != 0):
            raise ValueError("No-model Compat settings must not claim a checkpoint or enabled profile")
        if not isinstance(self.profiles, tuple) or any(not isinstance(p, CompatModelProfile) for p in self.profiles):
            raise ValueError("Compat profiles must be an immutable tuple")
        aliases = [p.public_alias for p in self.profiles]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Compat profile aliases must be unique")
        if self.profiles and self.public_alias not in aliases:
            raise ValueError("Compat default profile must be enabled")
        targets = {
            "qwen3.8-flash-next-lily-q4-64k": (65536, 3672),
            "qwen3.8-flash-next-lily-q4-128k": (131072, 4272),
            "qwen3.8-flash-next-lily-q4-262k": (262144, 4872),
            "qwen3.8-flash-next-lily-q4-mtp2-64k": (65536, 3672),
        }
        if any(p.public_alias not in targets or (p.context_tokens, p.total_deadline_seconds) != targets[p.public_alias]
               or p.default_output_tokens != DEFAULT_OUTPUT_BUDGET_TOKENS for p in self.profiles):
            raise ValueError("Compat profiles differ from canonical context policy")
        for p in self.profiles:
            if p.public_alias=='qwen3.8-flash-next-lily-q4-mtp2-64k':
                if type(p.mtp_drafts) is not int or type(p.max_batch) is not int or (p.profile_id,p.engine_id,p.mtp_drafts,p.kv_cache,p.max_batch)!=('ctx64k-mtp2','latest13f-defer-pc123-mtp2-opt64k',2,'bf16',1):raise ValueError('Compat opt-in engine profile differs')
            elif type(p.mtp_drafts) is not int or p.mtp_drafts!=0:raise ValueError('Established Compat profiles must retain MTP0')
        if self.public_alias=='qwen3.8-flash-next-lily-q4-mtp2-64k':raise ValueError('The opt-in profile cannot replace the established default')

    def select_profile(self, alias: str) -> CompatModelProfile | None:
        if not self.model_configured:
            return None
        if self.profiles:
            return next((profile for profile in self.profiles if profile.public_alias == alias), None)
        if alias == self.public_alias:
            return CompatModelProfile(alias, self.context_limit_tokens, MAX_TOTAL_REQUEST_SECONDS)
        return None



@dataclass(frozen=True)
class Caller:
    authorization: str
    allowed_models: frozenset[str]


class ProtocolInputError(ValueError):
    pass


class NonstreamUpstreamError(Exception):
    """A sanitized typed error from the internal streaming text aggregator."""

    def __init__(self, status_code: int):
        super().__init__("upstream streaming response did not satisfy the text contract")
        self.status_code = status_code


class ChatRequestAdmissionMiddleware:
    """Bound chat body buffering and active preprocessing before Pydantic decode."""

    def __init__(self, app: Any):
        self.app = app
        self._chat_slots = asyncio.Semaphore(MAX_INFLIGHT_CHAT_REQUESTS)

    @staticmethod
    async def _reject(send: Any, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/api/chat" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        scope["compat_received_at"] = asyncio.get_running_loop().time()

        # This check is synchronous with the following acquire, so the last
        # available slot cannot be oversubscribed by another task on the loop.
        if self._chat_slots.locked():
            await self._reject(send, 429, "Compat chat admission limit reached")
            return
        await self._chat_slots.acquire()
        try:
            length_headers = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
            if len(length_headers) > 1:
                await self._reject(send, 400, "ambiguous Content-Length")
                return
            if length_headers:
                try:
                    declared_length = int(length_headers[0])
                except ValueError:
                    await self._reject(send, 400, "invalid Content-Length")
                    return
                if declared_length < 0:
                    await self._reject(send, 400, "invalid Content-Length")
                    return
                if declared_length > MAX_REQUEST_BODY_BYTES:
                    await self._reject(send, 413, "chat request body exceeds 8 MiB")
                    return

            body_buffer = bytearray()
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                if message.get("type") != "http.request":
                    continue
                chunk = message.get("body", b"")
                body_buffer.extend(chunk)
                if len(body_buffer) > MAX_REQUEST_BODY_BYTES:
                    await self._reject(send, 413, "chat request body exceeds 8 MiB")
                    return
                if not message.get("more_body", False):
                    break

            buffered_body = bytes(body_buffer)
            body_replayed = False

            async def replay_receive() -> dict[str, Any]:
                nonlocal body_replayed
                if not body_replayed:
                    body_replayed = True
                    return {"type": "http.request", "body": buffered_body, "more_body": False}
                return await receive()

            await self.app(scope, replay_receive, send)
        finally:
            self._chat_slots.release()


def _sse_packet(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_headers(request: Request) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    if request.scope.get("http_version") in {"1.0", "1.1"}:
        headers["Connection"] = "keep-alive"
    return headers


def _protocol_body(message: str) -> dict[str, Any]:
    return {"done": True, "error": message[:300], "error_type": "protocol"}


def _unavailable_body() -> dict[str, Any]:
    return {"done": True, "error": "upstream unavailable", "error_type": "unavailable"}


def _safe_key_name(value: object) -> str:
    return "".join(char for char in str(value) if char.isascii() and (char.isalnum() or char in "_.-"))[:64]


def _validate_format(value: Any) -> dict[str, Any] | Literal["json"] | None:
    if value is None or value == "json":
        return value
    if not isinstance(value, dict):
        raise ValueError("format must be 'json' or a JSON Schema object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("format must be a JSON-safe schema") from exc
    if not encoded or len(encoded) > MAX_RESPONSE_SCHEMA_BYTES:
        raise ValueError("format schema is empty or too large")

    property_count = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_RESPONSE_SCHEMA_DEPTH:
            raise ValueError("format schema nesting is too deep")
        if isinstance(node, dict):
            reference = node.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise ValueError("remote format schema references are forbidden")
                raise ValueError("recursive format schema references are forbidden")
            if any(key in node for key in ("$dynamicRef", "$recursiveRef")):
                raise ValueError("dynamic format schema references are forbidden")
            properties = node.get("properties")
            if properties is not None:
                if not isinstance(properties, dict):
                    raise ValueError("format schema properties must be an object")
                property_count += len(properties)
                if property_count > MAX_RESPONSE_SCHEMA_PROPERTIES:
                    raise ValueError("format schema has too many properties")
            enum_members = node.get("enum")
            if enum_members is not None:
                if not isinstance(enum_members, list):
                    raise ValueError("format schema enum must be an array")
                if len(enum_members) > MAX_RESPONSE_SCHEMA_ENUM_MEMBERS:
                    raise ValueError("format schema enum is too large")
            max_items = node.get("maxItems")
            if max_items is not None and (
                isinstance(max_items, bool)
                or not isinstance(max_items, int)
                or not 0 <= max_items <= MAX_RESPONSE_SCHEMA_ARRAY_ITEMS
            ):
                raise ValueError("format schema maxItems is invalid or too large")
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    return value


def _finite_number(name: str, value: Any, *, minimum: float | None = None, maximum: float | None = None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ProtocolInputError(f"options.{name} must be a finite number")
    if minimum is not None and float(value) < minimum:
        raise ProtocolInputError(f"options.{name} is below its supported range")
    if maximum is not None and float(value) > maximum:
        raise ProtocolInputError(f"options.{name} is above its supported range")
    return value


def _map_options(options: dict[str, Any] | None, *, context_limit_tokens: int,
                 default_output_tokens: int = DEFAULT_OUTPUT_BUDGET_TOKENS) -> dict[str, Any]:
    if options is not None and not isinstance(options, dict):
        raise ProtocolInputError("options must be an object")
    mapped: dict[str, Any] = {"max_tokens": default_output_tokens}
    if options is None:
        return mapped
    for name, value in options.items():
        if name == "temperature":
            mapped[name] = _finite_number(name, value, minimum=0, maximum=2)
        elif name == "top_p":
            mapped[name] = _finite_number(name, value, minimum=0, maximum=1)
        elif name == "min_p":
            mapped[name] = _finite_number(name, value, minimum=0, maximum=1)
        elif name == "top_k":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2048:
                raise ProtocolInputError("options.top_k must be an integer from 0 to 2048")
            mapped[name] = value
        elif name == "seed":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                raise ProtocolInputError("options.seed must be a non-negative integer")
            mapped[name] = value
        elif name == "num_predict":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ProtocolInputError("options.num_predict must be a positive integer")
            if value > min(context_limit_tokens, MAX_OUTPUT_BUDGET_TOKENS):
                raise ProtocolInputError("options.num_predict cannot exceed the 65536-token output limit")
            mapped["max_tokens"] = value
        elif name == "repeat_penalty":
            mapped["repetition_penalty"] = _finite_number(name, value, minimum=0.01, maximum=10)
        elif name in {"presence_penalty", "frequency_penalty"}:
            mapped[name] = _finite_number(name, value, minimum=-2, maximum=2)
        elif name == "stop":
            if isinstance(value, str):
                if len(value) > 1024:
                    raise ProtocolInputError("options.stop is too long")
                mapped[name] = value
            elif (
                isinstance(value, list)
                and 1 <= len(value) <= 4
                and all(isinstance(item, str) and len(item) <= 1024 for item in value)
            ):
                mapped[name] = value
            else:
                raise ProtocolInputError("options.stop must be a string or up to four strings")
        elif name == "num_ctx":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProtocolInputError("options.num_ctx must be the integer fixed context profile")
            if value != context_limit_tokens or value != FIXED_CONTEXT_PROFILE_TOKENS:
                raise ProtocolInputError(
                    "only options.num_ctx=65536 is supported; smaller per-request context limits are not enforced"
                )
            # This exact value selects the only registered alias profile; no
            # request-level context override is sent to LiteLLM.
        else:
            safe_name = _safe_key_name(name) or "unknown"
            raise ProtocolInputError(f"unsupported option: {safe_name}")
    return mapped


def _messages_with_media(messages: list[dict[str, Any]], media: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = [dict(message) for message in messages]
    image_count = 0
    for message in prepared:
        for field in ("images", "audio", "video", "audio_url", "video_url", "image_url"):
            value = message.get(field)
            if value is not None and value != [] and value != {}:
                raise ProtocolInputError(f"message-level {field} media is unsupported; use verified PNG media")
        content = message.get("content")
        if isinstance(content, str) or content is None:
            continue
        if not isinstance(content, list):
            raise ProtocolInputError("message content must be text or a list of verified text/PNG parts")
        for part in content:
            if not isinstance(part, dict):
                raise ProtocolInputError("message content list contains an unsupported part")
            part_type = part.get("type")
            if part_type == "text":
                if set(part) - {"type", "text"}:
                    raise ProtocolInputError("text content parts cannot contain hidden media fields")
                if not isinstance(part.get("text"), str):
                    raise ProtocolInputError("text content part must contain a string")
                continue
            if part_type != "image_url":
                raise ProtocolInputError("audio, video, and unknown content parts are unsupported")
            if set(part) - {"type", "image_url"}:
                raise ProtocolInputError("image content part contains unsupported fields")
            image_url = part.get("image_url")
            data_url = image_url.get("url") if isinstance(image_url, dict) else None
            if isinstance(image_url, dict) and set(image_url) - {"url", "detail"}:
                raise ProtocolInputError("image_url contains unsupported fields")
            if not isinstance(data_url, str):
                raise ProtocolInputError("image_url must use a verified PNG data URL")
            _decode_png_data_url(data_url)
            image_count += 1
            if image_count > MAX_MEDIA_IMAGES:
                raise ProtocolInputError("only one PNG image per request is currently supported")

    if not media:
        return prepared
    if len(media) > 8:
        raise ProtocolInputError("at most eight legacy media entries are supported")
    if len(media) > MAX_MEDIA_IMAGES or image_count + len(media) > MAX_MEDIA_IMAGES:
        raise ProtocolInputError("only one PNG image per request is currently supported")

    item = media[0]
    kind = str(item.get("kind", "") or "").strip().casefold()
    if kind in {"audio", "video"}:
        raise ProtocolInputError(f"media kind '{kind}' is unsupported by the approved backend profile")
    if kind != "image":
        raise ValueError("unsupported media kind")
    encoded = str(item.get("data_base64", "") or "")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid media base64") from exc
    _validate_png_bytes(data)

    target = next((message for message in reversed(prepared) if message.get("role") == "user"), None)
    if target is None:
        target = {"role": "user", "content": "Understand the attached media and respond."}
        prepared.append(target)
    content = target.get("content")
    if isinstance(content, str):
        parts: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
    elif isinstance(content, list):
        parts = list(content)
    elif content is None:
        parts = []
    else:
        raise ProtocolInputError("image input requires string or list user content")
    parts.append(
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")},
        }
    )
    target["content"] = parts
    return prepared


def _validate_png_bytes(data: bytes) -> None:
    if not data or len(data) > MAX_MEDIA_BYTES or len(data) > MAX_TOTAL_MEDIA_BYTES:
        raise ValueError("media payload is empty or too large")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProtocolInputError("only PNG media was verified by the approved backend contract")


def _decode_png_data_url(value: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not value.startswith(prefix):
        raise ProtocolInputError("remote URLs and non-PNG data URLs are unsupported")
    try:
        data = base64.b64decode(value[len(prefix) :], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid media base64") from exc
    _validate_png_bytes(data)
    return data


def _legacy_metrics(chunk: dict[str, Any]) -> dict[str, int]:
    metrics: dict[str, int] = {}
    for key in LEGACY_METRIC_KEYS:
        value = chunk.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metrics[key] = value
    return metrics


def _error_type_for_status(status_code: int) -> Literal["protocol", "unavailable"]:
    return "protocol" if status_code in {400, 404, 405, 409, 415, 422} else "unavailable"


def _http_error_response(status_code: int) -> JSONResponse:
    if status_code in {401, 403, 429}:
        return JSONResponse(status_code=status_code, content={"detail": {401: "Unauthorized", 403: "Forbidden", 429: "Rate limit exceeded"}[status_code]})
    error_type = _error_type_for_status(status_code)
    payload = {"done": True, "error": "upstream protocol error" if error_type == "protocol" else "upstream unavailable", "error_type": error_type}
    return JSONResponse(status_code=400 if error_type == "protocol" else 503, content=payload)


async def _sse_data_lines(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    data_bytes = 0
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            data_bytes = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field != "data" or not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        data_bytes += len(value.encode("utf-8")) + (1 if data_lines else 0)
        if data_bytes > MAX_UPSTREAM_SSE_EVENT_BYTES:
            raise NonstreamUpstreamError(503)
        data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


async def _bounded_sse_data_lines(response: httpx.Response, deadline: float) -> AsyncIterator[str]:
    async with asyncio.timeout_at(deadline):
        async for line in _sse_data_lines(response):
            yield line


def _stream_error_status(error: dict[str, Any]) -> int:
    kind = error.get("type")
    code = error.get("code")
    combined = " ".join(str(value).lower() for value in (kind, code) if isinstance(value, str))
    if "rate" in combined or "429" in combined:
        return 429
    if "unauthor" in combined or "401" in combined:
        return 401
    if "forbidden" in combined or "403" in combined:
        return 403
    if any(marker in combined for marker in ("invalid_request", "context_length", "bad_request", "400")):
        return 400
    return 503


async def _aggregate_openai_stream_text(response: httpx.Response, *, deadline: float) -> tuple[str, str]:
    content_chunks: list[str] = []
    content_bytes = 0
    finish_reason: str | None = None
    got_done = False
    try:
        async for raw in _bounded_sse_data_lines(response, deadline):
            if raw.strip() == "[DONE]":
                got_done = True
                break
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                raise NonstreamUpstreamError(503) from None
            if not isinstance(event, dict):
                raise NonstreamUpstreamError(503)
            error = event.get("error")
            if isinstance(error, dict):
                raise NonstreamUpstreamError(_stream_error_status(error))
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                raise NonstreamUpstreamError(503)
            delta = choice.get("delta")
            if delta is not None and not isinstance(delta, dict):
                raise NonstreamUpstreamError(503)
            if isinstance(delta, dict):
                # Reasoning is deliberately ignored for the legacy pure-text response.
                content = delta.get("content", "")
                if content is None:
                    content = ""
                if not isinstance(content, str):
                    raise NonstreamUpstreamError(400)
                content_bytes += len(content.encode("utf-8"))
                if content_bytes > MAX_NONSTREAM_CONTENT_BYTES:
                    raise NonstreamUpstreamError(503)
                content_chunks.append(content)
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str) or reason not in {"stop", "length", "content_filter"} or finish_reason is not None:
                    raise NonstreamUpstreamError(503)
                finish_reason = reason
        if not got_done or finish_reason is None:
            raise NonstreamUpstreamError(503)
        return "".join(content_chunks), finish_reason
    except NonstreamUpstreamError:
        raise
    except TimeoutError:
        raise NonstreamUpstreamError(503) from None
    except httpx.HTTPError:
        raise NonstreamUpstreamError(503) from None
    except Exception:
        raise NonstreamUpstreamError(503) from None


async def _aggregate_text_before_disconnect(
    request: Request,
    upstream_response: httpx.Response,
    *,
    deadline: float,
) -> tuple[str, str] | None:
    aggregate_task = asyncio.create_task(_aggregate_openai_stream_text(upstream_response, deadline=deadline))
    disconnect_task = asyncio.create_task(_wait_request_disconnect(request))
    try:
        async with asyncio.timeout_at(deadline):
            done, _pending = await asyncio.wait(
                {aggregate_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        # When both complete in one loop turn, the caller disconnect wins.
        if disconnect_task in done:
            aggregate_task.cancel()
            await asyncio.gather(aggregate_task, return_exceptions=True)
            return None
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await aggregate_task
    except BaseException:
        disconnect_task.cancel()
        aggregate_task.cancel()
        await asyncio.gather(disconnect_task, aggregate_task, return_exceptions=True)
        raise
    finally:
        await upstream_response.aclose()


async def _legacy_stream(response: httpx.Response, *, deadline: float | None = None) -> AsyncIterator[bytes]:
    finish_reason = ""
    metrics: dict[str, int] = {}
    got_done = False
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + MAX_TOTAL_REQUEST_SECONDS
    try:
        async for raw in _bounded_sse_data_lines(response, deadline):
            if raw.strip() == "[DONE]":
                got_done = True
                break
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                yield _sse_packet({"done": True, "error": "malformed upstream event", "error_type": "unavailable"})
                return
            if not isinstance(event, dict):
                yield _sse_packet({"done": True, "error": "malformed upstream event", "error_type": "unavailable"})
                return
            if isinstance(event.get("error"), dict):
                yield _sse_packet({"done": True, "error": "upstream rejected request", "error_type": "protocol"})
                return
            metrics.update(_legacy_metrics(event))
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            thinking = delta.get("reasoning_content", delta.get("reasoning", ""))
            content = delta.get("content", "")
            if not isinstance(thinking, str):
                thinking = ""
            if not isinstance(content, str):
                content = ""
            if thinking or content:
                yield _sse_packet({"message": {"thinking": thinking, "content": content}, "done": False})
            reason = choice.get("finish_reason")
            if isinstance(reason, str) and reason:
                finish_reason = reason[:64]
        if not got_done:
            yield _sse_packet({"done": True, "error": "upstream stream ended before completion", "error_type": "unavailable"})
            return
        terminal: dict[str, Any] = {"message": {"thinking": "", "content": ""}, "done": True, "done_reason": finish_reason}
        if metrics:
            terminal["metrics"] = metrics
        yield _sse_packet(terminal)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        yield _sse_packet({"done": True, "error": "upstream request timed out", "error_type": "unavailable"})
    except Exception:
        yield _sse_packet({"done": True, "error": "upstream unavailable", "error_type": "unavailable"})
    finally:
        await response.aclose()


class _ClientGoneResponse(Response):
    """Finish an ASGI request without trying to write after caller disconnect."""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        return None


class _UpstreamStreamingResponse(StreamingResponse):
    """Close LiteLLM's response even if Starlette's ASGI send path fails."""

    def __init__(self, content: Any, upstream_response: httpx.Response, **kwargs: Any):
        super().__init__(content, **kwargs)
        self._upstream_response = upstream_response

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream_response.aclose()


async def _wait_request_disconnect(request: Request) -> None:
    while True:
        message = await request.receive()
        if message.get("type") == "http.disconnect":
            return


async def _cancel_task_and_close_response(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    outcome = await asyncio.gather(task, return_exceptions=True)
    if outcome and isinstance(outcome[0], httpx.Response):
        await outcome[0].aclose()


async def _send_before_disconnect(
    request: Request,
    upstream: httpx.AsyncClient,
    built: httpx.Request,
    *,
    deadline: float,
) -> httpx.Response | None:
    send_task = asyncio.create_task(upstream.send(built, stream=True))
    disconnect_task = asyncio.create_task(_wait_request_disconnect(request))
    try:
        async with asyncio.timeout_at(deadline):
            done, _pending = await asyncio.wait(
                {send_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        # If both become ready in one loop turn, the gone caller takes priority.
        if disconnect_task in done:
            await _cancel_task_and_close_response(send_task)
            return None
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await send_task
    except BaseException:
        disconnect_task.cancel()
        await _cancel_task_and_close_response(send_task)
        await asyncio.gather(disconnect_task, return_exceptions=True)
        raise


async def _read_before_disconnect(
    request: Request,
    upstream_response: httpx.Response,
    *,
    deadline: float,
) -> bytes | None:
    read_task = asyncio.create_task(upstream_response.aread())
    disconnect_task = asyncio.create_task(_wait_request_disconnect(request))
    try:
        async with asyncio.timeout_at(deadline):
            done, _pending = await asyncio.wait(
                {read_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        if disconnect_task in done:
            await _cancel_task_and_close_response(read_task)
            return None
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        return await read_task
    except BaseException:
        disconnect_task.cancel()
        await _cancel_task_and_close_response(read_task)
        await asyncio.gather(disconnect_task, return_exceptions=True)
        raise
    finally:
        await upstream_response.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    if settings is None:
        raise RuntimeError("portable ReleaseTrust-validated Settings are required")
    resolved_settings = settings
    owns_client = client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.upstream = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0),
            trust_env=False,
        )
        app.state.compat_settings = resolved_settings
        app.state.media_preprocessor = asyncio.Semaphore(MEDIA_PREPROCESS_CONCURRENCY)
        try:
            yield
        finally:
            if owns_client:
                await app.state.upstream.aclose()

    app = FastAPI(
        title="Local LLM Compat API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(ChatRequestAdmissionMiddleware)

    async def authenticate(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Caller:
        if not authorization or not authorization.lower().startswith("bearer ") or not authorization[7:].strip():
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        settings = request.app.state.compat_settings
        upstream = request.app.state.upstream
        try:
            response = await upstream.get(
                settings.litellm_base_url.rstrip("/") + "/v1/models",
                headers={"Authorization": authorization},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
        if response.status_code in {401, 403, 429}:
            safe_detail = {401: "Unauthorized", 403: "Forbidden", 429: "Rate limit exceeded"}[response.status_code]
            raise HTTPException(status_code=response.status_code, detail=safe_detail)
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="Authentication service unavailable")
        try:
            body = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="Authentication service returned an invalid model list") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise HTTPException(status_code=503, detail="Authentication service returned an invalid model list")
        models = frozenset(
            item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        return Caller(authorization=authorization, allowed_models=models)

    def check_alias_access(caller: Caller, requested: str, settings: Settings) -> CompatModelProfile:
        profile = settings.select_profile(requested)
        if profile is None:
            raise HTTPException(status_code=404, detail="Model Not Found")
        if requested not in caller.allowed_models:
            raise HTTPException(status_code=403, detail="Model is not allowed for this key")
        return profile

    @app.get("/api/stream-test")
    async def stream_test(
        request: Request,
        _caller: Caller = Depends(authenticate),
    ):
        interval = request.app.state.compat_settings.stream_test_interval_seconds

        async def generate() -> AsyncIterator[bytes]:
            for index in range(1, 11):
                yield _sse_packet({"message": {"content": f"{index} "}, "done": False})
                if interval > 0:
                    await asyncio.sleep(interval)
            yield _sse_packet({"message": {"content": ""}, "done": True})

        return StreamingResponse(
                generate(),
                media_type="text/event-stream; charset=utf-8",
                headers=_sse_headers(request),
        )

    @app.get("/api/capabilities")
    async def capabilities(
        request: Request,
        model: str | None = Query(default=None),
        caller: Caller = Depends(authenticate),
    ):
        settings = request.app.state.compat_settings
        requested = str(model or "").strip()
        if not settings.model_configured:
            return {"protocol_version": "anif_llm_gateway_v2", "model_configured": False,
                    "inference_ready": False, "models": [],
                    "model": {"exists": False, "capabilities": []},
                    "features": {"checkpoint_manifest_identity": False},
                    "next_step": "Attach a verified local checkpoint to this installation"}
        profile = settings.select_profile(requested or settings.public_alias)
        default_profile = settings.select_profile(settings.public_alias)
        assert default_profile is not None
        generation_profile = profile or default_profile
        result: dict[str, Any] = {
            "protocol_version": "anif_llm_gateway_v2",
            "models": [p.public_info() for p in settings.profiles if p.public_alias in caller.allowed_models],
            "features": {
                "thinking_stream_passthrough": True,
                "done_reason": True,
                "typed_error_frames": True,
                "structured_outputs": False,
                "context_options_passthrough": False,
                "runtime_model_observability": False,
                "model_blob_identity": False,
                "checkpoint_manifest_identity": True,
                "ollama_runtime_environment": False,
            },
            "input": ["text", "image"],
            "supported_image_formats": ["image/png"],
            "max_images": MAX_MEDIA_IMAGES,
            "max_request_body_bytes": MAX_REQUEST_BODY_BYTES,
            "audio_max_seconds": None,
            "video_max_seconds": None,
            "video_max_frames": None,
            "output": ["text"],
            "context_options_passthrough": False,
            "generation": {
                "num_predict_supported": True,
                "num_predict_maps_to": "max_tokens",
                "default_num_predict": generation_profile.default_output_tokens,
                "maximum_requested_num_predict": min(generation_profile.context_tokens, MAX_OUTPUT_BUDGET_TOKENS),
                "thinking_tokens_included_in_output_budget": True,
                "total_deadline_seconds": generation_profile.total_deadline_seconds,
                "context_clamp_signal": "done_reason=length for SSE; X-Legacy-Done-Reason=length for pure-text nonstream",
            },
        }
        if requested:
            if profile is None:
                result["model"] = {"exists": False, "capabilities": []}
            else:
                check_alias_access(caller, requested, settings)
                result["runtime"] = {
                    "loaded": None,
                    "model": profile.public_alias,
                    "context_length": None,
                    "configured_context_limit": profile.context_tokens,
                    "size": None,
                    "size_vram": None,
                    "processor": None,
                }
                result["model"] = {
                    **profile.public_info(),
                    "requested_model": profile.public_alias,
                    "exists": True,
                    "capabilities": ["completion", "vision"],
                    "runtime_model_id": settings.runtime_model_id,
                    "backend_health": "not_probed_by_compat",
                }
        return result

    @app.get("/api/model-blob-identity")
    async def model_blob_identity(
        request: Request,
        model: str = Query(...),
        caller: Caller = Depends(authenticate),
    ):
        settings = request.app.state.compat_settings
        if settings.select_profile(model) is None:
            return {
                "requested_model": model[:256],
                "manifest_present": False,
                "model_blob_present": False,
                "model_blob_sha256": None,
                "model_blob_size": None,
                "manifest_layer_digest_matches": None,
                "checkpoint_manifest_present": False,
                "checkpoint_manifest_sha256": None,
                "checkpoint_revision": None,
                "checkpoint_file_count": None,
            }
        check_alias_access(caller, model, settings)
        return {
            "requested_model": model,
            "manifest_present": False,
            "model_blob_present": False,
            "model_blob_sha256": None,
            "model_blob_size": None,
            "manifest_layer_digest_matches": None,
            "checkpoint_manifest_present": True,
            "checkpoint_manifest_sha256": settings.checkpoint_manifest_sha256,
            "checkpoint_revision": settings.checkpoint_revision,
            "checkpoint_file_count": settings.checkpoint_file_count,
            "runtime_model_id": settings.runtime_model_id,
        }

    @app.get("/api/ollama-runtime-environment")
    async def ollama_runtime_environment(_caller: Caller = Depends(authenticate)):
        return {
            "source": "not_applicable",
            "flash_attention_enabled": None,
            "kv_cache_type": None,
        }

    @app.post("/api/chat")
    async def chat_endpoint(
        request: Request,
        req: ChatRequest,
        caller: Caller = Depends(authenticate),
    ):
        settings = request.app.state.compat_settings
        profile = settings.select_profile(req.model)
        if profile is None:
            return Response(content="Model Not Found", status_code=404, media_type="text/plain; charset=utf-8")
        if req.model not in caller.allowed_models:
            raise HTTPException(status_code=403, detail="Model is not allowed for this key")
        forbidden={'engine_id','engine_path','model_path','path','args','extra_args','memory','kv_cache','context','context_tokens','context_length','num_ctx','profile_id','max_seq','max_batch','mtp_drafts','qsa_route','qsa_scores','cache_bytes','disk_cache_bytes'}
        if forbidden.intersection(req.model_extra or {}):
            return JSONResponse(status_code=400,content=_protocol_body('Runtime overrides require a trusted installation profile'))
        received_at = request.scope.get("compat_received_at", asyncio.get_running_loop().time())
        request_deadline = received_at + profile.total_deadline_seconds
        if asyncio.get_running_loop().time() >= request_deadline:
            return JSONResponse(status_code=503, content=_unavailable_body())
        try:
            async with request.app.state.media_preprocessor:
                prepared_messages = _messages_with_media(req.messages, req.media)
            _validate_format(req.format)
            mapped_options = _map_options(
                req.options,
                context_limit_tokens=profile.context_tokens,
                default_output_tokens=profile.default_output_tokens,
            )
        except ProtocolInputError as exc:
            return JSONResponse(status_code=400, content=_protocol_body(str(exc)))
        except ValueError as exc:
            return Response(content=str(exc), status_code=422, media_type="text/plain; charset=utf-8")

        if req.format is not None:
            return JSONResponse(
                status_code=400,
                content=_protocol_body("JSON and JSON Schema response formats are unsupported by this backend profile"),
            )
        if req.keep_alive is not None:
            return JSONResponse(
                status_code=400,
                content=_protocol_body("request-local keep_alive is unsupported; the supervisor owns bounded TTL"),
            )
        if not req.messages:
            return JSONResponse(status_code=400, content=_protocol_body("messages must not be empty"))

        payload: dict[str, Any] = {
            "model": profile.public_alias,
            "messages": prepared_messages,
            "stream": req.stream,
        }
        payload.update(mapped_options)
        if req.think is False:
            payload["reasoning_effort"] = "none"
            payload["enable_thinking"] = False
        elif req.think is True or req.think is None:
            pass
        elif req.think == "low":
            payload["reasoning_effort"] = "low"
        else:
            safe_think = _safe_key_name(req.think) or "unknown"
            return JSONResponse(status_code=400, content=_protocol_body(f"unsupported think setting: {safe_think}"))

        endpoint = settings.litellm_base_url.rstrip("/") + "/v1/chat/completions"
        upstream = request.app.state.upstream
        headers = {"Authorization": caller.authorization, "Content-Type": "application/json"}
        request_timeout = httpx.Timeout(connect=5.0, read=profile.total_deadline_seconds + 30.0, write=30.0, pool=5.0)
        if not req.stream:
            try:
                # LiteLLM 1.102.1 does not cancel its provider call for a
                # disconnected non-streaming proxy response. Use its streaming
                # response closer internally, then preserve the legacy text
                # response at this boundary.
                upstream_payload = dict(payload)
                upstream_payload["stream"] = True
                built = upstream.build_request("POST", endpoint, headers=headers, json=upstream_payload, timeout=request_timeout)
                upstream_response = await _send_before_disconnect(
                    request,
                    upstream,
                    built,
                    deadline=request_deadline,
                )
            except (httpx.HTTPError, TimeoutError):
                return JSONResponse(status_code=503, content=_unavailable_body())
            if upstream_response is None:
                return _ClientGoneResponse()
            if upstream_response.status_code != 200:
                try:
                    return _http_error_response(upstream_response.status_code)
                finally:
                    await upstream_response.aclose()
            content_type = upstream_response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/event-stream":
                await upstream_response.aclose()
                return JSONResponse(status_code=503, content=_unavailable_body())
            try:
                aggregate = await _aggregate_text_before_disconnect(
                    request,
                    upstream_response,
                    deadline=request_deadline,
                )
                if aggregate is None:
                    return _ClientGoneResponse()
            except NonstreamUpstreamError as exc:
                return _http_error_response(exc.status_code)
            except (httpx.HTTPError, TimeoutError):
                return JSONResponse(status_code=503, content=_unavailable_body())
            content, finish_reason = aggregate
            return Response(
                content=content,
                status_code=200,
                media_type="text/plain; charset=utf-8",
                headers={"X-Legacy-Done-Reason": finish_reason},
            )

        try:
            built = upstream.build_request("POST", endpoint, headers=headers, json=payload, timeout=request_timeout)
            upstream_response = await _send_before_disconnect(
                request,
                upstream,
                built,
                deadline=request_deadline,
            )
        except (httpx.HTTPError, TimeoutError):
            return JSONResponse(status_code=503, content=_unavailable_body())
        if upstream_response is None:
            return _ClientGoneResponse()
        if upstream_response.status_code != 200:
            try:
                return _http_error_response(upstream_response.status_code)
            finally:
                await upstream_response.aclose()

        return _UpstreamStreamingResponse(
            _legacy_stream(upstream_response, deadline=request_deadline),
            upstream_response,
            media_type="text/event-stream; charset=utf-8",
            headers=_sse_headers(request),
        )

    return app
