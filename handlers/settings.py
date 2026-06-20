"""
handlers/settings.py
--------------------
/settings menu for choosing where reminder messages are posted.
"""

import asyncio
import math

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from helpers import _is_chat_admin, _is_owner
from stores.settings_store import (
    get_reminder_destinations,
    get_selected_reminder_destination,
    set_selected_reminder_destination,
)

_PAGE_SIZE = 6


def _destination_id(destination: dict) -> str:
    thread_id = destination.get("thread_id")
    return f"{int(destination['chat_id'])}:{thread_id if thread_id is not None else 'main'}"


def _settings_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    destinations = get_reminder_destinations()
    selected = get_selected_reminder_destination()
    selected_id = _destination_id(selected) if selected else ""

    if not destinations:
        return (
            "Settings\n\nNo chats or topics have been seen yet. Use a command in the target chat first.",
            InlineKeyboardMarkup([]),
        )

    total_pages = max(1, math.ceil(len(destinations) / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    shown = destinations[start:start + _PAGE_SIZE]

    rows = []
    for destination in shown:
        did = _destination_id(destination)
        mark = "[selected] " if did == selected_id else ""
        label = str(destination.get("label") or destination["chat_id"])
        rows.append([
            InlineKeyboardButton(
                f"{mark}{label}"[:64],
                callback_data=f"settings:set:{did}:{page}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Previous", callback_data=f"settings:page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="settings:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next", callback_data=f"settings:page:{page + 1}"))
    rows.append(nav)

    selected_label = selected.get("label") if selected else "current command chat/topic"
    text = f"Settings\n\nReminder destination: {selected_label}"
    return text, InlineKeyboardMarkup(rows)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_owner(user) and not await _is_chat_admin(update, context):
        await update.message.reply_text("Only group admins can change settings.")
        return

    text, keyboard = await asyncio.to_thread(_settings_page, 0)
    await update.message.reply_text(text, reply_markup=keyboard)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    user = update.effective_user
    if not _is_owner(user):
        try:
            chat = query.message.chat if query.message else update.effective_chat
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status not in ("administrator", "creator"):
                await query.answer("Only group admins can change settings.", show_alert=True)
                return
        except Exception:
            await query.answer("Only group admins can change settings.", show_alert=True)
            return

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    page = 0

    if action == "noop":
        return
    if action == "page" and len(parts) >= 3:
        page = int(parts[2])
    elif action == "set" and len(parts) >= 5:
        chat_id = int(parts[2])
        thread_id = None if parts[3] == "main" else int(parts[3])
        page = int(parts[4])
        await asyncio.to_thread(set_selected_reminder_destination, chat_id, thread_id)

    text, keyboard = await asyncio.to_thread(_settings_page, page)
    await query.edit_message_text(text, reply_markup=keyboard)
