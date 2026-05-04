from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime

from config import TOKEN, TIMEZONE
from countdown_manager import (
    set_countdown,
    list_countdowns,
    set_reminder,
    list_reminders
)

# =========================
# HELP COMMAND
# =========================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Commands:\n\n"
        "/setdate <name> <YYYY-MM-DD>\n"
        "→ Set countdown\n\n"
        "/listcountdown\n"
        "→ Show all countdowns\n\n"
        "/setreminder <name> <HH:MM>\n"
        "→ Set daily reminder (GMT+8)\n\n"
        "/help\n"
        "→ Show this help"
    )


# =========================
# SET COUNTDOWN
# =========================
async def setdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = context.args[0]
        date_str = context.args[1]

        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        chat_id = update.effective_chat.id

        set_countdown(chat_id, name, date)

        await update.message.reply_text(
            f"✅ Countdown '{name}' set to {date} (GMT+8)"
        )

    except:
        await update.message.reply_text(
            "Usage: /setdate <name> <YYYY-MM-DD>"
        )


# =========================
# LIST COUNTDOWN
# =========================
async def listcountdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = list_countdowns(chat_id)

    if not data:
        await update.message.reply_text("📭 No active countdowns.")
        return

    today = datetime.now(TIMEZONE).date()

    msg = "📋 Your Countdown(s):\n\n"

    for name, date in data.items():
        days_left = (date - today).days
        msg += f"🔹 {name}: {date} ({days_left} days left)\n"

    await update.message.reply_text(msg)


# =========================
# SET REMINDER (NAMED)
# =========================
async def setreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = context.args[0]
        time_str = context.args[1]

        hour, minute = map(int, time_str.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError

        chat_id = update.effective_chat.id
        set_reminder(chat_id, name, time_str)

        await update.message.reply_text(
            f"🔔 Reminder '{name}' set at {time_str} (GMT+8)"
        )

    except:
        await update.message.reply_text(
            "Usage: /setreminder <name> <HH:MM>\nExample: /setreminder exam 12:00"
        )


# =========================
# DAILY REMINDER CHECKER
# =========================
async def reminder_checker(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE).strftime("%H:%M")

    data = list_reminders()

    for chat_id, user_data in data.items():
        reminders = user_data.get("reminders", {})

        for name, time_str in reminders.items():
            if now == time_str:
                await context.bot.send_message(
                    chat_id,
                    f"🔔 Reminder: '{name}' (GMT+8) - {time_str}"
                )


# =========================
# MAIN BOT SETUP
# =========================
app = ApplicationBuilder().token(TOKEN).build()

# commands
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("setdate", setdate))
app.add_handler(CommandHandler("listcountdown", listcountdown))
app.add_handler(CommandHandler("setreminder", setreminder))

# background job (runs every minute)
app.job_queue.run_repeating(reminder_checker, interval=60, first=0)

print("🤖 Bot is running...")
app.run_polling()