"""Credentialless one-call clients for agreed Connect Telegram operations.

Copyright 2026 Zero Employee Organizations. SPDX-License-Identifier: MIT
Original implementation; no Telegram SDK or direct provider transport.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

from pydantic import JsonValue, ValidationError
from zeo_core.integrations.hosted.client import (
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
)

from .models import (
    DurablePrefixVerifier,
    TelegramAcknowledgement,
    TelegramBatch,
    TelegramCallbackAnswer,
    TelegramCondition,
    TelegramError,
    TelegramInlineButton,
    TelegramRetention,
    TelegramSendCondition,
    TelegramSentMessage,
    TelegramUpdate,
)

_CONDITION_CODES = {
    TelegramCondition.RECEIVE_CONFLICT: "REQUEST_REFUSED",
    TelegramCondition.LEASE_LOST: "REQUEST_REFUSED",
    TelegramCondition.CURSOR_INVALID: "REQUEST_REFUSED",
    TelegramCondition.CURSOR_STALE: "REQUEST_REFUSED",
    TelegramCondition.RECOVERY_REQUIRED: "REQUEST_REFUSED",
    TelegramCondition.TRANSIENT_REPLAY: "REQUEST_REFUSED",
    TelegramCondition.RATE_LIMITED: "RATE_LIMITED",
    TelegramCondition.CONNECTION_NOT_USABLE: "CONNECTION_REPAIR_REQUIRED",
}


_SEND_CONDITION_CODES = {
    TelegramSendCondition.REQUEST_CHANGED: {"REQUEST_REFUSED"},
    TelegramSendCondition.RECIPIENT_NOT_ALLOWED: {"REQUEST_REFUSED"},
    TelegramSendCondition.CALLBACK_NOT_ISSUED: {"REQUEST_REFUSED"},
    TelegramSendCondition.CALLBACK_IN_FLIGHT: {"REQUEST_REFUSED"},
    TelegramSendCondition.CALLBACK_ANSWERED: {"REQUEST_REFUSED"},
    TelegramSendCondition.RECORD_EXPIRED: {"REQUEST_REFUSED"},
    TelegramSendCondition.RATE_LIMITED: {"RATE_LIMITED"},
    TelegramSendCondition.CONNECTION_NOT_USABLE: {
        "CONNECTION_REVOKED",
        "CONNECTION_REPAIR_REQUIRED",
    },
    TelegramSendCondition.IDENTITY_MISMATCH: {"RESULT_AMBIGUOUS"},
}


def _send_condition(
    response: HostedOperationResponse, receipt: dict[str, JsonValue]
) -> tuple[TelegramSendCondition | None, int | None]:
    raw = receipt.get("condition")
    if raw is None:
        return None, None
    condition = None
    try:
        condition = TelegramSendCondition(raw)
    except (ValueError, TypeError):
        pass
    if condition is None:
        raise TelegramError("UNKNOWN_CONDITION", outcome_unknown=True)
    error = response.normalized_error
    expected_status = (
        "ambiguous"
        if condition is TelegramSendCondition.IDENTITY_MISMATCH
        else "failed_safe"
    )
    if (
        response.status.value != expected_status
        or error is None
        or error.code.value not in _SEND_CONDITION_CODES[condition]
    ):
        raise TelegramError("INVALID_RECEIPT", outcome_unknown=True)
    delay = None
    if condition is TelegramSendCondition.RATE_LIMITED:
        delay = _integer(receipt.get("retry_after_seconds"))
    return condition, delay


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise TelegramError("INVALID_SHAPE")
    invalid = True
    try:
        invalid = len(value.encode("utf-8")) > maximum
    except UnicodeError:
        pass
    if invalid or any(ord(char) < 32 for char in value):
        raise TelegramError("INVALID_SHAPE")
    return value


def _integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise TelegramError("INVALID_SHAPE")
    return value


def _object(value: object, keys: set[str]) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TelegramError("INVALID_SHAPE")
    return cast(dict[str, JsonValue], value)


def _recovery_context(receipt: dict[str, JsonValue]) -> tuple[str | None, str | None]:
    pending = None
    recovery = None
    if "pending_ack" in receipt:
        value = _object(receipt["pending_ack"], {"state", "execution_id"})
        if value["state"] != "unresolved_stale":
            raise TelegramError("INVALID_RECEIPT")
        pending = _text(value["execution_id"], 200)
    if "recovery" in receipt:
        value = _object(receipt["recovery"], {"open", "supersedes"})
        if value["open"] is not True:
            raise TelegramError("INVALID_RECEIPT")
        recovery = _text(value["supersedes"], 200)
    if pending is not None and recovery is not None and pending != recovery:
        raise TelegramError("PENDING_INTENT_MISMATCH")
    return pending, recovery


@dataclass(frozen=True)
class _PendingAck:
    batch: TelegramBatch | None
    key: str | None
    execution_id: str | None = None
    parent: _PendingAck | None = None
    stale: bool = False


class _HostedTelegramCalls:
    """Shared bounded hosted call, never a provider transport."""

    _connection_id: str
    _client: HostedConnectionClient
    _max_bytes: int
    _clock: Callable[[], float]

    def _invoke(
        self,
        operation: str,
        arguments: dict[str, JsonValue],
        key: str,
        deadline: float,
        *,
        effect: bool,
        send: bool = False,
    ) -> HostedOperationResponse:
        _text(key, 200)
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (float, int))
            or not math.isfinite(deadline)
            or not math.isfinite(self._clock())
            or self._clock() >= deadline
        ):
            raise TelegramError("DEADLINE")
        request = HostedOperationRequest(
            connection_id=self._connection_id,
            operation_id=operation,
            arguments=arguments,
            idempotency_key=key,
        )
        # No expect: it is absent from the Broker's pinned Core 0.12.0.
        unavailable = False
        try:
            response = self._client.invoke(request)
            if not isinstance(response, HostedOperationResponse):
                raise ValueError
            response = HostedOperationResponse.model_validate(response.model_dump())
            encoded = json.dumps(
                response.model_dump(mode="json"), allow_nan=False
            ).encode("utf-8")
            if len(encoded) > self._max_bytes:
                raise ValueError
        except Exception:
            unavailable = True
        # Raise outside the handler: do not chain a private transport exception.
        if unavailable:
            raise TelegramError("UNAVAILABLE", outcome_unknown=effect)
        if not math.isfinite(self._clock()) or self._clock() >= deadline:
            raise TelegramError("DEADLINE", outcome_unknown=effect)
        receipt = response.receipt or {}
        replayed = receipt.get("replayed", False)
        if type(replayed) is not bool:
            raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
        pending_execution_id = None
        if not send:
            pending_execution_id, _ = _recovery_context(receipt)
        condition: TelegramCondition | TelegramSendCondition | None = None
        retry_after = None
        generic_refusal = send and response.status.value in {
            "refused",
            "failed_safe",
            "approval_required",
        }
        if "condition" not in receipt and not generic_refusal:
            raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
        if send:
            condition, retry_after = _send_condition(response, receipt)
        elif receipt["condition"] is not None:
            try:
                condition = TelegramCondition(receipt["condition"])
            except (ValueError, TypeError):
                raise TelegramError(
                    "UNKNOWN_CONDITION", outcome_unknown=effect
                ) from None
            error = response.normalized_error
            if error is None or error.code.value != _CONDITION_CODES[condition]:
                raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
            if condition is TelegramCondition.TRANSIENT_REPLAY and not replayed:
                raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
            if (
                condition
                in {
                    TelegramCondition.CURSOR_STALE,
                    TelegramCondition.RECOVERY_REQUIRED,
                }
                and response.status.value != "failed_safe"
            ):
                raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
            if condition is TelegramCondition.RATE_LIMITED:
                retry_after = _integer(receipt.get("retry_after_seconds"))
        if response.status.value != "confirmed":
            raise TelegramError(
                response.status.value.upper(),
                outcome_unknown=response.status.value == "ambiguous",
                condition=condition,
                retry_after_seconds=retry_after,
                execution_id=response.execution_id,
                pending_execution_id=pending_execution_id,
                approval_url=(
                    str(response.approval_url)
                    if send and response.approval_url is not None
                    else None
                ),
            )
        if condition is not None:
            raise TelegramError("INVALID_RECEIPT", outcome_unknown=effect)
        if replayed and not effect:
            raise TelegramError("REPLAY_WITHOUT_BATCH")
        if response.artifact is not None or not isinstance(response.result, dict):
            raise TelegramError("INVALID_RESULT", outcome_unknown=effect)
        return response


class TelegramClient(_HostedTelegramCalls):
    """Receive slice only; does not send messages, poll in a loop or own a DB.

    The injected hosted transport must enforce its configured call deadline
    and response-byte ceiling. Local checks before/after invoke cannot cancel
    a blocking transport. No timeout is invented in the pinned Core request.
    """

    def __init__(
        self,
        client: HostedConnectionClient,
        *,
        connection_id: str,
        consumers: tuple[str, ...],
        verifier: DurablePrefixVerifier,
        max_response_bytes: int = 1_048_576,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection_id = _text(connection_id, 200)
        if not consumers or type(consumers) is not tuple:
            raise TelegramError("CONSUMER_CONFIGURATION")
        self._consumers = tuple(_text(consumer, 200) for consumer in consumers)
        if len(set(self._consumers)) != len(self._consumers):
            raise TelegramError("CONSUMER_CONFIGURATION")
        self._max_bytes = _integer(max_response_bytes, 1)
        self._client = client
        self._verifier = verifier
        self._clock = clock
        self._bot_id: int | None = None
        self._epoch: int | None = None
        self._last_batch: TelegramBatch | None = None
        self._pending_ack: _PendingAck | None = None

    def read_updates(
        self, *, idempotency_key: str, deadline: float, maximum: int = 100
    ) -> TelegramBatch:
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise TelegramError("READ_LIMIT")
        response = self._invoke(
            "telegram.updates.read",
            {"max": maximum},
            idempotency_key,
            deadline,
            effect=False,
        )
        result = _object(
            response.result,
            {
                "bot_id",
                "epoch",
                "fence",
                "batch_cursor",
                "updates",
                "retention",
                "lost_to_retention",
            },
        )
        bot_id = _integer(result["bot_id"], 1)
        if self._bot_id is not None and bot_id != self._bot_id:
            raise TelegramError("BOT_CHANGED")
        epoch = _integer(result["epoch"])
        if self._epoch is not None and epoch < self._epoch:
            raise TelegramError("EPOCH_REGRESSED")
        fence = _integer(result["fence"])
        cursor = result["batch_cursor"]
        if cursor is not None:
            cursor = _text(cursor, 4096)
        rows = result["updates"]
        if not isinstance(rows, list) or len(rows) > maximum:
            raise TelegramError("INVALID_BATCH")
        updates = []
        seen_ids = set()
        for row in rows:
            if not isinstance(row, dict):
                raise TelegramError("INVALID_UPDATE")
            kinds = set(row) - {"update_id"}
            if len(kinds) != 1 or not kinds <= {"message", "callback_query"}:
                raise TelegramError("UNSUPPORTED_UPDATE")
            kind = next(iter(kinds))
            update_id = _integer(row.get("update_id"))
            if update_id in seen_ids or not isinstance(row[kind], dict):
                raise TelegramError("INVALID_UPDATE")
            seen_ids.add(update_id)
            updates.append(
                TelegramUpdate(
                    update_id, kind, json.dumps(row[kind], ensure_ascii=True)
                )
            )
        if updates and cursor is None:
            raise TelegramError("MISSING_CURSOR")
        retention = _object(
            result["retention"], {"state", "oldest_unacknowledged_age_seconds"}
        )
        state = retention["state"]
        if state not in ("ok", "warn", "critical", "expired"):
            raise TelegramError("INVALID_RETENTION")
        age = retention["oldest_unacknowledged_age_seconds"]
        if age is not None:
            age = _integer(age)
        lost = result["lost_to_retention"]
        if type(lost) is not bool:
            raise TelegramError("INVALID_RETENTION")
        pending_id, recovery_id = _recovery_context(response.receipt or {})
        batch = TelegramBatch(
            bot_id,
            epoch,
            fence,
            cursor,
            tuple(updates),
            TelegramRetention(str(state), age, lost),
            pending_id,
            recovery_id,
        )
        self._bot_id = bot_id
        self._epoch = epoch
        self._last_batch = batch
        return batch

    def acknowledge(
        self, batch: TelegramBatch, *, idempotency_key: str, deadline: float
    ) -> TelegramAcknowledgement:
        pending = self._pending_ack
        if pending is not None:
            if batch is not pending.batch or idempotency_key != pending.key:
                raise TelegramError("RECONCILE_ORIGINAL")
            attempt = pending
        else:
            if batch is not self._last_batch:
                raise TelegramError("UNISSUED_BATCH")
            if batch.recovery_execution_id or batch.pending_execution_id:
                raise TelegramError("RECOVERY_REQUIRED")
            attempt = _PendingAck(batch, idempotency_key)
        return self._submit_ack(attempt, deadline)

    def acknowledge_recovery(
        self, batch: TelegramBatch, *, idempotency_key: str, deadline: float
    ) -> TelegramAcknowledgement:
        """Explicit NEW prefix effect linked to Broker-asserted old uncertainty.

        No key is generated and no old outcome is inferred from a READ. The
        consumer's durable store must retain the original intent and persist
        the returned links; this object alone is not a crash-safe outbox.
        """
        pending = self._pending_ack
        if (
            pending is not None
            and batch is pending.batch
            and idempotency_key == pending.key
            and pending.parent is not None
        ):
            return self._submit_ack(pending, deadline)
        if batch is not self._last_batch or batch.recovery_execution_id is None:
            raise TelegramError("RECOVERY_NOT_GRANTED")
        previous_id = batch.recovery_execution_id
        if pending is None:
            # Restart: the authenticated Broker asserts the open E1; this is
            # only its reference, not proof or reconstruction of its outcome.
            parent = _PendingAck(None, None, previous_id)
        else:
            if pending.execution_id != previous_id:
                raise TelegramError("PENDING_INTENT_MISMATCH")
            parent = pending
        cursor = parent
        depth = 0
        while cursor is not None:
            if cursor.key == idempotency_key:
                raise TelegramError("RECOVERY_REQUIRES_NEW_KEY")
            depth += 1
            if depth > 16:
                raise TelegramError("RECOVERY_HISTORY_FULL")
            cursor = cursor.parent
        return self._submit_ack(
            _PendingAck(batch, idempotency_key, parent=parent), deadline
        )

    def _submit_ack(
        self, attempt: _PendingAck, deadline: float
    ) -> TelegramAcknowledgement:
        if attempt.stale:
            raise TelegramError("RECOVERY_REQUIRED")
        batch = attempt.batch
        key = attempt.key
        if batch is None or key is None or batch.batch_cursor is None:
            raise TelegramError("EMPTY_PREFIX")
        _text(key, 200)
        try:
            durable = self._verifier.is_durably_handled(
                batch=batch,
                consumers=self._consumers,
            )
        except Exception:
            raise TelegramError("DURABILITY_UNKNOWN") from None
        if durable is not True:
            raise TelegramError("PREFIX_NOT_DURABLE")
        arguments: dict[str, JsonValue] = {"batch_cursor": batch.batch_cursor}
        superseded_ids = []
        parent = attempt.parent
        while parent is not None:
            if parent.execution_id is None:
                raise TelegramError("RECOVERY_NOT_GRANTED")
            superseded_ids.append(parent.execution_id)
            parent = parent.parent
        if attempt.parent is not None:
            arguments["supersedes_execution_id"] = attempt.parent.execution_id
        try:
            response = self._invoke(
                "telegram.updates.acknowledge",
                arguments,
                key,
                deadline,
                effect=True,
            )
            result = _object(response.result, {"acknowledged", "confirmed_cursor"})
            if result["acknowledged"] is not True:
                raise TelegramError("INVALID_ACK", outcome_unknown=True)
            cursor = _text(result["confirmed_cursor"], 4096)
            execution_id = _text(response.execution_id, 200)
            if execution_id in superseded_ids or (
                attempt.execution_id is not None
                and execution_id != attempt.execution_id
            ):
                raise TelegramError("INVALID_ACK", outcome_unknown=True)
        except TelegramError as error:
            if error.code in {
                "INVALID_SHAPE",
                "INVALID_ACK",
                "INVALID_RECEIPT",
                "UNKNOWN_CONDITION",
                "INVALID_RESULT",
                "PENDING_INTENT_MISMATCH",
            }:
                error.outcome_unknown = True
            if error.condition is TelegramCondition.CURSOR_STALE:
                self._last_batch = None
                old_id = error.pending_execution_id
                if old_id is not None:
                    if old_id in superseded_ids or attempt.execution_id not in (
                        None,
                        old_id,
                    ):
                        raise TelegramError(
                            "PENDING_INTENT_MISMATCH", outcome_unknown=True
                        ) from None
                    self._pending_ack = replace(
                        attempt, execution_id=old_id, stale=True
                    )
                elif self._pending_ack is attempt:
                    self._pending_ack = replace(attempt, stale=True)
            elif error.outcome_unknown:
                if error.code == "AMBIGUOUS" and error.execution_id is not None:
                    if attempt.execution_id not in (None, error.execution_id):
                        raise TelegramError(
                            "PENDING_INTENT_MISMATCH", outcome_unknown=True
                        ) from None
                    attempt = replace(attempt, execution_id=error.execution_id)
                self._pending_ack = attempt
            raise
        except (ValidationError, ValueError, TypeError):
            self._pending_ack = attempt
            raise TelegramError("INVALID_ACK", outcome_unknown=True) from None
        self._pending_ack = None
        self._last_batch = None
        return TelegramAcknowledgement(
            cursor, response.replayed, execution_id, tuple(superseded_ids)
        )


def _message_text(value: object, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise TelegramError("INVALID_SHAPE")
    encoded = None
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        pass
    if encoded is None:
        raise TelegramError("INVALID_SHAPE")
    if len(encoded) > maximum or any(
        ord(char) < 32 and char not in "\n\r\t" for char in value
    ):
        raise TelegramError("INVALID_SHAPE")
    return value


def _chat_id(value: object) -> int:
    if type(value) is not int or value == 0 or not -(2**63) <= value < 2**63:
        raise TelegramError("INVALID_SHAPE")
    return value


@dataclass(frozen=True, repr=False)
class _PendingEffect:
    operation: str
    arguments_json: str
    key: str
    execution_id: str | None = None
    uncertain: bool = False


class TelegramSender(_HostedTelegramCalls):
    """Send/answer under Broker per-call admission with a trusted bot pin.

    Expected identity must come from trusted configuration or the authenticated
    connection listing, never the response being checked. The owning consumer
    durably records intents and decisions. This serial object is not an outbox.
    """

    def __init__(
        self,
        client: HostedConnectionClient,
        *,
        connection_id: str,
        expected_bot_id: int,
        max_response_bytes: int = 1_048_576,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection_id = _text(connection_id, 200)
        self._expected_bot_id = _integer(expected_bot_id, 1)
        self._max_bytes = _integer(max_response_bytes, 1)
        self._client = client
        self._clock = clock
        self._pending_effect: _PendingEffect | None = None

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        idempotency_key: str,
        deadline: float,
        inline_keyboard: tuple[tuple[TelegramInlineButton, ...], ...] | None = None,
    ) -> TelegramSentMessage:
        chat = _chat_id(chat_id)
        arguments: dict[str, JsonValue] = {
            "chat_id": chat,
            "text": _message_text(text, 4096),
        }
        if inline_keyboard is not None:
            if type(inline_keyboard) is not tuple or not 1 <= len(inline_keyboard) <= 8:
                raise TelegramError("INVALID_KEYBOARD")
            rows: list[JsonValue] = []
            for row in inline_keyboard:
                if type(row) is not tuple or not 1 <= len(row) <= 8:
                    raise TelegramError("INVALID_KEYBOARD")
                buttons: list[JsonValue] = []
                for button in row:
                    if type(button) is not TelegramInlineButton:
                        raise TelegramError("INVALID_KEYBOARD")
                    buttons.append(
                        {
                            "text": _text(button.text, 128),
                            "callback_data": _text(button.callback_data, 64),
                        }
                    )
                rows.append(buttons)
            arguments["inline_keyboard"] = rows
        response = self._submit_effect(
            "telegram.message.send",
            arguments,
            idempotency_key,
            deadline,
            expected_chat=chat,
        )
        result = cast(dict[str, JsonValue], response.result)
        return TelegramSentMessage(
            self._expected_bot_id,
            chat,
            cast(int, result["message_id"]),
            response.execution_id,
            response.replayed,
        )

    def answer_callback(
        self,
        callback_query_id: str,
        *,
        idempotency_key: str,
        deadline: float,
        text: str | None = None,
        show_alert: bool = False,
    ) -> TelegramCallbackAnswer:
        if type(show_alert) is not bool:
            raise TelegramError("INVALID_SHAPE")
        arguments: dict[str, JsonValue] = {
            "callback_query_id": _text(callback_query_id, 256),
            "show_alert": show_alert,
        }
        if text is not None:
            arguments["text"] = _message_text(text, 200, empty=True)
        response = self._submit_effect(
            "telegram.callback.answer",
            arguments,
            idempotency_key,
            deadline,
        )
        return TelegramCallbackAnswer(
            self._expected_bot_id,
            response.execution_id,
            response.replayed,
        )

    def _submit_effect(
        self,
        operation: str,
        arguments: dict[str, JsonValue],
        key: str,
        deadline: float,
        *,
        expected_chat: int | None = None,
    ) -> HostedOperationResponse:
        _text(key, 200)
        body = json.dumps(arguments, ensure_ascii=True, sort_keys=True, allow_nan=False)
        previous = self._pending_effect
        if previous is not None and (
            previous.operation != operation
            or previous.arguments_json != body
            or previous.key != key
        ):
            raise TelegramError(
                "RECONCILE_ORIGINAL",
                outcome_unknown=previous.uncertain,
                execution_id=previous.execution_id,
            )
        attempt = previous or _PendingEffect(operation, body, key)
        try:
            response = self._invoke(
                operation,
                arguments,
                key,
                deadline,
                effect=True,
                send=True,
            )
            keys = (
                {"bot_id", "chat_id", "message_id"}
                if expected_chat is not None
                else {"bot_id", "answered"}
            )
            result = _object(response.result, keys)
            execution_id = _text(response.execution_id, 200)
            if (
                attempt.execution_id is not None
                and execution_id != attempt.execution_id
            ):
                raise TelegramError("INVALID_RECEIPT", outcome_unknown=True)
            if _integer(result["bot_id"], 1) != self._expected_bot_id or (
                expected_chat is not None
                and _chat_id(result["chat_id"]) != expected_chat
            ):
                raise TelegramError(
                    "IDENTITY_MISMATCH",
                    outcome_unknown=True,
                    condition=TelegramSendCondition.IDENTITY_MISMATCH,
                    execution_id=execution_id,
                )
            if expected_chat is not None:
                _integer(result["message_id"], 1)
            elif result["answered"] is not True:
                raise TelegramError("INVALID_RESULT", outcome_unknown=True)
        except TelegramError as error:
            if error.code in {
                "INVALID_SHAPE",
                "INVALID_RECEIPT",
                "INVALID_RESULT",
                "UNKNOWN_CONDITION",
            }:
                error.outcome_unknown = True
            if error.outcome_unknown:
                if attempt.execution_id is not None and error.execution_id not in (
                    None,
                    attempt.execution_id,
                ):
                    self._pending_effect = replace(attempt, uncertain=True)
                    raise TelegramError(
                        "INVALID_RECEIPT",
                        outcome_unknown=True,
                        execution_id=attempt.execution_id,
                    ) from None
                self._pending_effect = replace(
                    attempt,
                    execution_id=attempt.execution_id or error.execution_id,
                    uncertain=True,
                )
            elif attempt.uncertain:
                # Expired/refused replay cannot prove an earlier unknown safe.
                self._pending_effect = attempt
                error.outcome_unknown = True
                error.execution_id = attempt.execution_id
            elif error.code == "APPROVAL_REQUIRED":
                # Same body/key after browser approval; no automatic dispatch.
                self._pending_effect = attempt
            else:
                self._pending_effect = None
            raise
        except (ValidationError, ValueError, TypeError, OverflowError):
            self._pending_effect = replace(attempt, uncertain=True)
            raise TelegramError(
                "INVALID_RESULT",
                outcome_unknown=True,
                execution_id=attempt.execution_id,
            ) from None
        self._pending_effect = None
        return response
