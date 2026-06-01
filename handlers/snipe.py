"""
handlers/snipe.py
─────────────────
/snipe — shows the last 10 plain-text messages the bot saw in this chat,
paginated one message per page with Prev/Next buttons.

Access is restricted to ROASTMAX_ALLOWED_IDS (same env var as /roastmax).
Only plain-text messages (no commands, no media) are logged.
Messages older than 2 hours are automatically expired by Redis TTL.
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import TIMEZONE
from handlers.ai import is_roastmax_allowed
from stores.snipe_store import get_snipe_messages

logger = logging.getLogger(__name__)

_SNIPE_MAX_SHOW = 10   # show at most the last 10 messages


def _format_snipe_page(messages: list, index: int) -> tuple:
    """
    Build (text, keyboard) for one snipe page.
    messages is already sorted most-recent-first, sliced to _SNIPE_MAX_SHOW.
    index 0 = most recent, index N-1 = oldest shown.
    """
    total = len(messages)
    if total == 0:
        return "👻 No messages logged yet.", None

    # Clamp index to valid range
    index = max(0, min(index, total - 1))
    msg   = messages[index]

    # Format timestamp in MYT
    try:
        ts       = datetime.fromtimestamp(msg["timestamp"], tz=TIMEZONE)
        time_str = ts.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        time_str = "unknown time"

    name = msg.get("name", "Unknown")
    text = msg.get("text", "")
    if len(text) > 800:
        text = text[:800] + "..."

    page_text = (
        f"🔍 *Snipe Log* — {index + 1} of {total}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 *{name}*\n"
        f"🕐 _{time_str} MYT_\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{text}"
    )

    # ◀ Newer goes to a lower index (more recent), Older ▶ goes to higher index
    prev_btn = (
        InlineKeyboardButton("◀ Newer", callback_data=f"snipe:{index - 1}")
        if index > 0
        else InlineKeyboardButton("─", callback_data="snipe:noop")
    )
    next_btn = (
        InlineKeyboardButton("Older ▶", callback_data=f"snipe:{index + 1}")
        if index < total - 1
        else InlineKeyboardButton("─", callback_data="snipe:noop")
    )
    counter_btn = InlineKeyboardButton(f"{index + 1}/{total}", callback_data="snipe:noop")

    keyboard = InlineKeyboardMarkup([[prev_btn, counter_btn, next_btn]])
    return page_text, keyboard


# ── /snipe command ────────────────────────────────────────────────────────────

async def snipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_roastmax_allowed(user_id):
        await update.message.reply_text("🔒 You don't have access to /snipe.")
        return

    chat_id  = update.effective_chat.id
    all_msgs = await asyncio.to_thread(get_snipe_messages, chat_id)   # most-recent-first
    messages = all_msgs[:_SNIPE_MAX_SHOW]                              # cap at 10

    if not messages:
        await update.message.reply_text(
            "👻 Nothing to snipe yet.\n"
            "The bot needs to see messages first — it only logs plain text sent after it joined."
        )
        return

    text, keyboard = _format_snipe_page(messages, 0)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ── Snipe pagination callback ─────────────────────────────────────────────────

async def snipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "snipe:noop":
        return

    user_id = update.effective_user.id
    if not is_roastmax_allowed(user_id):
        await query.answer("🔒 Access denied.", show_alert=True)
        return

    try:
        _, idx_str = query.data.split(":", 1)
        index = int(idx_str)
    except (ValueError, IndexError):
        return

    chat_id  = query.message.chat_id
    all_msgs = await asyncio.to_thread(get_snipe_messages, chat_id)
    messages = all_msgs[:_SNIPE_MAX_SHOW]

    if not messages:
        await query.edit_message_text("👻 Snipe log has expired or is empty.")
        return

    # Clamp silently — should not be needed since buttons are disabled at boundaries
    if index < 0 or index >= len(messages):
        return

    text, keyboard = _format_snipe_page(messages, index)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.debug("snipe_callback edit skipped: %s", e)