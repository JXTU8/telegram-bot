"""
Structural regression tests for the handler split.

The refactor moved wiring out of bot.py into handlers/registry.py and split
handlers/misc.py into focused modules. These tests guard the parts that are
easy to break silently: handler order/groups and the compatibility facade.
"""

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

from telegram.ext import ConversationHandler

import handlers.misc as facade
from handlers import registry

_EXPECTED_COMMANDS = {
    "start", "help", "log", "cancel", "ask", "listcountdown",
    "removecountdown", "luck", "luckboard", "streak", "ship", "shipboard",
    "roast", "compliment", "vibecheck", "truth", "dare", "wouldyourather",
    "coinflip", "8ball", "curse", "bless", "decide", "game", "hot",
    "cmdstats", "stats", "leaderboard", "recap", "profile", "status",
    "lucktest", "ban", "unban", "banlist", "say", "id",
}


class _FakeApp:
    def __init__(self):
        self.registered = []          # (group, handler)
        self.error_handlers = []

    def add_handler(self, handler, group=0):
        self.registered.append((group, handler))

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)


class RegistryTests(unittest.TestCase):
    def test_command_table_matches_the_documented_command_list(self):
        commands = set()
        for handler in registry._command_handlers():
            commands.update(handler.commands)
        self.assertEqual(commands, _EXPECTED_COMMANDS)

    def test_conversations_and_registration_groups(self):
        self.assertEqual(len(registry._conversation_handlers()), 3)

        app = _FakeApp()
        registry.register_handlers(app)

        groups = [group for group, _ in app.registered]
        self.assertEqual(groups.count(-1), 1, "ban gate must be alone in group -1")
        self.assertEqual(groups.count(1), 2, "seen-user + cmd-stats trackers")
        self.assertEqual(groups.count(2), 1, "game guess handler")
        self.assertEqual(len(app.error_handlers), 1)
        self.assertEqual(
            sum(1 for _, handler in app.registered
                if isinstance(handler, ConversationHandler)), 3,
        )

    def test_compatibility_facade_still_exports_every_old_name(self):
        for name in facade.__all__:
            self.assertTrue(hasattr(facade, name), f"handlers.misc is missing {name}")
        self.assertEqual(facade.log_command.__module__, "handlers.log")


if __name__ == "__main__":
    unittest.main()
