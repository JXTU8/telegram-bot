"""
handlers/general.py
───────────────────
Welcome and discovery commands: /start, /help (paginated), /cancel.

STRUCTURAL CHANGE (refactor):
    These used to sit at the top of the 790-line ``handlers/misc.py`` alongside
    stats, owner tools and background trackers. Grouping by *purpose* means a
    change to the help menu no longer requires opening a file full of Redis
    queries and ban logic. The help pages are plain data at the top of this
    module so adding a command is a one-line edit.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


# ── /start ────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track countdowns, run luck checks, host ships and more "
        "— all from one group chat.\n\n"
        "➕ /addcountdown — add a new countdown\n"
        "📋 /listcountdown — see all active countdowns\n"
        "🎲 /choose — let me decide for you\n"
        "🍀 /luck — check your daily luck\n"
        "🏆 /leaderboard — combined group leaderboard\n"
        "📋 /recap — today's group summary\n\n"
        "💡 Use /help to browse all available commands by category.",
        parse_mode="Markdown",
    )


# ── /help — page text + keyboard are data, not control flow ──────────────────

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
        "/decide opt1, opt2, opt3 — instant pick"
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
        "/truth — random truth question\n"
        "/dare — random dare question\n"
        "/wouldyourather — random would you rather\n"
        "/coinflip — heads or tails\n"
        "/curse @user — daily curse\n"
        "/bless @user — daily blessing"
    ),
    "summary": (
        "📊 *Summary*\n\n"
        "/leaderboard — combined luck and ship leaderboard\n"
        "/recap — today's full group activity summary\n"
        "/stats — group activity overview\n"
        "/profile — your bot profile in this chat"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/log — latest release notes\n"
        "/log all — every released version\n"
        "/log 1.0.1 — notes for one version\n"
        "/cancel — cancel an active setup flow\n"
        "/help — show this menu"
    ),
}

_HELP_PAGE_ORDER = [
    "countdown", "decisions", "ai", "luck", "fun", "summary", "other",
]
_HELP_PAGE_LABELS = {
    "countdown": "⏱ Countdown",
    "decisions": "🎲 Decisions",
    "ai":        "🤖 AI",
    "luck":      "🍀 Luck",
    "fun":       "🎉 Fun",
    "summary":   "📊 Summary",
    "other":     "⚙️ Other",
}


def _help_keyboard(current_page: str) -> InlineKeyboardMarkup:
    """Two buttons per row, with the active page marked."""
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
        # Tapping the already-active page produces "message not modified".
        logger.debug("help_callback edit skipped (message unchanged): %s", e)


# ── /cancel (standalone, outside a conversation) ─────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ No active flow to cancel.\n"
        "Use /help to see all available commands."
    )
