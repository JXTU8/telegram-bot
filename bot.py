"""
bot.py
------
Group countdown bot - MYT (GMT+8).

Flow to add a countdown:
  /addcountdown -> asks name -> asks date -> asks time -> done!
  (30 second timeout at each step - auto cancels if no response)

Flow to make a decision:
  /choose -> asks decision -> asks options -> dramatic reveal!

Ask AI anything:
  /ask <question> -> Serper Google search + Groq AI reply

Commands
--------
/start           -> Welcome message
/help            -> Command reference
/addcountdown    -> Add a new named countdown (multi-step)
/listcountdown   -> Show all active countdowns in this group
/removecountdown -> Remove a countdown by name
/choose          -> Let the bot decide for you
/ask             -> Ask AI anything
/fate            -> Check your daily luck
/cancel          -> Cancel the current flow
"""

import logging
import os
import random
import asyncio
from datetime import date, datetime
from time import monotonic

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

# ---------------------------------------------
# Logging
# ---------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------
# Groq AI setup
# ---------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq AI ready.")
else:
    groq_client = None
    logger.warning("GROQ_API_KEY not set - /ask command will be disabled.")

# ---------------------------------------------
# Conversation states
# ---------------------------------------------
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
    "🧠 Running the numbers...",
    "📊 Crunching the odds...",
    "🪄 Summoning a result...",
    "🧭 Letting fate navigate...",
    "🎰 Pulling the lever...",
    "🕯️ Asking the mysterious forces...",
    "🥁 Building suspense...",
    "🧮 Doing very serious math...",
    "🌌 Checking alternate timelines...",
    "📡 Receiving cosmic data...",
    "🎭 Preparing the reveal...",
    "🔍 Inspecting the possibilities...",
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
    "The council has reached a verdict.",
    "The odds have been judged.",
    "Case closed.",
    "The wheel has no regrets.",
    "Probability has spoken.",
    "This is now canon.",
    "The decision has left the chat.",
    "A bold choice. Respectable.",
    "The numbers made me do it.",
    "Fate signed the paperwork.",
    "Certified by absolutely no authority.",
    "The vibes are legally binding.",
    "That is the official unofficial answer.",
    "The timeline accepts this outcome.",
    "A decision has entered the arena.",
]


# ---------------------------------------------
# Helpers
# ---------------------------------------------
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s must be an integer. Using %s.", name, default)
        return default


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


# ---------------------------------------------
# Timeout handler
# ---------------------------------------------
async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⏰ *Timed out!* You took too long to respond.\nStart again with /addcountdown or /choose.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ---------------------------------------------
# /start
# ---------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to Countdown Bot!*\n\n"
        "I track multiple countdowns for your group and remind everyone daily.\n"
        "I can also make decisions and answer questions!\n\n"
        "➕ /addcountdown — add a new countdown\n"
        "📋 /listcountdown — see all active countdowns\n"
        "🗑️ /removecountdown — remove a countdown\n"
        "🎲 /choose — let me decide for you\n"
        "🤖 /ask — ask me anything\n\n"
        "🔮 /fate — check your daily luck\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /help
# ---------------------------------------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📌 *Available Commands*\n\n"
        "/addcountdown\n"
        "→ Add a new countdown (bot guides you step by step)\n\n"
        "/listcountdown\n"
        "→ Show all active countdowns in this group\n\n"
        "/removecountdown <name>\n"
        "→ Remove a countdown by name\n\n"
        "/choose\n"
        "→ Can't decide? Let the bot pick for you!\n\n"
        "/ask <question>\n"
        "→ Ask AI anything\n\n"
        "/fate\n"
        "→ Check your daily luck prediction\n\n"
        "/cancel\n"
        "→ Cancel the current flow\n\n"
        "/help\n"
        "→ Show this menu",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /ask - Serper Google search + Groq AI
# ---------------------------------------------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"
SERPER_HEADERS = {
    "X-API-KEY": SERPER_API_KEY,
    "Content-Type": "application/json",
}
SERPER_SESSION = requests.Session()
SEARCH_CACHE_TTL_SECONDS = max(0, _env_int("SEARCH_CACHE_TTL_SECONDS", 900))
SEARCH_CACHE_MAX_ITEMS = 128
_SEARCH_CACHE = {}


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _get_cached_search(query: str):
    cached = _SEARCH_CACHE.get(_cache_key(query))
    if not cached:
        return None

    cached_at, result = cached
    if monotonic() - cached_at <= SEARCH_CACHE_TTL_SECONDS:
        return result

    _SEARCH_CACHE.pop(_cache_key(query), None)
    return None


def _set_cached_search(query: str, result: str) -> None:
    if len(_SEARCH_CACHE) >= SEARCH_CACHE_MAX_ITEMS:
        oldest_key = min(_SEARCH_CACHE, key=lambda key: _SEARCH_CACHE[key][0])
        _SEARCH_CACHE.pop(oldest_key, None)

    _SEARCH_CACHE[_cache_key(query)] = (monotonic(), result)


def _search_web(query: str) -> str:
    """Search Google via Serper and return a short context string."""
    if not SERPER_API_KEY:
        return ""

    cached = _get_cached_search(query)
    if cached is not None:
        return cached

    try:
        resp = SERPER_SESSION.post(
            SERPER_URL,
            headers=SERPER_HEADERS,
            json={"q": query, "num": 5},
            timeout=(3, 8),
        )
        resp.raise_for_status()
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
                title = r.get("title", "Search result")
                results.append(f"{title}: {r['snippet']}")

        search_context = "\n".join(results)
        _set_cached_search(query, search_context)
        return search_context
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
            {"role": "user", "content": user_msg},
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
        search_context = await asyncio.to_thread(_search_web, question)
        answer = await asyncio.to_thread(_call_groq, question, search_context)

        max_len = 3900
        header = f"🤖 Q: {question}\n\n"
        first_chunk_limit = max(1, max_len - len(header))
        first_chunk = answer[:first_chunk_limit]
        remaining_answer = answer[first_chunk_limit:]

        await thinking_msg.edit_text(header + first_chunk)
        for i in range(0, len(remaining_answer), max_len):
            chunk = remaining_answer[i:i + max_len]
            await thinking_msg.reply_text(chunk)

    except Exception as e:
        logger.error("Ask error: %s", e)
        await thinking_msg.edit_text(
            "❌ Something went wrong with the AI. Please try again later."
        )


# ---------------------------------------------
# /addcountdown - Step 1: ask for name
# ---------------------------------------------
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

    chat_id = update.effective_chat.id
    name = context.user_data["new_countdown_name"]
    target_date = context.user_data["new_countdown_date"]
    created_by = update.effective_user.id

    add_countdown(chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)

    today = _today()
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
    logger.info("Chat %s added countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


# ---------------------------------------------
# /choose
# ---------------------------------------------
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
    raw = update.message.text.strip()
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
    verdict = random.choice(VERDICT_LINES)
    thinking = random.choice(THINKING_MESSAGES)

    weights = [random.randint(1, 100) for _ in options]
    total_weight = sum(weights)
    odds = [
        {
            "option": option,
            "weight": weight,
            "percentage": (weight / total_weight) * 100,
        }
        for option, weight in zip(options, weights)
    ]

    highest_weight = max(item["weight"] for item in odds)
    winning_options = [
        item["option"] for item in odds
        if item["weight"] == highest_weight
    ]
    chosen = random.choice(winning_options)
    percentage_lines = "\n".join(
        f"{'👉' if item['option'] == chosen else '   '} {item['option']} - {item['percentage']:.2f}%"
        for item in odds
    )

    thinking_msg = await update.message.reply_text(thinking)
    await asyncio.sleep(2)

    await thinking_msg.edit_text(
        f"🎯 Decision: {decision}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ The answer is... {chosen}!\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 Odds:\n{percentage_lines}\n\n"
        f"{verdict}",
    )

    context.user_data.clear()
    logger.info("Chat %s chose '%s' from %s", update.effective_chat.id, chosen, options)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------
# /listcountdown
# ---------------------------------------------
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


# ---------------------------------------------
# /removecountdown <name>
# ---------------------------------------------
async def remove_countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide the countdown name.\n"
            "Usage: `/removecountdown <name>`\n"
            "_(Use /listcountdown to see all names)_",
            parse_mode="Markdown",
        )
        return

    name = " ".join(context.args)
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


# ---------------------------------------------
# Daily reminder job
# ---------------------------------------------
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
    td = date.fromisoformat(entry["target_date"])
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
    logger.info("Sent reminder for '%s' to chat %s - %s days left", name, chat_id, days_left)


# ---------------------------------------------
# Restore jobs on startup
# ---------------------------------------------
async def restore_jobs(app) -> None:
    all_data = get_all_chats()
    count = 0
    for chat_id, countdowns in all_data.items():
        for name, entry in countdowns.items():
            h = entry.get("reminder_hour", 12)
            m = entry.get("reminder_minute", 0)
            _schedule_reminder(app, chat_id, name, h, m)
            count += 1
    logger.info("Restored %s reminder job(s) from Redis.", count)


# ---------------------------------------------
# /fate - Daily luck predictor
# ---------------------------------------------
FATE_LUCKY_ID = _env_int("FATE_LUCKY_ID", 0)
FATE_UNLUCKY_ID = _env_int("FATE_UNLUCKY_ID", 0)

FATE_TIERS = [
    {
        "name": "💀 CURSED",
        "range": (0, 10),
        "messages": [
            "Your ancestors are filing a complaint.",
            "Even your shadow is avoiding you today.",
            "The universe has personally chosen you to suffer.",
            "A black cat saw you and walked away in disgust.",
            "Mercury is in retrograde and it is specifically targeting you.",
        ],
    },
    {
        "name": "🌧️ Unlucky",
        "range": (11, 35),
        "messages": [
            "Things could be worse. They probably will be.",
            "Not your day. Maybe tomorrow.",
            "Your coffee will definitely go cold faster than usual.",
            "The queue will always be longer wherever you go.",
            "You will find a parking spot, but someone else will take it.",
        ],
    },
    {
        "name": "🌤️ Neutral",
        "range": (36, 64),
        "messages": [
            "Perfectly balanced, as all things should be.",
            "Not great, not terrible. Just vibing.",
            "The universe has no strong feelings about you today.",
            "You exist. That is about it for today.",
            "Coin flip energy. Could go either way.",
        ],
    },
    {
        "name": "✨ Blessed",
        "range": (65, 89),
        "messages": [
            "The stars are rooting for you today.",
            "Good things are coming. Stay ready.",
            "Your energy is immaculate today. Do not waste it.",
            "Luck is on your side. Make your move.",
            "Today is yours. Go claim it.",
        ],
    },
    {
        "name": "🌟 LEGENDARY",
        "range": (90, 100),
        "messages": [
            "The universe bows before you.",
            "You were BUILT for today. Absolutely unstoppable.",
            "Buy lottery. Seriously. Right now.",
            "Angels are personally cheering you on.",
            "This is your villain origin story but make it successful.",
        ],
    },
]

FATE_EXTREME_LUCKY_MESSAGES = [
    "THE COSMOS HAS CHOSEN YOU. Once-in-a-lifetime energy. You are literally untouchable today. The stars aligned specifically for you.",
    "ABSOLUTE MAXIMUM LUCK. You have been blessed by forces beyond this world. Today nothing can stop you. NOTHING.",
    "DIVINE INTERVENTION DETECTED. The universe has put everything on pause just to give you this moment. Legendary.",
]

FATE_EXTREME_UNLUCKY_MESSAGES = [
    "CATASTROPHICALLY CURSED. Something has gone terribly wrong in your cosmic alignment. Stay home. Do not touch anything.",
    "VOID-LEVEL BAD LUCK. The universe did not just forget about you. It actively chose violence. We are so sorry.",
    "MAXIMUM CURSE DETECTED. Even the laws of physics are against you today. We recommend staying very very still.",
]


def _get_fate(user_id: int):
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"{user_id}:{today_str}")

    if FATE_LUCKY_ID and user_id == FATE_LUCKY_ID:
        return 999, "🌈 COSMICALLY CHOSEN", rng.choice(FATE_EXTREME_LUCKY_MESSAGES)

    if FATE_UNLUCKY_ID and user_id == FATE_UNLUCKY_ID:
        return -999, "☠️ COSMICALLY CURSED", rng.choice(FATE_EXTREME_UNLUCKY_MESSAGES)

    extreme_roll = rng.randint(1, 30)
    if extreme_roll == 1:
        if rng.random() < 0.5:
            return 999, "🌈 COSMICALLY CHOSEN", rng.choice(FATE_EXTREME_LUCKY_MESSAGES)
        return -999, "☠️ COSMICALLY CURSED", rng.choice(FATE_EXTREME_UNLUCKY_MESSAGES)

    score = rng.randint(0, 100)
    for tier in FATE_TIERS:
        lo, hi = tier["range"]
        if lo <= score <= hi:
            return score, tier["name"], rng.choice(tier["messages"])

    return score, "🌤️ Neutral", "Just another day."


async def fate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    username = user.first_name or user.username or "You"

    score, tier, message = _get_fate(user_id)

    if score == 999:
        score_display = "999 ⚡ MAXIMUM"
    elif score == -999:
        score_display = "-999 💀 MINIMUM"
    else:
        filled = round(score / 10)
        bar = "█" * filled + "░" * (10 - filled)
        score_display = f"{score}/100  [{bar}]"

    await update.message.reply_text(
        f"🔮 *Daily Fate — {username}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Tier: *{tier}*\n"
        f"Score: `{score_display}`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{message}_",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# Main
# ---------------------------------------------
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
            ASK_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)],
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
    app.add_handler(CommandHandler("fate", fate_command))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
