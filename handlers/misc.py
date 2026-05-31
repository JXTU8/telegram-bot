"""
handlers/misc.py
────────────────
/start, /help (paginated), /stats, /recap, /leaderboard, /profile,
/status, /cancel, /ban, /unban, /banlist, and background tracking helpers.
"""

import asyncio
import logging
import os
import time as _time
from collections import OrderedDict
from datetime import datetime, date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import TIMEZONE, VERSION
from db import redis
from constants import _MONTH_NAMES
from stores.ban_store import ban_user, unban_user, get_banned_users
from stores.birthday_store import get_all_birthdays
from stores.countdown_store import get_all_countdowns
from stores.luck_store import get_fate_board, get_fate_streak
from stores.mvp_store import get_user_mvp_stats, get_today_mvp, get_mvp_board
from stores.quote_store import get_quote_count, get_user_quote_counts
from stores.reminder_store import get_user_remind_jobs
from stores.ship_store import get_top_ship_pairs, get_shipboard_reset_time
from stores.user_store import get_seen_users, track_seen_user
from helpers import (
    _display_user, _escape_md, _delete_tracked,
    _display_name_or_id, owner_only, _days_label,
    _is_owner, BOT_OWNER_ID,
)

from handlers.luck import _get_fate

logger = logging.getLogger(__name__)

# ── Seen-user in-memory cache ─────────────────────────────────────────────────
_SEEN_CACHE: OrderedDict = OrderedDict()
_SEEN_CACHE_TTL = 3600
_SEEN_CACHE_MAX = 10_000

# ── /start ────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track countdowns, run luck checks, set reminders, host ships and more "
        "— all from one group chat.\n\n"
        "➕ /addcountdown — add a new countdown\n"
        "📋 /listcountdown — see all active countdowns\n"
        "🎲 /choose — let me decide for you\n"
        "🍀 /luck — check your daily luck\n"
        "⏰ /remind — set a personal reminder\n"
        "🎂 /addbirthday — set your birthday\n"
        "🏆 /leaderboard — combined group leaderboard\n"
        "📋 /recap — today's group summary\n\n"
        "💡 Use /help to browse *all 40+ commands* by category.",
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
        "/game — number guessing game (1–100)\n"
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
        "/quote — show a random saved quote\n"
        "/quote (reply) — save the replied message to the archive\n"
        "/quotes — browse saved quotes with prev/next\n"
        "/deletequote <number> — delete a quote (admins only)"
    ),
    "reminders": (
        "⏰ *Reminders*\n\n"
        "/remind 10m take a break — set a personal reminder\n"
        "/remind tmr 3pm meeting — natural language reminder (AI)\n"
        "/cancelremind — view and cancel your pending reminders\n"
        "/remindall 1h group meeting — group-wide reminder (admins only)\n"
        "/birthday — see upcoming birthdays in this chat\n"
        "/addbirthday DD/MM — set your birthday\n"
        "/deletebirthday — delete your birthday\n"
        "/deletebirthday @user — delete someone's birthday (admins only)\n"
    ),
    "summary": (
        "📊 *Summary*\n\n"
        "/leaderboard — combined luck, ship and MVP leaderboard\n"
        "/recap — today's full group activity summary\n"
        "/stats — group activity overview\n"
        "/profile — your bot profile in this chat"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/cancel — cancel an active setup flow\n"
        "/help — show this menu"
    ),
}

_HELP_PAGE_ORDER  = ["countdown", "decisions", "ai", "luck", "fun", "quotes", "reminders", "summary", "other"]
_HELP_PAGE_LABELS = {
    "countdown": "⏱ Countdown",
    "decisions": "🎲 Decisions",
    "ai":        "🤖 AI",
    "luck":      "🍀 Luck",
    "fun":       "🎉 Fun",
    "quotes":    "💬 Quotes",
    "reminders": "⏰ Reminders",
    "summary":   "📊 Summary",
    "other":     "⚙️ Other",
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


# ── /leaderboard ──────────────────────────────────────────────────────────────

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id   = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    luck_board, pairs, today_mvp, mvp_board = await asyncio.gather(
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 3),
        asyncio.to_thread(get_today_mvp, chat_id, today_str),
        asyncio.to_thread(get_mvp_board, chat_id, 3),
        return_exceptions=True,
    )
    for val in (luck_board, pairs, today_mvp, mvp_board):
        if isinstance(val, Exception):
            logger.error("leaderboard_command gather error: %s", val)

    medals = ["🥇", "🥈", "🥉"]
    lines  = [f"🏆 *Group Leaderboard* — {today_str}\n"]

    lines.append("🍀 *Today's Luck*")
    board = luck_board if isinstance(luck_board, dict) else {}
    if board:
        sorted_board = sorted(board.items(), key=lambda kv: kv[1]["score"], reverse=True)
        for i, (_, item) in enumerate(sorted_board[:3], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{medal} *{_escape_md(item['name'])}* — {item['tier']} `{item['score']}`")
    else:
        lines.append("_No checks yet today. Use /luck!_")

    lines.append("\n💞 *Top Ships*")
    ship_list = pairs if isinstance(pairs, list) else []
    if ship_list:
        for i, pair in enumerate(ship_list, 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            a = _escape_md(pair["label_a"])
            b = _escape_md(pair["label_b"])
            lines.append(f"{medal} *{a}* × *{b}* `{pair['score']:.1f}%`")
    else:
        lines.append("_No ships yet. Use /ship!_")

    lines.append("\n🏆 *MVP*")
    mvp = today_mvp if isinstance(today_mvp, dict) else {}
    if mvp:
        lines.append(f"Today: *{_escape_md(mvp.get('name', '?'))}*")
    else:
        lines.append("_No MVP yet today. Use /mvp!_")

    board_rows = mvp_board if isinstance(mvp_board, list) else []
    if board_rows:
        lines.append("_All-time:_")
        for i, row in enumerate(board_rows, 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            name  = _display_name_or_id(row.get("name", ""), row.get("user_id", "?"))
            wins  = int(row.get("wins", 0))
            lines.append(f"{medal} *{_escape_md(name)}* — `{wins}` win{'s' if wins != 1 else ''}")

    lines.append("\n_Use /luckboard · /shipboard · /mvpboard for full lists_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /recap ────────────────────────────────────────────────────────────────────

async def recap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id   = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    (
        luck_board, pairs, today_mvp, quote_count, seen,
        countdowns, all_bdays,
    ) = await asyncio.gather(
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_today_mvp, chat_id, today_str),
        asyncio.to_thread(get_quote_count, chat_id),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_all_countdowns, chat_id),
        asyncio.to_thread(get_all_birthdays, chat_id),
        return_exceptions=True,
    )

    board        = luck_board  if isinstance(luck_board,  dict) else {}
    ship_list    = pairs       if isinstance(pairs,        list) else []
    mvp          = today_mvp   if isinstance(today_mvp,    dict) else {}
    total_quotes = quote_count if isinstance(quote_count,  int)  else 0
    seen_users   = seen        if isinstance(seen,          dict) else {}
    cd_dict      = countdowns  if isinstance(countdowns,   dict) else {}
    bday_dict    = all_bdays   if isinstance(all_bdays,    dict) else {}

    lines = [f"📋 *Daily Recap — {today_str}*\n"]

    if mvp:
        lines.append(f"🏆 *MVP:* {_escape_md(mvp.get('name', '?'))}")
    else:
        lines.append("🏆 *MVP:* _Not crowned yet — use /mvp_")

    if board:
        sorted_board = sorted(board.items(), key=lambda kv: kv[1]["score"], reverse=True)
        luckiest    = sorted_board[0][1]
        unluckiest  = sorted_board[-1][1]
        active      = len(board)
        lines.append(
            f"🍀 *Luckiest:* {_escape_md(luckiest['name'])} — {luckiest['tier']} `{luckiest['score']}`"
        )
        if active > 1:
            lines.append(
                f"💀 *Unluckiest:* {_escape_md(unluckiest['name'])} — {unluckiest['tier']} `{unluckiest['score']}`"
            )
        lines.append(f"👥 *Luck checks today:* {active}")
    else:
        lines.append("🍀 *Luck:* _Nobody checked yet — use /luck_")

    if ship_list:
        p = ship_list[0]
        a = _escape_md(p["label_a"])
        b = _escape_md(p["label_b"])
        lines.append(f"💞 *Top Ship:* {a} × {b} `{p['score']:.1f}%`")
    else:
        lines.append("💞 *Ships:* _None yet — use /ship_")

    today_date      = datetime.now(TIMEZONE).date()
    next_countdown  = None
    min_days        = float("inf")
    for cd_name, entry in cd_dict.items():
        try:
            td        = date.fromisoformat(entry["target_date"])
            days_left = (td - today_date).days
            if 0 <= days_left < min_days:
                min_days       = days_left
                next_countdown = (cd_name, days_left)
        except (ValueError, KeyError):
            pass

    if next_countdown:
        cd_name, days_left = next_countdown
        lines.append(f"⏱ *Next Countdown:* {_escape_md(cd_name)} — _{_days_label(days_left)}_")
    else:
        lines.append("⏱ *Countdowns:* _None active — use /addcountdown_")

    upcoming_bdays = []
    for uid_str, bday in bday_dict.items():
        try:
            d, m = int(bday.get("day", 0)), int(bday.get("month", 0))
            if not d or not m:
                continue
            bday_this_year = date(today_date.year, m, d)
            if bday_this_year < today_date:
                bday_this_year = date(today_date.year + 1, m, d)
            days_until = (bday_this_year - today_date).days
            if 0 <= days_until <= 7:
                upcoming_bdays.append((days_until, bday.get("name", uid_str)))
        except ValueError:
            pass
    upcoming_bdays.sort()

    if upcoming_bdays:
        parts = []
        for days_until, bname in upcoming_bdays[:3]:
            tag = "Today! 🎂" if days_until == 0 else f"in {days_until}d"
            parts.append(f"{_escape_md(bname)} ({tag})")
        lines.append(f"🎂 *Birthdays this week:* {', '.join(parts)}")
    else:
        lines.append("🎂 *Birthdays:* _None this week_")

    lines.append(f"💬 *Quotes saved:* {total_quotes} total")
    lines.append(f"👤 *Members tracked:* {len(seen_users)}")
    lines.append("\n_Run /leaderboard for the full rankings_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /stats ────────────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id   = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    results = await asyncio.gather(
        asyncio.to_thread(get_quote_count, chat_id),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_all_countdowns, chat_id),
        return_exceptions=True,
    )
    quote_count  = results[0] if not isinstance(results[0], Exception) else 0
    seen_users   = results[1] if not isinstance(results[1], Exception) else {}
    top_pairs    = results[2] if not isinstance(results[2], Exception) else []
    board        = results[3] if not isinstance(results[3], Exception) else {}
    countdowns   = results[4] if not isinstance(results[4], Exception) else {}
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error("stats_command gather[%s] failed: %s", i, r)

    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)

    member_count    = len(seen_users)
    countdown_count = len(countdowns)
    reset_h = reset_secs // 3600
    reset_m = (reset_secs % 3600) // 60

    if top_pairs:
        p         = top_pairs[0]
        ship_line = f"{_escape_md(p['label_a'])} × {_escape_md(p['label_b'])} `{p['score']:.1f}%`"
    else:
        ship_line = "No ships yet this cycle"

    luck_count = len(board)
    if board:
        top_uid    = max(board, key=lambda k: board[k]["score"])
        t          = board[top_uid]
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


# ── /profile ──────────────────────────────────────────────────────────────────

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
    name    = _display_user(target_user)
    today   = datetime.now(TIMEZONE).date()

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
    streak_line  = f"{streak_count} day(s) {streak_cat}" if streak_count else "No active streak"
    authored_quotes, saved_quotes = quote_counts
    mvp_wins  = int(mvp_stats.get("wins", 0)) if mvp_stats else 0
    last_mvp  = mvp_stats.get("last_won", "Never") if mvp_stats else "Never"
    seen      = await asyncio.to_thread(get_seen_users, chat_id)
    seen_name = _display_name_or_id(seen.get(str(user_id), name), user_id)

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


# ── /status ───────────────────────────────────────────────────────────────────

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

    redis_detail = _escape_md(redis_msg[:120].replace("`", "").replace("\n", " "))
    banned_count = len(await asyncio.to_thread(get_banned_users))

    lines = [
        "🧪 *Bot Status*",
        f"Version: *{VERSION}*",
        f"Redis: *{'ok' if redis_ok else 'error'}*",
        f"Redis detail: `{redis_detail}`",
        f"Job queue: *{'ok' if context.application.job_queue else 'missing'}*",
        f"Scheduled jobs: *{job_count if job_count >= 0 else 'unknown'}*",
        f"Banned users: *{banned_count}*",
        f"BOT\\_TOKEN: *{_env_status('BOT_TOKEN')}*",
        f"Redis URL/token: *{_env_status('UPSTASH_REDIS_REST_URL')}/{_env_status('UPSTASH_REDIS_REST_TOKEN')}*",
        f"Groq: *{'ready' if groq_client else 'missing key'}*",
        f"Serper: *{'ready' if SERPER_API_KEY else 'missing key'}*",
        f"Timezone: *{_escape_md(TIMEZONE.zone)}*",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /cancel (standalone) ──────────────────────────────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ No active flow to cancel.\n"
        "Use /help to see all available commands."
    )


# ── /ban, /unban, /banlist (owner only) ───────────────────────────────────────

def _resolve_ban_target(update: Update) -> tuple:
    """
    Resolve a ban target from the command message.
    Priority:
      1. text_mention entity (tap from Telegram's mention picker) — has a real user ID
      2. Reply to any message — use the sender's ID
      3. Bare numeric ID passed as the first argument
    Returns (user_id: int | None, display: str).
    """
    message = update.message

    # 1. text_mention — most reliable, always has the user ID
    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            u = entity.user
            return u.id, _display_user(u)

    # 2. Reply to a message
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, _display_user(u)

    # 3. Bare numeric ID as first argument
    args = (message.text or "").split()[1:]
    if args and args[0].lstrip("-").isdigit():
        uid = int(args[0])
        return uid, str(uid)

    return None, ""


@owner_only
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ban — block a user from using any bot command or interaction.
    Usage:
      /ban <user_id>           — by numeric ID
      /ban (reply to message)  — ban whoever sent that message
      /ban @mention            — use Telegram's mention picker (not a typed @username)
    """
    user_id, display = _resolve_ban_target(update)
    if user_id is None:
        await update.message.reply_text(
            "Usage:\n"
            "• `/ban <user_id>` — e.g. `/ban 123456789`\n"
            "• Reply to any of their messages + `/ban`\n"
            "• `/ban` with a mention from Telegram's picker\n\n"
            "_Typed @usernames won't work — use the picker or a reply._",
            parse_mode="Markdown",
        )
        return

    # Never allow banning the owner
    if user_id == BOT_OWNER_ID or _is_owner(
        type("_U", (), {"id": user_id, "username": "", "is_bot": False})()
    ):
        await update.message.reply_text("⚠️ Cannot ban the bot owner.")
        return

    newly_banned = await asyncio.to_thread(ban_user, user_id)
    name_safe = _escape_md(display)
    if newly_banned:
        await update.message.reply_text(
            f"🔨 *{name_safe}* (`{user_id}`) has been banned from using this bot.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ *{name_safe}* (`{user_id}`) is already banned.",
            parse_mode="Markdown",
        )


@owner_only
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /unban — restore bot access for a previously banned user.
    Same targeting options as /ban.
    """
    user_id, display = _resolve_ban_target(update)
    if user_id is None:
        await update.message.reply_text(
            "Usage:\n"
            "• `/unban <user_id>`\n"
            "• Reply to any of their messages + `/unban`",
            parse_mode="Markdown",
        )
        return

    was_banned = await asyncio.to_thread(unban_user, user_id)
    name_safe = _escape_md(display)
    if was_banned:
        await update.message.reply_text(
            f"✅ *{name_safe}* (`{user_id}`) has been unbanned.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ *{name_safe}* (`{user_id}`) wasn't banned.",
            parse_mode="Markdown",
        )


@owner_only
async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all currently banned user IDs."""
    banned = await asyncio.to_thread(get_banned_users)
    if not banned:
        await update.message.reply_text("✅ No users are currently banned.")
        return
    ids = "\n".join(f"• `{uid}`" for uid in sorted(banned))
    await update.message.reply_text(
        f"🔨 *Banned users ({len(banned)}):*\n{ids}",
        parse_mode="Markdown",
    )

@owner_only
async def say_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /say <chat_id> <message>
    Sends a message to any chat the bot is in.
    Example: /say -1003861255064 Hello everyone!
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/say <chat_id> <message>`\n"
            "Example: `/say -1003861255064 Hello everyone!`",
            parse_mode="Markdown",
        )
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid chat ID. Must be a number.")
        return

    text = " ".join(context.args[1:])

    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
        await update.message.reply_text(f"✅ Sent to `{chat_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: `{e}`", parse_mode="Markdown")

@owner_only
async def threadid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    thread_id = getattr(update.message, "message_thread_id", None)
    if thread_id:
        await update.message.reply_text(f"🧵 Thread ID: `{thread_id}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ This is the main chat (no thread ID).")
        
# ── Background: seen-user tracking ───────────────────────────────────────────

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
    chat = getattr(update, "effective_chat", None)
    if not chat:
        logger.warning("Conversation timeout fired without an effective chat.")
        return ConversationHandler.END
    await context.bot.send_message(
        chat_id=chat.id,
        text=f"⏰ *Timed out!* You took too long to respond.\n{hint}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END