"""
handlers/reminders.py
─────────────────────
/remind, /cancelremind, /remindall, and restore_remind_jobs on startup.
"""

import asyncio
import logging
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from stores.reminder_store import (
    increment_remind_count, decrement_remind_count,
    save_remind_job, delete_remind_job, try_claim_remind_job,
    get_user_remind_jobs, get_remind_job, get_all_remind_jobs,
)
from helpers import _display_user, _arg_text, _is_chat_admin, _is_owner, _escape_md

logger = logging.getLogger(__name__)

REMIND_MAX_PER_USER = 10

_REMIND_RE = re.compile(
    r"(?:in\s+)?(\d+)\s*(s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:(?:ou)?rs?)?)",
    re.IGNORECASE,
)

_UNIT_MAP = {
    "s": 1, "se": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "mi": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}
_MAX_REMIND_SECONDS = 365 * 24 * 3600


def _parse_remind_seconds(unit_str: str) -> int:
    key = unit_str.lower()
    if key in _UNIT_MAP:
        return _UNIT_MAP[key]
    for k, v in _UNIT_MAP.items():
        if key.startswith(k):
            return v
    return 60


# ── /remind ───────────────────────────────────────────────────────────────────

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "⏰ *Reminder usage:*\n"
            "`/remind 10m take a break`\n"
            "`/remind 30s check on something`\n"
            "`/remind 2h submit the report`\n\n"
            "Supported units: `s` `sec` `seconds` · `m` `min` `minutes` · `h` `hr` `hours`",
            parse_mode="Markdown",
        )
        return

    match = _REMIND_RE.search(text)
    if not match:
        await update.message.reply_text(
            "⚠️ Couldn't parse the time. Try:\n"
            "`/remind 10m take a break`\n`/remind 2h check dinner`\n`/remind 30sec drink water`",
            parse_mode="Markdown",
        )
        return

    amount = int(match.group(1))
    unit_str = match.group(2)
    per_unit = _parse_remind_seconds(unit_str)
    seconds = amount * per_unit

    if per_unit == 1:
        label = f"{amount} second{'s' if amount != 1 else ''}"
    elif per_unit == 60:
        label = f"{amount} minute{'s' if amount != 1 else ''}"
    else:
        label = f"{amount} hour{'s' if amount != 1 else ''}"

    if seconds < 5:
        await update.message.reply_text("⚠️ Minimum reminder time is 5 seconds.")
        return
    if seconds > _MAX_REMIND_SECONDS:
        await update.message.reply_text("⚠️ Maximum reminder time is 1 year.")
        return

    user_id = update.effective_user.id

    # Increment atomically first, then check — prevents concurrent bypass
    new_count = await asyncio.to_thread(increment_remind_count, user_id)
    if new_count > REMIND_MAX_PER_USER:
        await asyncio.to_thread(decrement_remind_count, user_id)
        await update.message.reply_text(
            f"⚠️ You already have {REMIND_MAX_PER_USER} pending reminders. "
            "Wait for some to fire or use /cancelremind to cancel one."
        )
        return

    reminder_text = re.sub(r"^to\b\s*", "", text[match.end():].strip())
    if not reminder_text:
        reminder_text = "You asked me to remind you of something!"

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_mention = user.mention_html() if user else "Hey"

    fire_at = time.time() + seconds
    job_id = await asyncio.to_thread(
        save_remind_job, chat_id, user_id, user_mention, reminder_text, fire_at
    )
    if not job_id:
        await asyncio.to_thread(decrement_remind_count, user_id)
        await update.message.reply_text("❌ Couldn't save that reminder. Please try again in a moment.")
        return

    async def _fire(
        ctx: ContextTypes.DEFAULT_TYPE,
        _cid=chat_id, _jid=job_id, _uid=user_id,
        _mention=user_mention, _text=reminder_text,
    ) -> None:
        # Atomically claim this job — prevents double-fire on rapid restarts
        claimed = await asyncio.to_thread(try_claim_remind_job, _jid)
        if not claimed:
            logger.info("Remind job %s already claimed by another scheduler, skipping.", _jid)
            return
        # Verify the job is still in Redis (user may have cancelled it).
        # At fire time, fire_at is usually <= now, so pending-only lookups skip it.
        existing = await asyncio.to_thread(get_remind_job, _cid, _uid, _jid)
        if not existing:
            logger.info("Remind job %s was cancelled, skipping fire.", _jid)
            return
        await ctx.bot.send_message(
            chat_id=_cid,
            text=f"⏰ Reminder for {_mention}!\n{_text}",
            parse_mode="HTML",
        )
        await asyncio.to_thread(delete_remind_job, _cid, _jid)
        await asyncio.to_thread(decrement_remind_count, _uid)

    context.application.job_queue.run_once(_fire, when=seconds, chat_id=chat_id)
    await update.message.reply_text(
        f"⏰ Got it! I'll remind you in *{label}*.\nUse /cancelremind to cancel it.",
        parse_mode="Markdown",
    )


# ── /cancelremind ─────────────────────────────────────────────────────────────

async def cancelremind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    jobs = await asyncio.to_thread(get_user_remind_jobs, chat_id, user_id)
    if not jobs:
        await update.message.reply_text("✅ You have no pending reminders in this chat.")
        return
    now = time.time()
    lines = ["⏰ *Your Pending Reminders:*\n"]
    buttons = []
    for i, job in enumerate(jobs, 1):
        remaining = max(0, job.get("fire_at", now) - now)
        if remaining < 60:
            time_str = f"{int(remaining)}s"
        elif remaining < 3600:
            time_str = f"{int(remaining / 60)}m"
        else:
            time_str = f"{remaining / 3600:.1f}h"
        preview = _escape_md((job.get("text") or "")[:40])
        lines.append(f"{i}. _{preview}_ — fires in {time_str}")
        buttons.append([InlineKeyboardButton(
            f"❌ Cancel #{i}",
            callback_data=f"cancelremind:{chat_id}:{job['job_id']}"
        )])
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cancelremind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, chat_id_str, job_id = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return
    user_id = update.effective_user.id
    jobs = await asyncio.to_thread(get_user_remind_jobs, chat_id, user_id)
    if not any(j.get("job_id") == job_id for j in jobs):
        await query.edit_message_text("⚠️ Reminder not found or already fired.")
        return
    await asyncio.to_thread(delete_remind_job, chat_id, job_id)
    await asyncio.to_thread(decrement_remind_count, user_id)
    remaining_jobs = await asyncio.to_thread(get_user_remind_jobs, chat_id, user_id)
    if not remaining_jobs:
        await query.edit_message_text("✅ Reminder cancelled. You have no more pending reminders.")
        return
    now = time.time()
    lines = ["⏰ *Your Pending Reminders:*\n"]
    buttons = []
    for i, job in enumerate(remaining_jobs, 1):
        secs_left = max(0, job.get("fire_at", now) - now)
        time_str = (f"{int(secs_left)}s" if secs_left < 60
                    else f"{int(secs_left / 60)}m" if secs_left < 3600
                    else f"{secs_left / 3600:.1f}h")
        preview = _escape_md((job.get("text") or "")[:40])
        lines.append(f"{i}. _{preview}_ — fires in {time_str}")
        buttons.append([InlineKeyboardButton(
            f"❌ Cancel #{i}",
            callback_data=f"cancelremind:{chat_id}:{job['job_id']}"
        )])
    try:
        await query.edit_message_text(
            "✅ Cancelled.\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        pass


# ── /remindall ────────────────────────────────────────────────────────────────

async def remindall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_admin = await _is_chat_admin(update, context)
    user = update.effective_user
    if not is_admin and not _is_owner(user):
        await update.message.reply_text("⚠️ Only group admins can use /remindall.")
        return

    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "⏰ *Group Reminder usage:*\n"
            "`/remindall 10m take a break`\n"
            "`/remindall 2h submit the report`",
            parse_mode="Markdown",
        )
        return

    match = _REMIND_RE.search(text)
    if not match:
        await update.message.reply_text(
            "⚠️ Couldn't parse the time. Example: `/remindall 1h meeting`",
            parse_mode="Markdown",
        )
        return

    amount = int(match.group(1))
    per_unit = _parse_remind_seconds(match.group(2))
    seconds = amount * per_unit

    if per_unit == 1:
        label = f"{amount} second{'s' if amount != 1 else ''}"
    elif per_unit == 60:
        label = f"{amount} minute{'s' if amount != 1 else ''}"
    else:
        label = f"{amount} hour{'s' if amount != 1 else ''}"

    if seconds < 5:
        await update.message.reply_text("⚠️ Minimum reminder time is 5 seconds.")
        return
    if seconds > _MAX_REMIND_SECONDS:
        await update.message.reply_text("⚠️ Maximum reminder time is 1 year.")
        return

    reminder_text = re.sub(r"^to\b\s*", "", text[match.end():].strip())
    if not reminder_text:
        reminder_text = "Group reminder!"

    chat_id = update.effective_chat.id
    set_by = _escape_md(_display_user(user))
    reminder_text_safe = _escape_md(reminder_text)

    async def _fire_group(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"📢 *Group Reminder* (set by {set_by})\n\n{reminder_text_safe}",
            parse_mode="Markdown",
        )

    context.application.job_queue.run_once(_fire_group, when=seconds, chat_id=chat_id)
    await update.message.reply_text(
        f"📢 Group reminder set! I'll remind everyone in *{label}*.",
        parse_mode="Markdown",
    )


# ── Restore one-shot remind jobs on startup ───────────────────────────────────

async def restore_remind_jobs(app) -> None:
    all_jobs = await asyncio.to_thread(get_all_remind_jobs)
    count = 0
    now = time.time()
    for chat_id, jobs in all_jobs.items():
        for job in jobs:
            delay = max(5.0, job["fire_at"] - now)
            job_id = job["job_id"]
            user_id = job["user_id"]
            mention = job["user_mention_html"]
            text = job["text"]

            async def _fire(
                ctx,
                _cid=chat_id, _jid=job_id, _uid=user_id,
                _mention=mention, _text=text,
            ):
                # Claim the job atomically — if another scheduler already claimed
                # it (e.g. previous restart's closure still running), skip.
                claimed = await asyncio.to_thread(try_claim_remind_job, _jid)
                if not claimed:
                    logger.info("Restored remind job %s already claimed, skipping.", _jid)
                    return
                existing = await asyncio.to_thread(get_remind_job, _cid, _uid, _jid)
                if not existing:
                    logger.info("Restored remind job %s was cancelled, skipping.", _jid)
                    return
                await ctx.bot.send_message(
                    chat_id=_cid,
                    text=f"⏰ Reminder for {_mention}!\n{_text}",
                    parse_mode="HTML",
                )
                await asyncio.to_thread(delete_remind_job, _cid, _jid)
                await asyncio.to_thread(decrement_remind_count, _uid)

            app.job_queue.run_once(_fire, when=delay, chat_id=chat_id)
            count += 1
    logger.info("Restored %s one-shot remind job(s) from Redis.", count)
