#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a bounded, redacted HTTP acceptance check against an edge URL."""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import time
from urllib.parse import urlsplit


USER_AGENT = "LiliuxFlow-EdgeAcceptance/0.1"
READY_PATH = "/_liliuxflow/backend/ready"
KEY_FILE_MAX_BYTES = 16 * 1024
ERROR_SCAN_MAX_BYTES = 512
STREAM_OUTPUT_MAX_BYTES = 1024 * 1024
STREAM_PROMPT = "Reply with the single token OK."
STREAM_DELIMITER = re.compile(rb"\r?\n\r?\n")
ERROR_1010 = re.compile(rb"error\s*1010|1010.{0,120}browser.{0,40}integrity", re.I)

STATIC_CHECKS = (
    ("welcome", "/", "text/html"),
    ("welcome_css", "/liliuxflow-welcome/welcome.css", "text/css"),
    ("welcome_js", "/liliuxflow-welcome/welcome.js", "javascript"),
    (
        "welcome_font",
        "/liliuxflow-welcome/fonts/InstrumentSans-latin-variable.woff2",
        "font/woff2",
    ),
    ("welcome_favicon", "/liliuxflow-welcome/favicon.svg", "image/svg+xml"),
)

ONLINE_CHECKS = (
    ("readiness", "GET", READY_PATH, None),
    ("ui", "GET", "/ui/", None),
    ("api", "GET", "/api", None),
    ("models", "GET", "/v1/models", "api"),
    ("api_docs", "GET", "/api-docs", None),
    ("openapi", "GET", "/openapi.json", None),
    ("swagger_css", "GET", "/swagger/swagger-ui.css", None),
    (
        "ui_asset",
        "GET",
        "/litellm-asset-prefix/_next/static/chunks/2fb6914g8i08s.js",
        None,
    ),
)

OFFLINE_CHECKS = (
    ("readiness", "GET", READY_PATH, None),
    ("ui", "GET", "/ui/", None),
    (
        "ui_asset",
        "GET",
        "/litellm-asset-prefix/_next/static/chunks/2fb6914g8i08s.js",
        None,
    ),
    ("api", "GET", "/api", None),
    ("models", "GET", "/v1/models", None),
)


class InputError(ValueError):
    """A safe-to-report input failure without echoing its value."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_base_url(value: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InputError("invalid_base_url") from exc
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise InputError("invalid_base_url")
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host == "localhost"
        if not is_loopback:
            raise InputError("http_requires_loopback")
    host_for_netloc = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    origin = f"{parsed.scheme}://{host_for_netloc}"
    if port is not None:
        origin += f":{port}"
    return origin, parsed.hostname, port


def _read_key_file(path_text: str) -> str:
    """Read only an explicitly supplied plaintext key or JSON api_key field."""
    path = Path(path_text)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InputError("key_file_invalid") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise InputError("key_file_invalid")
        raw = os.read(fd, KEY_FILE_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > KEY_FILE_MAX_BYTES:
        raise InputError("key_file_invalid")
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InputError("key_file_invalid") from exc
    if not text:
        raise InputError("key_file_invalid")
    if text.startswith("{"):
        try:
            value = json.loads(text).get("api_key")
        except (AttributeError, json.JSONDecodeError) as exc:
            raise InputError("key_file_invalid") from exc
        if not isinstance(value, str):
            raise InputError("key_file_invalid")
        key = value.strip()
    else:
        key = text
    if not key or "\r" in key or "\n" in key or len(key) > 8192:
        raise InputError("key_file_invalid")
    return key


def _response_media_type(value: str | None) -> str | None:
    if not value:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
        return media_type
    return "other"


def safe_header_summary(headers: dict[str, str] | http.client.HTTPMessage) -> dict[str, object]:
    """Return only small, allowlisted response metadata; never return cookies."""
    if hasattr(headers, "getheader"):
        content_type = headers.getheader("Content-Type")
        cache_control = headers.getheader("Cache-Control")
        content_length = headers.getheader("Content-Length")
        location = headers.getheader("Location")
    else:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        content_type = normalized.get("content-type")
        cache_control = normalized.get("cache-control")
        content_length = normalized.get("content-length")
        location = normalized.get("location")
    length: int | None = None
    if content_length and content_length.isdecimal():
        candidate = int(content_length)
        if candidate <= 2**31 - 1:
            length = candidate
    cache_tokens = {
        token.strip().split("=", 1)[0].lower()
        for token in (cache_control or "").split(",")
        if token.strip()
    }
    return {
        "content_type": _response_media_type(content_type),
        "content_length": length,
        "cache_control_no_store": "no-store" in cache_tokens,
        "location_present": location is not None,
    }


def _looks_like_html(content_type: str | None, body_prefix: bytes = b"") -> bool:
    if content_type == "text/html":
        return True
    sample = body_prefix.lstrip(b"\xef\xbb\xbf \t\r\n")[:16].lower()
    return sample.startswith((b"<", b"<!doctype", b"<!--"))


def evaluate_observation(
    mode: str,
    check_id: str,
    status: int,
    headers: dict[str, object],
    *,
    key_supplied: bool = False,
    body_prefix: bytes = b"",
    cloudflare_1010: bool = False,
) -> tuple[bool, str]:
    """Classify one status/header-only acceptance observation."""
    if cloudflare_1010:
        return False, "cloudflare_1010"
    media_type = headers.get("content_type")
    if status == 403 and media_type == "text/html":
        return False, "http403_html_edge_denial"
    if mode == "offline" and check_id == "readiness":
        ok = status == 503 and bool(headers.get("cache_control_no_store"))
        return ok, "offline_probe_503" if ok else "offline_probe_mismatch"
    if mode == "offline" and check_id in {"ui", "ui_asset", "api", "models"}:
        html = _looks_like_html(
            media_type if isinstance(media_type, str) else None, body_prefix
        )
        ok = status == 503 and not html
        if ok:
            return True, "backend_offline_503_non_html"
        return False, "offline_html_fallback" if html else "offline_status_mismatch"
    if check_id in {item[0] for item in STATIC_CHECKS}:
        expected = next(item[2] for item in STATIC_CHECKS if item[0] == check_id)
        mime_ok = (
            media_type == expected
            or (expected == "javascript" and media_type in {"text/javascript", "application/javascript"})
        )
        ok = status == 200 and mime_ok
        return ok, "static_asset_200" if ok else "static_asset_mismatch"
    if mode == "online-non-inference":
        if check_id == "readiness":
            ok = status == 204 and bool(headers.get("cache_control_no_store"))
            return ok, "ready_204" if ok else "readiness_mismatch"
        if check_id == "models":
            expected_status = 200 if key_supplied else 401
            ok = status == expected_status and media_type != "text/html"
            return ok, "authenticated_models_200" if key_supplied and ok else (
                "unauthenticated_models_401" if ok else "models_status_mismatch"
            )
        if check_id == "ui":
            ok = status == 200 and media_type == "text/html"
            return ok, "ui_200" if ok else "ui_response_mismatch"
        if check_id == "api":
            ok = status != 503 and not _looks_like_html(
                media_type if isinstance(media_type, str) else None, body_prefix
            )
            return ok, "api_backend_response" if ok else "api_offline_or_html"
        expected = {
            "api_docs": (200, "text/html"),
            "openapi": (200, "application/json"),
            "swagger_css": (200, "text/css"),
            "ui_asset": (200, "text/javascript"),
        }.get(check_id)
        if expected is not None:
            status_ok, media_ok = expected
            ok = status == status_ok and (
                media_type == media_ok
                or (check_id == "ui_asset" and media_type == "application/javascript")
            )
            return ok, "backend_asset_200" if ok else "backend_asset_mismatch"
    if mode in {"static", "online-non-inference", "offline"}:
        return False, "unexpected_observation"
    return False, "unsupported_mode"


def _new_connection(host: str, port: int | None, scheme: str, timeout: float):
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port=port, timeout=timeout, context=ssl.create_default_context()
        )
    return http.client.HTTPConnection(host, port=port, timeout=timeout)


def _cloudflare_1010(
    response: http.client.HTTPResponse, headers: dict[str, object]
) -> bool:
    if response.status != 403:
        return False
    length = headers.get("content_length")
    if headers.get("content_type") != "text/html" or not isinstance(length, int):
        return False
    # Inspect only a fixed prefix when the response length proves that it is
    # not the complete body. No backend error payload is retained or emitted.
    if length <= ERROR_SCAN_MAX_BYTES:
        return False
    prefix = response.read(ERROR_SCAN_MAX_BYTES)
    return bool(ERROR_1010.search(prefix))


def _run_check(
    host: str,
    port: int | None,
    scheme: str,
    mode: str,
    check: tuple[str, str, str, str | None],
    *,
    timeout: float,
    key: str | None,
) -> dict[str, object]:
    check_id, method, path, auth_scope = check
    started = time.monotonic()
    request_started_at = utc_now()
    connection = _new_connection(host, port, scheme, timeout)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "close",
    }
    if auth_scope == "api" and key is not None:
        request_headers["Authorization"] = f"Bearer {key}"
    result: dict[str, object] = {
        "id": check_id,
        "method": method,
        "path": path,
        "started_at": request_started_at,
        "finished_at": None,
        "elapsed_ms": None,
        "status": None,
        "response_headers": None,
        "passed": False,
        "classification": "network_error",
    }
    try:
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        headers = safe_header_summary(response.headers)
        marker = _cloudflare_1010(response, headers)
        passed, classification = evaluate_observation(
            mode,
            check_id,
            response.status,
            headers,
            key_supplied=key is not None and auth_scope == "api",
            body_prefix=b"",
            cloudflare_1010=marker,
        )
        result.update(
            status=response.status,
            response_headers=headers,
            passed=passed,
            classification=classification,
        )
    except (socket.timeout, TimeoutError):
        result["classification"] = "network_timeout"
    except ssl.SSLError:
        result["classification"] = "tls_error"
    except (OSError, http.client.HTTPException):
        result["classification"] = "network_error"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        result["finished_at"] = utc_now()
        connection.close()
    return result


def _record_sse_delimiters(chunk: bytes, tail: bytes) -> tuple[int, bytes]:
    combined = tail + chunk
    old_tail_length = len(tail)
    new_events = sum(
        1
        for match in STREAM_DELIMITER.finditer(combined)
        if match.end() > old_tail_length
    )
    return new_events, combined[-3:]


def _run_stream_smoke(
    host: str,
    port: int | None,
    scheme: str,
    *,
    timeout: float,
    stream_timeout: float,
    key: str,
    model: str,
) -> dict[str, object]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": STREAM_PROMPT}],
            "max_tokens": 64,
            "stream": True,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    connection = _new_connection(host, port, scheme, timeout)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "Connection": "close",
    }
    start = time.monotonic()
    request_started_at = utc_now()
    result: dict[str, object] = {
        "id": "stream_smoke",
        "method": "POST",
        "path": "/v1/chat/completions",
        "started_at": request_started_at,
        "finished_at": None,
        "status": None,
        "response_headers": None,
        "first_chunk_ms": None,
        "first_event_ms": None,
        "last_event_ms": None,
        "sse_event_count": 0,
        "read_calls_with_data": 0,
        "output_size_bytes": 0,
        "truncated": False,
        "elapsed_ms": None,
        "passed": False,
        "classification": "network_error",
    }
    try:
        connection.request("POST", "/v1/chat/completions", body=body, headers=request_headers)
        response = connection.getresponse()
        headers = safe_header_summary(response.headers)
        result.update(status=response.status, response_headers=headers)
        if _cloudflare_1010(response, headers):
            result["classification"] = "cloudflare_1010"
        elif response.status != 200:
            result["classification"] = "backend_http_status"
        elif headers.get("content_type") != "text/event-stream":
            result["classification"] = "not_sse"
        else:
            deadline = start + stream_timeout
            tail = b""
            first_chunk_at: float | None = None
            first_event_at: float | None = None
            last_event_at: float | None = None
            timed_out = False
            while int(result["output_size_bytes"]) < STREAM_OUTPUT_MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                if connection.sock is not None:
                    connection.sock.settimeout(min(timeout, remaining))
                try:
                    read_limit = min(
                        8192,
                        STREAM_OUTPUT_MAX_BYTES - int(result["output_size_bytes"]),
                    )
                    chunk = response.read1(read_limit)
                except (socket.timeout, TimeoutError):
                    timed_out = True
                    break
                if not chunk:
                    break
                now = time.monotonic()
                if first_chunk_at is None:
                    first_chunk_at = now
                result["read_calls_with_data"] = int(result["read_calls_with_data"]) + 1
                result["output_size_bytes"] = int(result["output_size_bytes"]) + len(chunk)
                added, tail = _record_sse_delimiters(chunk, tail)
                if added:
                    result["sse_event_count"] = int(result["sse_event_count"]) + added
                    if first_event_at is None:
                        first_event_at = now
                    last_event_at = now
            if int(result["output_size_bytes"]) >= STREAM_OUTPUT_MAX_BYTES:
                result["truncated"] = True
            result["first_chunk_ms"] = (
                round((first_chunk_at - start) * 1000) if first_chunk_at is not None else None
            )
            result["first_event_ms"] = (
                round((first_event_at - start) * 1000) if first_event_at is not None else None
            )
            result["last_event_ms"] = (
                round((last_event_at - start) * 1000) if last_event_at is not None else None
            )
            ok = (
                not timed_out
                and not result["truncated"]
                and int(result["output_size_bytes"]) > 0
                and int(result["sse_event_count"]) >= 2
                and int(result["read_calls_with_data"]) >= 2
            )
            result["passed"] = ok
            result["classification"] = "sse_frames_observed" if ok else (
                "stream_timeout" if timed_out else "stream_not_incremental_or_empty"
            )
    except (socket.timeout, TimeoutError):
        result["classification"] = "network_timeout"
    except ssl.SSLError:
        result["classification"] = "tls_error"
    except (OSError, http.client.HTTPException):
        result["classification"] = "network_error"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
        result["finished_at"] = utc_now()
        connection.close()
    return result


def _emit(report: dict[str, object], exit_code: int) -> int:
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="HTTPS edge URL; HTTP is allowed only for loopback")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mode", choices=("static", "online-non-inference", "offline"), default="static")
    modes.add_argument("--stream-smoke", action="store_true", help="one bounded SSE inference request; requires --allow-inference, --key-file, and --model")
    parser.add_argument("--key-file", help="explicit virtual-key file: plaintext or JSON with api_key")
    parser.add_argument("--model", help="required for --stream-smoke; select the already active model")
    parser.add_argument("--allow-inference", action="store_true", help="explicitly authorize one API inference request; this client does not manage the server")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--stream-timeout-seconds", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    started_at = utc_now()
    mode = "stream-smoke" if args.stream_smoke else args.mode
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": mode,
        "base_origin": None,
        "started_at": started_at,
        "finished_at": None,
        "key_supplied": bool(args.key_file),
        "inference_authorized": bool(args.allow_inference),
        "checks": [],
        "passed": False,
    }

    def fail_input(code: str) -> int:
        report["error"] = code
        report["finished_at"] = utc_now()
        return _emit(report, 2)

    if not 1 <= args.timeout_seconds <= 30 or not 1 <= args.stream_timeout_seconds <= 300:
        return fail_input("timeout_out_of_bounds")
    if mode == "stream-smoke":
        if not args.allow_inference or not args.key_file or not args.model:
            return fail_input("stream_requires_allow_inference_key_and_model")
        if len(args.model) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", args.model):
            return fail_input("invalid_model")
    elif args.key_file and mode != "online-non-inference":
        return fail_input("key_file_only_allowed_for_online_checks")
    elif args.model or args.allow_inference:
        return fail_input("stream_options_only_allowed_for_stream_smoke")

    try:
        origin, host, port = parse_base_url(args.base_url)
    except InputError as exc:
        return fail_input(str(exc))
    report["base_origin"] = origin
    scheme = urlsplit(origin).scheme

    key: str | None = None
    if args.key_file:
        try:
            key = _read_key_file(args.key_file)
        except InputError as exc:
            return fail_input(str(exc))

    if mode == "stream-smoke":
        checks = [
            _run_stream_smoke(
                host,
                port,
                scheme,
                timeout=args.timeout_seconds,
                stream_timeout=args.stream_timeout_seconds,
                key=key or "",
                model=args.model,
            )
        ]
    else:
        checks = []
        static = [
            (check_id, "GET", path, None)
            for check_id, path, _expected_type in STATIC_CHECKS
        ]
        selected = (
            static
            if mode == "static"
            else static + list(ONLINE_CHECKS)
            if mode == "online-non-inference"
            else static + list(OFFLINE_CHECKS)
        )
        for check in selected:
            checks.append(
                _run_check(
                    host,
                    port,
                    scheme,
                    mode,
                    check,
                    timeout=args.timeout_seconds,
                    key=key,
                )
            )
    report["checks"] = checks
    report["passed"] = bool(checks) and all(bool(check.get("passed")) for check in checks)
    report["finished_at"] = utc_now()
    return _emit(report, 0 if report["passed"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
