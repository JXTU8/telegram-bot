import os
import unittest

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

from handlers import ai


class _FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"text": "Gemini response"})()


class GeminiConfigurationTests(unittest.TestCase):
    def test_ask_uses_english_and_single_attempt(self):
        models = _FakeModels()
        previous_client = ai.gemini_client
        ai.gemini_client = type("FakeClient", (), {"models": models})()
        try:
            self.assertEqual(ai._call_gemini("hello", ""), "Gemini response")
        finally:
            ai.gemini_client = previous_client

        config = models.calls[0]["config"]
        self.assertIn("Always answer in natural English", config.system_instruction)
        self.assertTrue(config.automatic_function_calling.disable)
        self.assertFalse(config.automatic_continuation)
        self.assertEqual(config.http_options.timeout, 45_000)
        self.assertEqual(config.http_options.retry_options.attempts, 1)


if __name__ == "__main__":
    unittest.main()
