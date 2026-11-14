"""Offline transport boundary checks; no provider credentials or live traffic."""

import base64
import json
import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from zeo_integrations.twilio import (
    TwilioListError,
    TwilioRefused,
    TwilioSendCredentials,
    TwilioUnknownOutcome,
    WhatsAppClient,
)

ACCOUNT = "AC" + "a" * 32
KEY = "SK" + "b" * 32
SID = "SM" + "c" * 32
SENDER = "whatsapp:+15005550006"
RECIPIENT = "whatsapp:+15005550009"
NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


class WhatsAppClientTests(unittest.TestCase):
    def client(self, handler):
        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, trust_env=False, timeout=2)
        self.addCleanup(client.close)
        credentials = TwilioSendCredentials(ACCOUNT, KEY, "inert-test-secret")
        return WhatsAppClient(credentials, client)

    def receipt(self, **changes):
        return {"sid": SID, "account_sid": ACCOUNT, "status": "queued", **changes}

    def inbound(self, **changes):
        return {
            "sid": SID,
            "account_sid": ACCOUNT,
            "from": RECIPIENT,
            "to": SENDER,
            "body": "private order",
            "num_media": "0",
            "status": "received",
            "direction": "inbound",
            "date_sent": "Fri, 02 Jan 2026 11:59:00 +0000",
            **changes,
        }

    def test_text_exact_native_request_and_api_key_auth(self):
        requests = []

        def handle(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(
                str(request.url),
                f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}/Messages.json",
            )
            self.assertEqual(
                request.headers["authorization"],
                "Basic "
                + base64.b64encode(f"{KEY}:inert-test-secret".encode()).decode(),
            )
            self.assertEqual(
                request.content.decode(),
                urlencode({"From": SENDER, "To": RECIPIENT, "Body": "hello"}),
            )
            return httpx.Response(201, json=self.receipt())

        receipt = self.client(handle).send_text(SENDER, RECIPIENT, "hello")
        self.assertEqual((receipt.message_sid, receipt.status), (SID, "queued"))
        self.assertEqual(len(requests), 1)

    def test_template_request_excludes_freeform(self):
        def handle(request):
            from urllib.parse import parse_qs

            fields = parse_qs(request.content.decode())
            self.assertNotIn("Body", fields)
            self.assertEqual(fields["ContentSid"], ["HX" + "d" * 32])
            self.assertEqual(json.loads(fields["ContentVariables"][0]), {"1": "order"})
            return httpx.Response(201, json=self.receipt())

        self.client(handle).send_template(
            SENDER, RECIPIENT, "HX" + "d" * 32, {"1": "order"}
        )

    def test_window_refusal_redacts_native_error(self):
        client = self.client(
            lambda request: httpx.Response(
                400, json={"code": 63016, "message": "private order"}
            )
        )
        with self.assertRaises(TwilioRefused) as caught:
            client.send_text(SENDER, RECIPIENT, "private order")
        self.assertEqual(caught.exception.reason, "outside_customer_window")
        self.assertEqual(caught.exception.code, 63016)
        self.assertNotIn("private", str(caught.exception))

    def test_timeout_never_retries_and_is_unknown(self):
        calls = []

        def handle(request):
            calls.append(request)
            raise httpx.ReadTimeout("secret private order", request=request)

        with self.assertRaises(TwilioUnknownOutcome) as caught:
            self.client(handle).send_text(SENDER, RECIPIENT, "private")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_ambiguous_responses_never_confirm(self):
        for status, payload in [
            (500, {}),
            (302, {}),
            (200, self.receipt()),
            (201, {}),
            (201, self.receipt(account_sid="AC" + "e" * 32)),
            (201, self.receipt(status="unrecognized")),
            (201, None),
        ]:
            with self.subTest(status=status, payload=payload):
                with self.assertRaises(TwilioUnknownOutcome):
                    self.client(
                        lambda request, status=status, payload=payload: httpx.Response(
                            status, json=payload
                        )
                    ).send_text(SENDER, RECIPIENT, "hello")

    def test_invalid_local_fields_make_no_request(self):
        def handle(request):
            self.fail("Local validation must happen before dispatch")

        client = self.client(handle)
        for sender, body in [
            ("+15005550006", "hello"),
            (SENDER, ""),
            (SENDER, "x" * 1601),
        ]:
            with self.assertRaises(ValueError):
                client.send_text(sender, RECIPIENT, body)
        with self.assertRaises(ValueError):
            client.send_template(SENDER, RECIPIENT, "invalid", {})
        self.assertNotIn("inert-test-secret", repr(client._credentials))

    def test_bounded_list_and_redacted_records(self):
        def handle(request):
            self.assertEqual(request.url.params["To"], SENDER)
            self.assertEqual(request.url.params["PageSize"], "50")
            self.assertEqual(request.url.params["DateSent"], ">=2026-01-02")
            return httpx.Response(
                200, json={"messages": [self.inbound()], "next_page_uri": None}
            )

        rows = self.client(handle).inbound_list(
            SENDER, NOW - timedelta(hours=1), now=NOW
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message_sid, SID)
        self.assertNotIn("private order", repr(rows))
        self.assertNotIn(RECIPIENT, repr(rows))

    def test_list_failed_partial_or_untrusted_page_is_error(self):
        for payload in [
            {"messages": [self.inbound()]},
            {"messages": [self.inbound(to=RECIPIENT)], "next_page_uri": None},
            {"messages": [self.inbound(date_sent=None)], "next_page_uri": None},
            {
                "messages": [self.inbound()],
                "next_page_uri": "https://example.invalid/keys",
            },
            {
                "messages": [self.inbound(direction="outbound-api")],
                "next_page_uri": None,
            },
            {"messages": [self.inbound()], "next_page_uri": "/bad"},
        ]:
            with self.subTest(payload=payload):
                with self.assertRaises(TwilioListError):
                    self.client(
                        lambda request, payload=payload: httpx.Response(
                            200, json=payload
                        )
                    ).inbound_list(SENDER, NOW - timedelta(hours=1), now=NOW)

    def test_continuation_uses_token_and_preserves_filters(self):
        requests = []
        path = f"/2010-04-01/Accounts/{ACCOUNT}/Messages.json"

        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                continuation = (
                    path
                    + "?"
                    + urlencode(
                        {
                            "To": SENDER,
                            "DateSent": ">=2026-01-02",
                            "PageSize": "50",
                            "Page": "1",
                            "PageToken": "PAtest",
                        }
                    )
                )
                return httpx.Response(
                    200,
                    json={"messages": [self.inbound()], "next_page_uri": continuation},
                )
            self.assertEqual(request.url.params["PageToken"], "PAtest")
            return httpx.Response(200, json={"messages": [], "next_page_uri": None})

        rows = self.client(handle).inbound_list(
            SENDER, NOW - timedelta(hours=1), now=NOW
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(requests), 2)

    def test_page_limit_returns_no_partial_result(self):
        path = f"/2010-04-01/Accounts/{ACCOUNT}/Messages.json"
        continuation = (
            path
            + "?"
            + urlencode(
                {
                    "To": SENDER,
                    "DateSent": ">=2026-01-02",
                    "PageSize": "50",
                    "Page": "1",
                    "PageToken": "PAtest",
                }
            )
        )
        client = self.client(
            lambda request: httpx.Response(
                200, json={"messages": [self.inbound()], "next_page_uri": continuation}
            )
        )
        with self.assertRaises(TwilioListError):
            client.inbound_list(SENDER, NOW - timedelta(hours=1), now=NOW, max_pages=1)
