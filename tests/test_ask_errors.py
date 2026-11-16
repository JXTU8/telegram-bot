"""
Regression tests for /ask error handling.

The headline case: a Gemini 429/503 error string contains the word "model",
and the old substring-based handler told users their MODEL NAME was wrong.
These tests use real google-genai error objects to prove that can't happen.
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

import requests
from google.genai.errors import ClientError, ServerError
from telegram.error import BadRequest

from handlers import ai
from handlers.ask_errors import (
    AskEmptyAnswer, AskSafetyBlocked, classify_ask_error,
)


def api_error(code: int, message: str, status: str):
    """Build a real google-genai APIError with the shape the API returns."""
    payload = {"error": {"code": code, "message": message, "status": status}}
    cls = ServerError if code >= 500 else ClientError
    return cls(code, payload)


class ClassificationTests(unittest.TestCase):
    def test_429_containing_the_word_model_is_busy_not_a_bad_model(self):
        exc = api_error(
            429,
            "Resource has been exhausted. The model gemini-3.5-flash-lite is overloaded.",
            "RESOURCE_EXHAUSTED",
        )
        failure = classify_ask_error(exc)
        self.assertEqual(failure.category, "busy")
        self.assertIn("high demand", failure.message)
        self.assertNotIn("GEMINI_MODEL", failure.message)
        self.assertTrue(failure.retryable)

    def test_503_overloaded_is_busy(self):
        exc = api_error(
            503, "The model is overloaded. Please try again later.", "UNAVAILABLE"
        )
        failure = classify_ask_error(exc)
        self.assertEqual(failure.category, "busy")
        self.assertIn("high demand", failure.message)

    def test_other_transient_server_codes_are_busy(self):
        for code in (500, 502):
            with self.subTest(code=code):
                self.assertEqual(
                    classify_ask_error(api_error(code, "Server error", "INTERNAL")).category,
                    "busy",
                )

    def test_404_reports_the_real_model_problem(self):
        exc = api_error(
            404, "models/gemini-typo is not found for API version v1beta", "NOT_FOUND"
        )
        failure = classify_ask_error(exc)
        self.assertEqual(failure.category, "model")
        self.assertIn("GEMINI_MODEL", failure.message)

    def test_404_with_the_word_unavailable_is_still_model_not_busy(self):
        exc = api_error(
            404, "This model is unavailable in your region", "NOT_FOUND"
        )
        self.assertEqual(classify_ask_error(exc).category, "model")

    def test_401_and_403_are_distinct_auth_problems(self):
        key = classify_ask_error(
            api_error(401, "API key not valid", "UNAUTHENTICATED")
        )
        permission = classify_ask_error(
            api_error(403, "Permission denied for this project", "PERMISSION_DENIED")
        )
        self.assertEqual(key.category, "auth_key")
        self.assertEqual(permission.category, "auth_permission")
        self.assertIn("GEMINI_API_KEY", key.message)
        self.assertIn("permission", permission.message.lower())

    def test_400_reports_a_bad_request(self):
        exc = api_error(400, "Invalid or unsupported request", "INVALID_ARGUMENT")
        self.assertEqual(classify_ask_error(exc).category, "bad_request")

    def test_429_wrapped_in_another_exception_is_still_busy(self):
        inner = api_error(429, "quota exceeded", "RESOURCE_EXHAUSTED")
        outer = RuntimeError("worker thread failed")
        outer.__cause__ = inner
        self.assertEqual(classify_ask_error(outer).category, "busy")

    def test_timeouts_and_network_failures_are_distinct(self):
        self.assertEqual(
            classify_ask_error(asyncio.TimeoutError()).category, "timeout"
        )
        self.assertEqual(
            classify_ask_error(requests.exceptions.ReadTimeout()).category, "timeout"
        )
        network = classify_ask_error(requests.exceptions.ConnectionError("refused"))
        self.assertEqual(network.category, "network")
        self.assertIn("network", network.message.lower())

    def test_safety_and_empty_answers(self):
        self.assertEqual(
            classify_ask_error(AskSafetyBlocked("SAFETY")).category, "safety"
        )
        self.assertEqual(
            classify_ask_error(AskEmptyAnswer()).category, "empty_answer"
        )

    def test_telegram_4096_limit_and_benign_not_modified(self):
        too_long = classify_ask_error(BadRequest("Message is too long"))
        self.assertEqual(too_long.category, "telegram_too_long")
        self.assertIn("4096", too_long.message)

        benign = classify_ask_error(BadRequest("Message is not modified"))
        self.assertTrue(benign.silent)

    def test_unknown_error_is_honest_and_never_leaks_raw_text(self):
        failure = classify_ask_error(RuntimeError("secret internal detail"))
        self.assertEqual(failure.category, "unknown")
        self.assertNotIn("secret internal detail", failure.message)

    def test_every_failure_has_a_nonempty_user_message(self):
        samples = [
            api_error(429, "busy", "RESOURCE_EXHAUSTED"),
            api_error(503, "busy", "UNAVAILABLE"),
            api_error(404, "missing", "NOT_FOUND"),
            api_error(403, "denied", "PERMISSION_DENIED"),
            api_error(401, "bad key", "UNAUTHENTICATED"),
            api_error(400, "bad", "INVALID_ARGUMENT"),
            asyncio.TimeoutError(),
            requests.exceptions.ConnectionError("down"),
            AskSafetyBlocked("SAFETY"),
            AskEmptyAnswer(),
            BadRequest("Message is too long"),
            RuntimeError("mystery"),
        ]
        for exc in samples:
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(classify_ask_error(exc).message.strip())


class ChunkingTests(unittest.TestCase):
    def test_short_answer_is_one_message(self):
        messages = ai.build_answer_messages("why?", None, "Because.")
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith(ai._ASK_PREFIX))
        self.assertIn("Because.", messages[0])

    def test_long_answer_is_split_and_every_chunk_fits_telegram(self):
        answer = "x" * 12000
        messages = ai.build_answer_messages("why?", None, answer)
        self.assertGreater(len(messages), 1)
        for message in messages:
            self.assertLessEqual(ai._utf16_len(message), ai.TELEGRAM_MAX_CHARS)
        first_header = f"{ai._ASK_PREFIX}why?\n\n"
        rebuilt = messages[0][len(first_header):]
        for message in messages[1:]:
            self.assertTrue(message.startswith(ai._ASK_OVERFLOW_PREFIX))
            rebuilt += message[len(ai._ASK_OVERFLOW_PREFIX):]
        self.assertEqual(rebuilt, answer)

    def test_emoji_heavy_answer_stays_within_utf16_limit(self):
        messages = ai.build_answer_messages("emoji?", None, "😀" * 4000)
        self.assertGreater(len(messages), 1)
        for message in messages:
            self.assertLessEqual(ai._utf16_len(message), ai.TELEGRAM_MAX_CHARS)

    def test_a_very_long_question_does_not_eat_the_answer(self):
        question = "q" * ai.MAX_ASK_LENGTH
        messages = ai.build_answer_messages(question, None, "short answer")
        self.assertLessEqual(ai._utf16_len(messages[0]), ai.TELEGRAM_MAX_CHARS)
        self.assertTrue(messages[0].startswith(ai._ASK_PREFIX))
        self.assertIn("short answer", messages[0])

    def test_history_adds_the_original_question_to_the_header(self):
        history = [{"role": "user", "content": "first?"},
                   {"role": "assistant", "content": "first answer"}]
        header = ai.build_answer_messages("follow up?", history, "second answer")[0]
        self.assertIn("first?", header)
        self.assertIn("follow up?", header)


class _Message:
    def __init__(self):
        self.replies = []
        self.entities = []
        self.reply_to_message = None
        self.text = "/ask something"
        self.message_id = 1

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.text = text


class _User:
    id = 42
    is_bot = False
    first_name = "Alice"
    username = "alice"


class _Chat:
    id = -100
    type = "supergroup"


class _Update:
    def __init__(self, message):
        self.message = message
        self.effective_user = _User()
        self.effective_chat = _Chat()


class _Context:
    def __init__(self, args, bot=None):
        self.args = args
        self.bot = bot or type("Bot", (), {"id": 999})()


class AskCommandEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(ai, "gemini_client", object())
        patcher.start()
        self.addCleanup(patcher.stop)
        ai._ASK_COOLDOWNS.clear()

    async def _run(self, args):
        message = _Message()
        with mock.patch.object(ai, "_process_ask", new_callable=mock.AsyncMock) as process:
            await ai.ask_command(_Update(message), _Context(args))
        return message, process

    async def test_empty_prompt_gets_a_specific_message(self):
        for args in ([], ["   "], ["???"], ["😀"]):
            with self.subTest(args=args):
                message, process = await self._run(args)
                self.assertIn("Please add a question", "".join(message.replies))
                process.assert_not_called()

    async def test_real_question_reaches_the_ai(self):
        message, process = await self._run(["what", "is", "1+1"])
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0], "what is 1+1")
        self.assertEqual(message.replies, [])

    async def test_overlong_prompt_is_reported_not_ignored(self):
        message, process = await self._run(["x" * (ai.MAX_ASK_LENGTH + 1)])
        self.assertIn("too long", "".join(message.replies).lower())
        process.assert_not_called()


class AskFailureFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_429_on_the_gemini_call_shows_high_demand(self):
        thinking = _Message()

        async def fake_run(func, *args, timeout=45.0):
            raise api_error(
                429, "The model gemini-3.5-flash-lite is overloaded",
                "RESOURCE_EXHAUSTED",
            )

        with mock.patch.object(ai, "_run_ai_io", new=fake_run):
            await ai._process_ask(
                "hi", None, -100, thinking, _Context([]), use_search=False
            )
        self.assertIn("high demand", thinking.text)
        self.assertNotIn("GEMINI_MODEL", thinking.text)

    async def test_search_failure_does_not_fail_the_answer(self):
        thinking = _Message()
        calls = []

        async def fake_run(func, *args, timeout=45.0):
            calls.append(getattr(func, "__name__", str(func)))
            if func is ai._search_web:
                raise requests.exceptions.ConnectionError("Serper down")
            return "All good"

        with (
            mock.patch.object(ai, "_run_ai_io", new=fake_run),
            mock.patch.object(ai, "_save_ask_context", new=lambda *a: None),
        ):
            await ai._process_ask(
                "hi", None, -100, thinking, _Context([]), use_search=True
            )
        self.assertIn("All good", thinking.text)
        self.assertIn("_search_web", calls)
        self.assertIn("_call_gemini", calls)

    async def test_empty_answer_is_reported_plainly(self):
        thinking = _Message()

        async def fake_run(func, *args, timeout=45.0):
            return "   "

        with mock.patch.object(ai, "_run_ai_io", new=fake_run):
            await ai._process_ask(
                "hi", None, -100, thinking, _Context([]), use_search=False
            )
        self.assertIn("no answer", thinking.text.lower())


if __name__ == "__main__":
    unittest.main()
