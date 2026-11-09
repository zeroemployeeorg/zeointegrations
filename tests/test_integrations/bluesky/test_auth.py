"""Tests for BlueskyAuthProvider.

Mocks at the session-client boundary (a fake `_BlueskySessionClient`, never
the provider's own methods), matching NotionAuthProvider's own test
pattern (`_FakeSDKClient`) and RULING-235's "mock at the boundary, not the
function under test."
"""

import json
import stat
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zeo_integrations.bluesky import credential_paths as cp
from zeo_integrations.bluesky.auth import BlueskyAuthProvider


def _as_dict(value: object) -> dict[str, object]:
    """Narrow `BlueskyAuthProvider.get_credentials()`'s `object` return
    (the `AuthProviderProtocol` contract) back to the concrete
    `dict[str, object]` it always actually returns, for mypy's benefit in
    these tests."""
    assert isinstance(value, dict)
    return value


class _FakeSessionClient:
    def __init__(
        self, response: dict | None = None, error: Exception | None = None
    ) -> None:
        self._response = response or {
            "accessJwt": "access-tok",
            "refreshJwt": "refresh-tok",
            "did": "did:plc:abc123",
            "handle": "alice.bsky.social",
        }
        self._error = error
        self.create_post_record_calls: list[dict] = []

    def create_session(self, identifier: str, password: str) -> dict:
        if self._error:
            raise self._error
        return self._response

    def create_post_record(self, **kwargs: object) -> dict:
        self.create_post_record_calls.append(kwargs)
        return {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz", "cid": "bafyabc"}


class _UnauthorizedError(Exception):
    status = 401


@pytest.fixture
def creds_relpath(tmp_path: Path) -> Generator[Path]:
    """An actual allowed config path; no credentials are written in the worktree."""
    config = tmp_path / "config"
    scratch = config / "bluesky"
    scratch.mkdir(parents=True)
    with patch.object(cp, "platformdirs_config_dir", return_value=str(config)):
        yield scratch / "creds.json"


@pytest.fixture
def fake_factory() -> MagicMock:
    return MagicMock(side_effect=lambda service_url: _FakeSessionClient())


class TestBlueskyAuthProviderBasics:
    def test_name_property(self) -> None:
        assert BlueskyAuthProvider().name == "Bluesky"

    def test_authenticate_no_credentials_anywhere(self) -> None:
        provider = BlueskyAuthProvider()
        result = provider.authenticate()

        assert result.success is False
        assert result.error == "No Bluesky identifier/app_password provided"

    def test_authenticate_with_provided_credentials(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        result = provider.authenticate(
            identifier="alice.bsky.social",
            app_password="app-pw",  # noqa: S106
        )

        assert result.success is True
        assert provider._access_jwt == "access-tok"  # noqa: S105 -- provider-owned fake credential
        assert provider.authenticated is True
        assert provider.did == "did:plc:abc123"
        assert provider.handle == "alice.bsky.social"
        fake_factory.assert_called_once_with("https://bsky.social")

    def test_authenticate_invalid_credentials_401(self, creds_relpath: Path) -> None:
        factory = MagicMock(
            side_effect=lambda service_url: _FakeSessionClient(
                error=_UnauthorizedError("bad password")
            )
        )
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=factory
        )
        result = provider.authenticate(identifier="alice", app_password="wrong")  # noqa: S106

        assert result.success is False
        assert result.error is not None
        assert "unauthorized" in result.error.lower()

    def test_authenticate_generic_error_wrapped(self, creds_relpath: Path) -> None:
        factory = MagicMock(
            side_effect=lambda service_url: _FakeSessionClient(
                error=RuntimeError("network exploded")
            )
        )
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=factory
        )
        result = provider.authenticate(identifier="alice", app_password="pw")  # noqa: S106

        assert result.success is False
        assert result.error is not None
        assert "network exploded" in result.error

    def test_custom_service_url_is_refused_before_credentials_dispatch(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        result = provider.authenticate(
            identifier="alice",
            app_password="pw",  # noqa: S106
            service_url="https://custom.pds.example",
        )

        assert result.success is False
        fake_factory.assert_not_called()
        assert not creds_relpath.exists()


class TestBlueskyAuthProviderCredentialPersistence:
    def test_authenticate_persists_credentials_0600_atomic(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )

        result = provider.authenticate(identifier="alice", app_password="secret-pw")  # noqa: S106
        assert result.success is True

        assert creds_relpath.exists()
        mode = stat.S_IMODE(creds_relpath.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

        on_disk = json.loads(creds_relpath.read_text())
        assert on_disk["identifier"] == "alice"
        assert on_disk["app_password"] == "secret-pw"  # noqa: S105
        assert on_disk["did"] == "did:plc:abc123"
        assert "saved_at" in on_disk

    def test_authenticate_loads_from_credentials_file_when_not_provided(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        creds_relpath.write_text(
            json.dumps(
                {
                    "identifier": "stored-user.bsky.social",
                    "app_password": "stored-pw",
                    "service_url": "https://bsky.social",
                }
            )
        )
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )

        result = provider.authenticate()

        assert result.success is True
        fake_factory.assert_called_once()

    def test_no_credentials_file_save_returns_false(self) -> None:
        provider = BlueskyAuthProvider(credentials_file=None)
        provider.identifier = "alice"
        provider.app_password = "pw"  # noqa: S105

        assert provider._save_token_data() is False

    def test_directory_creation_failure_returns_false(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        provider.identifier = "alice"
        provider.app_password = "pw"  # noqa: S105

        failing_dir_result = MagicMock(ok=False, error="disk full")
        with patch(
            "zeo_integrations.bluesky.auth.create_directory_with_fallback",
            return_value=failing_dir_result,
        ):
            assert provider._save_token_data() is False

    def test_load_credentials_no_credentials_file_returns_none(self) -> None:
        provider = BlueskyAuthProvider(credentials_file=None)
        assert provider._load_credentials() is None

    def test_load_credentials_file_does_not_exist_returns_none(
        self, creds_relpath: Path
    ) -> None:
        # creds_relpath fixture creates the parent dir but not the file itself.
        provider = BlueskyAuthProvider(credentials_file=str(creds_relpath))
        assert provider._load_credentials() is None

    def test_load_credentials_read_failure_returns_none(
        self, creds_relpath: Path
    ) -> None:
        creds_relpath.write_text("not valid json{{{")
        provider = BlueskyAuthProvider(credentials_file=str(creds_relpath))
        assert provider._load_credentials() is None


class TestBlueskyAuthProviderRefreshAndCredentials:
    def test_refresh_credentials_reauthenticates_with_stored_password(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        provider.authenticate(identifier="alice", app_password="pw")  # noqa: S106

        result = provider.refresh_credentials()

        assert result.success is True
        assert fake_factory.call_count == 2  # one for authenticate, one for refresh

    def test_refresh_credentials_no_prior_auth_errors(self) -> None:
        provider = BlueskyAuthProvider()
        result = provider.refresh_credentials()

        assert result.success is False
        assert result.error is not None
        assert "no bluesky identifier" in result.error.lower()

    def test_get_credentials_returns_current_state(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        provider.authenticate(identifier="alice", app_password="pw")  # noqa: S106

        creds = _as_dict(provider.get_credentials())

        assert creds["identifier"] == "alice"
        assert creds["did"] == "did:plc:abc123"
        assert creds["access_jwt"] == "access-tok"  # noqa: S105

    def test_get_credentials_loads_from_file_when_not_in_memory(
        self, creds_relpath: Path
    ) -> None:
        creds_relpath.write_text(
            json.dumps({"identifier": "loaded-user", "app_password": "loaded-pw"})
        )
        provider = BlueskyAuthProvider(credentials_file=str(creds_relpath))

        creds = _as_dict(provider.get_credentials())

        assert creds["identifier"] == "loaded-user"

    def test_save_credentials_delegates_to_save_token_data(
        self, fake_factory: MagicMock, creds_relpath: Path
    ) -> None:
        provider = BlueskyAuthProvider(
            credentials_file=str(creds_relpath), session_client_factory=fake_factory
        )
        provider.authenticate(identifier="alice", app_password="pw")  # noqa: S106

        assert provider.save_credentials() is True


class TestBuildClient:
    def test_build_client_uses_injected_factory(self, fake_factory: MagicMock) -> None:
        provider = BlueskyAuthProvider(session_client_factory=fake_factory)
        provider.service_url = "https://bsky.social"

        client = provider.build_client()

        fake_factory.assert_called_once_with("https://bsky.social")
        assert isinstance(client, _FakeSessionClient)

    def test_build_client_refuses_unapproved_service_url(
        self, fake_factory: MagicMock
    ) -> None:
        provider = BlueskyAuthProvider(session_client_factory=fake_factory)

        with pytest.raises(ValueError, match="approved HTTPS"):
            provider.build_client("https://other.pds.example")

        fake_factory.assert_not_called()

    def test_build_client_no_factory_returns_real_bluesky_client(self) -> None:
        from zeo_integrations.bluesky.client import BlueskyClient

        provider = BlueskyAuthProvider()
        client = provider.build_client("https://bsky.social")

        assert isinstance(client, BlueskyClient)


@pytest.mark.parametrize("origin", ["http://bsky.social", "https://unapproved.example"])
def test_f1_credential_file_origin_cannot_select_a_password_destination(
    creds_relpath: Path, fake_factory: MagicMock, origin: str
) -> None:
    creds_relpath.write_text(
        json.dumps(
            {"identifier": "alice", "app_password": "fake", "service_url": origin}
        )
    )
    provider = BlueskyAuthProvider(
        credentials_file=str(creds_relpath), session_client_factory=fake_factory
    )
    result = provider.authenticate()
    assert result.success is False
    fake_factory.assert_not_called()


def test_f2_existing_credentials_directory_is_private_before_saving(
    creds_relpath: Path, fake_factory: MagicMock
) -> None:
    creds_relpath.parent.chmod(0o755)
    provider = BlueskyAuthProvider(
        credentials_file=str(creds_relpath), session_client_factory=fake_factory
    )
    result = provider.authenticate(identifier="alice", app_password="fake")
    assert result.success is True
    assert stat.S_IMODE(creds_relpath.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(creds_relpath.stat().st_mode) == 0o600


@pytest.mark.parametrize("relative", [False, True])
def test_f2_rejects_outside_credential_paths(
    creds_relpath: Path, relative: bool
) -> None:
    value = (
        "../../outside.json"
        if relative
        else str(creds_relpath.parent.parent.parent / "outside.json")
    )
    with pytest.raises(ValueError, match="configuration directory"):
        BlueskyAuthProvider(credentials_file=value)
    assert not (creds_relpath.parent.parent.parent / "outside.json").exists()


def test_f2_symlink_escape_cannot_read_or_overwrite_outside_file(
    creds_relpath: Path,
) -> None:
    outside = creds_relpath.parent.parent.parent / "outside.json"
    outside.write_text("unchanged")
    creds_relpath.symlink_to(outside)
    with pytest.raises(ValueError, match="configuration directory"):
        BlueskyAuthProvider(credentials_file=str(creds_relpath))
    assert outside.read_text() == "unchanged"


def test_f2_revalidates_a_path_changed_after_construction(creds_relpath: Path) -> None:
    outside = creds_relpath.parent.parent.parent / "outside.json"
    provider = BlueskyAuthProvider(credentials_file=str(creds_relpath))
    provider.credentials_file = str(outside)
    provider.identifier, provider.app_password = "alice", "fake"
    assert provider.save_credentials() is False
    assert provider._load_credentials() is None
    assert not outside.exists()
