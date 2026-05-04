"""
bot.py
──────
Group countdown bot — MYT (GMT+8).

Flow to add a countdown:
  /addcountdown → asks name → asks date → asks time → done!

Commands
────────
/start           → Welcome message
/help            → Command reference
/addcountdown    → Add a new named countdown (multi-step)
/listcountdown   → Show all active countdowns in this group
/removecountdown → Remove a countdown by name
/cancel          → Cancel the current /addcountdown flow
"""

import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TOKEN, TIMEZONE
from countdown_manager import (
    add_countdown,
    get_all_countdowns,
    get_all_chats,
    remove_countdown,
    countdown_exists,
)
from keep_alive import keep_alive

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Conversation states
# ─────────────────────────────────────────────
ASK_NAME, ASK_DATE, ASK_TIME = range(3)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _days_label(days: int) -> str:
    if days == 0:
        return "🎉 Today is the day!"
    if days < 0:
        return f"⏰ {abs(days)} day(s) overdue"
    return f"📅 {days} day(s) remaining"


def _job_name(chat_id: int, name: str) -> str:
    return f"{chat_id}::{name}"


def _schedule_reminder(app, chat_id: int, name: str, hour: int, minute: int) -> None:
    """Schedule a daily reminder job for a specific countdown."""
    jname = _job_name(chat_id, name)

    # Remove existing job for this countdown if any
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
        data={"countdown_name": name},
    )
    logger.info(
        "Scheduled reminder for chat %s countdown '%s' at %02d:%02d MYT",
        chat_id, name, hour, minute,
    )


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track multiple countdowns for your group and remind everyone daily.\n\n"
        "Get started:\n"
        "➕ `/addcountdown` — add a new countdown\n"
        "📋 `/listcountdown` — see all active countdowns\n"
        "🗑️ `/removecountdown` — remove a countdown\n\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📌 *Available Commands*\n\n"
        "`/addcountdown`\n"
        "→ Add a new countdown (bot will guide you step by step)\n\n"
        "`/listcountdown`\n"
        "→ Show all active countdowns in this group\n\n"
        "`/removecountdown <name>`\n"
        "→ Remove a countdown by name\n\n"
        "`/cancel`\n"
        "→ Cancel adding a countdown\n\n"
        "`/help`\n"
        "→ Show this menu",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# /addcountdown — Step 1: ask for name
# ─────────────────────────────────────────────
async def add_countdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "➕ *New Countdown*\n\n"
        "Step 1/3 — What do you want to call this countdown?\n"
        "_(e.g. Final Exam, Holiday, Birthday)_\n\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
    )
    return ASK_NAME


# Step 2: receive name, ask for date
async def received_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("⚠️ Name can't be empty. Try again:")
        return ASK_NAME

    chat_id = update.effective_chat.id
    if countdown_exists(chat_id, name):
        await update.message.reply_text(
            f"⚠️ A countdown named *{name}* already exists.\n"
            "Please use a different name or remove the existing one first.",
            parse_mode="Markdown",
        )
        return ASK_NAME

    context.user_data["new_countdown_name"] = name

    await update.message.reply_text(
        f"✅ Name set to *{name}*\n\n"
        "Step 2/3 — What is the target date?\n"
        "Format: `YYYY-MM-DD` _(e.g. 2025-12-31)_",
        parse_mode="Markdown",
    )
    return ASK_DATE


# Step 3: receive date, ask for time
async def received_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_str = update.message.text.strip()

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use `YYYY-MM-DD` _(e.g. 2025-12-31)_\nTry again:",
            parse_mode="Markdown",
        )
        return ASK_DATE

    if target_date < _today():
        await update.message.reply_text(
            f"⚠️ `{target_date}` is in the past. Please choose a future date:",
            parse_mode="Markdown",
        )
        return ASK_DATE

    context.user_data["new_countdown_date"] = target_date

    await update.message.reply_text(
        f"✅ Date set to *{target_date}*\n\n"
        "Step 3/3 — What time should the group be reminded daily?\n"
        "Format: `HH:MM` in 24hr MYT _(e.g. 08:30 or 20:00)_",
        parse_mode="Markdown",
    )
    return ASK_TIME


# Final step: receive time, save everything
async def received_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()

    try:
        parsed = datetime.strptime(time_str, "%H:%M")
        hour, minute = parsed.hour, parsed.minute
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use `HH:MM` _(e.g. 08:30)_\nTry again:",
            parse_mode="Markdown",
        )
        return ASK_TIME

    chat_id   = update.effective_chat.id
    name      = context.user_data["new_countdown_name"]
    target_date = context.user_data["new_countdown_date"]
    created_by  = update.effective_user.id

    add_countdown(chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)

    today     = _today()
    days_left = (target_date - today).days

    await update.message.reply_text(
        f"🎉 *Countdown Added!*\n\n"
        f"📛 Name: *{name}*\n"
        f"📆 Target: *{target_date}*\n"
        f"{_days_label(days_left)}\n"
        f"🔔 Daily reminder at *{hour:02d}:{minute:02d} MYT*",
        parse_mode="Markdown",
    )

    context.user_data.clear()
    logger.info("Chat %s added countdown '%s' → %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


# Cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. No countdown was added.")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# /listcountdown
# ─────────────────────────────────────────────
async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    countdowns = get_all_countdowns(chat_id)

    if not countdowns:
        await update.message.reply_text(
            "📭 No active countdowns.\nUse `/addcountdown` to add one!",
            parse_mode="Markdown",
        )
        return

    today = _today()
    lines = ["📋 *Active Countdowns:*\n"]
    for name, entry in countdowns.items():
        td = date.fromisoformat(entry["target_date"])
        days_left = (td - today).days
        h = entry["reminder_hour"]
        m = entry["reminder_minute"]
        lines.append(
            f"• *{name}*\n"
            f"  📆 {td}  |  {_days_label(days_left)}\n"
            f"  🔔 Reminder at {h:02d}:{m:02d} MYT\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
# /removecountdown <name>
# ─────────────────────────────────────────────
async def remove_countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide the countdown name.\n"
            "Usage: `/removecountdown <name>`\n"
            "_(Use /listcountdown to see all names)_",
            parse_mode="Markdown",
        )
        return

    name    = " ".join(context.args)
    chat_id = update.effective_chat.id
    removed = remove_countdown(chat_id, name)

    if removed:
        # Cancel its reminder job
        jname = _job_name(chat_id, name)
        for job in context.job_queue.get_jobs_by_name(jname):
            job.schedule_removal()
        await update.message.reply_text(f"🗑️ Countdown *{name}* has been removed.", parse_mode="Markdown")
        logger.info("Chat %s removed countdown '%s'", chat_id, name)
    else:
        await update.message.reply_text(
            f"❌ No countdown named *{name}* found.\n"
            "Use /listcountdown to see all active countdowns.",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────
# Daily reminder job
# ─────────────────────────────────────────────
async def daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job     = context.job
    chat_id = job.chat_id
    name    = job.data["countdown_name"]

    countdowns = get_all_countdowns(chat_id)
    entry = countdowns.get(name)

    if not entry:
        job.schedule_removal()
        return

    today     = _today()
    td        = date.fromisoformat(entry["target_date"])
    days_left = (td - today).days

    if days_left < 0:
        await context.bot.send_message(
            chat_id,
            f"✅ *{name}* countdown has passed! ({td})\n"
            "Use `/addcountdown` to add a new one.",
            parse_mode="Markdown",
        )
        remove_countdown(chat_id, name)
        job.schedule_removal()
        return

    await context.bot.send_message(
        chat_id,
        f"⏰ *{name}*\n"
        f"📆 Target: *{td}*\n"
        f"{_days_label(days_left)}",
        parse_mode="Markdown",
    )
    logger.info("Sent reminder for '%s' to chat %s — %s days left", name, chat_id, days_left)


# ─────────────────────────────────────────────
# Restore jobs on startup (in case bot restarted)
# ─────────────────────────────────────────────
async def restore_jobs(app) -> None:
    all_data = get_all_chats()
    count = 0
    for chat_id_str, countdowns in all_data.items():
        chat_id = int(chat_id_str)
        for name, entry in countdowns.items():
            h = entry.get("reminder_hour", 12)
            m = entry.get("reminder_minute", 0)
            _schedule_reminder(app, chat_id, name, h, m)
            count += 1
    logger.info("Restored %s reminder job(s) from disk.", count)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main() -> None:
    keep_alive()

    app = ApplicationBuilder().token(TOKEN).post_init(restore_jobs).build()

    # Conversation handler for /addcountdown
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addcountdown", add_countdown_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("listcountdown", list_countdown))
    app.add_handler(CommandHandler("removecountdown", remove_countdown_cmd))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()