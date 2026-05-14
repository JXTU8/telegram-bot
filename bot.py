"""
bot.py
------
Group countdown bot - MYT (GMT+8).

Commands
--------
/start
/help
/addcountdown
/listcountdown
/removecountdown
/choose
/ask
/cancel
"""

import asyncio
import html
import logging
import os
import random
from datetime import date, datetime

import requests
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
    countdown_exists,
    get_all_chats,
    get_all_countdowns,
    remove_countdown,
)
from keep_alive import keep_alive


logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-lite"

if GEMINI_API_KEY:
    logger.info("Gemini AI ready.")
else:
    logger.warning("GEMINI_API_KEY not set - /ask command will be disabled.")


ASK_NAME, ASK_DATE, ASK_TIME = range(3)
ASK_DECISION, ASK_OPTIONS = range(3, 5)

CONV_TIMEOUT = 30

THINKING_MESSAGES = [
    "🎲 Rolling the dice...",
    "🔮 Consulting the crystal ball...",
    "🌀 Spinning the wheel...",
    "🤔 Thinking really hard...",
    "⚡ Calculating your fate...",
    "🎯 Taking aim...",
    "🃏 Drawing a card...",
    "🌟 Reading the stars...",
]

VERDICT_LINES = [
    "The universe has spoken.",
    "No take backs!",
    "Trust the process.",
    "Destiny has decided.",
    "It is what it is.",
    "The stars don't lie.",
    "You asked, I answered.",
    "Don't blame me, blame fate.",
    "Final answer. No debates.",
    "Science has confirmed it.",
]


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _html(text: object) -> str:
    return html.escape(str(text))


def _days_label(days: int) -> str:
    if days == 0:
        return "🎉 Today is the day!"
    if days < 0:
        return f"⏰ {abs(days)} day(s) overdue"
    return f"📅 {days} day(s) remaining"


def _job_name(chat_id: int, name: str) -> str:
    return f"{chat_id}::{name}"


def _schedule_reminder(app, chat_id: int, name: str, hour: int, minute: int) -> None:
    if not app.job_queue:
        raise RuntimeError(
            "Job queue is missing. Use python-telegram-bot[job-queue] in requirements.txt."
        )

    job_name = _job_name(chat_id, name)

    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    reminder_time = datetime.now(TIMEZONE).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    ).timetz()

    app.job_queue.run_daily(
        daily_reminder,
        time=reminder_time,
        chat_id=chat_id,
        name=job_name,
        data={"countdown_name": name},
    )

    logger.info(
        "Scheduled reminder for chat %s countdown '%s' at %02d:%02d MYT",
        chat_id,
        name,
        hour,
        minute,
    )


def _ask_gemini(question: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Answer concisely in plain text only. "
                            "No markdown formatting, no bullet symbols, no headers. "
                            "Use Google Search when the question needs current information.\n\n"
                            f"Question: {question}"
                        )
                    }
                ]
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ],
    }

    response = requests.post(url, headers=headers, json=payload, timeout=45)
    response.raise_for_status()

    data = response.json()
    candidates = data.get("candidates", [])

    if not candidates:
        return "I couldn't generate an answer for that."

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = [part.get("text", "") for part in parts if part.get("text")]

    answer = "\n".join(text_parts).strip()
    return answer or "I couldn't generate an answer for that."


async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()

    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "⏰ <b>Timed out!</b> You took too long to respond.\n"
                "Start again with /addcountdown or /choose."
            ),
            parse_mode="HTML",
        )

    return ConversationHandler.END


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Welcome to Countdown Bot!</b>\n\n"
        "I track multiple countdowns for your group and remind everyone daily.\n"
        "I can also make decisions and answer questions with Google Search.\n\n"
        "➕ /addcountdown - add a new countdown\n"
        "📋 /listcountdown - see all active countdowns\n"
        "🗑️ /removecountdown - remove a countdown\n"
        "🎲 /choose - let me decide for you\n"
        "🤖 /ask - ask me anything\n\n"
        "Type /help for all commands.",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📌 <b>Available Commands</b>\n\n"
        "/addcountdown\n"
        "Add a new countdown step by step.\n\n"
        "/listcountdown\n"
        "Show all active countdowns in this group.\n\n"
        "/removecountdown &lt;name&gt;\n"
        "Remove a countdown by name.\n\n"
        "/choose\n"
        "Can't decide? Let the bot pick for you.\n\n"
        "/ask &lt;question&gt;\n"
        "Ask Gemini AI anything.\n\n"
        "/cancel\n"
        "Cancel the current flow.\n\n"
        "/help\n"
        "Show this menu.",
        parse_mode="HTML",
    )


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GEMINI_API_KEY:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up GEMINI_API_KEY."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /ask <your question>\n"
            "Example: /ask latest Malaysia news today"
        )
        return

    question = " ".join(context.args)
    thinking_msg = await update.message.reply_text("🤖 Thinking...")

    try:
        answer = await asyncio.to_thread(_ask_gemini, question)

        safe_question = _html(question)
        safe_answer = _html(answer)

        max_len = 3800
        header = f"🤖 <b>Q: {safe_question}</b>\n\n"
        chunks = [
            safe_answer[i:i + max_len]
            for i in range(0, len(safe_answer), max_len)
        ]

        await thinking_msg.edit_text(header + chunks[0], parse_mode="HTML")

        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode="HTML")

    except requests.HTTPError as e:
        logger.error("Gemini HTTP error: %s | %s", e, e.response.text)
        await thinking_msg.edit_text(
            "❌ Gemini returned an error. Check your model name, API key, or quota."
        )
    except Exception as e:
        logger.error("Gemini error: %s", e)
        await thinking_msg.edit_text(
            "❌ Something went wrong with the AI. Please try again later."
        )


async def add_countdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "➕ <b>New Countdown</b>\n\n"
        "Step 1/3 - What do you want to call this countdown?\n"
        "<i>Example: Final Exam, Holiday, Birthday</i>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.\n"
        "Type /cancel to stop.",
        parse_mode="HTML",
    )
    return ASK_NAME


async def received_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "⚠️ Name can't be empty. Try again:\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_NAME

    chat_id = update.effective_chat.id

    if countdown_exists(chat_id, name):
        await update.message.reply_text(
            f"⚠️ A countdown named <b>{_html(name)}</b> already exists.\n"
            "Please use a different name.\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_NAME

    context.user_data["new_countdown_name"] = name

    await update.message.reply_text(
        f"✅ Name set to <b>{_html(name)}</b>\n\n"
        "Step 2/3 - What is the target date?\n"
        "Format: <code>YYYY-MM-DD</code>\n"
        "Example: <code>2026-12-31</code>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    return ASK_DATE


async def received_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_str = update.message.text.strip()

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use <code>YYYY-MM-DD</code>\n"
            "Example: <code>2026-12-31</code>\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_DATE

    if target_date < _today():
        await update.message.reply_text(
            f"⚠️ <code>{target_date}</code> is in the past. "
            "Please choose today or a future date.\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_DATE

    context.user_data["new_countdown_date"] = target_date

    await update.message.reply_text(
        f"✅ Date set to <b>{target_date}</b>\n\n"
        "Step 3/3 - What time should the group be reminded daily?\n"
        "Format: <code>HH:MM</code> in 24hr MYT\n"
        "Example: <code>08:30</code> or <code>20:00</code>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    return ASK_TIME


async def received_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()

    try:
        parsed = datetime.strptime(time_str, "%H:%M")
        hour, minute = parsed.hour, parsed.minute
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use <code>HH:MM</code>\n"
            "Example: <code>08:30</code>\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_TIME

    chat_id = update.effective_chat.id
    name = context.user_data["new_countdown_name"]
    target_date = context.user_data["new_countdown_date"]
    created_by = update.effective_user.id

    add_countdown(chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)

    days_left = (target_date - _today()).days

    await update.message.reply_text(
        "🎉 <b>Countdown Added!</b>\n\n"
        f"📛 Name: <b>{_html(name)}</b>\n"
        f"📆 Target: <b>{target_date}</b>\n"
        f"{_days_label(days_left)}\n"
        f"🔔 Daily reminder at <b>{hour:02d}:{minute:02d} MYT</b>",
        parse_mode="HTML",
    )

    context.user_data.clear()

    logger.info(
        "Chat %s added countdown '%s' -> %s at %02d:%02d",
        chat_id,
        name,
        target_date,
        hour,
        minute,
    )

    return ConversationHandler.END


async def choose_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🎲 <b>Decision Maker</b>\n\n"
        "What's the issue? Tell me what you need to decide.\n"
        "<i>Example: Should I skip class? What should I eat?</i>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.\n"
        "Type /cancel to stop.",
        parse_mode="HTML",
    )
    return ASK_DECISION


async def received_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    decision = update.message.text.strip()

    if not decision:
        await update.message.reply_text(
            "⚠️ Can't be empty. What's the issue?\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_DECISION

    context.user_data["decision"] = decision

    await update.message.reply_text(
        f"Got it - <b>{_html(decision)}</b>\n\n"
        "Now give me the options, separated by commas.\n"
        "<i>Example: Yes, No, Maybe or Pizza, Burger, Sushi</i>\n\n"
        f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
        parse_mode="HTML",
    )
    return ASK_OPTIONS


async def received_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    options = [option.strip() for option in raw.split(",") if option.strip()]

    if len(options) < 2:
        await update.message.reply_text(
            "⚠️ Please give at least <b>2 options</b> separated by commas.\n"
            "<i>Example: Yes, No, Maybe</i>\n\n"
            f"⏰ You have <b>{CONV_TIMEOUT} seconds</b> to reply.",
            parse_mode="HTML",
        )
        return ASK_OPTIONS

    decision = context.user_data["decision"]
    chosen = random.choice(options)
    verdict = random.choice(VERDICT_LINES)
    thinking = random.choice(THINKING_MESSAGES)

    thinking_msg = await update.message.reply_text(thinking)
    await asyncio.sleep(2)

    escaped_options = ", ".join(_html(option) for option in options)

    await thinking_msg.edit_text(
        f"🎯 <b>Decision:</b> <i>{_html(decision)}</i>\n"
        f"📋 <b>Options:</b> {escaped_options}\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"✅ <b>The answer is... {_html(chosen)}!</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"<i>{_html(verdict)}</i>",
        parse_mode="HTML",
    )

    context.user_data.clear()

    logger.info(
        "Chat %s chose '%s' from %s",
        update.effective_chat.id,
        chosen,
        options,
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    countdowns = get_all_countdowns(chat_id)

    if not countdowns:
        await update.message.reply_text(
            "📭 No active countdowns.\nUse /addcountdown to add one!"
        )
        return

    today = _today()
    lines = ["📋 <b>Active Countdowns:</b>\n"]

    for name, entry in countdowns.items():
        target_date = date.fromisoformat(entry["target_date"])
        days_left = (target_date - today).days
        hour = entry["reminder_hour"]
        minute = entry["reminder_minute"]

        lines.append(
            f"• <b>{_html(name)}</b>\n"
            f"  📆 {target_date} | {_days_label(days_left)}\n"
            f"  🔔 Reminder at {hour:02d}:{minute:02d} MYT\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def remove_countdown_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide the countdown name.\n"
            "Usage: /removecountdown <name>\n"
            "Use /listcountdown to see all names."
        )
        return

    name = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    removed = remove_countdown(chat_id, name)

    if removed:
        job_name = _job_name(chat_id, name)

        if context.job_queue:
            for job in context.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()

        await update.message.reply_text(
            f"🗑️ Countdown <b>{_html(name)}</b> has been removed.",
            parse_mode="HTML",
        )

        logger.info("Chat %s removed countdown '%s'", chat_id, name)
        return

    await update.message.reply_text(
        f"❌ No countdown named <b>{_html(name)}</b> found.\n"
        "Use /listcountdown to see all active countdowns.",
        parse_mode="HTML",
    )


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    chat_id = job.chat_id
    name = job.data["countdown_name"]

    countdowns = get_all_countdowns(chat_id)
    entry = countdowns.get(name)

    if not entry:
        job.schedule_removal()
        return

    today = _today()
    target_date = date.fromisoformat(entry["target_date"])
    days_left = (target_date - today).days

    if days_left < 0:
        await context.bot.send_message(
            chat_id,
            f"✅ <b>{_html(name)}</b> countdown has passed! ({target_date})\n"
            "Use /addcountdown to add a new one.",
            parse_mode="HTML",
        )

        remove_countdown(chat_id, name)
        job.schedule_removal()
        return

    await context.bot.send_message(
        chat_id,
        f"⏰ <b>{_html(name)}</b>\n"
        f"📆 Target: <b>{target_date}</b>\n"
        f"{_days_label(days_left)}",
        parse_mode="HTML",
    )

    logger.info(
        "Sent reminder for '%s' to chat %s - %s days left",
        name,
        chat_id,
        days_left,
    )


async def restore_jobs(app) -> None:
    all_data = get_all_chats()
    count = 0

    for chat_id, countdowns in all_data.items():
        for name, entry in countdowns.items():
            hour = entry.get("reminder_hour", 12)
            minute = entry.get("reminder_minute", 0)
            _schedule_reminder(app, int(chat_id), name, hour, minute)
            count += 1

    logger.info("Restored %s reminder job(s).", count)


def main() -> None:
    keep_alive()

    app = ApplicationBuilder().token(TOKEN).post_init(restore_jobs).build()

    countdown_conv = ConversationHandler(
        entry_points=[CommandHandler("addcountdown", add_countdown_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, conversation_timeout)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    choose_conv = ConversationHandler(
        entry_points=[CommandHandler("choose", choose_start)],
        states={
            ASK_DECISION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_decision)
            ],
            ASK_OPTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, conversation_timeout)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(countdown_conv)
    app.add_handler(choose_conv)
    app.add_handler(CommandHandler("listcountdown", list_countdown))
    app.add_handler(CommandHandler("removecountdown", remove_countdown_cmd))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
