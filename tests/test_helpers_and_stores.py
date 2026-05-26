import json
import os
import unittest

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

from helpers import _display_name_or_id, _has_visible_text
from stores import mvp_store, quote_store


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        return True


class HelperTests(unittest.TestCase):
    def test_invisible_name_falls_back_to_id(self):
        self.assertFalse(_has_visible_text("ㅤ"))
        self.assertFalse(_has_visible_text("\u200b"))
        self.assertEqual(_display_name_or_id("ㅤ", 123), "123")
        self.assertEqual(_display_name_or_id("Alice", 123), "Alice")


class QuoteStoreTests(unittest.TestCase):
    def test_user_quote_counts(self):
        fake = FakeRedis()
        quote_store.redis = fake
        fake.set("quotes:1", json.dumps([
            {"author": "Alice", "text": "hi", "saved_by": "Bob"},
            {"author": "alice", "text": "yo", "saved_by": "Alice"},
            {"author": "Bob", "text": "ok", "saved_by": "Alice"},
        ]))
        self.assertEqual(quote_store.get_user_quote_counts(1, "Alice"), (2, 2))


class MvpStoreTests(unittest.TestCase):
    def test_daily_mvp_is_saved_once_and_board_counts_once(self):
        fake = FakeRedis()
        mvp_store.redis = fake

        first = mvp_store.save_mvp_win(1, "2026-05-26", "10", "Alice")
        second = mvp_store.save_mvp_win(1, "2026-05-26", "11", "Bob")

        self.assertEqual(first["user_id"], "10")
        self.assertEqual(second["user_id"], "10")
        self.assertEqual(mvp_store.get_today_mvp(1, "2026-05-26")["user_id"], "10")
        board = mvp_store.get_mvp_board(1)
        self.assertEqual(board[0]["user_id"], "10")
        self.assertEqual(board[0]["wins"], 1)
        self.assertEqual(mvp_store.get_user_mvp_stats(1, 10)["wins"], 1)


if __name__ == "__main__":
    unittest.main()
