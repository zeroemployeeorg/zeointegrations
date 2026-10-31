"""Exercise the facade and actual HTTP wrapper together with inert HTTPX mocks."""

import httpx
import pytest

from zeo_integrations._http import Client, HttpxTransport, IntegrationError, Response


class QueryProvider:
    def __init__(self, transport):
        self._http = transport

    def get(self, embedded: bool = False, with_params: bool = False):
        url = "https://fixture.example/items" + ("?existing=1" if embedded else "")
        params = {"term": "space value"} if with_params else {}
        return self._http.get(url, params=params).json()

    def post(self, embedded: bool = False, with_params: bool = False):
        url = "https://fixture.example/items" + ("?existing=1" if embedded else "")
        params = {"term": "space value"} if with_params else {}
        return self._http.post(url, params=params, json={"value": 1}).json()

    def binary(self):
        return self._http.get("https://fixture.example/items").content

    def encoded_path(self):
        return self._http.get("https://fixture.example/%5csecret").json()

    def nested(self, data: dict):
        return self._http.post("https://fixture.example/items", json=data).json()


class QueryClient(Client):
    provider = "fixture"
    implementation = QueryProvider
    origins = ("https://fixture.example",)
    operations = ("get", "post", "binary", "encoded_path", "nested")


@pytest.mark.parametrize("operation", ["get", "post"])
@pytest.mark.parametrize(
    "embedded,with_params",
    [(False, True), (True, False), (False, False), (True, True)],
)
def test_composed_query_is_not_a_redirect(operation, embedded, with_params):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    with HttpxTransport(
        transport=httpx.MockTransport(respond), origins=QueryClient.origins
    ) as transport:
        result = QueryClient(transport).invoke(
            operation,
            {"embedded": embedded, "with_params": with_params},
            idempotency_key="query-1",
            allow_effect=operation == "post",
        )
    assert result == {"ok": True}
    assert len(calls) == 1 and calls[0].method == operation.upper()
    assert calls[0].url.path == "/items"
    expected = {"term": "space value"} if with_params else {}
    if embedded and not with_params:
        expected = {"existing": "1"}
    assert dict(calls[0].url.params) == expected


def test_composed_binary_content_does_not_decode_utf8():
    def respond(request):
        return httpx.Response(200, content=b"\xff\x00\x01")

    with HttpxTransport(
        transport=httpx.MockTransport(respond), origins=QueryClient.origins
    ) as transport:
        result = QueryClient(transport).invoke("binary", {}, idempotency_key="binary-1")
    assert result == {"encoding": "base64", "data": "/wAB"}


def test_encoded_backslash_fails_before_transport():
    calls = []
    with HttpxTransport(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        origins=QueryClient.origins,
    ) as transport:
        with pytest.raises(IntegrationError, match="PATH"):
            QueryClient(transport).invoke("encoded_path", {}, idempotency_key="path-1")
    assert not calls


@pytest.mark.parametrize(
    "arguments",
    [{1: "value"}, {"data": {2: "value"}}, {"data": {"items": [{False: "value"}]}}],
)
def test_non_string_keys_fail_with_integration_error(arguments):
    calls = []
    with HttpxTransport(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        origins=QueryClient.origins,
    ) as transport:
        with pytest.raises(IntegrationError, match="ARGUMENT_KEY"):
            QueryClient(transport).invoke(
                "nested", arguments, idempotency_key="key-1", allow_effect=True
            )
    assert not calls


@pytest.mark.parametrize(
    "destination",
    ["https://other.example/items?term=x", "https://fixture.example/other?term=x"],
)
def test_facade_still_rejects_changed_origin_or_path(destination):
    class ChangedDestination:
        def request(self, request):
            return Response(200, b'{"ok": true}', {}, destination)

    with pytest.raises(IntegrationError, match="REDIRECT"):
        QueryClient(ChangedDestination()).invoke(
            "get", {"with_params": True}, idempotency_key="redirect-1"
        )
