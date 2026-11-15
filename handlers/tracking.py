"""
handlers/tracking.py
────────────────────
Background, cross-cutting handlers that are not commands:
  • seen-user tracking (group 1)  • command usage counters (group 1)
  • the global error handler      • the shared conversation timeout message

STRUCTURAL CHANGE (refactor):
    These were tacked onto the bottom of ``handlers/misc.py``. They run on
    *every* update and share module-level caches/cooldowns, so isolating them
    here keeps their state away from command logic and makes the handler groups
    registered in ``handlers/registry.py`` easy to reason about.
"""

import asyncio
import logging
import time as _time
from collections import OrderedDict

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from helpers import _delete_tracked, _display_user, _thread_kwargs
from stores.stats_store import increment_cmd_stat
from stores.user_store import track_seen_user

logger = logging.getLogger(__name__)

# ── Seen-user in-memory cache ─────────────────────────────────────────────────
# Avoids a Redis write for every message from the same person within the hour.

_SEEN_CACHE: OrderedDict = OrderedDict()
_SEEN_CACHE_TTL = 3600
_SEEN_CACHE_MAX = 10_000


async def seen_user_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or user.is_bot:
        return
    cache_key = (chat.id, user.id)
    now = _time.monotonic()
    if now - _SEEN_CACHE.get(cache_key, 0) < _SEEN_CACHE_TTL:
        _SEEN_CACHE.move_to_end(cache_key)
        return
    expired = [
        key for key, seen_at in _SEEN_CACHE.items()
        if now - seen_at >= _SEEN_CACHE_TTL
    ]
    for key in expired:
        del _SEEN_CACHE[key]
    if len(_SEEN_CACHE) >= _SEEN_CACHE_MAX:
        evict_count = _SEEN_CACHE_MAX // 10
        for _ in range(evict_count):
            _SEEN_CACHE.popitem(last=False)
        logger.debug("_SEEN_CACHE evicted %s entries (was at cap)", evict_count)
    _SEEN_CACHE[cache_key] = now
    await asyncio.to_thread(track_seen_user, chat.id, user.id, _display_user(user))


# ── Command usage tracking ───────────────────────────────────────────────────

async def command_stats_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires on every command message (group 1). Increments the usage counter."""
    msg = update.message
    if not msg or not msg.text or not msg.text.startswith("/"):
        return
    # Strip the leading slash, any @BotName suffix, and lowercase.
    raw = msg.text.split()[0].lstrip("/").split("@")[0].lower()
    if raw:
        await asyncio.to_thread(increment_cmd_stat, raw)


# ── Global error handler ─────────────────────────────────────────────────────

_last_conflict_log: float = 0.0
_CONFLICT_LOG_COOLDOWN = 60.0


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_conflict_log
    from telegram.error import Conflict, NetworkError, TimedOut

    err = context.error

    if isinstance(err, Conflict):
        now = _time.monotonic()
        if now - _last_conflict_log >= _CONFLICT_LOG_COOLDOWN:
            logger.warning(
                "Conflict: duplicate bot instance detected (resolves after deploy finishes)"
            )
            _last_conflict_log = now
        return

    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Transient network error (will retry): %s", err)
        return

    logger.exception("Unhandled Telegram update error", exc_info=err)
    message = getattr(update, "effective_message", None)
    if not message:
        return
    try:
        await message.reply_text("❌ Something broke while handling that. The error was logged.")
    except Exception as e:
        logger.warning("Failed to notify user about handler error: %s", e)


# ── Shared conversation timeout ──────────────────────────────────────────────

async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """End any timed-out wizard with a hint pointing back to the right command."""
    if "new_countdown_name" in context.user_data or "new_countdown_date" in context.user_data:
        hint = "Start again with /addcountdown."
        thread_kwargs = {}
    elif "edit_countdown_name" in context.user_data:
        hint = "Start again with /editcountdown."
        thread_kwargs = {}
    elif "decision" in context.user_data:
        hint = "Start again with /choose."
        thread_kwargs = _thread_kwargs(update)
    else:
        hint = "Start again with the relevant command."
        thread_kwargs = _thread_kwargs(update)
    await _delete_tracked(context)
    context.user_data.clear()
    chat = getattr(update, "effective_chat", None)
    if not chat:
        logger.warning("Conversation timeout fired without an effective chat.")
        return ConversationHandler.END
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"⏰ *Timed out!* You took too long to respond.\n{hint}",
        parse_mode="Markdown",
        **thread_kwargs,
    )
    return ConversationHandler.END
