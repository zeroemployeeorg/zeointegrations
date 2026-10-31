# SPDX-License-Identifier: MIT
# Copyright 2026 Zero Employee Organizations.
# Source: supplied expanded collection; see docs/migrations/http-boundary.json.
"""Streaming HTTP execution inside a host's admitted network boundary.

Copyright 2026 Zero Employee Organizations. SPDX-License-Identifier: MIT
The host supplies a transport with DNS/egress policy and credentials already bound
in custody. There is deliberately no default socket transport, retry or auth flow.
"""

from __future__ import annotations

import time

import httpx

from .runtime import IntegrationError, Request, Response, _url


class HttpxTransport:
    """Execute requests using an explicitly supplied, host-owned transport.

    Construct inside Connect's custody callback, after its durable claim. The
    supplied BaseTransport owns credential injection and DNS/egress restrictions;
    it must honour HTTPX timeout extensions and must not retry. This class owns
    neither authorization nor an idempotency store. Closing it closes that
    transport: give each instance its own transport or a host-owned lease wrapper.
    """

    def __init__(self, *, transport: httpx.BaseTransport, origins: tuple[str, ...]):
        if not isinstance(transport, httpx.BaseTransport):
            raise IntegrationError("TRANSPORT_REQUIRED")
        if not origins or any(_url(origin) != origin for origin in origins):
            raise IntegrationError("ORIGIN")
        self._origins = frozenset(origins)
        self._http = httpx.Client(
            transport=transport, trust_env=False, follow_redirects=False
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def request(self, request: Request) -> Response:
        if _url(request.url) not in self._origins:
            raise IntegrationError("ORIGIN")
        if request.method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise IntegrationError("METHOD")
        if not 0 < request.timeout_seconds <= 300:
            raise IntegrationError("DEADLINE")
        if not 0 < request.max_response_bytes <= 16 * 1024 * 1024:
            raise IntegrationError("SIZE_LIMIT")
        headers = dict(request.headers)
        # Cookies are never carried between operations; authentication belongs to
        # the transport supplied by custody, not caller-controlled headers.
        if any(
            k.lower() in {"authorization", "cookie", "host", "proxy-authorization"}
            for k in headers
        ):
            raise IntegrationError("CREDENTIAL_ARGUMENT")
        headers["Accept-Encoding"] = "identity"
        deadline = time.monotonic() + request.timeout_seconds
        failure = "TRANSPORT_UNCONFIRMED"
        try:
            # Build directly to avoid httpx.Client's persistent cookie jar.
            wire = httpx.Request(
                request.method,
                request.url,
                params=request.query or None,
                headers=headers,
                json=request.json_body,
                data=request.form_body,
                extensions={
                    "timeout": httpx.Timeout(request.timeout_seconds).as_dict()
                },
            )
            response = self._http.send(wire, stream=True, follow_redirects=False)
            try:
                if str(response.url) != str(wire.url):
                    raise IntegrationError("ORIGIN")
                if 300 <= response.status_code < 400:
                    raise IntegrationError("REDIRECT")
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise IntegrationError("ENCODING")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise IntegrationError("DEADLINE")
                    if len(body) + len(chunk) > request.max_response_bytes:
                        raise IntegrationError("SIZE_LIMIT")
                    body.extend(chunk)
                if time.monotonic() >= deadline:
                    raise IntegrationError("DEADLINE")
                # Only headers consumed by adapters; no cookies or arbitrary
                # credential-bearing headers cross the provider boundary.
                safe_headers = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower()
                    in {
                        "content-type",
                        "retry-after",
                        "x-ratelimit-remaining",
                        "x-restli-id",
                    }
                }
                return Response(
                    response.status_code, bytes(body), safe_headers, str(response.url)
                )
            finally:
                response.close()
                self._http.cookies.clear()
        except Exception as exc:
            if isinstance(exc, IntegrationError):
                failure = exc.code
        # No chained exception can retain a credential-bearing HTTP request.
        raise IntegrationError(failure, outcome_unknown=request.effect)
