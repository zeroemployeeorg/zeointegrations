"""Form webhook authentication against a configured, exact external URL.

Authentication alone does not establish freshness or business authority.
The caller must deduplicate MessageSid and attach its trusted ingress time.
"""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

from zeo_integrations.twilio.client import _account, _message_fields


@dataclass(frozen=True)
class TwilioWebhookCredentials:
    account_sid: str = field(repr=False)
    auth_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _account(self.account_sid)
            or not isinstance(self.auth_token, str)
            or not self.auth_token
        ):
            raise ValueError("Explicit webhook validation credentials required")


@dataclass(frozen=True)
class WebhookMessage:
    message_sid: str
    account_sid: str = field(repr=False)
    from_number: str = field(repr=False)
    to_number: str = field(repr=False)
    body: str = field(repr=False)
    num_media: int


def validate_webhook_signature(
    canonical_url: str,
    fields: dict[str, str],
    signature: str,
    auth_token: str,
) -> bool:
    """Implement the documented URL-plus-sorted-form HMAC-SHA1 protocol.

    Only URL-encoded forms are supported. Never derive canonical_url from
    untrusted Host or forwarding headers. Pass all received fields unchanged.
    """
    try:
        url = urlsplit(canonical_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or len(canonical_url) > 4096
            or not isinstance(signature, str)
            or not re.fullmatch(r"[A-Za-z0-9+/]{27}=", signature)
            or not isinstance(auth_token, str)
            or not auth_token
            or not isinstance(fields, dict)
            or len(fields) > 100
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in fields.items()
            )
        ):
            return False
        signed = canonical_url + "".join(key + fields[key] for key in sorted(fields))
        digest = hmac.new(auth_token.encode(), signed.encode(), hashlib.sha1).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature)
    except (ValueError, TypeError, UnicodeError):
        return False


def parse_whatsapp_webhook(
    canonical_url: str,
    raw_form: bytes,
    signature: str,
    credentials: TwilioWebhookCredentials,
) -> WebhookMessage:
    """Validate before interpreting fields; reject duplicates and malformed forms."""
    try:
        if not isinstance(credentials, TwilioWebhookCredentials):
            raise ValueError
        if not isinstance(raw_form, bytes) or len(raw_form) > 131072:
            raise ValueError
        text = raw_form.decode("utf-8", errors="strict")
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError
        pairs = parse_qsl(
            text,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=100,
        )
        fields = dict(pairs)
        if len(fields) != len(pairs) or not validate_webhook_signature(
            canonical_url,
            fields,
            signature,
            credentials.auth_token,
        ):
            raise ValueError
        sid = fields["MessageSid"]
        if (
            fields["AccountSid"] != credentials.account_sid
            or fields.get("SmsMessageSid", sid) != sid
            or fields.get("SmsStatus", "received") != "received"
            or fields.get("MessageStatus", "received") != "received"
        ):
            raise ValueError
        media = _message_fields(
            sid, fields["From"], fields["To"], fields["Body"], fields["NumMedia"]
        )
        return WebhookMessage(
            sid,
            credentials.account_sid,
            fields["From"],
            fields["To"],
            fields["Body"],
            media,
        )
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("Invalid WhatsApp webhook") from None
