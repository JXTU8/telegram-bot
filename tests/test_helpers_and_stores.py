import os
import unittest

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

from helpers import _display_name_or_id, _has_visible_text


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


if __name__ == "__main__":
    unittest.main()
