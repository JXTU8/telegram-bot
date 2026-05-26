"""
handlers/misc.py
────────────────
/start, /help (paginated), /stats, and the shared conversation_timeout handler.
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import TIMEZONE
from stores.quote_store import get_quote_count
from stores.user_store import get_seen_users
from stores.ship_store import get_top_ship_pairs, get_shipboard_reset_time
from stores.luck_store import get_fate_board
from helpers import _escape_md, _delete_tracked

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
        "/rank topic: item1, item2, item3 — daily consistent ranking\n"
        "/toss — pick a random person from mentions or the group\n"
        "/poll question: opt1, opt2 — send a native Telegram poll"
    ),
    "ai": (
        "🤖 *AI*\n\n"
        "/ask <question> — search the web + ask AI anything\n"
        "/8ball <question> — magic 8-ball powered by AI\n"
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
        "/roast @user — personalised AI roast\n"
        "/compliment @user — personalised AI compliment\n"
        "/vibecheck — group mood score\n"
        "/mvp — today's most valuable group member\n"
        "/truth — random truth question\n"
        "/dare — random dare\n"
        "/wouldyourather — random would you rather\n"
        "/coinflip — heads or tails\n"
        "/curse @user — fake daily curse\n"
        "/bless @user — fake daily blessing"
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
        "/remind 2h submit the report — supports s/m/h units\n"
        "/cancelremind — view and cancel your pending reminders\n"
        "/remindall 1h group meeting — group-wide reminder (admins only)\n"
        "/birthday — see upcoming birthdays in this chat\n"
        "/addbirthday DD/MM — set your birthday\n"
        "/deletebirthday — delete your birthday\n"
        "/deletebirthday @user — delete someone's birthday (admins only)\n"
        "_Personal reminders survive bot restarts._"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/stats — group activity summary\n"
        "/cancel — cancel the current flow\n"
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

    quote_count, seen_users, top_pairs, board = await asyncio.gather(
        asyncio.to_thread(get_quote_count, chat_id),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_fate_board, chat_id, today_str),
    )
    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)

    member_count = len(seen_users)
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
        f"\n🍀 *Today's Luck*",
        f"Checks: *{luck_count}*",
        f"Luckiest: {lucky_line}",
        f"\n💞 *Ships* _(resets in {reset_h}h {reset_m}m)_",
        f"Top pair: {ship_line}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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