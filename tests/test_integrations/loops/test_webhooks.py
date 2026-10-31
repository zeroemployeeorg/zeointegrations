import base64
import hashlib
import hmac
import unittest

from zeo_integrations.loops import WebhookVerificationError, verify_webhook


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.key = b"synthetic-test-signing-key-only"
        self.secret = "whsec_" + base64.b64encode(self.key).decode()
        self.body = b'{"eventName":"testing.testEvent"}'
        self.args = {
            "webhook_id": "fixture_1",
            "timestamp": "1000",
            "signature": self.sign(self.body),
            "secrets": (self.secret,),
            "now": 1000,
        }

    def sign(self, body):
        digest = hmac.digest(self.key, b"fixture_1.1000." + body, "sha256")
        return "v1," + base64.b64encode(digest).decode()

    def test_raw_body_binding_and_returned_digest(self):
        value = verify_webhook(self.body, **self.args)
        self.assertEqual(value.body_sha256, hashlib.sha256(self.body).hexdigest())
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(self.body + b" ", **self.args)

    def test_timestamp_boundaries_and_future(self):
        for now in (700, 1300):
            verify_webhook(self.body, **(self.args | {"now": now}))
        for now in (699, 1301):
            with self.assertRaises(WebhookVerificationError):
                verify_webhook(self.body, **(self.args | {"now": now}))

    def test_two_secret_rotation_and_multiple_signatures(self):
        other = "whsec_" + base64.b64encode(b"another-test-secret").decode()
        verify_webhook(
            self.body,
            **(
                self.args
                | {
                    "secrets": (other, self.secret),
                    "signature": "v2,ignored v1,invalid " + self.args["signature"],
                }
            ),
        )

    def test_wrong_headers_and_malformed_secret_refused(self):
        for changes in (
            {"webhook_id": "another"},
            {"timestamp": "1001"},
            {"signature": "v2,ignored"},
            {"secrets": ("whsec_!bad",)},
            {"timestamp": "1000.0"},
            {"secrets": ()},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(WebhookVerificationError):
                    verify_webhook(self.body, **(self.args | changes))

    def test_webhook_input_caps_and_ambiguous_id(self):
        for changes in (
            {"webhook_id": "fixture.1"},
            {"webhook_id": "x" * 257},
            {"signature": "x" * 8193},
            {"timestamp": "1" * 17},
            {"secrets": (self.secret,) * 3},
        ):
            with self.assertRaises(WebhookVerificationError):
                verify_webhook(self.body, **(self.args | changes))
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(b"x" * 8_000_001, **self.args)
