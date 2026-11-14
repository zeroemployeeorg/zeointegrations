"""Offline signature and strict form parsing checks."""

import base64
import hashlib
import hmac
import unittest
from urllib.parse import urlencode

from zeo_integrations.twilio import (
    TwilioWebhookCredentials,
    parse_whatsapp_webhook,
    validate_webhook_signature,
)

URL = "https://example.invalid/inbound?mode=whatsapp"
TOKEN = "inert-offline-token"
ACCOUNT = "AC" + "a" * 32


def sign(url, fields):
    text = url + "".join(key + fields[key] for key in sorted(fields))
    return base64.b64encode(
        hmac.new(TOKEN.encode(), text.encode(), hashlib.sha1).digest()
    ).decode()


class WebhookTests(unittest.TestCase):
    def fields(self):
        return {
            "AccountSid": ACCOUNT,
            "MessageSid": "SM" + "c" * 32,
            "From": "whatsapp:+15005550009",
            "To": "whatsapp:+15005550006",
            "Body": "private café + order",
            "NumMedia": "0",
        }

    def parse(self, raw, signature, url=URL):
        return parse_whatsapp_webhook(
            url, raw, signature, TwilioWebhookCredentials(ACCOUNT, TOKEN)
        )

    def test_documented_signature_vector(self):
        # Published Twilio form validation example, using only inert example data.
        fields = {
            "CallSid": "CA1234567890ABCDE",
            "Caller": "+14158675310",
            "Digits": "1234",
            "From": "+14158675310",
            "To": "+18005551212",
        }
        self.assertTrue(
            validate_webhook_signature(
                "https://example.com/myapp.php?foo=1&bar=2",
                fields,
                "L/OH5YylLD5NRKLltdqwSvS0BnU=",
                "12345",
            )
        )

    def test_valid_form_and_private_repr(self):
        fields = self.fields()
        message = self.parse(urlencode(fields).encode(), sign(URL, fields))
        self.assertEqual(message.body, fields["Body"])
        self.assertNotIn("private", repr(message))
        self.assertNotIn(fields["From"], repr(message))

    def test_url_body_account_and_duplicate_tampering(self):
        fields = self.fields()
        raw = urlencode(fields).encode()
        signature = sign(URL, fields)
        cases = [
            (raw, signature, URL + "&changed=1"),
            (raw + b"&Body=changed", signature, URL),
            (raw.replace(b"private", b"changed"), signature, URL),
            (raw + b"&bad=%ZZ", signature, URL),
            (raw, "invalid", URL),
        ]
        for body, sig, url in cases:
            with self.subTest(body=body, url=url):
                with self.assertRaisesRegex(ValueError, "Invalid WhatsApp webhook"):
                    self.parse(body, sig, url)
        fields["AccountSid"] = "AC" + "e" * 32
        with self.assertRaises(ValueError):
            self.parse(urlencode(fields).encode(), sign(URL, fields))

    def test_signed_status_callback_is_not_inbound(self):
        fields = self.fields()
        fields["MessageStatus"] = "delivered"
        with self.assertRaises(ValueError):
            self.parse(urlencode(fields).encode(), sign(URL, fields))

    def test_signed_invalid_fields_rejected(self):
        for key, value in [
            ("MessageSid", "invalid"),
            ("From", "+15005550009"),
            ("NumMedia", "99"),
            ("SmsMessageSid", "SM" + "e" * 32),
        ]:
            fields = self.fields()
            fields[key] = value
            with self.assertRaises(ValueError):
                self.parse(urlencode(fields).encode(), sign(URL, fields))
