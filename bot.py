from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime, time
import pytz

from config import TOKEN, TIMEZONE
from countdown_manager import add_countdown, get_countdown, list_all

# =========================
# HELP COMMAND
# =========================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = """
📌 Available Commands:

/setdate YYYY-MM-DD
→ Set a countdown until that date (Malaysia time)

/listcountdown
→ Show all active countdowns

/help
→ Show this help menu
"""
    await update.message.reply_text(message)


# =========================
# SET COUNTDOWN
# =========================
async def set_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date_str = context.args[0]
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

        chat_id = update.effective_chat.id
        add_countdown(chat_id, target_date)

        await update.message.reply_text(
            f"✅ Countdown set to {target_date} (MYT GMT+8)"
        )

    except:
        await update.message.reply_text(
            "Usage: /setdate YYYY-MM-DD"
        )


# =========================
# LIST COUNTDOWNS
# =========================
async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = list_all()

    if not data:
        await update.message.reply_text("📭 No active countdowns.")
        return

    msg = "📋 Active Countdowns:\n\n"

    today = datetime.now(TIMEZONE).date()

    for chat_id, target_date in data.items():
        days_left = (target_date - today).days
        msg += f"Chat {chat_id}: {target_date} ({days_left} days left)\n"

    await update.message.reply_text(msg)


# =========================
# DAILY REMINDER (12:00 MYT)
# =========================
async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id

    target_date = get_countdown(chat_id)

    if not target_date:
        return

    today = datetime.now(TIMEZONE).date()

    if today > target_date:
        await context.bot.send_message(chat_id, "✅ Countdown finished!")
        return

    days_left = (target_date - today).days

    await context.bot.send_message(
        chat_id,
        f"📅 {days_left} days left until {target_date}"
    )


# =========================
# MAIN SETUP
# =========================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("setdate", set_date))
app.add_handler(CommandHandler("listcountdown", list_countdown))

print("Bot running...")
app.run_polling()