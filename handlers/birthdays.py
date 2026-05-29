"""
handlers/birthdays.py
─────────────────────
/birthday, /addbirthday, /deletebirthday, and the daily birthday check job.
"""

import asyncio
import calendar
import logging
import random
import re
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from constants import BIRTHDAY_MESSAGES, _MONTH_NAMES
from stores.birthday_store import save_birthday, get_all_birthdays, get_all_birthday_chats, delete_birthday
from helpers import _display_user, _arg_text, _mentioned_target, _escape_md, _today, _is_chat_admin

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_until_birthday(day: int, month: int) -> int:
    today = _today()
    this_year = _birthday_date_for_year(today.year, day, month)
    if this_year < today:
        next_bday = _birthday_date_for_year(today.year + 1, day, month)
    else:
        next_bday = this_year
    return (next_bday - today).days


def _birthday_date_for_year(year: int, day: int, month: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        if month == 2 and day == 29:
            return date(year, 2, 28)
        raise


def _is_birthday_today(day: int, month: int) -> bool:
    today = _today()
    if day == today.day and month == today.month:
        return True
    return (
        day == 29
        and month == 2
        and today.day == 28
        and today.month == 2
        and not calendar.isleap(today.year)
    )


async def _parse_and_save_birthday(
    update: Update, chat_id: int, user, text: str
) -> bool:
    """
    Parse a DD/MM string, validate it, save to store, and reply.
    Returns True on success, False on validation failure (already replied).
    """
    match = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})", text.strip())
    if not match:
        await update.message.reply_text(
            "⚠️ Use the format `DD/MM`.\n_(e.g. `/birthday 25/12` for December 25)_",
            parse_mode="Markdown",
        )
        return False

    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12):
        await update.message.reply_text("⚠️ Invalid month. Must be between 1 and 12.")
        return False
    max_day = calendar.monthrange(2000, month)[1]
    if not (1 <= day <= max_day):
        await update.message.reply_text(f"⚠️ Invalid day for month {_MONTH_NAMES[month]}.")
        return False

    name = _display_user(user)
    await asyncio.to_thread(save_birthday, chat_id, user.id, name, day, month)
    days_left = _days_until_birthday(day, month)
    tag = "🎉 That's today!" if days_left == 0 else f"in {days_left} days"
    await update.message.reply_text(
        f"🎂 *Birthday saved!*\n"
        f"*{_escape_md(name)}* — {day:02d} {_MONTH_NAMES[month]}  _{tag}_\n\n"
        f"Use `/birthday` to see everyone's upcoming birthdays.",
        parse_mode="Markdown",
    )
    return True


# ── /birthday ─────────────────────────────────────────────────────────────────

async def birthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /birthday            — show upcoming birthdays in this chat
    /birthday list       — same as above
    Use /addbirthday DD/MM to set your own birthday.
    """
    chat_id = update.effective_chat.id
    text = _arg_text(context)

    # If the user passed any argument that isn't "list", they probably meant
    # /addbirthday. Redirect them so the two commands stay distinct.
    if text and text.strip().lower() != "list":
        await update.message.reply_text(
            "💡 To *set* your birthday use `/addbirthday DD/MM`\n"
            "_(e.g. `/addbirthday 25/12` for December 25)_\n\n"
            "Use `/birthday` with no arguments to see upcoming birthdays.",
            parse_mode="Markdown",
        )
        return

    bdays = await asyncio.to_thread(get_all_birthdays, chat_id)
    if not bdays:
        await update.message.reply_text(
            "🎂 No birthdays saved yet!\n"
            "Use `/addbirthday DD/MM` to register yours.\n"
            "_(e.g. `/addbirthday 25/12` for December 25)_",
            parse_mode="Markdown",
        )
        return
    entries = []
    for uid_str, info in bdays.items():
        d, m = info.get("day", 1), info.get("month", 1)
        name = info.get("name", uid_str)
        try:
            days_left = _days_until_birthday(d, m)
        except ValueError as e:
            logger.warning("Skipping corrupt birthday in chat %s user %s: %s", chat_id, uid_str, e)
            continue
        entries.append((days_left, d, m, name))
    if not entries:
        await update.message.reply_text(
            "⚠️ No valid birthdays found. Please re-add birthdays with `/addbirthday DD/MM`.",
            parse_mode="Markdown",
        )
        return
    entries.sort()
    lines = ["🎂 *Upcoming Birthdays*\n"]
    for days_left, d, m, name in entries:
        if days_left == 0:
            tag = "🎉 Today!"
        elif days_left == 1:
            tag = "Tomorrow!"
        else:
            tag = f"in {days_left} days"
        lines.append(f"• *{_escape_md(name)}* — {d:02d} {_MONTH_NAMES[m]}  _{tag}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /addbirthday ──────────────────────────────────────────────────────────────

async def addbirthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = _arg_text(context)

    if not text:
        await update.message.reply_text(
            "🎂 *Set your birthday*\n\n"
            "Usage: `/addbirthday DD/MM`\n"
            "_(e.g. `/addbirthday 25/12` for December 25)_",
            parse_mode="Markdown",
        )
        return

    await _parse_and_save_birthday(update, chat_id, user, text)


# ── /deletebirthday ───────────────────────────────────────────────────────────

async def deletebirthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    target_mention = _mentioned_target(update, context)

    if target_mention:
        is_admin = await _is_chat_admin(update, context)

        if not is_admin:
            await update.message.reply_text("⚠️ Only admins can delete someone else's birthday.")
            return

        target_uid = None
        for entity in (update.message.entities or []):
            if entity.type == "text_mention" and entity.user:
                target_uid = entity.user.id
                break

        if not target_uid:
            await update.message.reply_text(
                "⚠️ Please mention the user directly (they must be a group member with a visible account)."
            )
            return

        deleted = await asyncio.to_thread(delete_birthday, chat_id, target_uid)
        if deleted:
            await update.message.reply_text(
                f"🗑️ Birthday for *{_escape_md(target_mention)}* has been removed.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ No birthday found for *{_escape_md(target_mention)}*.",
                parse_mode="Markdown",
            )
    else:
        deleted = await asyncio.to_thread(delete_birthday, chat_id, user.id)
        if deleted:
            await update.message.reply_text(
                "🗑️ Your birthday has been removed.\n"
                "Use `/addbirthday DD/MM` to set it again.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "⚠️ You don't have a birthday saved here yet.\n"
                "Use `/addbirthday DD/MM` to set one.",
                parse_mode="Markdown",
            )


# ── Daily birthday check job ──────────────────────────────────────────────────

async def birthday_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs daily at 00:01 MYT. Sends birthday greetings to each chat."""
    all_chats = await asyncio.to_thread(get_all_birthday_chats)
    send_sem = asyncio.Semaphore(8)
    tasks = []

    async def _send_birthday(_chat_id: int, _uid_str: str, _name: str, _msg: str) -> None:
        async with send_sem:
            try:
                await context.bot.send_message(chat_id=_chat_id, text=_msg)
                logger.info("Sent birthday message to chat %s for %s", _chat_id, _name)
            except Exception as e:
                logger.warning("Birthday message failed for chat %s user %s: %s", _chat_id, _uid_str, e)

    for chat_id, bdays in all_chats.items():
        try:
            for uid_str, info in bdays.items():
                d, m = info.get("day", 0), info.get("month", 0)
                if _is_birthday_today(d, m):
                    name = info.get("name", "Someone")
                    try:
                        msg = random.choice(BIRTHDAY_MESSAGES).format(name=name)
                    except Exception as e:
                        logger.warning("Birthday template failed for chat %s user %s: %s", chat_id, uid_str, e)
                        msg = f"🎉 Happy birthday, {name}!"
                    tasks.append(asyncio.create_task(_send_birthday(chat_id, uid_str, name, msg)))
        except Exception as e:
            logger.warning("Birthday check failed for chat %s; continuing: %s", chat_id, e)
    if tasks:
        await asyncio.gather(*tasks)
