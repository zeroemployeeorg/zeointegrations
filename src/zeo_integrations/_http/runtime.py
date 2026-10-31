# SPDX-License-Identifier: MIT
# Copyright 2026 Zero Employee Organizations.
# Source: supplied expanded collection; see docs/migrations/http-boundary.json.
"""One explicitly admitted provider invocation; no ambient network or credentials.

Copyright 2026 Zero Employee Organizations. SPDX-License-Identifier: MIT
Transport implementations run inside Connect and own credential injection,
origin admission, deadlines, byte limits, grants and durable idempotency.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol, get_type_hints
from urllib.parse import unquote, urlsplit

from pydantic import ConfigDict, create_model


class IntegrationError(Exception):
    def __init__(self, code: str, *, outcome_unknown: bool = False):
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class Request:
    provider: str
    operation: str
    method: str
    url: str
    query: dict
    headers: dict
    json_body: Any
    form_body: Any
    idempotency_key: str
    timeout_seconds: float
    max_response_bytes: int
    effect: bool


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict
    # Actual destination after dispatch; redirects must never be followed.
    url: str


class Transport(Protocol):
    def request(self, request: Request) -> Response: ...


class HTTPError(IntegrationError):
    pass


class RequestException(IntegrationError):
    pass


class HTTPException(IntegrationError):
    def __init__(self, status_code=None, detail=None):
        super().__init__("PROVIDER_REJECTED")


class _Response:
    def __init__(self, response):
        self.status_code = response.status
        self.headers = response.headers
        self.content = response.body
        self.text = response.body.decode("utf-8")
        self.ok = 200 <= response.status < 300

    def raise_for_status(self):
        if not self.ok:
            raise HTTPError("PROVIDER_REJECTED")

    def json(self):
        if not self.content:
            return None
        return json.loads(self.text, parse_constant=lambda _: _invalid())


def _invalid():
    raise IntegrationError("INVALID_JSON")


def _bounded(value, cap):
    try:
        encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise IntegrationError("INVALID_JSON") from None
    if len(encoded) > cap:
        raise IntegrationError("SIZE_LIMIT")
    return encoded


def _url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise IntegrationError("ORIGIN")
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
        raise IntegrationError("ORIGIN")
    decoded = unquote(p.path)
    if "\\" in value or any(x in {".", ".."} for x in decoded.split("/")):
        raise IntegrationError("PATH")
    if any(ord(c) < 32 for c in unquote(value)):
        raise IntegrationError("PATH")
    return f"{p.scheme}://{p.netloc}"


class _Session:
    """Requests-shaped internal adapter, never requests.Session."""

    def __init__(self, owner):
        self.owner = owner
        self.headers = {}

    @property
    def idempotency_key(self):
        return self.owner._key

    def request(self, method, url, **kwargs):
        return self.owner._request(method, url, **kwargs)

    def __getattr__(self, name):
        if name in {"get", "post", "put", "patch", "delete", "head"}:
            return lambda url, **kw: self.request(name.upper(), url, **kw)
        raise AttributeError(name)


class Client:
    """Provider facade with closed per-operation argument models.

    Use one instance per invocation lane; concurrent invocation is refused.
    Explicit invoke is the only execution entry. Non-GET calls conservatively
    require effect admission, including POST searches. No automatic retry.
    """

    provider: str
    implementation: type
    origins: tuple[str, ...]
    operations: tuple[str, ...]

    def __init__(
        self,
        transport: Transport,
        *,
        configuration=None,
        timeout_seconds: float = 30,
        max_bytes: int = 1_048_576,
        max_requests: int = 20,
    ):
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise IntegrationError("POLICY")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 16_777_216:
            raise IntegrationError("POLICY")
        if type(max_requests) is not int or not 1 <= max_requests <= 100:
            raise IntegrationError("POLICY")
        configuration = configuration or {}
        _bounded(configuration, max_bytes)
        for key, value in configuration.items():
            if key in {"cloud_id", "org_slug"} and (
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value)
            ):
                raise IntegrationError("CONFIGURATION")
        self.transport = transport
        self.timeout = timeout_seconds
        self.max_bytes = max_bytes
        self.max_requests = max_requests
        self._active = False
        self._lock = threading.Lock()
        self._effect = False
        self._dispatched_effect = False
        self._count = 0
        self._session = _Session(self)
        self._impl = self.implementation(self._session, **configuration)

    @classmethod
    def input_model(cls, operation):
        if operation not in cls.operations:
            raise IntegrationError("OPERATION")
        fn = getattr(cls.implementation, operation)
        hints = get_type_hints(fn, include_extras=True)
        fields = {}
        for name, p in inspect.signature(fn).parameters.items():
            if name == "self":
                continue
            if p.kind in {p.VAR_POSITIONAL, p.VAR_KEYWORD}:
                raise IntegrationError("UNSUPPORTED_SIGNATURE")
            fields[name] = (
                hints.get(name, Any),
                ... if p.default is p.empty else p.default,
            )
        return create_model(
            operation + "Input",
            __config__=ConfigDict(extra="forbid", strict=True),
            **fields,
        )

    def invoke(
        self,
        operation: str,
        arguments: dict,
        *,
        idempotency_key: str,
        allow_effect: bool = False,
    ):
        if self._active:
            raise IntegrationError("BUSY")
        if not isinstance(idempotency_key, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,200}", idempotency_key
        ):
            raise IntegrationError("IDEMPOTENCY_KEY")
        _bounded(arguments, self.max_bytes)

        def check_keys(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k.lower() in {
                        "authorization",
                        "cookie",
                        "api_key",
                        "access_token",
                        "refresh_token",
                        "password",
                        "client_secret",
                    }:
                        raise IntegrationError("CREDENTIAL_ARGUMENT")
                    check_keys(v)
            elif isinstance(value, list):
                for item in value:
                    check_keys(item)

        check_keys(arguments)
        parsed = self.input_model(operation).model_validate(arguments)
        # Scalar IDs cannot smuggle URL delimiters into provider path templates.
        for name, value in parsed:
            if isinstance(value, str) and (
                name.endswith(("_id", "_uid", "_key"))
                or name
                in {
                    "id",
                    "country",
                    "state",
                    "postal_code",
                    "table_id_or_name",
                    "id_or_url",
                    "crypto",
                    "model_owner",
                    "model_name",
                    "organization_id",
                    "cloud_name",
                    "cloud_id",
                    "filename",
                }
            ):
                if (
                    not value
                    or any(c in value for c in "/\\?#%")
                    or value in {".", ".."}
                ):
                    raise IntegrationError("PATH_ARGUMENT")
        if not self._lock.acquire(blocking=False):
            raise IntegrationError("BUSY")
        self._deadline = time.monotonic() + self.timeout
        self._failure = None
        self._active, self._effect = True, allow_effect is True
        self._dispatched_effect, self._count = False, 0
        self._operation, self._key = operation, idempotency_key
        error = None
        try:
            result = getattr(self._impl, operation)(**parsed.model_dump())
            if isinstance(result, _Response):
                result = result.json()
            if self._failure:
                raise IntegrationError(self._failure)
            if time.monotonic() >= self._deadline:
                raise IntegrationError("DEADLINE")
            if isinstance(result, bytes):
                import base64

                result = {
                    "encoding": "base64",
                    "data": base64.b64encode(result).decode("ascii"),
                }
            _bounded(result, self.max_bytes)
        except Exception as exc:
            error = (
                exc.code
                if isinstance(exc, IntegrationError)
                else "INVALID_PROVIDER_RESULT"
            )
        finally:
            self._active = False
            self._lock.release()
        if error:
            raise IntegrationError(error, outcome_unknown=self._dispatched_effect)
        return result

    def _request(self, method, url, **kwargs):
        try:
            return self._dispatch(method, url, **kwargs)
        except Exception as exc:
            self._failure = (
                exc.code if isinstance(exc, IntegrationError) else "TRANSPORT"
            )
            raise

    def _dispatch(self, method, url, **kwargs):
        if not self._active:
            raise IntegrationError("OUTSIDE_INVOCATION")
        if self._failure:
            raise IntegrationError(self._failure)
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise IntegrationError("DEADLINE")
        method = method.upper()
        if method not in {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"}:
            raise IntegrationError("METHOD")
        effect = method not in {"GET", "HEAD"}
        if effect and not self._effect:
            raise IntegrationError("EFFECT_ADMISSION_REQUIRED")
        if self._dispatched_effect:
            raise IntegrationError("MULTIPLE_CALLS_AFTER_EFFECT")
        if self._count >= self.max_requests:
            raise IntegrationError("REQUEST_LIMIT")
        if _url(url) not in self.origins:
            raise IntegrationError("ORIGIN")
        unknown = set(kwargs) - {"headers", "params", "json", "data", "timeout"}
        if unknown:
            raise IntegrationError("UNSUPPORTED_TRANSPORT_ARGUMENT")
        headers = {**self._session.headers, **(kwargs.get("headers") or {})}
        if any(
            k.lower() in {"authorization", "cookie", "proxy-authorization"}
            or "key" in k.lower()
            or "token" in k.lower()
            for k in headers
        ):
            raise IntegrationError("CREDENTIAL_HEADER")
        request = Request(
            self.provider,
            self._operation,
            method,
            url,
            kwargs.get("params") or {},
            headers,
            kwargs.get("json"),
            kwargs.get("data"),
            self._key,
            remaining,
            self.max_bytes,
            effect,
        )
        _bounded(
            [request.query, headers, request.json_body, request.form_body],
            self.max_bytes,
        )
        self._count += 1
        self._dispatched_effect = effect
        response = self.transport.request(request)
        if time.monotonic() >= self._deadline:
            raise IntegrationError("DEADLINE")
        if not isinstance(response, Response) or type(response.status) is not int:
            raise IntegrationError("RESPONSE")
        if response.url != url or 300 <= response.status < 400:
            raise IntegrationError("REDIRECT")
        if not isinstance(response.body, bytes) or len(response.body) > self.max_bytes:
            raise IntegrationError("SIZE_LIMIT")
        if response.status >= 400:
            # Only explicit provider rejection is safe. Timeouts and 5xx remain unknown.
            if response.status in {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}:
                self._dispatched_effect = False
            raise IntegrationError(
                "RATE_LIMITED" if response.status == 429 else "PROVIDER_REJECTED"
            )
        if response.status < 200:
            raise IntegrationError("RESPONSE")
        return _Response(response)
