"""Bounded WhatsApp operations with explicit credentials and no automatic retries."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

_SID = re.compile(r"(?:SM|MM)[0-9a-fA-F]{32}\Z")
_ADDRESS = re.compile(r"whatsapp:\+[1-9][0-9]{1,14}\Z")
_STATUSES = frozenset(
    {
        "queued",
        "sending",
        "sent",
        "failed",
        "delivered",
        "undelivered",
        "receiving",
        "received",
        "accepted",
        "scheduled",
        "read",
        "partially_delivered",
        "canceled",
    }
)
_MAX_RESPONSE = 262144
_PAGE_SIZE = 50
_MAX_PAGES = 10


class TwilioRefused(Exception):
    """A definite refusal. Provider text and submitted values are never included."""

    def __init__(self, reason: str, code: int | None = None) -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


class TwilioUnknownOutcome(Exception):
    """The write might have executed. Hold it for reconciliation; do not resend."""

    def __init__(self) -> None:
        super().__init__("Twilio write outcome is unknown")


class TwilioListError(Exception):
    """A failed or incomplete read, never an empty successful listing."""

    def __init__(self) -> None:
        super().__init__("Twilio inbound listing failed or is incomplete")


def _account(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"AC[0-9a-fA-F]{32}", value))


def _address(value: str) -> bool:
    return isinstance(value, str) and bool(_ADDRESS.fullmatch(value))


@dataclass(frozen=True)
class TwilioSendCredentials:
    """Revocable API key; account Auth Tokens are not accepted for sending."""

    account_sid: str = field(repr=False)
    api_key_sid: str = field(repr=False)
    api_key_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _account(self.account_sid)
            or not isinstance(self.api_key_sid, str)
            or not re.fullmatch(r"SK[0-9a-fA-F]{32}", self.api_key_sid)
            or not isinstance(self.api_key_secret, str)
            or not self.api_key_secret
        ):
            raise ValueError("Invalid Twilio API key credentials")


@dataclass(frozen=True)
class MessageReceipt:
    """Provider acceptance is not proof of handset delivery."""

    message_sid: str
    status: str


@dataclass(frozen=True)
class InboundMessage:
    """MessageSid is the caller's dedup key; message content is private."""

    message_sid: str
    from_number: str = field(repr=False)
    to_number: str = field(repr=False)
    body: str = field(repr=False)
    num_media: int
    date_sent: datetime


def _message_fields(
    sid: Any, sender: Any, recipient: Any, body: Any, media: Any
) -> int:
    if (
        not isinstance(sid, str)
        or not _SID.fullmatch(sid)
        or not _address(sender)
        or not _address(recipient)
        or not isinstance(body, str)
        or len(body) > 16000
        or not isinstance(media, str)
        or not re.fullmatch(r"[0-9]{1,2}", media)
        or not 0 <= int(media) <= 10
    ):
        raise ValueError("Invalid WhatsApp message fields")
    return int(media)


class WhatsAppClient:
    """Synchronous transport. Caller owns the injected client's timeout and lifecycle.

    Construct that client with trust_env=False, bounded timeouts, no logging of
    request/response content, and no retry transport. Grants and consent belong
    to the caller; this library does not discover credentials or grant authority.
    """

    def __init__(self, credentials: TwilioSendCredentials, http_client: httpx.Client):
        if not isinstance(credentials, TwilioSendCredentials):
            raise ValueError("Explicit API key credentials required")
        self._credentials = credentials
        self._http = http_client
        self._path = f"/2010-04-01/Accounts/{credentials.account_sid}/Messages.json"

    def _request(self, method: str, *, data=None, params=None) -> tuple[int, Any]:
        credentials = self._credentials
        with self._http.stream(
            method,
            "https://api.twilio.com" + self._path,
            auth=(credentials.api_key_sid, credentials.api_key_secret),
            data=data,
            params=params,
            follow_redirects=False,
        ) as response:
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE:
                    raise ValueError("Provider response exceeds limit")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            return response.status_code, payload

    def _send(
        self, sender: str, recipient: str, fields: dict[str, str]
    ) -> MessageReceipt:
        if not _address(sender) or not _address(recipient):
            raise ValueError("WhatsApp E.164 addresses required")
        try:
            status, payload = self._request(
                "POST", data={"From": sender, "To": recipient, **fields}
            )
        except (httpx.HTTPError, ValueError, OSError):
            raise TwilioUnknownOutcome() from None
        if 400 <= status < 500 and status != 408:
            code = payload.get("code") if isinstance(payload, dict) else None
            code = code if type(code) is int else None
            reason = "outside_customer_window" if code == 63016 else "provider_refused"
            raise TwilioRefused(reason, code)
        if (
            status != 201
            or not isinstance(payload, dict)
            or payload.get("account_sid") != self._credentials.account_sid
            or not isinstance(payload.get("sid"), str)
            or not _SID.fullmatch(payload["sid"])
            or not isinstance(payload.get("status"), str)
            or payload["status"] not in _STATUSES
        ):
            raise TwilioUnknownOutcome()
        return MessageReceipt(payload["sid"], payload["status"])

    def send_text(self, from_number: str, to_number: str, text: str) -> MessageReceipt:
        """Send within the customer window; a refusal never triggers a retry."""
        if not isinstance(text, str) or not 1 <= len(text) <= 1600:
            raise ValueError("Text must contain 1 to 1600 characters")
        return self._send(from_number, to_number, {"Body": text})

    def send_template(
        self,
        from_number: str,
        to_number: str,
        content_sid: str,
        variables: dict[str, str],
    ) -> MessageReceipt:
        """Send an approved Content template, including outside the customer window."""
        if (
            not isinstance(content_sid, str)
            or not re.fullmatch(r"HX[0-9a-fA-F]{32}", content_sid)
            or not isinstance(variables, dict)
            or len(variables) > 100
            or any(
                not isinstance(key, str)
                or not re.fullmatch(r"[1-9][0-9]{0,2}", key)
                or not isinstance(value, str)
                or len(value) > 1600
                for key, value in variables.items()
            )
        ):
            raise ValueError("Invalid WhatsApp template fields")
        encoded = json.dumps(variables, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("Template variables exceed limit")
        return self._send(
            from_number,
            to_number,
            {
                "ContentSid": content_sid,
                "ContentVariables": encoded,
            },
        )

    def inbound_list(
        self,
        to_number: str,
        date_sent_after: datetime,
        *,
        now: datetime,
        max_pages: int = _MAX_PAGES,
    ) -> tuple[InboundMessage, ...]:
        """Read a complete bounded window, or fail without returning partial rows.

        DateSent filtering is day-granular upstream. Each returned timestamp is
        also checked locally. Only a validated continuation token is used; no
        provider-controlled URL or filter is followed.
        """
        if (
            not _address(to_number)
            or not isinstance(date_sent_after, datetime)
            or not isinstance(now, datetime)
            or date_sent_after.tzinfo is None
            or now.tzinfo is None
            or not timedelta(0) <= now - date_sent_after <= timedelta(hours=24)
            or type(max_pages) is not int
            or not 1 <= max_pages <= _MAX_PAGES
        ):
            raise ValueError("A timezone-aware window of at most 24 hours is required")
        records: dict[str, InboundMessage] = {}
        token: str | None = None
        seen_tokens: set[str] = set()
        try:
            for page in range(max_pages):
                params = {
                    "To": to_number,
                    "DateSent": ">="
                    + date_sent_after.astimezone(UTC).date().isoformat(),
                    "PageSize": str(_PAGE_SIZE),
                    "Page": str(page),
                }
                if token is not None:
                    params["PageToken"] = token
                status, payload = self._request("GET", params=params)
                if status != 200 or not isinstance(payload, dict):
                    raise ValueError("Invalid listing")
                messages = payload.get("messages")
                if not isinstance(messages, list) or len(messages) > _PAGE_SIZE:
                    raise ValueError("Invalid listing")
                for item in messages:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid message")
                    media = _message_fields(
                        item.get("sid"),
                        item.get("from"),
                        item.get("to"),
                        item.get("body"),
                        item.get("num_media"),
                    )
                    if (
                        item.get("account_sid") != self._credentials.account_sid
                        or item.get("to") != to_number
                        or item.get("direction") != "inbound"
                        or item.get("status") != "received"
                    ):
                        raise ValueError("Invalid inbound identity")
                    sent = parsedate_to_datetime(item["date_sent"])
                    if sent.tzinfo is None:
                        raise ValueError("Invalid sent timestamp")
                    if sent < date_sent_after or sent > now:
                        continue
                    record = InboundMessage(
                        item["sid"],
                        item["from"],
                        item["to"],
                        item["body"],
                        media,
                        sent,
                    )
                    if (
                        record.message_sid in records
                        and records[record.message_sid] != record
                    ):
                        raise ValueError("Conflicting duplicate message")
                    records[record.message_sid] = record
                next_page = payload.get("next_page_uri")
                if "next_page_uri" not in payload:
                    raise ValueError("Missing pagination proof")
                if next_page is None:
                    return tuple(
                        sorted(
                            records.values(),
                            key=lambda row: (
                                row.date_sent,
                                row.message_sid,
                            ),
                        )
                    )
                if not isinstance(next_page, str) or not next_page or not messages:
                    raise ValueError("Invalid pagination")
                target = urlsplit(next_page)
                if (
                    target.scheme
                    or target.netloc
                    or target.fragment
                    or target.path != self._path
                    or len(next_page) > 4096
                ):
                    raise ValueError("Invalid continuation target")
                pairs = parse_qsl(target.query, strict_parsing=True, max_num_fields=10)
                query = dict(pairs)
                if len(query) != len(pairs):
                    raise ValueError("Duplicate continuation fields")
                # Validate the entire continuation filter set, then reconstruct
                # the request with our fixed host, recipient, and date bound.
                expected = {
                    key: str(value)
                    for key, value in params.items()
                    if key != "PageToken"
                }
                expected["Page"] = str(page + 1)
                next_token = query.pop("PageToken", None)
                if (
                    query != expected
                    or not isinstance(next_token, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", next_token)
                    or next_token in seen_tokens
                ):
                    raise ValueError("Invalid continuation filters")
                token = next_token
                seen_tokens.add(token)
        except (
            httpx.HTTPError,
            ValueError,
            OSError,
            KeyError,
            TypeError,
            OverflowError,
        ):
            raise TwilioListError() from None
        raise TwilioListError()
