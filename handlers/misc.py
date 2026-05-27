"""
handlers/misc.py
────────────────
/start, /help (paginated), /stats, and the shared conversation_timeout handler.
"""

import asyncio
import logging
import os
import time as _time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import TIMEZONE
from db import redis
from stores.birthday_store import get_all_birthdays
from stores.countdown_store import get_all_countdowns
from stores.luck_store import get_fate_board, get_fate_streak
from stores.mvp_store import get_user_mvp_stats
from stores.quote_store import get_quote_count, get_user_quote_counts
from stores.reminder_store import get_user_remind_jobs
from stores.ship_store import get_top_ship_pairs, get_shipboard_reset_time
from stores.user_store import get_seen_users, track_seen_user
from helpers import (
    _display_user, _escape_md, _delete_tracked,
    _display_name_or_id, owner_only,
)

logger = logging.getLogger(__name__)

# ── Seen-user in-memory cache (Issue 25) ──────────────────────────────────────
# Avoids a Redis read+write on every single message from a known user.
_SEEN_CACHE: dict = {}        # (chat_id, user_id) -> monotonic timestamp
_SEEN_CACHE_TTL = 3600        # 1 hour — only write to Redis if not seen within this window

logger = logging.getLogger(__name__)

# ── /start ────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track multiple countdowns for your group and remind everyone daily.\n"
        "I can also make decisions, check luck, set reminders and more!\n\n"
        "➕ /addcountdown — add a new countdown\n"
        "📋 /listcountdown — see all active countdowns\n"
        "🎲 /choose — let me decide for you\n"
        "🍀 /luck — check your daily luck\n"
        "⏰ /remind — set a personal reminder\n"
        "🎂 /addbirthday — set your birthday\n"
        "🎉 /help — see all commands",
        parse_mode="Markdown",
    )


# ── /help (paginated) ─────────────────────────────────────────────────────────

HELP_PAGES = {
    "countdown": (
        "⏱ *Countdown*\n\n"
        "/addcountdown — add a new countdown, step by step\n"
        "/listcountdown — see all active countdowns in this group\n"
        "/removecountdown <code> — remove a countdown\n"
        "/editcountdown <code> — edit a countdown's date or time"
    ),
    "decisions": (
        "🎲 *Decisions*\n\n"
        "/choose — can't decide? let the bot pick for you\n"
        "/decide opt1, opt2, opt3 — instant pick\n"
        "/rank topic: item1, item2, item3 — rank anything\n"
        "/toss — pick a random person from mentions or the group\n"
        "/poll question: opt1, opt2 — send a native Telegram poll"
    ),
    "ai": (
        "🤖 *AI*\n\n"
        "/ask <question> — ask AI anything\n"
        "/8ball <question> — magic 8-ball\n"
        "/hot <anything> — rate anything out of 100"
    ),
    "luck": (
        "🍀 *Daily Luck*\n\n"
        "/luck — check your personal daily luck\n"
        "/luck @user — check someone else's daily luck\n"
        "/luckboard — today's luck leaderboard with streak badges\n"
        "/streak — check your current luck streak"
    ),
    "fun": (
        "🎉 *Fun*\n\n"
        "/ship @user1 @user2 — compatibility percentage\n"
        "/shipboard — top ship pairs in this group\n"
        "/roast @user — personalised roast\n"
        "/compliment @user — personalised compliment\n"
        "/vibecheck — group mood score\n"
        "/mvp — today's MVP\n"
        "/mvpboard — all-time MVP leaderboard\n"
        "/truth — random truth question\n"
        "/dare — random dare question\n"
        "/wouldyourather — random would you rather\n"
        "/coinflip — heads or tails\n"
        "/curse @user — daily curse\n"
        "/bless @user — daily blessing"
    ),
    "quotes": (
        "💬 *Quotes*\n\n"
        "/quote — reply to any message to save it\n"
        "/quotes — browse saved quotes with prev/next\n"
        "/deletequote <number> — delete a quote (admins only)"
    ),
    "reminders": (
        "⏰ *Reminders*\n\n"
        "/remind 10m take a break — set a personal reminder\n"
        "/cancelremind — view and cancel your pending reminders\n"
        "/remindall 1h group meeting — group-wide reminder (admins only)\n"
        "/birthday — see upcoming birthdays in this chat\n"
        "/addbirthday DD/MM — set your birthday\n"
        "/deletebirthday — delete your birthday\n"
        "/deletebirthday @user — delete someone's birthday (admins only)\n"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/stats — group activity summary\n"
        "/profile — your bot profile in this chat\n"
        "/cancel — cancel an active setup flow\n"
        "/help — show this menu"
    ),
}

_HELP_PAGE_ORDER = ["countdown", "decisions", "ai", "luck", "fun", "quotes", "reminders", "other"]
_HELP_PAGE_LABELS = {
    "countdown": "⏱ Countdown",
    "decisions": "🎲 Decisions",
    "ai": "🤖 AI",
    "luck": "🍀 Luck",
    "fun": "🎉 Fun",
    "quotes": "💬 Quotes",
    "reminders": "⏰ Reminders",
    "other": "⚙️ Other",
}


def _help_keyboard(current_page: str) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for page_key in _HELP_PAGE_ORDER:
        label = _HELP_PAGE_LABELS[page_key]
        if page_key == current_page:
            label = f"› {label} ‹"
        row.append(InlineKeyboardButton(label, callback_data=f"help:{page_key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = "countdown"
    await update.message.reply_text(
        HELP_PAGES[page] + "\n\n_Tap a category below:_",
        parse_mode="Markdown",
        reply_markup=_help_keyboard(page),
    )


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, page = query.data.split(":", 1)
    if page not in HELP_PAGES:
        return
    try:
        await query.edit_message_text(
            HELP_PAGES[page] + "\n\n_Tap a category below:_",
            parse_mode="Markdown",
            reply_markup=_help_keyboard(page),
        )
    except Exception as e:
        logger.debug("help_callback edit skipped (message unchanged): %s", e)


# ── /stats ────────────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    quote_count, seen_users, top_pairs, board, countdowns = await asyncio.gather(
        asyncio.to_thread(get_quote_count, chat_id),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_all_countdowns, chat_id),
    )
    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)

    member_count = len(seen_users)
    countdown_count = len(countdowns)
    reset_h = reset_secs // 3600
    reset_m = (reset_secs % 3600) // 60

    if top_pairs:
        p = top_pairs[0]
        ship_line = f"{_escape_md(p['label_a'])} × {_escape_md(p['label_b'])} `{p['score']:.1f}%`"
    else:
        ship_line = "No ships yet this cycle"

    luck_count = len(board)
    if board:
        top_uid = max(board, key=lambda k: board[k]["score"])
        t = board[top_uid]
        lucky_line = f"{_escape_md(t['name'])} — {t['tier']} (`{t['score']}`)"
    else:
        lucky_line = "Nobody checked today"

    lines = [
        "📊 *Group Stats*\n",
        f"👥 Members tracked: *{member_count}*",
        f"💬 Quotes saved: *{quote_count}*",
        f"⏱ Active countdowns: *{countdown_count}*",
        f"\n🍀 *Today's Luck*",
        f"Checks: *{luck_count}*",
        f"Luckiest: {lucky_line}",
        f"\n💞 *Ships* _(resets in {reset_h}h {reset_m}m)_",
        f"Top pair: {ship_line}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /profile ─────────────────────────────────────────────────────────────────

def _profile_target(update: Update):
    message = update.message
    for entity in message.entities or []:
        if entity.type == "text_mention" and getattr(entity, "user", None):
            return entity.user, None
        if entity.type == "mention":
            return None, message.parse_entity(entity)
    return update.effective_user, None


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    target_user, username_only = _profile_target(update)
    if username_only and target_user is None:
        await update.message.reply_text(
            "⚠️ I can only show profiles for direct mentions or yourself.\n"
            "Try tapping the user from Telegram's mention picker, or use /profile for your own stats."
        )
        return

    user_id = target_user.id
    name = _display_user(target_user)
    today = datetime.now(TIMEZONE).date()

    bdays, reminders, streak, mvp_stats, quote_counts = await asyncio.gather(
        asyncio.to_thread(get_all_birthdays, chat_id),
        asyncio.to_thread(get_user_remind_jobs, chat_id, user_id),
        asyncio.to_thread(get_fate_streak, user_id),
        asyncio.to_thread(get_user_mvp_stats, chat_id, user_id),
        asyncio.to_thread(get_user_quote_counts, chat_id, name),
    )

    bday = bdays.get(str(user_id))
    if bday:
        birthday_line = f"{bday.get('day', 0):02d}/{bday.get('month', 0):02d}"
    else:
        birthday_line = "Not set"

    streak_count, streak_cat = streak
    streak_line = f"{streak_count} day(s) {streak_cat}" if streak_count else "No active streak"
    authored_quotes, saved_quotes = quote_counts
    mvp_wins = int(mvp_stats.get("wins", 0)) if mvp_stats else 0
    last_mvp = mvp_stats.get("last_won", "Never") if mvp_stats else "Never"
    seen = await asyncio.to_thread(get_seen_users, chat_id)
    seen_name = _display_name_or_id(seen.get(str(user_id), name), user_id)

    # Lazy import to avoid circular-import at startup
    from handlers.luck import _get_fate
    luck_score, luck_tier, _ = _get_fate(user_id)
    if luck_score == 999:
        luck_display = "999 ⚡ MAXIMUM"
    elif luck_score == -999:
        luck_display = "-999 💀 MINIMUM"
    else:
        luck_display = f"{luck_score}/100"

    await update.message.reply_text(
        "\n".join([
            f"👤 *Profile — {_escape_md(seen_name)}*",
            f"🍀 Today's luck: *{_escape_md(luck_tier)}* (`{luck_display}`)",
            f"📈 Luck streak: *{_escape_md(streak_line)}*",
            f"🏆 MVP wins: *{mvp_wins}*",
            f"Last MVP: *{_escape_md(last_mvp)}*",
            f"⏰ Pending reminders: *{len(reminders)}*",
            f"🎂 Birthday: *{_escape_md(birthday_line)}*",
            f"💬 Quotes authored/saved: *{authored_quotes}/{saved_quotes}*",
            f"📆 Today: *{today.isoformat()} MYT*",
        ]),
        parse_mode="Markdown",
    )


# ── /status and background tracking ──────────────────────────────────────────

def _env_status(name: str) -> str:
    return "set" if os.getenv(name) else "missing"


def _redis_health() -> tuple:
    try:
        if hasattr(redis, "ping"):
            redis.ping()
        else:
            redis.get("__healthcheck__")
        return True, "ok"
    except Exception as e:
        return False, str(e)


@owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    redis_ok, redis_msg = await asyncio.to_thread(_redis_health)
    try:
        job_count = len(context.application.job_queue.jobs())
    except Exception:
        job_count = -1

    from handlers.ai import groq_client, SERPER_API_KEY

    # Sanitize redis_msg: strip backticks (already done) AND escape Markdown specials
    redis_detail = _escape_md(redis_msg[:120].replace("`", "").replace("\n", " "))

    lines = [
        "🧪 *Bot Status*",
        f"Redis: *{'ok' if redis_ok else 'error'}*",
        f"Redis detail: `{redis_detail}`",
        f"Job queue: *{'ok' if context.application.job_queue else 'missing'}*",
        f"Scheduled jobs: *{job_count if job_count >= 0 else 'unknown'}*",
        f"BOT\\_TOKEN: *{_env_status('BOT_TOKEN')}*",
        f"Redis URL/token: *{_env_status('UPSTASH_REDIS_REST_URL')}/{_env_status('UPSTASH_REDIS_REST_TOKEN')}*",
        f"Groq: *{'ready' if groq_client else 'missing key'}*",
        f"Serper: *{'ready' if SERPER_API_KEY else 'missing key'}*",
        f"Timezone: *{_escape_md(TIMEZONE.zone)}*",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /cancel (standalone — only fires when not inside a conversation) ──────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Friendly reply when /cancel is typed outside of any active conversation."""
    await update.message.reply_text(
        "ℹ️ No active flow to cancel.\n"
        "Use /help to see all available commands."
    )


async def seen_user_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or user.is_bot:
        return
    # Only write to Redis when the user hasn't been seen within the last hour.
    cache_key = (chat.id, user.id)
    now = _time.monotonic()
    if now - _SEEN_CACHE.get(cache_key, 0) < _SEEN_CACHE_TTL:
        return
    _SEEN_CACHE[cache_key] = now
    await asyncio.to_thread(track_seen_user, chat.id, user.id, _display_user(user))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram update error", exc_info=context.error)
    message = getattr(update, "effective_message", None)
    if not message:
        return
    try:
        await message.reply_text("❌ Something broke while handling that. The error was logged.")
    except Exception as e:
        logger.warning("Failed to notify user about handler error: %s", e)


# ── Shared conversation timeout ───────────────────────────────────────────────

async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "new_countdown_name" in context.user_data or "new_countdown_date" in context.user_data:
        hint = "Start again with /addcountdown."
    elif "edit_countdown_name" in context.user_data:
        hint = "Start again with /editcountdown."
    elif "decision" in context.user_data:
        hint = "Start again with /choose."
    else:
        hint = "Start again with the relevant command."
    await _delete_tracked(context)
    context.user_data.clear()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⏰ *Timed out!* You took too long to respond.\n{hint}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END