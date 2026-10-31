"Fixed-origin, one-attempt Loops content client. No send or schedule surface."

import json
import math
import threading
import time
from collections import deque
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

import httpx
from pydantic import TypeAdapter


class LoopsError(RuntimeError):
    """Sanitized error; mutation success is not implied by a failed response."""

    outcome_unknown = True


class LoopsRejected(LoopsError):
    """Definite refusal, with a status safe for Broker policy."""

    outcome_unknown = False

    def __init__(self, status: int) -> None:
        super().__init__(f"Loops request refused ({status})")
        self.status = status


class LoopsUnknown(LoopsError):
    """A response does not establish whether a mutation took effect."""


class AuthenticationError(LoopsRejected):
    pass


class NotFoundError(LoopsRejected):
    pass


class ConflictError(LoopsRejected):
    pass


class PayloadTooLargeError(LoopsRejected):
    pass


class RateLimitError(LoopsRejected):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(429)
        self.retry_after = retry_after


class TransportError(LoopsUnknown):
    """One attempt failed; a mutation may have reached the provider."""


class InvalidResponseError(LoopsUnknown):
    pass


def _id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in value)
    ):
        raise ValueError("invalid Loops resource identity")
    return quote(value, safe="")


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        if value.isascii() and value.isdigit():
            return min(float(value), 86400.0)
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            return None
        return min(max(0.0, (stamp - datetime.now(UTC)).total_seconds()), 86400.0)
    except (ValueError, TypeError, OverflowError):
        return None


class _HttpClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or len(api_key) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in api_key)
        ):
            raise ValueError("invalid injected Loops API key")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
        ):
            raise ValueError("timeout must be finite and within 60 seconds")
        self._http = httpx.Client(
            base_url="https://app.loops.so/api/",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept-Encoding": "identity",
            },
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._attempts: deque[float] = deque()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _reserve_attempt(self) -> None:
        # Conservative per-client bound. Broker must aggregate across keys for
        # a team; this cannot infer that different keys belong to the same team.
        with self._lock:
            now = time.monotonic()
            while self._attempts and now - self._attempts[0] >= 60:
                self._attempts.popleft()
            if len(self._attempts) >= 60:
                raise RateLimitError(max(0.0, 60 - (now - self._attempts[0])))
            self._attempts.append(now)

    def _request[T](
        self, method: str, path: str, result_type: type[T], *, body=None, params=None
    ) -> T:
        # Encode once: the byte cap applies to exactly the bytes sent. Escaped
        # LMX can require up to six JSON bytes per UTF-8 input byte.
        encoded = None
        invalid = False
        if body is not None:
            try:
                encoded = json.dumps(
                    body, allow_nan=False, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            except (ValueError, TypeError, RecursionError):
                invalid = True
            if invalid:
                raise ValueError("invalid Loops request data")
            if len(encoded) > 400_000:
                raise ValueError("Loops request exceeds byte bound")
        self._reserve_attempt()
        deadline = time.monotonic() + 30.0
        failure = None
        try:
            with self._http.stream(
                method,
                f"v1/{path}",
                content=encoded,
                params=params,
                headers={"Content-Type": "application/json"}
                if encoded is not None
                else None,
            ) as response:
                _check_deadline(deadline)
                status = response.status_code
                error = {
                    401: AuthenticationError,
                    403: AuthenticationError,
                    404: NotFoundError,
                    409: ConflictError,
                    413: PayloadTooLargeError,
                }.get(status)
                if error is not None:
                    raise error(status)
                if status == 429:
                    raise RateLimitError(
                        _retry_after(response.headers.get("Retry-After"))
                    )
                if status in {400, 405, 410, 415, 422}:
                    raise LoopsRejected(status)
                if not 200 <= status < 300:
                    raise LoopsUnknown(f"Loops request outcome unknown ({status})")
                if (
                    response.headers.get("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise InvalidResponseError("encoded Loops response refused")
                content = bytearray()
                # No chunk_size: buffering would hide a slow trickle from the clock.
                for chunk in response.iter_bytes():
                    _check_deadline(deadline)
                    if len(content) + len(chunk) > 1_048_576:
                        raise InvalidResponseError("Loops response exceeds byte bound")
                    content.extend(chunk)
            _check_deadline(deadline)
            data = json.loads(content)
            if (
                isinstance(data, dict)
                and "success" in data
                and data["success"] is not True
            ):
                raise InvalidResponseError("Loops response did not confirm success")
            result = TypeAdapter(result_type).validate_python(data, strict=True)
            _check_deadline(deadline)
            return result
        except httpx.HTTPError:
            failure = TransportError("Loops transport failed; outcome may be unknown")
        except (ValueError, RecursionError):
            failure = InvalidResponseError("Loops returned invalid response data")
        # Outside the handler: no retained exception context with request/PII.
        raise failure


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TransportError("Loops response deadline exceeded; outcome may be unknown")


def _asset_url(value: str, *, asset_id: str | None = None) -> None:
    invalid = False
    try:
        parsed = urlsplit(value)
        invalid = (
            not value.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment != ""
            or parsed.netloc != parsed.hostname
        )
        if asset_id is not None:
            invalid |= (
                parsed.hostname != "loops-assets.s3.amazonaws.com"
                or parsed.path != f"/uploads/{_id(asset_id)}"
            )
        else:
            invalid |= parsed.hostname not in {"assets.loops.so", "images.vialoops.com"}
    except (ValueError, TypeError):
        invalid = True
    if invalid:
        raise InvalidResponseError("unapproved Loops asset URL")
