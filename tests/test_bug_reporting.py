"""
Tests for the global /bug reporting system.

A small in-memory FakeRedis stands in for Upstash so the atomic cooldown, ban
and announced-group logic can be exercised without network access.
"""

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

import helpers
from changelog import load_releases
from handlers import bug, tracking
from handlers.bug import build_bug_report, bug_command, banbug_command, unbanbug_command
from handlers.general import HELP_PAGES
from handlers.log import render_release
from stores import bug_store


class FakeRedis:
    """Only the commands used by stores/bug_store.py are implemented."""

    def __init__(self):
        self.strings = {}     # key -> (value, expire_at_monotonic or None)
        self.sets = {}        # key -> set[str]

    def _live(self, key):
        item = self.strings.get(key)
        if item is None:
            return None
        _, expire_at = item
        if expire_at is not None and time.monotonic() >= expire_at:
            del self.strings[key]
            return None
        return item

    def get(self, key):
        item = self._live(key)
        return item[0] if item else None

    def set(self, key, value, nx=False, ex=None, **kwargs):
        if nx and self._live(key) is not None:
            return False
        expire_at = time.monotonic() + ex if ex else None
        self.strings[key] = (value, expire_at)
        return True

    def ttl(self, key):
        item = self._live(key)
        if item is None:
            return -2
        _, expire_at = item
        return max(1, int(expire_at - time.monotonic() + 0.999)) if expire_at else -1

    def delete(self, *keys):
        return sum(1 for key in keys if self.strings.pop(key, None) is not None)

    def sadd(self, key, *members):
        current = self.sets.setdefault(key, set())
        added = 0
        for member in members:
            member = str(member)
            if member not in current:
                current.add(member)
                added += 1
        return added

    def smembers(self, key):
        return set(self.sets.get(key, set()))


class _RedisMixin:
    def setUp(self):
        self.fake_redis = FakeRedis()
        patcher = mock.patch.object(bug_store, "redis", self.fake_redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        admin_patcher = mock.patch.object(bug, "BUG_ADMIN_ID", 999999)
        admin_patcher.start()
        self.addCleanup(admin_patcher.stop)


class BugStoreTests(_RedisMixin, unittest.TestCase):
    def test_cooldown_allows_once_then_blocks(self):
        self.assertEqual(bug_store.claim_bug_cooldown(42, 600), 0)
        remaining = bug_store.claim_bug_cooldown(42, 600)
        self.assertTrue(0 < remaining <= 600)
        bug_store.release_bug_cooldown(42)
        self.assertEqual(bug_store.claim_bug_cooldown(42, 600), 0)

    def test_cooldown_is_per_user(self):
        self.assertEqual(bug_store.claim_bug_cooldown(1, 600), 0)
        self.assertEqual(bug_store.claim_bug_cooldown(2, 600), 0)
        self.assertGreater(bug_store.claim_bug_cooldown(1, 600), 0)

    def test_expired_cooldown_window_opens_again(self):
        self.assertEqual(bug_store.claim_bug_cooldown(7, 600), 0)
        self.fake_redis.strings["bug_cooldown:7"] = ("1", time.monotonic() - 1)
        self.assertEqual(bug_store.claim_bug_cooldown(7, 600), 0)

    def test_ban_and_unban_round_trip(self):
        self.assertFalse(bug_store.is_bug_banned(42))
        self.assertTrue(bug_store.ban_bug_user(42))
        self.assertFalse(bug_store.ban_bug_user(42))
        self.assertTrue(bug_store.is_bug_banned(42))
        self.assertEqual(bug_store.get_bug_banned_users(), {42})
        self.assertTrue(bug_store.unban_bug_user(42))
        self.assertFalse(bug_store.unban_bug_user(42))
        self.assertFalse(bug_store.is_bug_banned(42))

    def test_moderation_fails_closed_when_redis_is_down(self):
        with mock.patch.object(self.fake_redis, "get", side_effect=RuntimeError("down")):
            self.assertTrue(bug_store.is_bug_banned(42))
        with mock.patch.object(self.fake_redis, "set", side_effect=RuntimeError("down")):
            self.assertEqual(bug_store.claim_bug_cooldown(42, 600), -1)

    def test_announcement_is_claimed_exactly_once_per_group(self):
        self.assertTrue(bug_store.claim_bug_announcement(-1001))
        self.assertFalse(bug_store.claim_bug_announcement(-1001))
        self.assertTrue(bug_store.claim_bug_announcement(-1002))
        self.assertEqual(bug_store.get_announced_chats(), {-1001, -1002})

    def test_announcement_fails_closed_when_redis_is_down(self):
        with mock.patch.object(self.fake_redis, "sadd", side_effect=RuntimeError("down")):
            self.assertFalse(bug_store.claim_bug_announcement(-1001))


# ── Minimal Telegram fakes ───────────────────────────────────────────────────

class _User:
    def __init__(self, uid=123456789, username="alice", first_name="Alice"):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.is_bot = False


class _Chat:
    def __init__(self, chat_id=-1001234567, chat_type="supergroup", title="My Group"):
        self.id = chat_id
        self.type = chat_type
        self.title = title


class _Message:
    def __init__(self, text="/bug something"):
        self.text = text
        self.entities = []
        self.reply_to_message = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("delivery failed")
        self.sent.append(kwargs)


class _Update:
    def __init__(self, user, chat, message):
        self.effective_user = user
        self.effective_chat = chat
        self.message = message


class _Context:
    def __init__(self, args, bot):
        self.args = args
        self.bot = bot


class BugCommandTests(_RedisMixin, unittest.IsolatedAsyncioTestCase):
    def _make(self, args, *, chat=None, fail=False, uid=123456789):
        bot = _Bot(fail=fail)
        message = _Message()
        update = _Update(_User(uid=uid), chat or _Chat(), message)
        return update, _Context(args, bot), bot, message

    async def test_report_is_forwarded_with_every_required_field(self):
        update, ctx, bot, message = self._make(["/luck", "shows", "zero", "after", "midnight"])
        await bug_command(update, ctx)

        self.assertEqual(len(bot.sent), 1)
        sent = bot.sent[0]
        self.assertEqual(sent["chat_id"], 999999)
        text = sent["text"]
        for expected in (
            "shows zero after midnight", "@alice", "123456789",
            "My Group", "-1001234567", "/banbug 123456789", "/ban 123456789",
        ):
            self.assertIn(expected, text)
        self.assertIn("✅", "".join(message.replies))

    async def test_private_chat_is_labelled(self):
        chat = _Chat(chat_id=123456789, chat_type="private", title=None)
        update, ctx, bot, _ = self._make(["broken"], chat=chat)
        await bug_command(update, ctx)
        self.assertIn("Private chat", bot.sent[0]["text"])

    async def test_missing_description_shows_usage_and_sends_nothing(self):
        update, ctx, bot, message = self._make([])
        await bug_command(update, ctx)
        self.assertEqual(bot.sent, [])
        self.assertIn("Usage", "".join(message.replies))
        self.assertNotIn("One report per", "".join(message.replies))

    async def test_overlong_description_is_rejected(self):
        update, ctx, bot, message = self._make(["x" * 1001])
        await bug_command(update, ctx)
        self.assertEqual(bot.sent, [])
        self.assertIn("under", "".join(message.replies))

    async def test_cooldown_blocks_the_second_report(self):
        update, ctx, bot, _ = self._make(["first"])
        await bug_command(update, ctx)
        update2, ctx2, _, message2 = self._make(["second"])
        ctx2.bot = bot
        await bug_command(update2, ctx2)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("wait", "".join(message2.replies).lower())

    async def test_blocked_user_is_silently_ignored(self):
        bug_store.ban_bug_user(123456789)
        update, ctx, bot, message = self._make(["spam"])
        await bug_command(update, ctx)
        self.assertEqual(bot.sent, [])
        self.assertEqual(message.replies, [])

    async def test_failed_delivery_releases_the_cooldown(self):
        update, ctx, bot, message = self._make(["first"], fail=True)
        await bug_command(update, ctx)
        self.assertIn("Couldn't deliver", "".join(message.replies))
        self.assertEqual(bug_store.claim_bug_cooldown(123456789, 600), 0)

    async def test_unconfigured_admin_is_reported_without_using_cooldown(self):
        with mock.patch.object(bug, "BUG_ADMIN_ID", 0):
            update, ctx, bot, message = self._make(["hello"])
            await bug_command(update, ctx)
        self.assertEqual(bot.sent, [])
        self.assertIn("isn't set up", "".join(message.replies))
        self.assertEqual(bug_store.claim_bug_cooldown(123456789, 600), 0)


class BugModerationTests(_RedisMixin, unittest.IsolatedAsyncioTestCase):
    def _make(self, args, uid=1):
        bot = _Bot()
        message = _Message()
        update = _Update(_User(uid=uid, username="owner"), _Chat(), message)
        return update, _Context(args, bot), message

    async def test_owner_can_ban_and_unban(self):
        with mock.patch.object(helpers, "BOT_OWNER_ID", 1):
            update, ctx, message = self._make(["42"])
            await banbug_command(update, ctx)
            self.assertTrue(bug_store.is_bug_banned(42))
            self.assertIn("no longer use /bug", "".join(message.replies))

            update, ctx, message = self._make(["42"])
            await unbanbug_command(update, ctx)
            self.assertFalse(bug_store.is_bug_banned(42))
            self.assertIn("can use /bug again", "".join(message.replies))

    async def test_non_owner_cannot_moderate(self):
        with mock.patch.object(helpers, "BOT_OWNER_ID", 999):
            update, ctx, message = self._make(["42"], uid=1)
            await banbug_command(update, ctx)
        self.assertFalse(bug_store.is_bug_banned(42))
        self.assertEqual(message.replies, [])

    async def test_bad_arguments_show_usage(self):
        with mock.patch.object(helpers, "BOT_OWNER_ID", 1):
            update, ctx, message = self._make([])
            await banbug_command(update, ctx)
            update, ctx, message2 = self._make(["not-a-number"])
            await unbanbug_command(update, ctx)
        self.assertIn("Usage", "".join(message.replies))
        self.assertIn("Usage", "".join(message2.replies))


class BugAnnouncementTests(_RedisMixin, unittest.IsolatedAsyncioTestCase):
    async def _run_tracker(self, chat, text):
        bot = _Bot()
        message = _Message(text=text)
        update = _Update(_User(), chat, message)
        await tracking.bug_announcement_tracker(update, _Context([], bot))
        return bot

    async def test_notice_is_sent_once_per_group_on_a_command(self):
        chat = _Chat(chat_id=-1001)
        first = await self._run_tracker(chat, "/start")
        second = await self._run_tracker(chat, "/help")
        self.assertEqual(len(first.sent), 1)
        self.assertEqual(second.sent, [])
        self.assertIn("/bug", first.sent[0]["text"])
        self.assertNotIn("One report per", first.sent[0]["text"])

    async def test_dms_and_plain_messages_do_not_announce(self):
        dm = await self._run_tracker(_Chat(chat_id=1, chat_type="private"), "/start")
        plain = await self._run_tracker(_Chat(chat_id=-1002), "just chatting")
        self.assertEqual(dm.sent, [])
        self.assertEqual(plain.sent, [])
        self.assertEqual(bug_store.get_announced_chats(), set())

    async def test_passive_tracker_dispatches_all_jobs_and_isolates_failures(self):
        calls = []

        async def seen(u, c):
            calls.append("seen")

        async def boom(u, c):
            raise RuntimeError("tracker exploded")

        async def announce(u, c):
            calls.append("announce")

        bot = _Bot()
        update = _Update(_User(), _Chat(), _Message("/start"))
        ctx = _Context([], bot)
        with mock.patch.multiple(
            tracking,
            seen_user_tracker=seen,
            command_stats_tracker=boom,
            bug_announcement_tracker=announce,
        ):
            await tracking.passive_tracker(update, ctx)  # must not raise
        self.assertCountEqual(calls, ["seen", "announce"])


class ChangelogAndHelpTests(unittest.TestCase):
    def test_help_documents_bug_usage(self):
        self.assertIn("/bug <what went wrong>", HELP_PAGES["other"])
        self.assertNotIn("one report per", HELP_PAGES["other"].lower())

    def test_latest_log_entry_announces_bug(self):
        releases = load_releases()
        self.assertEqual(releases[0].version, "1.1.0")
        self.assertIn("/bug <description>", render_release(releases[0]))

    def test_report_escapes_markdown_but_keeps_copyable_ids(self):
        text = build_bug_report(
            "broken *bold* _and_ [link]",
            display_name="Al_ice", username="al_ice", user_id=42,
            chat_label="My*Group", chat_id=-100, timestamp="2026-11-15 15:22",
        )
        self.assertIn(r"broken \*bold\* \_and\_ \[link]", text)
        self.assertIn("`42`", text)
        self.assertIn("`/banbug 42`", text)
        self.assertIn(r"Al\_ice", text)


if __name__ == "__main__":
    unittest.main()
