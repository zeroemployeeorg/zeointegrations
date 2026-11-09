"""Tests for BlueskyClient -- the AT Protocol HTTP boundary.

Mocks at the true external boundary (requests.Session.post), never the
function under test (RULING-235) -- every assertion below is against the
real BlueskyClient code building real request payloads and parsing real
response shapes, with only the network call itself faked.
"""

from unittest.mock import MagicMock

import pytest

from zeo_integrations.bluesky.client import BlueskyAPIError, BlueskyClient


def _mock_response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestCreateSession:
    def test_success_returns_session_dict_and_stores_access_jwt(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(
            200,
            {
                "accessJwt": "access-token",  # noqa: S106 -- fake test value
                "refreshJwt": "refresh-token",  # noqa: S106 -- fake test value
                "did": "did:plc:abc123",
                "handle": "alice.bsky.social",
            },
        )
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        result = client.create_session("alice.bsky.social", "app-password-123")  # noqa: S106 -- fake test value

        assert result["accessJwt"] == "access-token"  # noqa: S105
        assert result["did"] == "did:plc:abc123"
        assert client._access_jwt == "access-token"  # noqa: S105

        # The real request shape, per com.atproto.server.createSession.
        session.post.assert_called_once()
        call = session.post.call_args
        assert (
            call.args[0] == "https://bsky.social/xrpc/com.atproto.server.createSession"
        )
        assert call.kwargs["json"] == {
            "identifier": "alice.bsky.social",
            "password": "app-password-123",
        }

    def test_service_url_trailing_slash_stripped(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(200, {"accessJwt": "t"})  # noqa: S106
        client = BlueskyClient(service_url="https://bsky.social/", session=session)

        client.create_session("alice", "pw")  # noqa: S106

        call = session.post.call_args
        assert (
            call.args[0] == "https://bsky.social/xrpc/com.atproto.server.createSession"
        )

    def test_401_raises_bluesky_api_error_with_status(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(
            401,
            {
                "error": "AuthenticationRequired",
                "message": "Invalid identifier or password",
            },
        )
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        with pytest.raises(BlueskyAPIError) as exc_info:
            client.create_session("alice", "wrong-password")  # noqa: S106

        assert exc_info.value.status == 401
        assert "Invalid identifier or password" in str(exc_info.value)

    def test_error_response_non_json_body_falls_back_to_text(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.status_code = 500
        response.json.side_effect = ValueError("not json")
        response.text = "Internal Server Error"
        session.post.return_value = response
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        with pytest.raises(BlueskyAPIError) as exc_info:
            client.create_session("alice", "pw")  # noqa: S106

        assert "Internal Server Error" in str(exc_info.value)
        assert exc_info.value.status == 500


class TestCreatePostRecord:
    def test_success_builds_correct_record_shape(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(
            200, {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz", "cid": "bafyabc"}
        )
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        result = client.create_post_record(
            repo="did:plc:abc123",
            text="hello world",
            access_jwt="access-token",  # noqa: S106
            created_at="2026-08-31T00:00:00+00:00",
        )

        assert result["uri"] == "at://did:plc:abc/app.bsky.feed.post/xyz"
        assert result["cid"] == "bafyabc"

        call = session.post.call_args
        assert call.args[0] == "https://bsky.social/xrpc/com.atproto.repo.createRecord"
        assert call.kwargs["json"] == {
            "repo": "did:plc:abc123",
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": "hello world",
                "createdAt": "2026-08-31T00:00:00+00:00",
            },
        }
        assert call.kwargs["headers"] == {"Authorization": "Bearer access-token"}

    def test_facets_included_when_provided(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(200, {"uri": "at://x", "cid": "y"})
        client = BlueskyClient(service_url="https://bsky.social", session=session)
        facets = [
            {
                "index": {"byteStart": 0, "byteEnd": 5},
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": "https://example.com",
                    }
                ],
            }
        ]

        client.create_post_record(
            repo="did:plc:abc",
            text="hello",
            access_jwt="tok",  # noqa: S106
            facets=facets,
        )

        record = session.post.call_args.kwargs["json"]["record"]
        assert record["facets"] == facets

    def test_created_at_defaults_to_now_when_omitted(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(200, {"uri": "at://x", "cid": "y"})
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        client.create_post_record(repo="did:plc:abc", text="hi", access_jwt="tok")  # noqa: S106

        record = session.post.call_args.kwargs["json"]["record"]
        assert "createdAt" in record
        assert record["createdAt"]  # non-empty ISO string

    def test_error_response_raises(self) -> None:
        session = MagicMock()
        session.post.return_value = _mock_response(
            400, {"error": "InvalidRequest", "message": "text too long"}
        )
        client = BlueskyClient(service_url="https://bsky.social", session=session)

        with pytest.raises(BlueskyAPIError) as exc_info:
            client.create_post_record(
                repo="did:plc:abc", text="x" * 5000, access_jwt="tok"
            )  # noqa: S106

        assert exc_info.value.status == 400
        assert "text too long" in str(exc_info.value)


class TestClientConstruction:
    def test_default_session_is_real_requests_session(self) -> None:
        import requests

        client = BlueskyClient()
        assert isinstance(client._session, requests.Session)
        assert client.service_url == "https://bsky.social"


@pytest.mark.parametrize(
    "origin",
    [
        "http://bsky.social",
        "https://unapproved.example",
        "https://bsky.social.evil",
        "https://user:pw@bsky.social",
        "https://bsky.social:444",
        "https://bsky.social/path",
        "https://bsky.social?redirect=evil",
        "https://bsky.social#evil",
        "https://bsky.social\t",
    ],
)
def test_f1_refused_origin_never_receives_credentials(origin: str) -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="approved HTTPS"):
        BlueskyClient(service_url=origin, session=session).create_session(
            "alice", "fake"
        )
    session.post.assert_not_called()


def test_f1_checks_mutated_origin_and_disables_both_post_redirects() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        200, {"accessJwt": "fake", "uri": "at://ok"}
    )
    client = BlueskyClient(session=session)
    client.create_session("alice", "fake")
    client.create_post_record(repo="did:plc:alice", text="text", access_jwt="fake")
    assert session.post.call_count == 2
    assert all(
        call.kwargs["allow_redirects"] is False for call in session.post.call_args_list
    )
    client.service_url = "http://bsky.social"
    with pytest.raises(ValueError, match="approved HTTPS"):
        client.create_session("alice", "fake")
    assert session.post.call_count == 2


def test_f1_redirect_is_refused_instead_of_used_as_a_session() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(302, {"message": "Moved"})
    with pytest.raises(BlueskyAPIError) as error:
        BlueskyClient(session=session).create_session("alice", "fake")
    assert error.value.status == 302
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("json_error", [False, True])
def test_f4_server_error_diagnostic_is_bounded(json_error: bool) -> None:
    session = MagicMock()
    response = _mock_response(500, {"message": "x" * 100_000})
    response.text = "x" * 100_000
    if json_error:
        response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    with pytest.raises(BlueskyAPIError) as error:
        BlueskyClient(session=session).create_session("alice", "fake")
    assert len(str(error.value)) < 320
    assert str(error.value).endswith("x" * 256)
    assert error.value.status == 500
