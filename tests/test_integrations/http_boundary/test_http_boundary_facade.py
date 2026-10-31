"""Synthetic operation admission and uncertain outcomes; no provider calls."""

import pytest
from pydantic import ValidationError

from zeo_integrations._http import Client, IntegrationError, Response


class SyntheticProvider:
    def __init__(self, transport):
        self._http = transport

    def read(self, limit: int = 2):
        return self._http.get(
            "https://fixture.example/items", params={"limit": limit}
        ).json()

    def write(self, data: dict):
        return self._http.post("https://fixture.example/items", json=data).json()


class SyntheticClient(Client):
    provider = "fixture"
    implementation = SyntheticProvider
    origins = ("https://fixture.example",)
    operations = ("read", "write")


class FakeTransport:
    def __init__(self, status=200, body=b'{"ok": true}'):
        self.calls = []
        self.status = status
        self.body = body

    def request(self, request):
        self.calls.append(request)
        return Response(self.status, self.body, {}, request.url)


def test_read_preserves_bounded_request_and_host_key():
    transport = FakeTransport()
    client = SyntheticClient(transport)
    assert client.invoke("read", {"limit": 3}, idempotency_key="read-1") == {"ok": True}
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request.url == "https://fixture.example/items"
    assert request.method == "GET" and request.query == {"limit": 3}
    assert request.idempotency_key == "read-1" and not request.effect


@pytest.mark.parametrize("arguments", [{"extra": True}, {"limit": "3"}])
def test_closed_arguments_fail_before_dispatch(arguments):
    transport = FakeTransport()
    with pytest.raises(ValidationError):
        SyntheticClient(transport).invoke("read", arguments, idempotency_key="read-1")
    assert not transport.calls


def test_unknown_operation_has_no_dispatch():
    transport = FakeTransport()
    with pytest.raises(IntegrationError, match="OPERATION"):
        SyntheticClient(transport).invoke("unknown", {}, idempotency_key="read-1")
    assert not transport.calls


def test_effect_needs_separate_host_admission():
    transport = FakeTransport()
    with pytest.raises(IntegrationError, match="EFFECT_ADMISSION_REQUIRED"):
        SyntheticClient(transport).invoke(
            "write", {"data": {}}, idempotency_key="write-1"
        )
    assert not transport.calls


@pytest.mark.parametrize("status,unknown", [(400, False), (429, False), (500, True)])
def test_effect_failure_is_not_retried_or_falsely_known(status, unknown):
    transport = FakeTransport(status=status)
    with pytest.raises(IntegrationError) as caught:
        SyntheticClient(transport).invoke(
            "write", {"data": {}}, idempotency_key="write-1", allow_effect=True
        )
    assert len(transport.calls) == 1
    assert caught.value.outcome_unknown is unknown


def test_malformed_effect_response_remains_unknown():
    transport = FakeTransport(body=b"not-json")
    with pytest.raises(IntegrationError) as caught:
        SyntheticClient(transport).invoke(
            "write", {"data": {}}, idempotency_key="write-1", allow_effect=True
        )
    assert len(transport.calls) == 1 and caught.value.outcome_unknown


def test_nested_credentials_never_reach_dispatch():
    transport = FakeTransport()
    with pytest.raises(IntegrationError, match="CREDENTIAL_ARGUMENT"):
        SyntheticClient(transport).invoke(
            "write",
            {"data": {"nested": {"api_key": "synthetic-secret"}}},
            idempotency_key="write-1",
            allow_effect=True,
        )
    assert not transport.calls
