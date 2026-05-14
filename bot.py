"""
bot.py
──────
Group countdown bot — MYT (GMT+8).

Flow to add a countdown:
  /addcountdown → asks name → asks date → asks time → done!
  (30 second timeout at each step — auto cancels if no response)

Flow to make a decision:
  /choose → asks decision → asks options → dramatic reveal!

Ask AI anything:
  /ask <question> → DuckDuckGo search + Groq AI reply

Commands
────────
/start           → Welcome message
/help            → Command reference
/addcountdown    → Add a new named countdown (multi-step)
/listcountdown   → Show all active countdowns in this group
/removecountdown → Remove a countdown by name
/choose          → Let the bot decide for you
/ask             → Ask AI anything
/cancel          → Cancel the current flow
"""

import logging
import os
import random
import asyncio
from datetime import date, datetime

import requests
from groq import Groq
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
# Groq AI setup
# ─────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq AI ready.")
else:
    groq_client = None
    logger.warning("GROQ_API_KEY not set — /ask command will be disabled.")

# ─────────────────────────────────────────────
# Conversation states
# ─────────────────────────────────────────────
ASK_NAME, ASK_DATE, ASK_TIME = range(3)
ASK_DECISION, ASK_OPTIONS    = range(3, 5)

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
    jname = _job_name(chat_id, name)

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
# Timeout handler
# ─────────────────────────────────────────────
async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⏰ *Timed out!* You took too long to respond.\nStart again with /addcountdown or /choose.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track multiple countdowns for your group and remind everyone daily.\n"
        "I can also make decisions and answer questions!\n\n"
        "➕ `/addcountdown` — add a new countdown\n"
        "📋 `/listcountdown` — see all active countdowns\n"
        "🗑️ `/removecountdown` — remove a countdown\n"
        "🎲 `/choose` — let me decide for you\n"
        "🤖 `/ask` — ask me anything\n\n"
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
        "→ Add a new countdown (bot guides you step by step)\n\n"
        "`/listcountdown`\n"
        "→ Show all active countdowns in this group\n\n"
        "`/removecountdown <name>`\n"
        "→ Remove a countdown by name\n\n"
        "`/choose`\n"
        "→ Can't decide? Let the bot pick for you!\n\n"
        "`/ask <question>`\n"
        "→ Ask AI anything\n\n"
        "`/cancel`\n"
        "→ Cancel the current flow\n\n"
        "`/help`\n"
        "→ Show this menu",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# /ask — DuckDuckGo search + Groq AI
# ─────────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")


def _search_web(query: str) -> str:
    """Search Google via Serper and return a short context string."""
    if not SERPER_API_KEY:
        return ""
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": 5},
            timeout=8,
        )
        data = resp.json()
        results = []
        if data.get("answerBox"):
            box = data["answerBox"]
            if box.get("answer"):
                results.append(box["answer"])
            elif box.get("snippet"):
                results.append(box["snippet"])
        if data.get("knowledgeGraph", {}).get("description"):
            results.append(data["knowledgeGraph"]["description"])
        for r in data.get("organic", [])[:3]:
            if r.get("snippet"):
                results.append(f"{r['title']}: {r['snippet']}")
        return "\n".join(results)
    except Exception as e:
        logger.warning("Serper search failed: %s", e)
        return ""


def _call_groq(question: str, search_context: str) -> str:
    """Call Groq with optional search context."""
    system_msg = (
        "You are a helpful assistant. Answer concisely in plain text only. "
        "No markdown formatting, no bullet symbols, no headers. "
        "Use the web search results below if relevant, otherwise use your own knowledge."
    )
    user_msg = question
    if search_context:
        user_msg = f"Web search results for context:\n{search_context}\n\nQuestion: {question}"

    chat = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=1024,
    )
    return chat.choices[0].message.content.strip()


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not groq_client:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up the `GROQ_API_KEY`."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/ask <your question>`\n"
            "_(e.g. `/ask what is tung tung tung sahur?`)_",
            parse_mode="Markdown",
        )
        return

    question = " ".join(context.args)
    thinking_msg = await update.message.reply_text("🤖 Searching and thinking...")

    try:
        # Step 1 — search the web
        search_context = await asyncio.to_thread(_search_web, question)

        # Step 2 — ask Groq with context
        answer = await asyncio.to_thread(_call_groq, question, search_context)

        # Escape HTML special chars
        safe_question = question.replace("<", "&lt;").replace(">", "&gt;")
        safe_answer   = answer.replace("<", "&lt;").replace(">", "&gt;")

        # Split into multiple messages if too long
        max_len = 3800
        header  = f"🤖 <b>Q: {safe_question}</b>\n\n"
        chunks  = [safe_answer[i:i + max_len] for i in range(0, len(safe_answer), max_len)]

        await thinking_msg.edit_text(header + chunks[0], parse_mode="HTML")
        for chunk in chunks[1:]:
            await thinking_msg.reply_text(chunk, parse_mode="HTML")

    except Exception as e:
        logger.error("Ask error: %s", e)
        await thinking_msg.edit_text(
            "❌ Something went wrong with the AI. Please try again later."
        )


# ─────────────────────────────────────────────
# /addcountdown — Step 1: ask for name
# ─────────────────────────────────────────────
async def add_countdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "➕ *New Countdown*\n\n"
        "Step 1/3 — What do you want to call this countdown?\n"
        "_(e.g. Final Exam, Holiday, Birthday)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
    )
    return ASK_NAME


async def received_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            f"⚠️ Name can't be empty. Try again:\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_NAME

    chat_id = update.effective_chat.id
    if countdown_exists(chat_id, name):
        await update.message.reply_text(
            f"⚠️ A countdown named *{name}* already exists.\n"
            f"Please use a different name.\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_NAME

    context.user_data["new_countdown_name"] = name
    await update.message.reply_text(
        f"✅ Name set to *{name}*\n\n"
        "Step 2/3 — What is the target date?\n"
        "Format: `YYYY-MM-DD` _(e.g. 2025-12-31)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
    )
    return ASK_DATE


async def received_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_str = update.message.text.strip()

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use `YYYY-MM-DD` _(e.g. 2025-12-31)_\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DATE

    if target_date < _today():
        await update.message.reply_text(
            f"⚠️ `{target_date}` is in the past. Please choose a future date.\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DATE

    context.user_data["new_countdown_date"] = target_date
    await update.message.reply_text(
        f"✅ Date set to *{target_date}*\n\n"
        "Step 3/3 — What time should the group be reminded daily?\n"
        "Format: `HH:MM` in 24hr MYT _(e.g. 08:30 or 20:00)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
    )
    return ASK_TIME


async def received_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_str = update.message.text.strip()

    try:
        parsed = datetime.strptime(time_str, "%H:%M")
        hour, minute = parsed.hour, parsed.minute
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use `HH:MM` _(e.g. 08:30)_\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_TIME

    chat_id     = update.effective_chat.id
    name        = context.user_data["new_countdown_name"]
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


# ─────────────────────────────────────────────
# /choose
# ─────────────────────────────────────────────
async def choose_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🎲 *Decision Maker*\n\n"
        "What's the issue? Tell me what you need to decide.\n"
        "_(e.g. Should I skip class? What should I eat?)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
    )
    return ASK_DECISION


async def received_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    decision = update.message.text.strip()

    if not decision:
        await update.message.reply_text(
            f"⚠️ Can't be empty. What's the issue?\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DECISION

    context.user_data["decision"] = decision
    await update.message.reply_text(
        f"Got it — *\"{decision}\"*\n\n"
        "Now give me the options, separated by commas.\n"
        "_(e.g. Yes, No, Maybe  or  Pizza, Burger, Sushi)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
    )
    return ASK_OPTIONS


async def received_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw     = update.message.text.strip()
    options = [o.strip() for o in raw.split(",") if o.strip()]

    if len(options) < 2:
        await update.message.reply_text(
            "⚠️ Please give at least *2 options* separated by commas.\n"
            "_(e.g. Yes, No, Maybe)_\n\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_OPTIONS

    decision = context.user_data["decision"]
    chosen   = random.choice(options)
    verdict  = random.choice(VERDICT_LINES)
    thinking = random.choice(THINKING_MESSAGES)

    thinking_msg = await update.message.reply_text(thinking)
    await asyncio.sleep(2)

    await thinking_msg.edit_text(
        f"🎯 *Decision:* _{decision}_\n"
        f"📋 *Options:* {', '.join(options)}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ *The answer is... {chosen}!*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{verdict}_",
        parse_mode="Markdown",
    )

    context.user_data.clear()
    logger.info("Chat %s chose '%s' from %s", update.effective_chat.id, chosen, options)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# /listcountdown
# ─────────────────────────────────────────────
async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
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
        td        = date.fromisoformat(entry["target_date"])
        days_left = (td - today).days
        h         = entry["reminder_hour"]
        m         = entry["reminder_minute"]
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
    entry      = countdowns.get(name)

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
# Restore jobs on startup
# ─────────────────────────────────────────────
async def restore_jobs(app) -> None:
    all_data = get_all_chats()
    count    = 0
    for chat_id, countdowns in all_data.items():
        for name, entry in countdowns.items():
            h = entry.get("reminder_hour", 12)
            m = entry.get("reminder_minute", 0)
            _schedule_reminder(app, chat_id, name, h, m)
            count += 1
    logger.info("Restored %s reminder job(s) from Redis.", count)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
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
            ASK_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_decision)],
            ASK_OPTIONS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)],
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