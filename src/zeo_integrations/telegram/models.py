"""Client-owned Telegram receive data, outside Core's unchanged hosted types.

Copyright 2026 Zero Employee Organizations. SPDX-License-Identifier: MIT
Original implementation; contract provenance is recorded in NOTICE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue


class TelegramCondition(StrEnum):
    RECEIVE_CONFLICT = "receive_conflict"
    LEASE_LOST = "lease_lost"
    CURSOR_INVALID = "cursor_invalid"
    CURSOR_STALE = "cursor_stale"
    RECOVERY_REQUIRED = "recovery_required"
    RATE_LIMITED = "rate_limited"
    CONNECTION_NOT_USABLE = "connection_not_usable"
    TRANSIENT_REPLAY = "transient_replay"


class TelegramSendCondition(StrEnum):
    REQUEST_CHANGED = "request_changed"
    RECIPIENT_NOT_ALLOWED = "recipient_not_allowed"
    CALLBACK_NOT_ISSUED = "callback_not_issued"
    CALLBACK_IN_FLIGHT = "callback_in_flight"
    CALLBACK_ANSWERED = "callback_answered"
    RECORD_EXPIRED = "record_expired"
    RATE_LIMITED = "rate_limited"
    CONNECTION_NOT_USABLE = "connection_not_usable"
    IDENTITY_MISMATCH = "identity_mismatch"


class TelegramError(RuntimeError):
    """Sanitized disposition; never retains transport exceptions or raw content."""

    def __init__(
        self,
        code: str,
        *,
        outcome_unknown: bool = False,
        condition: TelegramCondition | TelegramSendCondition | None = None,
        retry_after_seconds: int | None = None,
        execution_id: str | None = None,
        pending_execution_id: str | None = None,
        approval_url: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown
        self.condition = condition
        self.retry_after_seconds = retry_after_seconds
        self.execution_id = execution_id
        self.pending_execution_id = pending_execution_id
        # Existing hosted approval data only; never included in str/repr.
        self.approval_url = approval_url


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    kind: str
    _payload_json: str = field(repr=False)

    def payload(self) -> JsonValue:
        """Return a fresh copy: mutation cannot change the issued batch."""
        return json.loads(self._payload_json)


@dataclass(frozen=True)
class TelegramRetention:
    state: str
    oldest_unacknowledged_age_seconds: int | None
    lost_to_retention: bool


@dataclass(frozen=True)
class TelegramBatch:
    bot_id: int
    epoch: int
    fence: int
    batch_cursor: str | None = field(repr=False)
    updates: tuple[TelegramUpdate, ...] = field(repr=False)
    retention: TelegramRetention
    pending_execution_id: str | None = field(default=None, repr=False)
    recovery_execution_id: str | None = field(default=None, repr=False)

    @property
    def update_ids(self) -> tuple[int, ...]:
        return tuple(update.update_id for update in self.updates)

    @property
    def business_keys(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            (self.bot_id, self.epoch, update.update_id) for update in self.updates
        )


@dataclass(frozen=True)
class TelegramAcknowledgement:
    confirmed_cursor: str = field(repr=False)
    replayed: bool
    execution_id: str
    superseded_execution_ids: tuple[str, ...] = ()


class DurablePrefixVerifier(Protocol):
    """Trusted consumer-store adapter, not data received from Telegram.

    True attests ALL configured consumers durably handled this ordered batch
    prefix, including earlier deliveries. Storage failure/unknown must not
    return True. The implementation owns stable (bot_id, epoch, update_id) dedup,
    changed-payload conflicts, consumer routing and receiver authentication.
    The immutable batch includes the current payload for conflict checking;
    seeing a previously stored ID alone is not a durable disposition of it.
    Epoch is Broker reset provenance, never a lease fence. Empty deliveries
    can have a cursor for Broker-recorded drops; previous consumer deliveries
    must still be durably settled before confirming such a prefix.
    """

    def is_durably_handled(
        self,
        *,
        batch: TelegramBatch,
        consumers: tuple[str, ...],
    ) -> bool: ...


@dataclass(frozen=True)
class TelegramInlineButton:
    """Opaque consumer reference; neither a URL nor business action authority."""

    text: str = field(repr=False)
    callback_data: str = field(repr=False)


@dataclass(frozen=True)
class TelegramSentMessage:
    """Provider acceptance, not a human read or a new success on replay."""

    bot_id: int = field(repr=False)
    chat_id: int = field(repr=False)
    message_id: int = field(repr=False)
    execution_id: str
    replayed: bool


@dataclass(frozen=True)
class TelegramCallbackAnswer:
    """Neutral callback UX acknowledgement, not a business commit or ACK."""

    bot_id: int = field(repr=False)
    execution_id: str
    replayed: bool
