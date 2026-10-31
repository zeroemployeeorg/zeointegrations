"Raw-byte signature check from Broker reader-database-design rev e, section 3.2."

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass


class WebhookVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedWebhook:
    webhook_id: str
    timestamp: int
    body_sha256: str


def verify_webhook(
    raw_body: bytes,
    *,
    webhook_id: str,
    timestamp: str,
    signature: str,
    secrets: tuple[str, ...],
    now: int | None = None,
) -> VerifiedWebhook:
    """Verify bytes before parsing; receiver owns replay deduplication and commits.

    Up to two injected secrets permit rotation. Nothing is persisted or logged.
    This local cryptographic result is not proof of consent or a committed event.
    """
    if (
        not isinstance(raw_body, bytes)
        or len(raw_body) > 8_000_000
        or not isinstance(webhook_id, str)
        or not 1 <= len(webhook_id) <= 256
        or any(ord(c) < 33 or ord(c) > 126 for c in webhook_id)
        or "." in webhook_id
        or not isinstance(timestamp, str)
        or not timestamp.isascii()
        or not timestamp.isdigit()
        or len(timestamp) > 16
        or not isinstance(signature, str)
        or len(signature) > 8192
        or not isinstance(secrets, tuple)
        or not 1 <= len(secrets) <= 2
    ):
        raise WebhookVerificationError("invalid webhook input")
    current = int(time.time()) if now is None else now
    if type(current) is not int or abs(current - int(timestamp)) > 300:
        raise WebhookVerificationError("webhook timestamp outside tolerance")
    signed = (
        webhook_id.encode("ascii") + b"." + timestamp.encode("ascii") + b"." + raw_body
    )
    candidates = []
    for part in signature.split():
        version, separator, value = part.partition(",")
        if version != "v1" or not separator:
            continue
        try:
            candidate = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(candidate) == 32:
            candidates.append(candidate)
    accepted = False
    for secret in secrets:
        key = None
        try:
            key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
        except (AttributeError, binascii.Error, ValueError):
            pass
        if not key:
            raise WebhookVerificationError("invalid injected webhook secret")
        expected = hmac.digest(key, signed, "sha256")
        for candidate in candidates:
            accepted |= hmac.compare_digest(expected, candidate)
    if not accepted:
        raise WebhookVerificationError("webhook signature mismatch")
    return VerifiedWebhook(
        webhook_id, int(timestamp), hashlib.sha256(raw_body).hexdigest()
    )
