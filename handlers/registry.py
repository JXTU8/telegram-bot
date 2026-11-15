"""
handlers/registry.py
────────────────────
The bot's wiring diagram: which update goes to which handler, in which order.

STRUCTURAL CHANGE (refactor):
    ``bot.py`` used to contain ~120 lines of registration: the ban gate, two
    wrapper factories, three hand-built ConversationHandlers, 37 add_handler
    calls and the startup hook. It mixed *policy* (what the bot does) with
    *plumbing* (how it starts). Now bot.py only builds the Application, and all
    plumbing is here, in one declarative place.

    Adding a command is a one-line edit to ``_command_handlers()``. Handler
    ORDER AND GROUPS ARE SIGNIFICANT — see the comments in register_handlers().
"""

from __future__ import annotations

import asyncio
import logging
import time as _time

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, TypeHandler, filters,
)

from helpers import (
    ASK_DATE, ASK_DECISION, ASK_EDIT_FIELD, ASK_EDIT_VALUE, ASK_NAME,
    ASK_OPTIONS, ASK_TIME, CONV_TIMEOUT,
    _is_owner, _patch_message_reply_to_thread,
)
from stores.ban_store import is_banned

from handlers.admin import (
    ban_command, banlist_command, cmdstats_command, id_command, say_command,
    status_command, unban_command,
)
from handlers.ai import (
    ask_command, ask_followup_handler, choose_start, received_decision,
    received_options,
)
from handlers.bug import banbug_command, bug_command, unbanbug_command
from handlers.countdown import (
    add_countdown_start, cancel, editcountdown_start, list_countdown,
    received_date, received_edit_field, received_edit_value, received_name,
    received_time, remove_countdown_cmd, restore_jobs,
)
from handlers.fun import (
    bless_command, coinflip_command, compliment_command, curse_command,
    dare_command, decide_command, eightball_command, game_command,
    game_guess_handler, hot_command, roast_command, ship_command,
    shipboard_command, truth_command, vibecheck_command,
    would_you_rather_command,
)
from handlers.general import (
    cancel_command, help_callback, help_command, start_command,
)
from handlers.log import log_command
from handlers.luck import (
    luck_command, luckboard_command, lucktest_command, streak_command,
)
from handlers.stats import (
    leaderboard_command, profile_command, recap_command, stats_command,
)
from handlers.tracking import conversation_timeout, error_handler, passive_tracker

logger = logging.getLogger(__name__)


# ── Ban gate (group -1, runs before every other handler) ─────────────────────
# Banned status is cached per-user for 5 minutes to avoid a Redis round-trip on
# every update/click. A newly banned user can still send one more update within
# that window, which is an acceptable trade-off.

_BAN_CACHE: dict[int, tuple[float, bool]] = {}   # user_id → (checked_at, banned)
_BAN_CACHE_TTL = 300                              # seconds


async def ban_gate(update: Update, context) -> None:
    if not update.effective_user or update.effective_user.is_bot:
        return
    if _is_owner(update.effective_user):
        return
    uid = update.effective_user.id
    now = _time.monotonic()
    cached = _BAN_CACHE.get(uid)
    if cached and now - cached[0] < _BAN_CACHE_TTL:
        if cached[1]:
            raise ApplicationHandlerStop
        return
    banned = await asyncio.to_thread(is_banned, uid)
    _BAN_CACHE[uid] = (now, banned)
    if banned:
        raise ApplicationHandlerStop


# ── Message-thread reply shim ────────────────────────────────────────────────
# In forum/topic chats, reply_text() must carry message_thread_id. Patching it
# once per update means handlers can keep calling reply_text() normally.

def _threaded(handler):
    async def wrapper(update: Update, context):
        if getattr(update, "message", None):
            _patch_message_reply_to_thread(update)
        return await handler(update, context)
    return wrapper


# ── Conversation builders ────────────────────────────────────────────────────

def _conversation_handlers() -> list[ConversationHandler]:
    """The three multi-step wizards, each with /cancel and a shared timeout."""
    def timeout_state(*, threaded: bool = False) -> list[MessageHandler]:
        handler = _threaded(conversation_timeout) if threaded else conversation_timeout
        return [MessageHandler(filters.ALL, handler)]

    countdown_conv = ConversationHandler(
        entry_points=[CommandHandler("addcountdown", add_countdown_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time)],
            ConversationHandler.TIMEOUT: timeout_state(),
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    choose_conv = ConversationHandler(
        entry_points=[CommandHandler("choose", _threaded(choose_start))],
        states={
            ASK_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, _threaded(received_decision))],
            ASK_OPTIONS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _threaded(received_options))],
            ConversationHandler.TIMEOUT: timeout_state(threaded=True),
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("editcountdown", editcountdown_start)],
        states={
            ASK_EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_field)],
            ASK_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_value)],
            ConversationHandler.TIMEOUT: timeout_state(),
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    return [countdown_conv, choose_conv, edit_conv]


# ── Command table ────────────────────────────────────────────────────────────
# STRUCTURAL CHANGE: this replaces 37 near-identical add_handler calls (now 40
# with /bug, /banbug, /unbanbug). The two countdown list/remove commands opt out
# of the thread-reply shim because they build their own HTML replies directly.

def _command_handlers() -> list[CommandHandler]:
    def cmd(name: str, handler, *, threaded: bool = True) -> CommandHandler:
        active = _threaded(handler) if threaded else handler
        # Only fire on real messages, never on edits or channel posts.
        return CommandHandler(name, active, filters=filters.UpdateType.MESSAGE)

    return [
        cmd("start",           start_command),
        cmd("help",            help_command),
        cmd("log",             log_command),
        cmd("bug",             bug_command),
        cmd("cancel",          cancel_command),
        cmd("ask",             ask_command),
        cmd("listcountdown",   list_countdown, threaded=False),
        cmd("removecountdown", remove_countdown_cmd, threaded=False),
        cmd("luck",            luck_command),
        cmd("luckboard",       luckboard_command),
        cmd("streak",          streak_command),
        cmd("ship",            ship_command),
        cmd("shipboard",       shipboard_command),
        cmd("roast",           roast_command),
        cmd("compliment",      compliment_command),
        cmd("vibecheck",       vibecheck_command),
        cmd("truth",           truth_command),
        cmd("dare",            dare_command),
        cmd("wouldyourather",  would_you_rather_command),
        cmd("coinflip",        coinflip_command),
        cmd("8ball",           eightball_command),
        cmd("curse",           curse_command),
        cmd("bless",           bless_command),
        cmd("decide",          decide_command),
        cmd("game",            game_command),
        cmd("hot",             hot_command),
        cmd("cmdstats",        cmdstats_command),
        cmd("stats",           stats_command),
        cmd("leaderboard",     leaderboard_command),
        cmd("recap",           recap_command),
        cmd("profile",         profile_command),
        cmd("status",          status_command),
        cmd("lucktest",        lucktest_command),
        cmd("ban",             ban_command),
        cmd("unban",           unban_command),
        cmd("banlist",         banlist_command),
        cmd("banbug",          banbug_command),
        cmd("unbanbug",        unbanbug_command),
        cmd("say",             say_command),
        cmd("id",              id_command),
    ]


# ── Registration ─────────────────────────────────────────────────────────────

def register_handlers(app) -> None:
    """Register everything on the Application, preserving group/order semantics."""
    # group=-1 — the ban gate must see every update before anything else.
    app.add_handler(TypeHandler(Update, ban_gate), group=-1)

    # Callback patterns use "prefix:.+" so they work under both re.match
    # (PTB 20.x) and re.fullmatch (PTB 21.x). The old "^prefix:" form only
    # matched the literal string under fullmatch, so inline buttons hung.
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^help:.+"))

    # Conversations must be registered before the plain-text message handlers.
    for conversation in _conversation_handlers():
        app.add_handler(conversation)

    for command_handler in _command_handlers():
        app.add_handler(command_handler)

    # group 0 — reply-to-an-/ask-answer follow-ups (no /ask needed).
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.REPLY,
        _threaded(ask_followup_handler),
    ))

    # group 1 — ONE passive tracker. PTB runs at most one handler per group, so
    # seen-user tracking, command stats and the one-time /bug notice are all
    # dispatched from handlers/tracking.passive_tracker.
    app.add_handler(MessageHandler(filters.ALL, passive_tracker), group=1)

    # group 2 — number guesses; deliberately isolated from the trackers.
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, _threaded(game_guess_handler),
    ), group=2)

    app.add_error_handler(error_handler)


# ── Startup hook ─────────────────────────────────────────────────────────────

async def on_startup(app) -> None:
    """Runs once after the bot connects. Restores countdown jobs from Redis."""
    try:
        await restore_jobs(app)
    except Exception as e:
        logger.error("on_startup: restoring countdown jobs failed: %s", e)
    logger.info("on_startup complete — countdown jobs restored.")
