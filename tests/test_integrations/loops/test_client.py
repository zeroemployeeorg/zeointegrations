"""Network-free synthetic responses based on OpenAPI 1.22.6 shapes."""

import importlib.metadata
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError

import zeo_integrations.loops as loops


def campaign():
    return {
        "id": "campaign_1",
        "url": "https://app.loops.so/campaigns/campaign_1",
        "name": "Fixture",
        "status": "Draft",
        "createdAt": "2026-10-24T00:00:00Z",
        "updatedAt": "2026-10-24T00:00:00Z",
        "emailMessageId": "email_1",
        "emailMessageContentRevisionId": "revision_1",
        "campaignGroupId": None,
        "mailingListId": None,
        "audienceSegmentId": None,
        "audienceFilter": None,
        "scheduling": {"method": "now", "timestamp": None},
    }


def message():
    return {
        "id": "email_1",
        "campaignId": "campaign_1",
        "subject": "Fixture",
        "previewText": "Preview",
        "fromName": "Fixture",
        "fromEmail": "hello",
        "replyToEmail": "",
        "emailFormat": "styled",
        "lmx": "<Paragraph>Hi</Paragraph>",
        "contentRevisionId": "revision_2",
        "updatedAt": "2026-10-24T00:00:00Z",
    }


class ClientTests(unittest.TestCase):
    def make_client(self, status=200, data=None, headers=None, *, contacts=False):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(status, json=data, headers=headers)

        client_class = loops.LoopsContactsClient if contacts else loops.LoopsClient
        client = client_class("synthetic-key", transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return client, calls

    def test_account_and_fixed_origin(self):
        client, calls = self.make_client(
            data={"success": True, "teamName": "Fixture team"}
        )
        self.assertEqual(client.test_api_key().team_name, "Fixture team")
        self.assertEqual(str(calls[0].url), "https://app.loops.so/api/v1/api-key")
        self.assertEqual(calls[0].headers["Authorization"], "Bearer synthetic-key")

    def test_typed_statuses_are_one_attempt_and_sanitized(self):
        statuses = {
            401: loops.AuthenticationError,
            403: loops.AuthenticationError,
            404: loops.NotFoundError,
            409: loops.ConflictError,
            413: loops.PayloadTooLargeError,
            429: loops.RateLimitError,
            302: loops.LoopsError,
            500: loops.LoopsError,
        }
        for status, error in statuses.items():
            with self.subTest(status=status):
                client, calls = self.make_client(
                    status,
                    {"message": "synthetic-key provider-private-body"},
                    {"Location": "https://elsewhere.invalid", "Retry-After": "42"},
                )
                with self.assertRaises(error) as caught:
                    client.test_api_key()
                self.assertEqual(len(calls), 1)
                self.assertNotIn("synthetic-key", str(caught.exception))
                self.assertNotIn("provider-private-body", str(caught.exception))
                if status == 429:
                    self.assertEqual(caught.exception.retry_after, 42)

    def test_transport_failure_is_not_retried_or_exposed(self):
        calls = []

        def fail(request):
            calls.append(request)
            raise httpx.ReadTimeout("synthetic-key private", request=request)

        with loops.LoopsClient(
            "synthetic-key", transport=httpx.MockTransport(fail)
        ) as client:
            with self.assertRaises(loops.TransportError) as caught:
                client.create_campaign("Fixture")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("synthetic-key", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_invalid_provider_shape_is_sanitized(self):
        for data in ({"success": True}, {"success": 1, "teamName": "Fixture"}, []):
            client, _ = self.make_client(data=data)
            with self.assertRaises(loops.InvalidResponseError):
                client.test_api_key()

    def test_create_is_explicit_draft_payload(self):
        client, calls = self.make_client(data=campaign())
        self.assertEqual(client.create_campaign("Fixture").status, "Draft")
        self.assertEqual(json.loads(calls[0].content), {"name": "Fixture"})

    def test_definite_refusals_and_unknown_outcomes_are_separate(self):
        definite = {400, 401, 403, 404, 405, 409, 410, 413, 415, 422, 429}
        for status in definite | {302, 408, 500, 502, 503, 504}:
            with self.subTest(status=status):
                client, calls = self.make_client(status, {"message": "private"})
                expected = (
                    loops.LoopsRejected if status in definite else loops.LoopsUnknown
                )
                with self.assertRaises(expected) as caught:
                    client.create_campaign("Fixture")
                self.assertEqual(len(calls), 1)
                self.assertEqual(
                    caught.exception.outcome_unknown, status not in definite
                )
                if status in definite:
                    self.assertEqual(caught.exception.status, status)

    def test_lmx_limit_is_utf8_bytes_and_rejected_before_call(self):
        client, calls = self.make_client(data=message())
        for lmx in ("a" * 60001, "é" * 30001, ""):
            with self.subTest(length=len(lmx)):
                with self.assertRaises(ValidationError):
                    client.update_email_message(
                        "email_1", "revision_1", "s", "p", "n", "hello", lmx
                    )
        self.assertEqual(calls, [])
        loops.EmailUpdate(
            expected_revision_id="revision_1",
            subject="s",
            preview_text="p",
            from_name="n",
            from_email="hello",
            lmx="é" * 30000,
        )

    def test_audience_condition_count_is_bounded(self):
        with self.assertRaises(ValidationError):
            loops.AudienceFilter(
                match="all",
                conditions=[
                    loops.PropertyCondition(key="secondary", operator="isFalse")
                ]
                * 21,
            )

    def test_scheduling_is_rejected_before_transport(self):
        client, calls = self.make_client()
        with self.assertRaises(TypeError):
            client.create_campaign("Fixture", scheduling={"method": "now"})
        with self.assertRaises(TypeError):
            client.update_campaign_audience("campaign_1", scheduling={"method": "now"})
        with self.assertRaises(ValidationError):
            loops.AudienceUpdate(mailing_list_id="list_1", scheduling={"method": "now"})
        self.assertEqual(calls, [])

    def test_audience_payload_contains_only_selected_fields(self):
        client, calls = self.make_client(data=campaign())
        client.update_campaign_audience("campaign_1", mailing_list_id="list_1")
        self.assertEqual(json.loads(calls[0].content), {"mailingListId": "list_1"})

    def test_audience_null_opt_in_and_value_less_property_are_distinct(self):
        client, calls = self.make_client(data=campaign())
        client.update_campaign_audience(
            "campaign_1",
            audience_filter=loops.AudienceFilter(
                match="all",
                conditions=[
                    loops.OptInCondition(status=None),
                    loops.PropertyCondition(key="secondary", operator="isFalse"),
                ],
            ),
        )
        conditions = json.loads(calls[0].content)["audienceFilter"]["conditions"]
        self.assertEqual(conditions[0], {"type": "optIn", "status": None})
        self.assertEqual(
            conditions[1],
            {"type": "property", "key": "secondary", "operator": "isFalse"},
        )

    def test_contact_find_and_suppression_bind_requested_email(self):
        client, calls = self.make_client(
            contacts=True,
            data=[
                {
                    "id": "contact_1",
                    "email": "fixture@example.test",
                    "subscribed": False,
                }
            ],
        )
        self.assertFalse(client.find_contact("fixture@example.test")[0].subscribed)
        self.assertEqual(calls[0].url.params["email"], "fixture@example.test")
        client, _ = self.make_client(
            contacts=True,
            data={
                "contact": {"id": "c", "email": "another@example.test", "userId": None},
                "isSuppressed": True,
                "removalQuota": {"limit": 5, "remaining": 4},
            },
        )
        with self.assertRaises(loops.InvalidResponseError):
            client.check_suppression("fixture@example.test")

    def test_upload_completion_binds_returned_asset(self):
        client, calls = self.make_client(
            data={
                "emailAssetId": "asset_1",
                "finalUrl": "https://assets.loops.so/asset_1/image.png",
            }
        )
        self.assertEqual(client.complete_upload("asset_1").email_asset_id, "asset_1")
        self.assertEqual(calls[0].url.path, "/api/v1/uploads/asset_1/complete")

    def test_theme_page_retains_cursor_and_mailing_lists_are_typed(self):
        client, calls = self.make_client(
            data={
                "pagination": {
                    "totalResults": 2,
                    "returnedResults": 0,
                    "perPage": 20,
                    "totalPages": 2,
                    "nextCursor": "next",
                    "nextPage": "2",
                },
                "data": [],
            }
        )
        page = client.list_themes(cursor="current")
        self.assertEqual(page.pagination.next_cursor, "next")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.params["cursor"], "current")
        client, _ = self.make_client(
            data=[
                {
                    "id": "list_1",
                    "name": "Fixture",
                    "description": None,
                    "isPublic": True,
                }
            ]
        )
        self.assertIsInstance(client.list_mailing_lists()[0], loops.MailingList)

    def test_revision_bound_message_and_sender_local_part(self):
        client, calls = self.make_client(data=message())
        result = client.update_email_message(
            "email_1",
            "revision_1",
            "Subject",
            "Preview",
            "Sender",
            "hello",
            "<Paragraph>Hello</Paragraph>",
        )
        self.assertEqual(result.content_revision_id, "revision_2")
        self.assertEqual(
            json.loads(calls[0].content)["expectedRevisionId"], "revision_1"
        )
        with self.assertRaises(ValidationError):
            client.update_email_message(
                "email_1", "r", "s", "p", "n", "full@example.test", "x"
            )
        self.assertEqual(len(calls), 1)

    def test_no_dangerous_or_generic_public_method(self):
        client, _ = self.make_client()
        public = {name for name in dir(client) if not name.startswith("_")}
        self.assertTrue(
            {"send", "schedule", "preview_email", "workflow", "request"}.isdisjoint(
                public
            )
        )

    def test_path_injection_refused_before_request(self):
        client, calls = self.make_client()
        for value in ("../other", "a/b", "x?token=bad", "https://elsewhere.invalid"):
            with self.assertRaises(ValueError):
                client.get_campaign(value)
        self.assertEqual(calls, [])

    def test_mismatched_provider_identity_refused(self):
        client, _ = self.make_client(data=campaign() | {"id": "another"})
        with self.assertRaises(loops.InvalidResponseError):
            client.get_campaign("campaign_1")

    def test_contact_properties_cannot_override_subscription(self):
        with self.assertRaises(ValidationError):
            loops.ContactUpdate(
                email="fixture@example.test", properties={"subscribed": True}
            )
        client, calls = self.make_client(
            data={"success": True, "id": "contact_1"}, contacts=True
        )
        client.update_contact(
            loops.ContactUpdate(email="fixture@example.test", subscribed=False)
        )
        self.assertEqual(
            json.loads(calls[0].content),
            {"email": "fixture@example.test", "subscribed": False},
        )

    def test_upload_size_and_credential_containment(self):
        client, calls = self.make_client(
            data={
                "emailAssetId": "asset_1",
                "presignedUrl": "https://loops-assets.s3.amazonaws.com/uploads/asset_1?signature=synthetic",
            }
        )
        ticket = client.create_upload("image/png", 100)
        self.assertNotIn("signature=synthetic", repr(ticket))
        with self.assertRaises(ValueError):
            client.create_upload("image/png", 4_000_001)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.host, "app.loops.so")

    def test_local_limit_does_not_silently_sleep_or_retry(self):
        client, calls = self.make_client(data={"success": True, "teamName": "Fixture"})
        for _ in range(60):
            client.test_api_key()
        with self.assertRaises(loops.RateLimitError):
            client.test_api_key()
        self.assertEqual(len(calls), 60)

    def test_distribution_is_installed_not_source_path(self):
        distribution = importlib.metadata.distribution("zeo-integrations")
        self.assertEqual(distribution.version, "0.1.0")
        self.assertNotIn("packages/loops/src", Path(loops.__file__).as_posix())
        self.assertTrue(Path(loops.__file__).with_name("client.py").is_file())

    def test_contacts_are_separate_from_draft_surface(self):
        client, _ = self.make_client()
        for name in (
            "create_contact",
            "update_contact",
            "find_contact",
            "check_suppression",
            "unsubscribe_contact",
        ):
            self.assertFalse(hasattr(client, name))
        contacts, _ = self.make_client(contacts=True)
        self.assertFalse(hasattr(contacts, "create_campaign"))
        self.assertFalse(hasattr(contacts, "unsubscribe_contact"))

    def test_upload_urls_refuse_unapproved_authorities_and_paths(self):
        urls = (
            "https://storage.invalid/signed",
            "http://loops-assets.s3.amazonaws.com/uploads/asset_1",
            "https://loops-assets.s3.amazonaws.com:443/uploads/asset_1",
            "https://user@loops-assets.s3.amazonaws.com/uploads/asset_1",
            "https://loops-assets.s3.amazonaws.com/uploads/another",
            "https://loops-assets.s3.amazonaws.com/uploads/asset_1#fragment",
        )
        for url in urls:
            with self.subTest(url=url):
                client, _ = self.make_client(
                    data={"emailAssetId": "asset_1", "presignedUrl": url}
                )
                with self.assertRaises(loops.InvalidResponseError):
                    client.create_upload("image/png", 100)
        for host in ("assets.loops.so", "images.vialoops.com"):
            client, _ = self.make_client(
                data={
                    "emailAssetId": "asset_1",
                    "finalUrl": f"https://{host}/image.png",
                }
            )
            client.complete_upload("asset_1")
        for url in (
            "https://assets.loops.so.evil.invalid/a",
            "https://assets.loops.so:443/a",
        ):
            client, _ = self.make_client(
                data={"emailAssetId": "asset_1", "finalUrl": url}
            )
            with self.assertRaises(loops.InvalidResponseError):
                client.complete_upload("asset_1")

    def test_slow_unbuffered_stream_expires_without_retry(self):
        clock = [0.0]

        class SlowStream(httpx.SyncByteStream):
            def __iter__(self):
                for _ in range(10):
                    clock[0] += 9
                    yield b" "

        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, stream=SlowStream())

        with patch(
            "zeo_integrations.loops.transport.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            with loops.LoopsClient(
                "synthetic-key", transport=httpx.MockTransport(handle)
            ) as client:
                with self.assertRaises(loops.TransportError) as caught:
                    client.test_api_key()
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock[0], 36)

    def test_response_cap_and_invalid_json_have_no_exception_context(self):
        for body in (b"x" * 1_048_577, b"[" * 2000, b"{bad-json"):
            with self.subTest(length=len(body)):
                with loops.LoopsClient(
                    "synthetic-key",
                    transport=httpx.MockTransport(
                        lambda _, body=body: httpx.Response(200, content=body)
                    ),
                ) as client:
                    with self.assertRaises(loops.InvalidResponseError) as caught:
                        client.test_api_key()
                    self.assertIsNone(caught.exception.__context__)

    def test_encoding_timeout_and_proxy_guards(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://invalid.invalid:1"}):
            client, calls = self.make_client(
                data={"success": True, "teamName": "Fixture"}
            )
            client.test_api_key()
            self.assertFalse(client._http._trust_env)
            self.assertEqual(calls[0].headers["Accept-Encoding"], "identity")
            self.assertEqual(calls[0].extensions["timeout"]["read"], 10.0)
        with loops.LoopsClient(
            "synthetic-key",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"Content-Encoding": "gzip"},
                    stream=httpx.ByteStream(b"not-decoded"),
                )
            ),
        ) as client:
            with self.assertRaises(loops.InvalidResponseError):
                client.test_api_key()
        for timeout in (0, -1, float("nan"), 61, True):
            with self.assertRaises(ValueError):
                loops.LoopsClient("synthetic-key", timeout=timeout)

    def test_request_bound_counts_actual_escaped_json(self):
        client, calls = self.make_client(data=message())
        client.update_email_message(
            "email_1", "r", "s", "p", "n", "hello", "\x00" * 60000
        )
        self.assertLess(len(calls[0].content), 400_000)
        contacts, calls = self.make_client(contacts=True)
        with self.assertRaises(ValueError):
            contacts.update_contact(
                loops.ContactUpdate(
                    email="fixture@example.test", properties={"large": "x" * 400_001}
                )
            )
        self.assertEqual(calls, [])

    def test_audience_update_refuses_non_draft_result(self):
        client, _ = self.make_client(data=campaign() | {"status": "Sent"})
        with self.assertRaises(loops.InvalidResponseError):
            client.update_campaign_audience("campaign_1", mailing_list_id="list_1")
