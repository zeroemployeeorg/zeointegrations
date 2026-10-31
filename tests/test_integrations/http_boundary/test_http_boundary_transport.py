# SPDX-License-Identifier: MIT
# Copyright 2026 Zero Employee Organizations.
# Source: supplied expanded collection; see docs/migrations/http-boundary.json.
"""Execution boundaries with synthetic providers; never call the network."""

from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from zeo_integrations._http import HttpxTransport, IntegrationError, Request


def request(**overrides):
    return replace(
        Request(
            "test",
            "read",
            "GET",
            "https://example.com/items",
            {},
            {},
            None,
            None,
            "key",
            30,
            100,
            False,
        ),
        **overrides,
    )


def transport(handler):
    return HttpxTransport(
        transport=httpx.MockTransport(handler), origins=("https://example.com",)
    )


def test_stream_and_cookies():
    seen = []

    def respond(wire):
        seen.append(wire)
        return httpx.Response(
            200, json={"ok": True}, headers={"set-cookie": "sid=private"}
        )

    with transport(respond) as client:
        assert client.request(request()).body == b'{"ok":true}'
        client.request(request())
    assert len(seen) == 2
    assert all("cookie" not in wire.headers for wire in seen)
    assert all(wire.headers["accept-encoding"] == "identity" for wire in seen)


@pytest.mark.parametrize(
    "status,headers,body,code",
    [
        (302, {"location": "https://elsewhere.com"}, b"", "REDIRECT"),
        (200, {"content-encoding": "br"}, b"", "ENCODING"),
        (200, {}, b"x" * 101, "SIZE_LIMIT"),
    ],
)
def test_response_refusals_are_not_retried(status, headers, body, code):
    seen = []

    def respond(wire):
        seen.append(wire)
        return httpx.Response(status, headers=headers, content=body)

    with transport(respond) as client, pytest.raises(IntegrationError) as caught:
        client.request(request(method="POST", effect=True))
    assert caught.value.code == code
    assert caught.value.outcome_unknown
    assert len(seen) == 1


def test_transport_exception_does_not_retain_request():
    def fail(wire):
        raise httpx.ConnectError("SECRET", request=wire)

    with transport(fail) as client, pytest.raises(IntegrationError) as caught:
        client.request(request())
    assert "SECRET" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://elsewhere.com"},
        {"headers": {"Host": "elsewhere.com"}},
        {"headers": {"Authorization": "SECRET"}},
        {"timeout_seconds": float("nan")},
    ],
)
def test_request_refused_before_network(change):
    wire = Mock()
    with transport(wire) as client, pytest.raises(IntegrationError):
        client.request(request(**change))
    wire.assert_not_called()


@pytest.mark.parametrize("unbound", [None, object(), "not-a-host-transport"])
def test_unbound_transport_never_constructs_default_http_client(monkeypatch, unbound):
    factory = Mock()
    monkeypatch.setattr(httpx, "Client", factory)
    with pytest.raises(IntegrationError, match="TRANSPORT_REQUIRED"):
        HttpxTransport(transport=unbound, origins=("https://example.com",))
    factory.assert_not_called()
