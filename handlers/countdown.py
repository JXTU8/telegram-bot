"""
handlers/countdown.py
─────────────────────
/addcountdown, /editcountdown, /listcountdown, /removecountdown
Daily reminder job + job restore on startup.
"""

import asyncio
import html as _html
import logging
import threading
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import TIMEZONE, DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE
from stores.countdown_store import (
    add_countdown, get_countdown, get_all_countdowns, remove_countdown,
    countdown_exists, get_countdown_by_code, get_countdown_creator, get_all_chats,
)
from stores.user_store import get_seen_users

from helpers import (
    _today, _days_label, _job_name,
    _track, _delete_tracked, _is_chat_admin,
    _escape_md,
    ASK_NAME, ASK_DATE, ASK_TIME,
    ASK_EDIT_FIELD, ASK_EDIT_VALUE,
    CONV_TIMEOUT,
)

logger = logging.getLogger(__name__)

_MAX_NAME_LENGTH = 50
_reminder_generation_lock = threading.Lock()
_reminder_generations: dict[str, int] = {}


def _h(text: str) -> str:
    """HTML-escape a string for use in parse_mode='HTML' messages."""
    return _html.escape(str(text))


# ── Daily reminder job ────────────────────────────────────────────────────────

async def daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    chat_id = job.chat_id
    name = job.data["countdown_name"]
    generation = job.data.get("generation")
    jname = _job_name(chat_id, name)
    with _reminder_generation_lock:
        if generation != _reminder_generations.get(jname):
            job.schedule_removal()
            return
    countdowns = await asyncio.to_thread(get_all_countdowns, chat_id)
    entry = countdowns.get(name)
    if not entry:
        job.schedule_removal()
        return
    today = _today()
    try:
        td = date.fromisoformat(entry["target_date"])
    except (KeyError, ValueError):
        job.schedule_removal()
        return
    days_left = (td - today).days
    if days_left < 0:
        await context.bot.send_message(
            chat_id,
            f"✅ <b>{_h(name)}</b> countdown has passed! ({td})\nUse /addcountdown to add a new one.",
            parse_mode="HTML",
        )
        await asyncio.to_thread(remove_countdown, chat_id, name)
        job.schedule_removal()
        return
    await context.bot.send_message(
        chat_id,
        f"⏰ <b>{_h(name)}</b>\n📆 Target: <b>{td}</b>\n{_days_label(days_left)}",
        parse_mode="HTML",
    )
    logger.info("Sent reminder for '%s' to chat %s — %s days left", name, chat_id, days_left)


# ── Scheduler helper ──────────────────────────────────────────────────────────

def _schedule_reminder(app, chat_id: int, name: str, hour: int, minute: int) -> None:
    jname = _job_name(chat_id, name)
    with _reminder_generation_lock:
        generation = _reminder_generations.get(jname, 0) + 1
        _reminder_generations[jname] = generation
    for job in app.job_queue.get_jobs_by_name(jname):
        job.schedule_removal()
    reminder_time = datetime.now(TIMEZONE).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).timetz()
    app.job_queue.run_daily(
        daily_reminder,
        time=reminder_time,
        chat_id=chat_id,
        name=jname,
        data={"countdown_name": name, "generation": generation},
    )
    logger.info(
        "Scheduled reminder for chat %s countdown '%s' at %02d:%02d MYT",
        chat_id, name, hour, minute,
    )


# ── Restore jobs on startup ───────────────────────────────────────────────────

async def restore_jobs(app) -> None:
    all_data = await asyncio.to_thread(get_all_chats)
    count = 0
    for chat_id, countdowns in all_data.items():
        for name, entry in countdowns.items():
            h = entry.get("reminder_hour", DEFAULT_REMINDER_HOUR)
            m = entry.get("reminder_minute", DEFAULT_REMINDER_MINUTE)
            _schedule_reminder(app, chat_id, name, h, m)
            count += 1
    logger.info("Restored %s countdown reminder job(s) from Redis.", count)


# ── /addcountdown flow ────────────────────────────────────────────────────────

async def add_countdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bot_msg = await update.message.reply_text(
        "➕ *New Countdown*\n\n"
        "Step 1/3 — What do you want to call this countdown?\n"
        f"_(e.g. Final Exam, Holiday, Birthday)_  ·  max {_MAX_NAME_LENGTH} chars\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
    )
    _track(context, update.message, bot_msg)
    return ASK_NAME


async def received_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(
            f"⚠️ Name can't be empty. Try again:\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_NAME
    if len(name) > _MAX_NAME_LENGTH:
        await update.message.reply_text(
            f"⚠️ Name is too long ({len(name)} chars). "
            f"Please keep it under {_MAX_NAME_LENGTH} characters.\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_NAME
    context.user_data["new_countdown_name"] = name
    bot_msg = await update.message.reply_text(
        f"✅ Name set to <b>{_h(name)}</b>\n\n"
        "Step 2/3 — What is the target date?\n"
        "Format: <code>YYYY-MM-DD</code> (e.g. 2025-12-31)\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    _track(context, update.message, bot_msg)
    return ASK_DATE


async def received_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_str = update.message.text.strip()
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "⚠️ Invalid format. Use `YYYY-MM-DD` _(e.g. 2025-12-31)_\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DATE
    if target_date <= _today():
        await update.message.reply_text(
            f"⚠️ `{target_date}` must be a future date (not today or in the past).\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DATE
    context.user_data["new_countdown_date"] = target_date
    bot_msg = await update.message.reply_text(
        f"✅ Date set to <b>{target_date}</b>\n\n"
        "Step 3/3 — What time should the group be reminded daily?\n"
        f"Format: <code>HH:MM</code> in 24hr MYT (e.g. {DEFAULT_REMINDER_HOUR:02d}:{DEFAULT_REMINDER_MINUTE:02d})\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    _track(context, update.message, bot_msg)
    return ASK_TIME


async def received_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()
    try:
        parsed = datetime.strptime(time_str, "%H:%M")
        hour, minute = parsed.hour, parsed.minute
    except ValueError:
        await update.message.reply_text(
            "⚠️ Invalid format. Use `HH:MM` _(e.g. 08:30)_\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_TIME

    chat_id     = update.effective_chat.id
    name        = context.user_data.get("new_countdown_name")
    target_date = context.user_data.get("new_countdown_date")
    if not name or not target_date:
        logger.error("received_time: missing user_data keys. name=%s date=%s", name, target_date)
        await update.message.reply_text(
            "❌ Something went wrong — please start again with /addcountdown."
        )
        context.user_data.clear()
        return ConversationHandler.END

    created_by = update.effective_user.id
    code = await asyncio.to_thread(add_countdown, chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)
    today     = _today()
    days_left = (target_date - today).days
    _track(context, update.message)
    await _delete_tracked(context)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 <b>Countdown Added!</b>\n\n"
            f"📛 Name: <b>{_h(name)}</b>\n"
            f"🔑 Code: <code>{_h(code)}</code>\n"
            f"📆 Target: <b>{target_date}</b>\n"
            f"{_days_label(days_left)}\n"
            f"🔔 Daily reminder at <b>{hour:02d}:{minute:02d} MYT</b>\n\n"
            f"<i>Edit: /editcountdown {_h(code)}  ·  Remove: /removecountdown {_h(code)}</i>"
        ),
        parse_mode="HTML",
    )
    context.user_data.clear()
    logger.info("Chat %s added countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


# ── /editcountdown flow ───────────────────────────────────────────────────────

async def editcountdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: `/editcountdown <code or name>`\n_(Use /listcountdown to see codes)_",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    arg = " ".join(context.args).strip()
    name = None
    if len(arg) <= 4 and arg.isalnum():
        name = await asyncio.to_thread(get_countdown_by_code, chat_id, arg.lower())
    if name is None:
        name = arg
    if not await asyncio.to_thread(countdown_exists, chat_id, name):
        await update.message.reply_text(
            f"⚠️ No countdown found for `{arg}`.\nUse /listcountdown to see all countdowns.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    creator_id = await asyncio.to_thread(get_countdown_creator, chat_id, name)
    is_admin   = await _is_chat_admin(update, context)
    if not is_admin and (creator_id is None or user_id != creator_id):
        await update.message.reply_text(
            "⚠️ Only the person who created this countdown or a group admin can edit it."
        )
        return ConversationHandler.END
    context.user_data["edit_countdown_name"] = name
    bot_msg = await update.message.reply_text(
        f"✏️ <b>Editing: {_h(name)}</b>\n\n"
        "What do you want to change? Type <code>date</code> or <code>time</code>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.\n"
        "Type /cancel to stop.",
        parse_mode="HTML",
    )
    _track(context, update.message, bot_msg)
    return ASK_EDIT_FIELD


async def received_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field = update.message.text.strip().lower()
    if field not in ("date", "time"):
        await update.message.reply_text(
            "⚠️ Please type `date` or `time`.\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_EDIT_FIELD
    context.user_data["edit_field"] = field
    _track(context, update.message)
    if field == "date":
        prompt = "Enter the new target date:\nFormat: <code>YYYY-MM-DD</code> (e.g. 2025-12-31)"
    else:
        prompt = "Enter the new reminder time:\nFormat: <code>HH:MM</code> in 24hr MYT (e.g. 08:30)"
    bot_msg = await update.message.reply_text(
        f"{prompt}\n\n⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    _track(context, bot_msg)
    return ASK_EDIT_VALUE


async def received_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    name    = context.user_data.get("edit_countdown_name")
    field   = context.user_data.get("edit_field")
    if not name or not field:
        await update.message.reply_text(
            "❌ Something went wrong — please start again with /editcountdown."
        )
        context.user_data.clear()
        return ConversationHandler.END

    value_str = update.message.text.strip()
    entry = await asyncio.to_thread(get_countdown, chat_id, name)
    if not entry:
        await update.message.reply_text("⚠️ Countdown no longer exists.")
        context.user_data.clear()
        return ConversationHandler.END
    if field == "date":
        try:
            new_date = datetime.strptime(value_str, "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid format. Use `YYYY-MM-DD` _(e.g. 2025-12-31)_\n"
                f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
                parse_mode="Markdown",
            )
            return ASK_EDIT_VALUE
        if new_date <= _today():
            await update.message.reply_text(
                f"⚠️ `{new_date}` must be a future date.\n"
                f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
                parse_mode="Markdown",
            )
            return ASK_EDIT_VALUE
        target_date = new_date
        hour   = entry["reminder_hour"]
        minute = entry["reminder_minute"]
    else:
        try:
            parsed = datetime.strptime(value_str, "%H:%M")
            hour, minute = parsed.hour, parsed.minute
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid format. Use `HH:MM` _(e.g. 08:30)_\n"
                f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
                parse_mode="Markdown",
            )
            return ASK_EDIT_VALUE
        target_date = date.fromisoformat(entry["target_date"])
    created_by = entry.get("created_by", update.effective_user.id)
    await asyncio.to_thread(add_countdown, chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)
    _track(context, update.message)
    await _delete_tracked(context)
    days_left = (target_date - _today()).days
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ <b>Countdown Updated — {_h(name)}</b>\n\n"
            f"📆 Target: <b>{target_date}</b>\n"
            f"{_days_label(days_left)}\n"
            f"🔔 Daily reminder at <b>{hour:02d}:{minute:02d} MYT</b>"
        ),
        parse_mode="HTML",
    )
    context.user_data.clear()
    logger.info("Chat %s edited countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _delete_tracked(context)
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ── /listcountdown ────────────────────────────────────────────────────────────

async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
    countdowns = await asyncio.to_thread(get_all_countdowns, chat_id)
    if not countdowns:
        await update.message.reply_text(
            "📭 No active countdowns.\nUse `/addcountdown` to add one!",
            parse_mode="Markdown",
        )
        return
    today = _today()
    seen  = await asyncio.to_thread(get_seen_users, chat_id)

    needs_backfill = [n for n, e in countdowns.items() if not e.get("code")]
    for bname in needs_backfill:
        e = countdowns[bname]
        try:
            td = date.fromisoformat(e["target_date"])
            new_code = await asyncio.to_thread(
                add_countdown, chat_id, bname, td,
                e.get("reminder_hour", DEFAULT_REMINDER_HOUR),
                e.get("reminder_minute", DEFAULT_REMINDER_MINUTE),
                e.get("created_by", 0),
            )
            countdowns[bname]["code"] = new_code
            logger.info(
                "list_countdown: backfilled code '%s' for '%s' in chat %s",
                new_code, bname, chat_id,
            )
        except Exception as ex:
            logger.warning("list_countdown: backfill failed for '%s': %s", bname, ex)

    def _safe_sort_key(kv):
        try:
            return (date.fromisoformat(kv[1]["target_date"]) - today).days
        except (KeyError, ValueError):
            return 9999

    sorted_entries = sorted(countdowns.items(), key=_safe_sort_key)

    def _days_left_safe(entry):
        try:
            return (date.fromisoformat(entry.get("target_date", "")) - today).days
        except ValueError:
            return 0

    stale_names = [name for name, entry in sorted_entries if _days_left_safe(entry) < 0]
    for stale_name in stale_names:
        await asyncio.to_thread(remove_countdown, chat_id, stale_name)
        jname = _job_name(chat_id, stale_name)
        for job in context.job_queue.get_jobs_by_name(jname):
            job.schedule_removal()
        logger.info("list_countdown: auto-removed overdue '%s' from chat %s", stale_name, chat_id)
    sorted_entries = [(n, e) for n, e in sorted_entries if n not in stale_names]

    if not sorted_entries:
        await update.message.reply_text(
            "📭 No active countdowns.\nUse `/addcountdown` to add one!",
            parse_mode="Markdown",
        )
        return

    lines = ["<b>📋 Active Countdowns (soonest first):</b>\n"]
    for name, entry in sorted_entries:
        try:
            td = date.fromisoformat(entry["target_date"])
        except (KeyError, ValueError):
            td = today
        days_left    = (td - today).days
        h            = entry.get("reminder_hour", DEFAULT_REMINDER_HOUR)
        m            = entry.get("reminder_minute", DEFAULT_REMINDER_MINUTE)
        code         = entry.get("code", "—")
        creator_id   = str(entry.get("created_by", ""))
        creator_name = seen.get(creator_id, "Unknown")
        lines.append(
            f"• <b>{_h(name)}</b> <code>[{_h(code)}]</code>\n"
            f"  📆 {td}  |  {_days_label(days_left)}\n"
            f"  🔔 {h:02d}:{m:02d} MYT  |  👤 {_h(creator_name)}\n"
        )
    lines.append("<i>Edit: /editcountdown &lt;code&gt;  ·  Remove: /removecountdown &lt;code&gt;</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /removecountdown ──────────────────────────────────────────────────────────

async def remove_countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide the countdown code or name.\n"
            "Usage: `/removecountdown a3k` or `/removecountdown <name>`\n"
            "_(Use /listcountdown to see codes)_",
            parse_mode="Markdown",
        )
        return
    arg  = " ".join(context.args).strip()
    name = None
    if len(arg) <= 4 and arg.isalnum():
        name = await asyncio.to_thread(get_countdown_by_code, chat_id, arg.lower())
    if name is None:
        name = arg
    creator_id = await asyncio.to_thread(get_countdown_creator, chat_id, name)
    is_admin   = await _is_chat_admin(update, context)
    if not is_admin and (creator_id is None or user_id != creator_id):
        await update.message.reply_text(
            "⚠️ Only the person who created this countdown or a group admin can remove it."
        )
        return
    removed = await asyncio.to_thread(remove_countdown, chat_id, name)
    if removed:
        jname = _job_name(chat_id, name)
        for job in context.job_queue.get_jobs_by_name(jname):
            job.schedule_removal()
        await update.message.reply_text(
            f"🗑️ Countdown <b>{_h(name)}</b> has been removed.",
            parse_mode="HTML",
        )
        logger.info("Chat %s removed countdown '%s'", chat_id, name)
    else:
        await update.message.reply_text(
            f"⚠️ No countdown found for `{arg}`.\nUse /listcountdown to see all active countdowns.",
            parse_mode="Markdown",
        )