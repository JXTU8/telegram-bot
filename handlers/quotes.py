"""
handlers/quotes.py
──────────────────
/quote, /quotes (paginated), /deletequote.
"""

import asyncio
import logging
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from stores.quote_store import save_quote, get_all_quotes, get_quote_count, delete_quote
from helpers import _display_user, _is_chat_admin, _is_owner, _escape_md

logger = logging.getLogger(__name__)


# ── Shared page builder ───────────────────────────────────────────────────────

_QUOTE_TEXT_DISPLAY_MAX = 3000


def _quote_display_text(text: str) -> str:
    text = text or ""
    if len(text) <= _QUOTE_TEXT_DISPLAY_MAX:
        return text
    return text[:_QUOTE_TEXT_DISPLAY_MAX].rstrip() + "..."


def _build_quote_page(chat_id: int, quotes: list, index: int):
    """Return (text, keyboard) for a quote page."""
    total = len(quotes)
    if total == 0:
        return "⚠️ No quotes found.", None
    index = max(0, min(index, total - 1))
    q = quotes[index]
    quote_text = _quote_display_text(q.get("text", ""))
    text = (
        f'💬 *"{_escape_md(quote_text)}"*\n'
        f'— {_escape_md(q.get("author", "Unknown"))}\n'
        f'_(saved by {_escape_md(q.get("saved_by", "Unknown"))}) · #{index + 1}/{total}_'
    )
    prev_idx = (index - 1) % total
    next_idx = (index + 1) % total
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀ Prev", callback_data=f"quote:{chat_id}:{prev_idx}"),
        InlineKeyboardButton(f"{index + 1}/{total}", callback_data="quote:noop"),
        InlineKeyboardButton("Next ▶", callback_data=f"quote:{chat_id}:{next_idx}"),
    ]])
    return text, keyboard


# ── /quote ────────────────────────────────────────────────────────────────────

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    replied = message.reply_to_message
    saved_by = _display_user(update.effective_user)

    # No reply — show a random quote from the archive instead of a help message
    if not replied:
        quotes = await asyncio.to_thread(get_all_quotes, update.effective_chat.id)
        if not quotes:
            await message.reply_text(
                "💬 No quotes saved yet!\n"
                "Reply to any message with /quote to start the archive.",
            )
            return
        index = random.randrange(len(quotes))
        text, keyboard = _build_quote_page(update.effective_chat.id, quotes, index)
        await message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return

    if not replied.from_user or not replied.text:
        await message.reply_text("⚠️ Only text messages can be quoted. Reply to a text message with /quote.")
        return

    author = _display_user(replied.from_user)
    text = replied.text

    if not text:
        await message.reply_text("The quote can't be empty!")
        return

    count = await asyncio.to_thread(save_quote, update.effective_chat.id, author, text, saved_by)
    if count == -1:
        await message.reply_text("⚠️ That quote is already in the archive!")
        return
    await message.reply_text(
        f'💬 Saved!\n*"{_escape_md(_quote_display_text(text))}"* — {_escape_md(author)}\n_#{count} in this chat_',
        parse_mode="Markdown",
    )


# ── /quotes ───────────────────────────────────────────────────────────────────

async def quotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    quotes = await asyncio.to_thread(get_all_quotes, chat_id)
    if not quotes:
        await update.message.reply_text(
            "⚠️ No quotes saved yet. Reply to any message with /quote to start the archive!"
        )
        return
    index = random.randrange(len(quotes))
    text, keyboard = _build_quote_page(chat_id, quotes, index)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def quotes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "quote:noop":
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, chat_id_str, idx_str = parts
    try:
        chat_id = int(chat_id_str)
        index = int(idx_str)
    except ValueError:
        return
    quotes = await asyncio.to_thread(get_all_quotes, chat_id)
    if not quotes:
        await query.edit_message_text("⚠️ No quotes found.")
        return
    if index >= len(quotes):
        index = len(quotes) - 1
    elif index < 0:
        index = 0
    text, keyboard = _build_quote_page(chat_id, quotes, index)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.debug("quotes_callback edit skipped (message unchanged): %s", e)


# ── /deletequote ──────────────────────────────────────────────────────────────

async def deletequote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user

    if not context.args or not context.args[0].isdigit():
        total = await asyncio.to_thread(get_quote_count, chat_id)
        await update.message.reply_text(
            f"Usage: `/deletequote <number>`\n"
            f"There are currently *{total}* quote(s) saved.\n"
            "Use /quotes to browse them.\n_(Group admins only)_",
            parse_mode="Markdown",
        )
        return

    is_admin = await _is_chat_admin(update, context)
    if not is_admin and not _is_owner(user):
        await update.message.reply_text("⚠️ Only group admins can delete quotes.")
        return

    index = int(context.args[0])
    success, msg = await asyncio.to_thread(delete_quote, chat_id, index)
    await update.message.reply_text(msg)
