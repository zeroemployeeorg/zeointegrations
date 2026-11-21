"""A configured endpoint must use TLS: https for TTS, wss for realtime ASR."""

import unittest

from pydantic import ValidationError

from zeo_integrations.voice.openai import OpenAITTSConfig
from zeo_integrations.voice.speechmatics import SpeechmaticsASRConfig


class EndpointTests(unittest.TestCase):
    def test_openai_refuses_non_https_endpoint(self) -> None:
        for endpoint in ("http://example.test/v1", "ws://example.test", "example.test"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValidationError, "https://"):
                    OpenAITTSConfig(endpoint=endpoint)

    def test_openai_accepts_https_endpoint_and_unset(self) -> None:
        config = OpenAITTSConfig(endpoint="https://example.test/v1")
        self.assertEqual(config.endpoint, "https://example.test/v1")
        self.assertIsNone(OpenAITTSConfig().endpoint)

    def test_speechmatics_refuses_non_wss_endpoint(self) -> None:
        for endpoint in ("ws://example.test", "https://example.test", "example.test"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValidationError, "wss://"):
                    SpeechmaticsASRConfig(endpoint=endpoint)

    def test_speechmatics_accepts_wss_endpoint_and_unset(self) -> None:
        config = SpeechmaticsASRConfig(endpoint="wss://example.test/v2")
        self.assertEqual(config.endpoint, "wss://example.test/v2")
        self.assertIsNone(SpeechmaticsASRConfig().endpoint)
