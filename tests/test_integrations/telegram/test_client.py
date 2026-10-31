"""Synthetic only: no provider, credentials, hosted service or real durable DB."""

from __future__ import annotations

import unittest
from dataclasses import replace

from zeo_core.integrations.hosted.client import (
    HostedConnectionClient,
    HostedOperationResponse,
)

from zeo_integrations.telegram import (
    TelegramClient,
    TelegramError,
    TelegramInlineButton,
    TelegramSendCondition,
    TelegramSender,
)


def confirmed(result, *, receipt=None, execution_id="synthetic-execution"):
    return HostedOperationResponse(
        status="confirmed",
        execution_id=execution_id,
        result=result,
        receipt={"condition": None, **(receipt or {})},
    )


def refused(condition=None, *, code="REQUEST_REFUSED", replayed=False, message="x"):
    receipt = {"replayed": replayed, "condition": condition}
    return HostedOperationResponse(
        status="failed_safe",
        execution_id="synthetic-refusal",
        normalized_error={"code": code, "message": message},
        receipt=receipt,
    )


def batch(*, bot_id=7, epoch=0, cursor="opaque-issued", updates=None):
    return {
        "bot_id": bot_id,
        "epoch": epoch,
        "fence": 2,
        "batch_cursor": cursor,
        "updates": updates
        if updates is not None
        else [
            {"update_id": 10, "message": {"text": "private fixture"}},
            {"update_id": 14, "callback_query": {"data": "opaque-action"}},
        ],
        "retention": {"state": "ok", "oldest_unacknowledged_age_seconds": None},
        "lost_to_retention": False,
    }


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, request):
        self.calls.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_artifact(self, **kwargs):
        raise AssertionError("receive slice must not fetch artifacts")


class Verifier:
    def __init__(self):
        self.accept = True
        self.calls = []

    def is_durably_handled(self, **kwargs):
        self.calls.append(kwargs)
        return self.accept


class TelegramClientTests(unittest.TestCase):
    def client(self, *responses):
        transport = Transport(*responses)
        verifier = Verifier()
        client = TelegramClient(
            HostedConnectionClient(transport=transport),
            connection_id="synthetic-connection",
            consumers=("agenda", "app"),
            verifier=verifier,
            clock=lambda: 1.0,
        )
        return client, transport, verifier

    def read(self, client):
        return client.read_updates(idempotency_key="read-one", deadline=10)

    def ack(self, client, value, key="ack-one"):
        return client.acknowledge(value, idempotency_key=key, deadline=10)

    def test_read_and_durable_all_consumer_ack(self):
        client, transport, verifier = self.client(
            confirmed(batch()),
            confirmed({"acknowledged": True, "confirmed_cursor": "confirmed"}),
        )
        value = self.read(client)
        result = self.ack(client, value)
        self.assertEqual(result.confirmed_cursor, "confirmed")
        self.assertEqual(
            verifier.calls,
            [{"batch": value, "consumers": ("agenda", "app")}],
        )
        bodies = [call.model_dump(exclude_none=True) for call in transport.calls]
        self.assertEqual(bodies[0]["arguments"], {"max": 100})
        self.assertEqual(bodies[1]["arguments"], {"batch_cursor": "opaque-issued"})
        for body in bodies:
            self.assertNotIn("expect", body)
            self.assertNotIn("offset", body["arguments"])
            self.assertNotIn("fence", body["arguments"])

    def test_uncommitted_consumer_blocks_ack_and_forged_batch_is_rejected(self):
        client, transport, verifier = self.client(confirmed(batch()))
        value = self.read(client)
        verifier.accept = False
        with self.assertRaisesRegex(TelegramError, "PREFIX_NOT_DURABLE"):
            self.ack(client, value)
        with self.assertRaisesRegex(TelegramError, "UNISSUED_BATCH"):
            self.ack(client, replace(value, batch_cursor="invented"))
        self.assertEqual(len(transport.calls), 1)

    def test_changed_payload_requires_a_current_durable_disposition(self):
        changed = batch(
            updates=[{"update_id": 10, "message": {"text": "changed redelivery"}}]
        )
        client, transport, verifier = self.client(confirmed(changed))
        value = self.read(client)

        def check_current_batch(**kwargs):
            verifier.calls.append(kwargs)
            # Synthetic store knows the prior content for this stable ID.
            return kwargs["batch"].updates[0].payload()["text"] == "private fixture"

        verifier.is_durably_handled = check_current_batch
        with self.assertRaisesRegex(TelegramError, "PREFIX_NOT_DURABLE"):
            self.ack(client, value)
        self.assertIs(verifier.calls[0]["batch"], value)
        self.assertEqual(len(transport.calls), 1)

    def test_sparse_ids_do_not_require_fabricated_missing_updates(self):
        client, _, _ = self.client(confirmed(batch()))
        self.assertEqual(self.read(client).update_ids, (10, 14))

    def test_payload_copy_and_repr_do_not_expose_or_mutate_raw_content(self):
        client, _, _ = self.client(confirmed(batch()))
        value = self.read(client)
        payload = value.updates[0].payload()
        payload["text"] = "changed outside batch"
        self.assertEqual(value.updates[0].payload()["text"], "private fixture")
        self.assertNotIn("private fixture", repr(value))
        self.assertNotIn("private fixture", repr(value.updates[0]))

    def test_empty_is_not_unavailable_and_cannot_be_acked(self):
        client, _, _ = self.client(confirmed(batch(cursor=None, updates=[])))
        value = self.read(client)
        self.assertEqual(value.updates, ())
        with self.assertRaisesRegex(TelegramError, "EMPTY_PREFIX"):
            self.ack(client, value)
        unavailable, _, _ = self.client(RuntimeError("private transport URL"))
        with self.assertRaises(TelegramError) as caught:
            self.read(unavailable)
        self.assertEqual(str(caught.exception), "UNAVAILABLE")
        self.assertNotIn("private", str(caught.exception))

    def test_refusal_condition_does_not_depend_on_human_message(self):
        for message in ("redacted", "translated explanation"):
            for condition in ("receive_conflict", "lease_lost", "cursor_invalid"):
                with self.subTest(message=message, condition=condition):
                    client, transport, _ = self.client(
                        refused(condition, message=message)
                    )
                    with self.assertRaises(TelegramError) as caught:
                        self.read(client)
                    self.assertEqual(caught.exception.condition.value, condition)
                    self.assertEqual(len(transport.calls), 1)

    def test_unknown_or_mismatched_condition_is_not_success(self):
        for response in (
            refused("invented"),
            refused("result_not_retained"),
            refused("lease_lost", code="RATE_LIMITED"),
            refused("transient_replay", replayed=False),
        ):
            client, _, _ = self.client(response)
            with self.assertRaises(TelegramError):
                self.read(client)

    def test_transient_replay_never_exposes_an_old_batch_or_retries(self):
        client, transport, _ = self.client(refused("transient_replay", replayed=True))
        with self.assertRaises(TelegramError) as caught:
            self.read(client)
        self.assertEqual(caught.exception.condition.value, "transient_replay")
        self.assertEqual(len(transport.calls), 1)
        client, _, _ = self.client(confirmed(batch(), receipt={"replayed": True}))
        with self.assertRaisesRegex(TelegramError, "REPLAY_WITHOUT_BATCH"):
            self.read(client)

    def test_generic_kernel_refusal_has_no_invented_condition(self):
        client, _, _ = self.client(refused())
        with self.assertRaises(TelegramError) as caught:
            self.read(client)
        self.assertIsNone(caught.exception.condition)

    def test_required_null_condition_is_distinct_from_a_missing_field(self):
        client, _, _ = self.client(
            HostedOperationResponse(
                status="confirmed",
                execution_id="synthetic-missing",
                result=batch(),
                receipt={},
            )
        )
        with self.assertRaisesRegex(TelegramError, "INVALID_RECEIPT"):
            self.read(client)
        client, _, _ = self.client(confirmed(batch()))
        self.assertEqual(self.read(client).bot_id, 7)

    def test_invalid_limits_and_expired_deadline_never_invoke(self):
        client, transport, _ = self.client()
        for maximum in (-1, 0, True, 101):
            with self.subTest(maximum=maximum), self.assertRaises(TelegramError):
                client.read_updates(
                    maximum=maximum, idempotency_key="read", deadline=10
                )
        with self.assertRaises(TelegramError):
            client.read_updates(idempotency_key="read", deadline=1)
        self.assertEqual(transport.calls, [])

    def test_malformed_or_unsupported_update_prevents_cursor_use(self):
        for updates in (
            [{"update_id": 1, "edited_message": {}}],
            [{"update_id": True, "message": {}}],
            [{"update_id": 2, "message": {}}, {"update_id": 2, "message": {}}],
            [{"update_id": 1, "message": {}, "callback_query": {}}],
        ):
            client, _, _ = self.client(confirmed(batch(updates=updates)))
            with self.subTest(updates=updates), self.assertRaises(TelegramError):
                self.read(client)

    def test_reset_epoch_distinguishes_reused_id_and_identical_payload(self):
        client, _, _ = self.client(confirmed(batch(epoch=0)), confirmed(batch(epoch=1)))
        before = self.read(client)
        after = self.read(client)
        self.assertEqual(before.updates, after.updates)
        self.assertEqual(before.business_keys, ((7, 0, 10), (7, 0, 14)))
        self.assertEqual(after.business_keys, ((7, 1, 10), (7, 1, 14)))

    def test_fence_change_does_not_reset_business_dedup(self):
        newer = batch()
        newer["fence"] = 99
        client, _, _ = self.client(confirmed(batch()), confirmed(newer))
        before = self.read(client)
        after = self.read(client)
        self.assertEqual(before.business_keys, after.business_keys)

    def test_epoch_rollback_missing_or_boolean_epoch_is_unavailable(self):
        for invalid in (-1, True, None):
            client, _, _ = self.client(confirmed(batch(epoch=invalid)))
            with self.subTest(epoch=invalid), self.assertRaises(TelegramError):
                self.read(client)
        client, _, _ = self.client(confirmed(batch(epoch=2)), confirmed(batch(epoch=1)))
        self.read(client)
        with self.assertRaisesRegex(TelegramError, "EPOCH_REGRESSED"):
            self.read(client)

    def test_delivery_order_is_not_manufactured_from_numeric_ids(self):
        rows = [{"update_id": 9, "message": {}}, {"update_id": 2, "message": {}}]
        client, _, _ = self.client(confirmed(batch(updates=rows)))
        self.assertEqual(self.read(client).update_ids, (9, 2))

    def test_empty_recorded_drop_boundary_still_requires_durable_prefix(self):
        client, transport, verifier = self.client(
            confirmed(batch(updates=[])),
            confirmed({"acknowledged": True, "confirmed_cursor": "confirmed"}),
        )
        value = self.read(client)
        verifier.accept = False
        with self.assertRaisesRegex(TelegramError, "PREFIX_NOT_DURABLE"):
            self.ack(client, value)
        self.assertEqual(len(transport.calls), 1)
        verifier.accept = True
        self.ack(client, value)
        self.assertEqual(
            transport.calls[-1].arguments, {"batch_cursor": "opaque-issued"}
        )

    def test_bot_change_does_not_reset_stable_business_identity(self):
        client, _, _ = self.client(confirmed(batch()), confirmed(batch(bot_id=8)))
        self.read(client)
        with self.assertRaisesRegex(TelegramError, "BOT_CHANGED"):
            self.read(client)

    def test_ambiguous_ack_requires_original_batch_and_key(self):
        unknown = HostedOperationResponse(
            status="ambiguous",
            execution_id="synthetic-unknown",
            receipt={"condition": None},
            normalized_error={"code": "RESULT_AMBIGUOUS", "message": "unknown"},
        )
        client, transport, _ = self.client(
            confirmed(batch()),
            unknown,
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed"},
                execution_id="synthetic-unknown",
            ),
        )
        value = self.read(client)
        with self.assertRaises(TelegramError) as caught:
            self.ack(client, value)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.ack(client, value, key="fresh-key")
        self.assertEqual(len(transport.calls), 2)
        self.ack(client, value)
        self.assertEqual(
            transport.calls[1].idempotency_key, transport.calls[2].idempotency_key
        )
        self.assertEqual(transport.calls[1].arguments, transport.calls[2].arguments)

    def test_malformed_success_after_ack_is_unknown_not_a_safe_retry(self):
        client, transport, _ = self.client(
            confirmed(batch()),
            confirmed({"acknowledged": True, "confirmed_cursor": 17}),
        )
        value = self.read(client)
        with self.assertRaises(TelegramError) as caught:
            self.ack(client, value)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.ack(client, value, key="fresh-key")
        self.assertEqual(len(transport.calls), 2)

    def test_crash_or_revoked_ack_does_not_turn_subsequent_read_into_ack(self):
        # A synthetic Broker retains confirmed=11, pending=15. Client requests
        # never carry either offset; only the Broker may choose confirmed.
        for failure in (
            RuntimeError("transport lost after pending intent"),
            refused(message="operation is unavailable"),
        ):
            client, transport, _ = self.client(
                confirmed(batch()), failure, confirmed(batch())
            )
            value = self.read(client)
            with self.assertRaises(TelegramError):
                self.ack(client, value)
            self.read(client)
            self.assertEqual(transport.calls[-1].operation_id, "telegram.updates.read")
            self.assertEqual(transport.calls[-1].arguments, {"max": 100})

    def test_rate_delay_is_typed_not_parsed_from_message(self):
        response = refused("rate_limited", code="RATE_LIMITED", message="wait 999")
        data = response.model_dump(mode="json")
        data["receipt"]["retry_after_seconds"] = 3
        client, transport, _ = self.client(HostedOperationResponse.model_validate(data))
        with self.assertRaises(TelegramError) as caught:
            self.read(client)
        self.assertEqual(caught.exception.retry_after_seconds, 3)
        self.assertEqual(len(transport.calls), 1)

    def recovery_responses(self, final):
        first = HostedOperationResponse(
            status="ambiguous",
            execution_id="execution-E1",
            normalized_error={"code": "RESULT_AMBIGUOUS", "message": "unknown"},
            receipt={"condition": None},
        )
        stale = refused("cursor_stale").model_dump(mode="json")
        stale["receipt"]["pending_ack"] = {
            "state": "unresolved_stale",
            "execution_id": "execution-E1",
        }
        second_batch = batch(epoch=1, cursor="cursor-C2")
        second = confirmed(
            second_batch,
            receipt={
                "pending_ack": {
                    "state": "unresolved_stale",
                    "execution_id": "execution-E1",
                },
                "recovery": {"open": True, "supersedes": "execution-E1"},
            },
        )
        return [
            confirmed(batch()),
            first,
            HostedOperationResponse.model_validate(stale),
            second,
            final,
        ], first

    def enter_recovery(self, client):
        original = self.read(client)
        with self.assertRaises(TelegramError) as ambiguous:
            self.ack(client, original, key="key-K1")
        self.assertTrue(ambiguous.exception.outcome_unknown)
        with self.assertRaises(TelegramError) as stale:
            self.ack(client, original, key="key-K1")
        self.assertEqual(stale.exception.condition.value, "cursor_stale")
        self.assertEqual(stale.exception.pending_execution_id, "execution-E1")
        return self.read(client)

    def test_linked_recovery_is_explicit_new_operation_not_old_resolution(self):
        responses, original_receipt = self.recovery_responses(
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed-C2"},
                execution_id="execution-E2",
            )
        )
        before = original_receipt.model_dump(mode="json")
        client, transport, _ = self.client(*responses)
        fresh = self.enter_recovery(client)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.ack(client, fresh, key="key-K1")
        with self.assertRaisesRegex(TelegramError, "RECOVERY_REQUIRES_NEW_KEY"):
            client.acknowledge_recovery(fresh, idempotency_key="key-K1", deadline=10)
        self.assertEqual(len(transport.calls), 4)
        result = client.acknowledge_recovery(
            fresh, idempotency_key="key-K2", deadline=10
        )
        self.assertEqual(result.execution_id, "execution-E2")
        self.assertEqual(result.superseded_execution_ids, ("execution-E1",))
        self.assertEqual(
            transport.calls[-1].arguments,
            {
                "batch_cursor": "cursor-C2",
                "supersedes_execution_id": "execution-E1",
            },
        )
        self.assertEqual(original_receipt.model_dump(mode="json"), before)

    def test_lost_linked_reply_resends_exact_original_new_body_and_key(self):
        responses, _ = self.recovery_responses(RuntimeError("lost E2 reply"))
        responses.append(
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed-C2"},
                receipt={"replayed": True},
                execution_id="execution-E2",
            )
        )
        client, transport, _ = self.client(*responses)
        fresh = self.enter_recovery(client)
        with self.assertRaises(TelegramError) as lost:
            client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)
        self.assertTrue(lost.exception.outcome_unknown)
        with self.assertRaises(TelegramError):
            self.ack(client, fresh, key="different-key")
        result = self.ack(client, fresh, key="key-K2")
        self.assertTrue(result.replayed)
        self.assertEqual(result.superseded_execution_ids, ("execution-E1",))
        self.assertEqual(transport.calls[-2].arguments, transport.calls[-1].arguments)
        self.assertEqual(
            transport.calls[-2].idempotency_key, transport.calls[-1].idempotency_key
        )

    def test_known_stale_original_is_not_resent_even_with_its_old_key(self):
        responses, _ = self.recovery_responses(
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed-C2"},
                execution_id="execution-E2",
            )
        )
        client, transport, _ = self.client(*responses)
        original = self.read(client)
        with self.assertRaises(TelegramError):
            self.ack(client, original, key="key-K1")
        with self.assertRaises(TelegramError):
            self.ack(client, original, key="key-K1")
        self.assertEqual(len(transport.calls), 3)
        with self.assertRaisesRegex(TelegramError, "RECOVERY_REQUIRED"):
            self.ack(client, original, key="key-K1")
        self.assertEqual(len(transport.calls), 3)
        fresh = self.read(client)
        client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)

    def test_ordinary_stale_cursor_without_open_intent_cannot_be_reused(self):
        client, transport, _ = self.client(
            confirmed(batch()),
            refused("cursor_stale"),
            confirmed(batch(cursor="fresh-C2")),
            confirmed({"acknowledged": True, "confirmed_cursor": "confirmed-C2"}),
        )
        old = self.read(client)
        with self.assertRaises(TelegramError):
            self.ack(client, old, key="old-key")
        with self.assertRaisesRegex(TelegramError, "UNISSUED_BATCH"):
            self.ack(client, old, key="old-key")
        self.assertEqual(len(transport.calls), 2)
        fresh = self.read(client)
        self.ack(client, fresh, key="new-prefix-key")
        self.assertEqual(transport.calls[-1].arguments, {"batch_cursor": "fresh-C2"})

    def test_recovery_needs_issued_batch_and_current_durable_prefix(self):
        responses, _ = self.recovery_responses(
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed-C2"},
                execution_id="execution-E2",
            )
        )
        client, transport, verifier = self.client(*responses)
        fresh = self.enter_recovery(client)
        with self.assertRaisesRegex(TelegramError, "RECOVERY_NOT_GRANTED"):
            client.acknowledge_recovery(
                replace(fresh, recovery_execution_id="invented"),
                idempotency_key="key-K2",
                deadline=10,
            )
        verifier.accept = False
        with self.assertRaisesRegex(TelegramError, "PREFIX_NOT_DURABLE"):
            client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)
        self.assertEqual(len(transport.calls), 4)
        verifier.accept = True
        client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)

    def test_unlinked_ack_does_not_bypass_broker_recovery_context(self):
        response = confirmed(
            batch(epoch=1, cursor="cursor-C2"),
            receipt={"recovery": {"open": True, "supersedes": "execution-E1"}},
        )
        client, transport, _ = self.client(
            response,
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "confirmed-C2"},
                execution_id="execution-E2",
            ),
        )
        fresh = self.read(client)
        with self.assertRaisesRegex(TelegramError, "RECOVERY_REQUIRED"):
            self.ack(client, fresh, key="key-K2")
        self.assertEqual(len(transport.calls), 1)
        linked = client.acknowledge_recovery(
            fresh, idempotency_key="key-K2", deadline=10
        )
        self.assertEqual(linked.superseded_execution_ids, ("execution-E1",))

    def test_malformed_or_mismatched_recovery_metadata_cannot_authorize(self):
        receipts = [
            {"recovery": {"open": False, "supersedes": "execution-E1"}},
            {"pending_ack": {"state": "invented", "execution_id": "execution-E1"}},
            {
                "pending_ack": {
                    "state": "unresolved_stale",
                    "execution_id": "execution-E1",
                },
                "recovery": {"open": True, "supersedes": "execution-E9"},
            },
        ]
        for receipt in receipts:
            client, transport, _ = self.client(
                confirmed(batch(epoch=1), receipt=receipt)
            )
            with self.subTest(receipt=receipt), self.assertRaises(TelegramError):
                self.read(client)
            self.assertEqual(len(transport.calls), 1)

    def test_wrong_open_intent_refuses_without_new_effect(self):
        responses, _ = self.recovery_responses(
            confirmed({"acknowledged": True, "confirmed_cursor": "unused"})
        )
        wrong = confirmed(
            batch(epoch=1),
            receipt={"recovery": {"open": True, "supersedes": "execution-E9"}},
        )
        responses[3] = wrong
        client, transport, _ = self.client(*responses)
        fresh = self.enter_recovery(client)
        with self.assertRaisesRegex(TelegramError, "PENDING_INTENT_MISMATCH"):
            client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)
        self.assertEqual(len(transport.calls), 4)

    def test_linked_confirmation_cannot_reuse_the_old_execution_identity(self):
        responses, _ = self.recovery_responses(
            confirmed(
                {"acknowledged": True, "confirmed_cursor": "bad-link"},
                execution_id="execution-E1",
            )
        )
        client, transport, _ = self.client(*responses)
        fresh = self.enter_recovery(client)
        with self.assertRaises(TelegramError) as caught:
            client.acknowledge_recovery(fresh, idempotency_key="key-K2", deadline=10)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.ack(client, fresh, key="key-K3")
        self.assertEqual(len(transport.calls), 5)

    def test_recovery_condition_status_and_code_are_closed(self):
        for condition in ("cursor_stale", "recovery_required"):
            response = refused(condition)
            client, _, _ = self.client(confirmed(batch()), response)
            value = self.read(client)
            with self.assertRaises(TelegramError) as caught:
                self.ack(client, value)
            self.assertEqual(caught.exception.condition.value, condition)
            bad = response.model_dump(mode="json")
            bad["status"] = "refused"
            client, _, _ = self.client(HostedOperationResponse.model_validate(bad))
            with self.assertRaisesRegex(TelegramError, "INVALID_RECEIPT"):
                self.read(client)

    def test_response_size_ceiling_refuses_without_echo(self):
        client, _, _ = self.client(confirmed(batch()))
        client._max_bytes = 10
        with self.assertRaisesRegex(TelegramError, "UNAVAILABLE"):
            self.read(client)


class TelegramSenderTests(unittest.TestCase):
    def sender(self, *responses):
        transport = Transport(*responses)
        self.now = 1.0
        sender = TelegramSender(
            HostedConnectionClient(transport=transport),
            connection_id="synthetic-connection",
            expected_bot_id=7,
            clock=lambda: self.now,
        )
        return sender, transport

    def sent(self, *, bot=7, chat=-42, message=17, replayed=False, execution="send-1"):
        return confirmed(
            {"bot_id": bot, "chat_id": chat, "message_id": message},
            receipt={"replayed": replayed},
            execution_id=execution,
        )

    def send(self, sender, *, key="send-key", text="private message", keyboard=None):
        return sender.send_message(
            -42,
            text,
            idempotency_key=key,
            deadline=10,
            inline_keyboard=keyboard,
        )

    def answer(self, sender, *, key="answer-key"):
        return sender.answer_callback(
            "private-callback",
            idempotency_key=key,
            deadline=10,
        )

    def unknown(self, *, execution="send-1"):
        return HostedOperationResponse(
            status="ambiguous",
            execution_id=execution,
            receipt={"condition": None},
            normalized_error={"code": "RESULT_AMBIGUOUS", "message": "redacted"},
        )

    def test_send_plain_text_negative_chat_and_private_result(self):
        sender, transport = self.sender(self.sent())
        result = self.send(sender, text="line one\nline two")
        self.assertEqual(
            (result.bot_id, result.chat_id, result.message_id), (7, -42, 17)
        )
        self.assertNotIn("-42", repr(result))
        request = transport.calls[0]
        self.assertEqual(request.operation_id, "telegram.message.send")
        self.assertEqual(
            request.arguments, {"chat_id": -42, "text": "line one\nline two"}
        )
        self.assertNotIn("expect", request.model_dump(exclude_none=True))
        self.assertEqual(len(transport.calls), 1)

    def test_inline_button_order_and_private_repr(self):
        sender, transport = self.sender(self.sent())
        first = TelegramInlineButton("Private label", "opaque-first")
        second = TelegramInlineButton("Second", "opaque-second")
        self.send(sender, keyboard=((first, second),))
        self.assertEqual(
            transport.calls[0].arguments["inline_keyboard"],
            [
                [
                    {"text": "Private label", "callback_data": "opaque-first"},
                    {"text": "Second", "callback_data": "opaque-second"},
                ]
            ],
        )
        self.assertNotIn("Private label", repr(first))
        self.assertNotIn("opaque-first", repr(first))

    def test_invalid_chat_has_no_invocation(self):
        sender, transport = self.sender()
        for chat in (True, False, 0, "@username", 2**63):
            with self.subTest(chat=chat), self.assertRaises(TelegramError):
                sender.send_message(chat, "text", idempotency_key="k", deadline=10)
        self.assertEqual(transport.calls, [])

    def test_message_utf8_empty_and_unicode_bounds(self):
        sender, transport = self.sender(self.sent())
        for text in ("", "x" * 4097, "é" * 2049, "\ud800", "bad\x00text"):
            with self.subTest(size=len(text)), self.assertRaises(TelegramError):
                self.send(sender, text=text)
        self.assertEqual(transport.calls, [])
        self.send(sender, text="é" * 2048)
        self.assertEqual(len(transport.calls), 1)

    def test_callback_reference_uses_byte_bound(self):
        sender, transport = self.sender(self.sent())
        for data in ("", "x" * 65, "é" * 33, "\ud800"):
            with self.subTest(size=len(data)), self.assertRaises(TelegramError):
                self.send(sender, keyboard=((TelegramInlineButton("Label", data),),))
        self.assertEqual(transport.calls, [])
        self.send(sender, keyboard=((TelegramInlineButton("Label", "é" * 32),),))

    def test_keyboard_shape_excludes_url_and_extra_data(self):
        sender, transport = self.sender()
        button = TelegramInlineButton("Label", "opaque")
        for keyboard in (
            [],
            (),
            ((),),
            ((button,) * 9,),
            ((button,),) * 9,
            (({"text": "Label", "url": "https://example.invalid"},),),
            ((TelegramInlineButton("", "x"),),),
            ((TelegramInlineButton("x" * 129, "x"),),),
        ):
            with (
                self.subTest(keyboard_type=type(keyboard)),
                self.assertRaises(TelegramError),
            ):
                self.send(sender, keyboard=keyboard)
        self.assertEqual(transport.calls, [])

    def test_callback_answer_has_separate_neutral_effect(self):
        sender, transport = self.sender(confirmed({"bot_id": 7, "answered": True}))
        result = sender.answer_callback(
            "private-callback",
            text="Received",
            show_alert=False,
            idempotency_key="answer-key",
            deadline=10,
        )
        self.assertEqual(result.bot_id, 7)
        self.assertEqual(transport.calls[0].operation_id, "telegram.callback.answer")
        self.assertEqual(
            transport.calls[0].arguments,
            {
                "callback_query_id": "private-callback",
                "text": "Received",
                "show_alert": False,
            },
        )
        self.assertNotIn("private-callback", repr(result))

    def test_callback_answer_validates_local_bounds(self):
        sender, transport = self.sender()
        for text in ("x" * 201, "é" * 101, "\ud800"):
            with self.assertRaises(TelegramError):
                sender.answer_callback(
                    "id", text=text, idempotency_key="k", deadline=10
                )
        for value in ("", "x" * 257, True):
            with self.assertRaises(TelegramError):
                sender.answer_callback(value, idempotency_key="k", deadline=10)
        with self.assertRaises(TelegramError):
            sender.answer_callback("id", show_alert=1, idempotency_key="k", deadline=10)
        self.assertEqual(transport.calls, [])

    def test_expected_bot_is_a_required_trusted_integer(self):
        for bot in (True, 0, -7, "7"):
            with self.assertRaises(TelegramError):
                TelegramSender(
                    HostedConnectionClient(transport=Transport()),
                    connection_id="synthetic",
                    expected_bot_id=bot,
                )

    def test_wrong_confirmed_identity_is_unknown_and_blocks_new_key(self):
        for response in (self.sent(bot=8), self.sent(chat=-43)):
            sender, transport = self.sender(response)
            with self.assertRaises(TelegramError) as caught:
                self.send(sender)
            self.assertTrue(caught.exception.outcome_unknown)
            self.assertEqual(
                caught.exception.condition, TelegramSendCondition.IDENTITY_MISMATCH
            )
            with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
                self.send(sender, key="new")
            self.assertEqual(len(transport.calls), 1)

    def test_malformed_confirmed_result_is_not_safe_to_resend(self):
        for result in (
            {"bot_id": 7, "chat_id": -42, "message_id": True},
            {"bot_id": 7, "chat_id": -42, "message_id": 17, "text": "private"},
            {"bot_id": 7, "chat_id": -42},
        ):
            sender, transport = self.sender(confirmed(result))
            with self.assertRaises(TelegramError) as caught:
                self.send(sender)
            self.assertTrue(caught.exception.outcome_unknown)
            with self.assertRaises(TelegramError):
                self.send(sender, key="new")
            self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_send_keeps_original_body_key_and_operation(self):
        sender, transport = self.sender(self.unknown())
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertEqual(caught.exception.execution_id, "send-1")
        for changed in (
            lambda: self.send(sender, key="new"),
            lambda: self.send(sender, text="changed"),
            lambda: self.answer(sender),
        ):
            with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
                changed()
        self.assertEqual(len(transport.calls), 1)

    def test_lost_confirmed_response_replays_only_original_intent(self):
        sender, transport = self.sender(
            RuntimeError("private token URL"),
            self.sent(replayed=True),
        )
        with self.assertRaises(TelegramError):
            self.send(sender)
        result = self.send(sender)
        self.assertTrue(result.replayed)
        self.assertEqual(transport.calls[0].arguments, transport.calls[1].arguments)
        self.assertEqual(
            transport.calls[0].idempotency_key, transport.calls[1].idempotency_key
        )

    def test_expired_record_cannot_erase_unknown_outcome(self):
        sender, transport = self.sender(
            self.unknown(),
            refused("record_expired"),
        )
        with self.assertRaises(TelegramError):
            self.send(sender)
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(caught.exception.execution_id, "send-1")
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.send(sender, key="new")
        self.assertEqual(len(transport.calls), 2)

    def test_stored_success_is_marked_replay_not_new_delivery(self):
        sender, transport = self.sender(self.sent(replayed=True))
        self.assertTrue(self.send(sender).replayed)
        self.assertEqual(len(transport.calls), 1)

    def test_unknown_callback_blocks_a_second_key(self):
        sender, transport = self.sender(self.unknown())
        with self.assertRaises(TelegramError):
            self.answer(sender)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.answer(sender, key="second")
        self.assertEqual(len(transport.calls), 1)

    def test_callback_owner_conditions_are_closed_typed_refusals(self):
        for condition in (
            "callback_not_issued",
            "callback_answered",
            "callback_in_flight",
        ):
            sender, transport = self.sender(refused(condition))
            with self.assertRaises(TelegramError) as caught:
                self.answer(sender)
            self.assertEqual(caught.exception.condition.value, condition)
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertEqual(len(transport.calls), 1)

    def test_changed_request_uses_hosted_code_and_condition(self):
        sender, _ = self.sender(refused("request_changed"))
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertEqual(
            caught.exception.condition, TelegramSendCondition.REQUEST_CHANGED
        )
        self.assertEqual(caught.exception.code, "FAILED_SAFE")

    def test_unknown_condition_code_or_status_is_not_accepted(self):
        wrong_status = self.unknown().model_dump(mode="json")
        wrong_status["receipt"] = {"condition": "recipient_not_allowed"}
        wrong_status["normalized_error"] = {"code": "REQUEST_REFUSED", "message": "x"}
        for response in (
            refused("invented"),
            refused("recipient_not_allowed", code="RATE_LIMITED"),
            HostedOperationResponse.model_validate(wrong_status),
        ):
            sender, _ = self.sender(response)
            with self.assertRaises(TelegramError) as caught:
                self.send(sender)
            self.assertTrue(caught.exception.outcome_unknown)

    def test_revoked_connection_does_not_parse_human_text(self):
        for code in ("CONNECTION_REVOKED", "CONNECTION_REPAIR_REQUIRED"):
            sender, _ = self.sender(
                refused(
                    "connection_not_usable", code=code, message="private explanation"
                )
            )
            with self.assertRaises(TelegramError) as caught:
                self.send(sender)
            self.assertEqual(
                caught.exception.condition, TelegramSendCondition.CONNECTION_NOT_USABLE
            )
            self.assertNotIn("private", str(caught.exception))

    def test_rate_delay_is_typed_without_sleep_or_retry(self):
        data = refused("rate_limited", code="RATE_LIMITED").model_dump(mode="json")
        data["receipt"]["retry_after_seconds"] = 9
        sender, transport = self.sender(HostedOperationResponse.model_validate(data))
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertEqual(caught.exception.retry_after_seconds, 9)
        self.assertEqual(len(transport.calls), 1)
        for delay in (-1, True, "9"):
            data["receipt"]["retry_after_seconds"] = delay
            sender, _ = self.sender(HostedOperationResponse.model_validate(data))
            with self.assertRaises(TelegramError):
                self.send(sender)

    def test_browser_approval_preserves_body_and_key_without_auto_retry(self):
        approval = HostedOperationResponse(
            status="approval_required",
            execution_id="approval-1",
            approval_url="https://approval.invalid/synthetic",
            receipt={"condition": None},
        )
        sender, transport = self.sender(approval, self.sent())
        with self.assertRaisesRegex(TelegramError, "APPROVAL_REQUIRED") as caught:
            self.send(sender)
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertEqual(
            caught.exception.approval_url,
            "https://approval.invalid/synthetic",
        )
        self.assertNotIn("approval.invalid", repr(caught.exception))
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.send(sender, key="new")
        self.assertEqual(len(transport.calls), 1)
        self.send(sender)
        self.assertEqual(transport.calls[0].arguments, transport.calls[1].arguments)

    def test_generic_admission_refusal_never_becomes_a_grant(self):
        response = HostedOperationResponse(
            status="refused",
            execution_id="refusal-1",
            normalized_error={"code": "REQUEST_REFUSED", "message": "unavailable"},
        )
        sender, transport = self.sender(response)
        with self.assertRaisesRegex(TelegramError, "REFUSED") as caught:
            self.send(sender)
        self.assertIsNone(caught.exception.condition)
        self.assertEqual(len(transport.calls), 1)

    def test_deadline_before_dispatch_has_no_invocation(self):
        sender, transport = self.sender()
        self.now = 10
        with self.assertRaisesRegex(TelegramError, "DEADLINE") as caught:
            self.send(sender)
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertEqual(transport.calls, [])

    def test_deadline_after_dispatch_is_unknown(self):
        sender, transport = self.sender()
        response = self.sent()

        def slow(request):
            transport.calls.append(request)
            self.now = 11
            return response

        transport.invoke = slow
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.send(sender, key="new")
        self.assertEqual(len(transport.calls), 1)

    def test_transport_error_and_size_ceiling_do_not_echo_private_values(self):
        sender, transport = self.sender(RuntimeError("private token-bearing URL"))
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertEqual(str(caught.exception), "UNAVAILABLE")
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("private", repr(sender._pending_effect))
        self.assertEqual(len(transport.calls), 1)
        sender, _ = self.sender(self.sent())
        sender._max_bytes = 10
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)

    def test_definite_first_refusal_allows_a_separate_later_intent(self):
        sender, transport = self.sender(refused("recipient_not_allowed"), self.sent())
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertFalse(caught.exception.outcome_unknown)
        self.send(sender, key="later")
        self.assertEqual(len(transport.calls), 2)

    def test_replay_cannot_change_a_known_execution_identity(self):
        sender, transport = self.sender(
            self.unknown(),
            self.sent(execution="different", replayed=True),
        )
        with self.assertRaises(TelegramError):
            self.send(sender)
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.send(sender, key="new")
        self.assertEqual(len(transport.calls), 2)

    def test_missing_condition_in_confirmed_send_is_unknown(self):
        sender, _ = self.sender(
            HostedOperationResponse(
                status="confirmed",
                execution_id="send-1",
                result={"bot_id": 7, "chat_id": -42, "message_id": 17},
            )
        )
        with self.assertRaisesRegex(TelegramError, "INVALID_RECEIPT") as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)

    def test_later_safe_refusal_does_not_reclassify_original_unknown(self):
        sender, transport = self.sender(
            self.unknown(), refused("connection_not_usable", code="CONNECTION_REVOKED")
        )
        with self.assertRaises(TelegramError):
            self.send(sender)
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertTrue(caught.exception.outcome_unknown)
        with self.assertRaisesRegex(TelegramError, "RECONCILE_ORIGINAL"):
            self.answer(sender)
        self.assertEqual(len(transport.calls), 2)

    def test_callback_confirmed_then_second_key_gets_broker_refusal(self):
        sender, transport = self.sender(
            confirmed({"bot_id": 7, "answered": True}),
            refused("callback_answered"),
        )
        self.answer(sender)
        with self.assertRaises(TelegramError) as caught:
            self.answer(sender, key="second")
        self.assertEqual(
            caught.exception.condition, TelegramSendCondition.CALLBACK_ANSWERED
        )
        self.assertEqual(len(transport.calls), 2)

    def test_allowed_chat_changed_after_approval_is_not_success(self):
        sender, transport = self.sender(
            HostedOperationResponse(
                status="approval_required",
                execution_id="approval-1",
                approval_url="https://approval.invalid/synthetic",
                receipt={"condition": None},
            ),
            refused("recipient_not_allowed"),
        )
        with self.assertRaisesRegex(TelegramError, "APPROVAL_REQUIRED"):
            self.send(sender)
        with self.assertRaises(TelegramError) as caught:
            self.send(sender)
        self.assertEqual(
            caught.exception.condition, TelegramSendCondition.RECIPIENT_NOT_ALLOWED
        )
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertEqual(
            transport.calls[0].idempotency_key, transport.calls[1].idempotency_key
        )


if __name__ == "__main__":
    unittest.main()
