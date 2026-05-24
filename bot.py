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
/ship            -> Compatibility percentage
/roast           -> Friendly roast
/compliment      -> Random compliment
/vibecheck       -> Group mood score
/rank            -> Random ranking
/truth           -> Truth question
/dare            -> Dare challenge
/wouldyourather  -> Would you rather question
/coinflip        -> Heads or tails
/8ball           -> Magic 8-ball
/luck            -> Check someone's luck
/fateboard       -> Today's fate leaderboard
/curse           -> Fake daily curse
/bless           -> Fake daily blessing
/cancel          -> Cancel the current flow
"""

import logging
import os
import random
import asyncio
import re
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
    get_countdown_by_code,
    save_fate_entry,
    get_fate_board,
    save_quote,
    get_random_quote,
    get_quote_count,
    get_all_quotes,
    delete_quote,
    track_seen_user,
    get_seen_users,
    save_ship_pair,
    get_top_ship_pairs,
    update_fate_streak,
    get_fate_streak,
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


BOT_OWNER_ID = _env_int("BOT_OWNER_ID", 0)
BOT_OWNER_USERNAMES = {
    username.strip().lstrip("@").casefold()
    for username in os.getenv("BOT_OWNER_USERNAME", "").replace(",", " ").split()
    if username.strip()
}


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
        "🎉 /help — see all fun commands\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /help — paginated with inline keyboard
# ---------------------------------------------
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import CallbackQueryHandler

HELP_PAGES = {
    "countdown": (
        "⏱ *Countdown*\n\n"
        "/addcountdown — add a new countdown, step by step\n"
        "/listcountdown — see all active countdowns in this group\n"
        "/removecountdown <name> — remove a countdown by name"
    ),
    "decisions": (
        "🎲 *Decisions*\n\n"
        "/choose — can't decide? let the bot pick for you\n"
        "/decide opt1, opt2, opt3 — instant pick\n"
        "/rank topic: item1, item2, item3 — randomly rank things\n"
        "/poll question: opt1, opt2 — send a native Telegram poll"
    ),
    "ai": (
        "🤖 *AI*\n\n"
        "/ask <question> — search the web + ask AI anything\n"
        "/8ball <question> — magic 8-ball powered by AI\n"
        "/hot <anything> — rate anything out of 100"
    ),
    "fate": (
        "🔮 *Daily Fate*\n\n"
        "/fate — check your personal daily luck\n"
        "/fateboard — today's fate leaderboard for the group\n"
        "/luck @user — check someone's daily luck score"
    ),
    "fun": (
        "🎉 *Fun*\n\n"
        "/ship @user1 @user2 — compatibility percentage\n"
        "/roast @user — personalised AI roast\n"
        "/compliment @user — personalised AI compliment\n"
        "/vibecheck — group mood score\n"
        "/mvp — today's most valuable group member\n"
        "/truth — random truth question\n"
        "/dare — random dare\n"
        "/wouldyourather — random would you rather\n"
        "/coinflip — heads or tails\n"
        "/curse @user — fake daily curse\n"
        "/bless @user — fake daily blessing"
    ),
    "quotes": (
        "💬 *Quotes*\n\n"
        "/quote — reply to any message to save it\n"
        "/quotes — browse saved quotes with prev/next\n"
        "/deletequote <number> — delete a quote by its number"
    ),
    "reminders": (
        "⏰ *Reminders*\n\n"
        "/remind 10m take a break — set a personal reminder\n"
        "/remind 2h check the oven — supports s/sec, m/min, h/hr and more"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/cancel — cancel the current flow\n"
        "/help — show this menu"
    ),
}

_HELP_PAGE_ORDER = ["countdown", "decisions", "ai", "fate", "fun", "quotes", "reminders", "other"]

_HELP_PAGE_LABELS = {
    "countdown": "⏱ Countdown",
    "decisions": "🎲 Decisions",
    "ai": "🤖 AI",
    "fate": "🔮 Fate",
    "fun": "🎉 Fun",
    "quotes": "💬 Quotes",
    "reminders": "⏰ Reminders",
    "other": "⚙️ Other",
}


def _help_keyboard(current_page: str) -> InlineKeyboardMarkup:
    """Build a 2-column keyboard of page buttons, highlighting the current one."""
    buttons = []
    row = []
    for page_key in _HELP_PAGE_ORDER:
        label = _HELP_PAGE_LABELS[page_key]
        if page_key == current_page:
            label = f"› {label} ‹"
        row.append(InlineKeyboardButton(label, callback_data=f"help:{page_key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = "countdown"
    await update.message.reply_text(
        HELP_PAGES[page] + "\n\n_Tap a category below:_",
        parse_mode="Markdown",
        reply_markup=_help_keyboard(page),
    )


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, page = query.data.split(":", 1)
    if page not in HELP_PAGES:
        return
    try:
        await query.edit_message_text(
            HELP_PAGES[page] + "\n\n_Tap a category below:_",
            parse_mode="Markdown",
            reply_markup=_help_keyboard(page),
        )
    except Exception:
        pass


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


def _call_groq_fun(prompt: str) -> str:
    """Call Groq for short fun commands without using web search."""
    chat = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    "You write short, playful Telegram group chat content. "
                    "Plain text only. No markdown. Keep it friendly, funny, and safe. "
                    "Give only the answer, no intro."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=120,
        temperature=0.9,
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
def _track(context: ContextTypes.DEFAULT_TYPE, *messages) -> None:
    """Store message IDs so they can be bulk-deleted at the end of the flow."""
    ids: list = context.user_data.setdefault("_cd_msg_ids", [])
    for msg in messages:
        if msg is not None:
            ids.append((msg.chat_id, msg.message_id))


async def _delete_tracked(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete every tracked message, silently ignoring failures."""
    for chat_id, msg_id in context.user_data.pop("_cd_msg_ids", []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


async def add_countdown_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bot_msg = await update.message.reply_text(
        "➕ *New Countdown*\n\n"
        "Step 1/3 — What do you want to call this countdown?\n"
        "_(e.g. Final Exam, Holiday, Birthday)_\n\n"
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

    chat_id = update.effective_chat.id
    if countdown_exists(chat_id, name):
        await update.message.reply_text(
            f"⚠️ A countdown named *{name}* already exists.\n"
            f"Please use a different name.\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_NAME

    context.user_data["new_countdown_name"] = name
    bot_msg = await update.message.reply_text(
        f"✅ Name set to *{name}*\n\n"
        "Step 2/3 — What is the target date?\n"
        "Format: `YYYY-MM-DD` _(e.g. 2025-12-31)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
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

    if target_date < _today():
        await update.message.reply_text(
            f"⚠️ `{target_date}` is in the past. Please choose a future date.\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_DATE

    context.user_data["new_countdown_date"] = target_date
    bot_msg = await update.message.reply_text(
        f"✅ Date set to *{target_date}*\n\n"
        "Step 3/3 — What time should the group be reminded daily?\n"
        "Format: `HH:MM` in 24hr MYT _(e.g. 08:30 or 20:00)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
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

    chat_id = update.effective_chat.id
    name = context.user_data["new_countdown_name"]
    target_date = context.user_data["new_countdown_date"]
    created_by = update.effective_user.id

    code = await asyncio.to_thread(add_countdown, chat_id, name, target_date, hour, minute, created_by)
    _schedule_reminder(context.application, chat_id, name, hour, minute)

    today = _today()
    days_left = (target_date - today).days

    _track(context, update.message)
    await _delete_tracked(context)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 *Countdown Added!*\n\n"
            f"📛 Name: *{name}*\n"
            f"🔑 Code: `{code}`\n"
            f"📆 Target: *{target_date}*\n"
            f"{_days_label(days_left)}\n"
            f"🔔 Daily reminder at *{hour:02d}:{minute:02d} MYT*\n\n"
            f"_To remove: `/removecountdown {code}`_"
        ),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    logger.info("Chat %s added countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


# ---------------------------------------------
# /choose
# ---------------------------------------------
async def choose_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bot_msg = await update.message.reply_text(
        "🎲 *Decision Maker*\n\n"
        "What's the issue? Tell me what you need to decide.\n"
        "_(e.g. Should I skip class? What should I eat?)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
    )
    _track(context, update.message, bot_msg)
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
    bot_msg = await update.message.reply_text(
        f"Got it — *\"{decision}\"*\n\n"
        "Now give me the options, separated by commas.\n"
        "_(e.g. Yes, No, Maybe  or  Pizza, Burger, Sushi)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
    )
    _track(context, update.message, bot_msg)
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

    _track(context, update.message)
    await _delete_tracked(context)

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
    await _delete_tracked(context)
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------
# /listcountdown
# ---------------------------------------------
async def list_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    countdowns = await asyncio.to_thread(get_all_countdowns, chat_id)

    if not countdowns:
        await update.message.reply_text(
            "📭 No active countdowns.\nUse `/addcountdown` to add one!",
            parse_mode="Markdown",
        )
        return

    today = _today()

    # Sort entries by days remaining (soonest first; overdue shown last)
    sorted_entries = sorted(
        countdowns.items(),
        key=lambda kv: (date.fromisoformat(kv[1]["target_date"]) - today).days
    )

    lines = ["📋 *Active Countdowns (soonest first):*\n"]
    for name, entry in sorted_entries:
        td = date.fromisoformat(entry["target_date"])
        days_left = (td - today).days
        h = entry["reminder_hour"]
        m = entry["reminder_minute"]
        code = entry.get("code", "—")
        lines.append(
            f"• *{name}* `[{code}]`\n"
            f"  📆 {td}  |  {_days_label(days_left)}\n"
            f"  🔔 Reminder at {h:02d}:{m:02d} MYT\n"
        )

    lines.append("_Remove with_ `/removecountdown <code>` _or_ `/removecountdown <name>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------
# /removecountdown <code or name>
# ---------------------------------------------
async def remove_countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide the countdown code or name.\n"
            "Usage: `/removecountdown a3k` or `/removecountdown <name>`\n"
            "_(Use /listcountdown to see codes)_",
            parse_mode="Markdown",
        )
        return

    arg = " ".join(context.args).strip()

    # Try resolving as a short code first (3–4 chars, alphanumeric)
    name = None
    if len(arg) <= 4 and arg.isalnum():
        name = await asyncio.to_thread(get_countdown_by_code, chat_id, arg.lower())

    # Fall back to treating the arg as the full name
    if name is None:
        name = arg

    removed = await asyncio.to_thread(remove_countdown, chat_id, name)

    if removed:
        jname = _job_name(chat_id, name)
        for job in context.job_queue.get_jobs_by_name(jname):
            job.schedule_removal()
        await update.message.reply_text(f"🗑️ Countdown *{name}* has been removed.", parse_mode="Markdown")
        logger.info("Chat %s removed countdown '%s'", chat_id, name)
    else:
        await update.message.reply_text(
            f"⚠️ No countdown found for `{arg}`.\n"
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

def _remember_fate(chat_id: int, user_id: int, name: str, score: int, tier: str) -> None:
    """Sync helper — call via asyncio.to_thread."""
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    save_fate_entry(chat_id, today_str, user_id, name, score, tier)


def _update_streak_sync(user_id: int, today_str: str, tier_category: str) -> int:
    return update_fate_streak(user_id, today_str, tier_category)


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
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    # Determine streak category
    if score == 999:
        tier_category = "lucky"
    elif score == -999:
        tier_category = "unlucky"
    elif score >= 65:
        tier_category = "lucky"
    elif score <= 35:
        tier_category = "unlucky"
    else:
        tier_category = "neutral"

    # Non-blocking Redis writes
    asyncio.create_task(
        asyncio.to_thread(_remember_fate, update.effective_chat.id, user_id, username, score, tier)
    )
    asyncio.create_task(
        asyncio.to_thread(track_seen_user, update.effective_chat.id, user_id, username)
    )
    streak = await asyncio.to_thread(_update_streak_sync, user_id, today_str, tier_category)

    if score == 999:
        score_display = "999 ⚡ MAXIMUM"
    elif score == -999:
        score_display = "-999 💀 MINIMUM"
    else:
        filled = round(score / 10)
        bar = "█" * filled + "░" * (10 - filled)
        score_display = f"{score}/100  [{bar}]"

    streak_line = ""
    if streak >= 2:
        if tier_category == "lucky":
            streak_line = f"\n🔥 *Lucky streak: {streak} days in a row!*"
        elif tier_category == "unlucky":
            streak_line = f"\n💀 *Unlucky streak: {streak} days in a row...*"

    await update.message.reply_text(
        f"🔮 *Daily Fate — {username}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Tier: *{tier}*\n"
        f"Score: `{score_display}`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{message}_"
        f"{streak_line}",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# Fun commands
# ---------------------------------------------
SHIP_OWNER_BLOCK_LINES = [
    "Nice try, but you do not ship da GOAT. The owner is outside the romance algorithm.",
    "Access denied. The owner is canonically unshippable.",
    "You should not ship da GOAT. That is premium lore and the bot refuses.",
    "Compatibility scan cancelled. The owner has main-character immunity.",
]

SHIP_TIER_LINES = [
    (10, "This ship is still buffering."),
    (25, "Low battery chemistry."),
    (45, "There is potential, but the universe is squinting."),
    (65, "Not bad. The vibes are warming up."),
    (80, "Strong ship energy detected."),
    (94, "Dangerously compatible. The chat may need to sit down."),
    (100, "Legendary ship. The timeline is shaking."),
]

ROAST_LINES = [
    "{target}, your confidence loads faster than your common sense.",
    "{target}, you bring side quest energy to main quest problems.",
    "{target}, your aura said 'software update required'.",
    "{target}, you are proof that chaos can have a username.",
    "{target}, your plan has the structural integrity of wet tissue.",
    "{target}, you are not wrong often, but today is looking ambitious.",
    "{target}, your brain opened 47 tabs and all of them froze.",
    "{target}, you have premium nonsense with free-tier execution.",
    "{target}, your logic took a lunch break and never clocked back in.",
    "{target}, you are built different. Not better, just different.",
]

COMPLIMENT_LINES = [
    "{target}, your vibe is clean today.",
    "{target}, you are carrying excellent main-character energy.",
    "{target}, your presence improves the group chat economy.",
    "{target}, you are suspiciously easy to root for.",
    "{target}, your aura has good lighting.",
    "{target}, you are the reason the chat has range.",
    "{target}, your brain has sparkle settings enabled.",
    "{target}, you make ordinary moments feel less ordinary.",
    "{target}, you are quietly iconic.",
    "{target}, you are doing better than you think.",
]

VIBE_TIERS = [
    (20, "The group chat needs a reboot."),
    (40, "Chaotic but still breathing."),
    (60, "Stable enough. Do not shake it."),
    (80, "Good vibes are loading properly."),
    (100, "Elite group energy. Screenshot-worthy."),
]

TRUTH_QUESTIONS = [
    "What is one thing you pretend not to care about but actually do?",
    "Who in this chat gives the best advice?",
    "What is the most embarrassing thing you have searched online?",
    "What is one habit you know is bad but still do?",
    "What is a song you would never admit is on repeat?",
    "Who here would survive a drama episode the longest?",
    "What is the most unserious reason you got annoyed recently?",
    "What is one thing you are secretly proud of?",
    "Who in this chat is most likely to overthink a simple text?",
    "What is your most harmless guilty pleasure?",
]

DARE_PROMPTS = [
    "Send the last saved meme in your gallery.",
    "Compliment someone in this chat with zero sarcasm.",
    "Let the chat choose your profile picture for 10 minutes.",
    "Say 'I was wrong' even if you were obviously right.",
    "Send a voice note saying one dramatic sentence.",
    "Type your next message with maximum formal energy.",
    "Let someone in the chat pick your next snack or drink.",
    "Reply to the next message like a movie trailer narrator.",
    "Send your current battery percentage with no context.",
    "Use only polite corporate language for the next 5 minutes.",
]

WOULD_YOU_RATHER_PROMPTS = [
    "Would you rather always be 10 minutes late or always 30 minutes early?",
    "Would you rather know every secret or forget every embarrassing memory?",
    "Would you rather have unlimited money for food or travel?",
    "Would you rather read minds for one day or rewind one day?",
    "Would you rather be famous for talent or famous by accident?",
    "Would you rather never need sleep or never need to study?",
    "Would you rather have perfect luck or perfect timing?",
    "Would you rather only text or only voice note for a week?",
    "Would you rather win every argument or never need to argue?",
    "Would you rather be able to pause time or skip boring moments?",
]

EIGHT_BALL_ANSWERS = [
    "Yes.",
    "No.",
    "Absolutely.",
    "Not today.",
    "The signs point to yes.",
    "The signs point to chaos.",
    "Ask again after snacks.",
    "Highly likely.",
    "Extremely suspicious, but yes.",
    "I would not bet my lunch on it.",
    "The answer is hiding, but leaning yes.",
    "The answer is hiding, but leaning no.",
]

CURSE_LINES = [
    "{target} is cursed to forget why they opened an app.",
    "{target} is cursed with warm drinks turning cold too fast.",
    "{target} is cursed to type a message and immediately see a typo.",
    "{target} is cursed with one extra loading screen today.",
    "{target} is cursed to hear 'we need to talk' with no context.",
    "{target} is cursed to crave food that is unavailable.",
]

BLESS_LINES = [
    "{target} is blessed with perfect timing today.",
    "{target} is blessed with unexpectedly good news.",
    "{target} is blessed with strong focus and low nonsense.",
    "{target} is blessed with clear skin, clear mind, clear path.",
    "{target} is blessed with the ability to choose correctly today.",
    "{target} is blessed with main-character background music.",
]


def _display_user(user) -> str:
    return user.first_name or user.username or str(user.id)


def _arg_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args).strip()


def _normalize_target(target: str) -> str:
    return target.strip().lstrip("@").casefold()


def _daily_rng(label: str, *parts):
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    seed = ":".join(str(part) for part in (label, today_str, *parts))
    return random.Random(seed)


def _stable_rng(label: str, *parts):
    seed = ":".join(str(part) for part in (label, *parts))
    return random.Random(seed)


def _mentioned_target(update: Update, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    message = update.message
    if not message:
        return ""

    ignored_usernames = set()
    ignored_user_ids = set()
    if context:
        bot_username = getattr(context.bot, "username", None)
        bot_id = getattr(context.bot, "id", None)
        if bot_username:
            ignored_usernames.add(bot_username.casefold())
        if bot_id:
            ignored_user_ids.add(bot_id)

    for entity in message.entities or []:
        if entity.type != "bot_command":
            continue

        command_text = message.parse_entity(entity)
        if "@" in command_text:
            ignored_usernames.add(command_text.split("@", 1)[1].casefold())

    for entity in message.entities or []:
        if entity.type not in ("mention", "text_mention"):
            continue

        if getattr(entity, "user", None):
            if entity.user.id in ignored_user_ids:
                continue
            return _display_user(entity.user)

        mention = message.parse_entity(entity)
        if _normalize_target(mention) in ignored_usernames:
            continue
        return mention

    return ""


def _target_from_mention_or_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    return _mentioned_target(update, context) or _display_user(update.effective_user)


def _ship_target(label: str, user_id=None, username: str = "", explicit_username: bool = False) -> dict:
    return {
        "label": label,
        "user_id": user_id,
        "username": username.strip().lstrip("@").casefold(),
        "explicit_username": explicit_username,
    }


def _ship_mentions_from_message(update: Update) -> list[dict]:
    message = update.message
    if not message:
        return []

    targets = []
    for entity in message.entities or []:
        if entity.type == "text_mention" and getattr(entity, "user", None):
            targets.append(
                _ship_target(
                    _display_user(entity.user),
                    user_id=entity.user.id,
                    username=entity.user.username or "",
                    explicit_username=bool(entity.user.username),
                )
            )
        elif entity.type == "mention":
            mention = message.parse_entity(entity)
            targets.append(
                _ship_target(
                    mention,
                    username=mention,
                    explicit_username=True,
                )
            )

    return targets


def _extract_ship_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = _arg_text(context)
    replied = update.message.reply_to_message
    mentioned_targets = _ship_mentions_from_message(update)

    if len(mentioned_targets) >= 2:
        return mentioned_targets[:2]

    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            return [
                _ship_target(parts[0]),
                _ship_target(parts[1]),
            ]

    if replied and replied.from_user and len(context.args) == 1:
        other_target = mentioned_targets[0] if mentioned_targets else _ship_target(
            context.args[0],
            username=context.args[0] if context.args[0].startswith("@") else "",
            explicit_username=context.args[0].startswith("@"),
        )
        return [
            _ship_target(
                _display_user(replied.from_user),
                user_id=replied.from_user.id,
                username=replied.from_user.username or "",
                explicit_username=bool(replied.from_user.username),
            ),
            other_target,
        ]

    if len(context.args) >= 2:
        return [
            _ship_target(
                context.args[0],
                username=context.args[0] if context.args[0].startswith("@") else "",
                explicit_username=context.args[0].startswith("@"),
            ),
            _ship_target(
                context.args[1],
                username=context.args[1] if context.args[1].startswith("@") else "",
                explicit_username=context.args[1].startswith("@"),
            ),
        ]

    return []


def _is_protected_ship_target(target: dict) -> bool:
    if BOT_OWNER_ID and target.get("user_id") == BOT_OWNER_ID:
        return True

    username = target.get("username", "")
    if target.get("explicit_username") and username in BOT_OWNER_USERNAMES:
        return True

    return False


def _ship_comment(score: float) -> str:
    for limit, comment in SHIP_TIER_LINES:
        if score <= limit:
            return comment
    return SHIP_TIER_LINES[-1][1]


async def ship_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    targets = _extract_ship_targets(update, context)
    if len(targets) < 2:
        await update.message.reply_text(
            "Usage: /ship @user1 @user2\n"
            "Tip: you can also reply to someone with /ship @user"
        )
        return

    for target in targets:
        if _is_protected_ship_target(target):
            await update.message.reply_text(random.choice(SHIP_OWNER_BLOCK_LINES))
            return

    target_a, target_b = targets[0]["label"], targets[1]["label"]
    normalized = sorted([_normalize_target(target_a), _normalize_target(target_b)])

    if normalized[0] == normalized[1]:
        await update.message.reply_text(
            f"💞 Ship Result\n"
            f"{target_a} x {target_b}\n\n"
            f"Compatibility: 100.00%\n"
            f"That is not a ship. That is self-love with documentation."
        )
        return

    rng = _daily_rng("ship", normalized[0], normalized[1])
    score = rng.randint(0, 10000) / 100
    chat_id = update.effective_chat.id

    # Track users if we have real user objects
    for t in targets:
        if t.get("user_id"):
            asyncio.create_task(
                asyncio.to_thread(track_seen_user, chat_id, t["user_id"], t["label"])
            )

    # Persist to ship leaderboard (non-blocking)
    pair_key = f"{normalized[0]}:{normalized[1]}"
    asyncio.create_task(
        asyncio.to_thread(save_ship_pair, chat_id, pair_key, target_a, target_b, score)
    )

    filled = round(score / 10)
    bar = "█" * filled + "░" * (10 - filled)
    await update.message.reply_text(
        f"💞 *Ship Result*\n"
        f"{target_a} × {target_b}\n\n"
        f"Compatibility: `{score:.2f}%`  [{bar}]\n"
        f"_{_ship_comment(score)}_",
        parse_mode="Markdown",
    )


async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    fallback = random.choice(ROAST_LINES).format(target=target)
    if not groq_client:
        await update.message.reply_text(fallback)
        return
    try:
        prompt = (
            f"Write one short, playful, friendly roast for someone named '{target}' in a Telegram group chat. "
            "Use their name. Keep it funny and harmless, not mean. One sentence only."
        )
        result = await asyncio.to_thread(_call_groq_fun, prompt)
        await update.message.reply_text(result)
    except Exception as e:
        logger.warning("Groq roast failed: %s", e)
        await update.message.reply_text(fallback)


async def compliment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    fallback = random.choice(COMPLIMENT_LINES).format(target=target)
    if not groq_client:
        await update.message.reply_text(fallback)
        return
    try:
        prompt = (
            f"Write one short, warm, genuine compliment for someone named '{target}' in a Telegram group chat. "
            "Use their name. Keep it wholesome and specific-sounding. One sentence only."
        )
        result = await asyncio.to_thread(_call_groq_fun, prompt)
        await update.message.reply_text(result)
    except Exception as e:
        logger.warning("Groq compliment failed: %s", e)
        await update.message.reply_text(fallback)


async def vibecheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TIMEZONE)
    hour = now.hour
    if hour < 12:
        period = "morning"
    elif hour < 18:
        period = "afternoon"
    else:
        period = "night"
    rng = _daily_rng("vibecheck", update.effective_chat.id, period)
    score = rng.randint(0, 100)
    mood = next(message for limit, message in VIBE_TIERS if score <= limit)

    await update.message.reply_text(
        f"📡 Vibe Check\n"
        f"Group mood: {score}/100\n"
        f"{mood}"
    )


def _parse_rank_text(text: str):
    title = "Random Ranking"
    item_text = text

    if ":" in text:
        title, item_text = [part.strip() for part in text.split(":", 1)]
        title = title or "Random Ranking"

    if "," in item_text:
        items = [item.strip() for item in item_text.split(",") if item.strip()]
    else:
        items = [item.strip() for item in item_text.split() if item.strip()]

    return title, items[:12]


async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: /rank topic: item1, item2, item3\n"
            "Example: /rank food: pizza, burger, sushi"
        )
        return

    title, items = _parse_rank_text(text)
    if len(items) < 2:
        await update.message.reply_text("Give me at least 2 things to rank.")
        return

    random.shuffle(items)
    lines = [f"🏆 {title}"]
    lines.extend(f"{index}. {item}" for index, item in enumerate(items, start=1))
    await update.message.reply_text("\n".join(lines))


async def truth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🧃 Truth\n{random.choice(TRUTH_QUESTIONS)}")


async def dare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🎬 Dare\n{random.choice(DARE_PROMPTS)}")


async def would_you_rather_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"⚖️ Would You Rather\n{random.choice(WOULD_YOU_RATHER_PROMPTS)}")


async def coinflip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🪙 Flipping...")
    await asyncio.sleep(1)
    result = random.choice(["Heads", "Tails"])
    icon = "🌕" if result == "Heads" else "🌑"
    await msg.edit_text(f"🪙 Coinflip: {icon} {result}")


async def eightball_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = _arg_text(context)
    if not question:
        await update.message.reply_text("Usage: /8ball <question>")
        return

    fallback = random.choice(EIGHT_BALL_ANSWERS)
    if not groq_client:
        await update.message.reply_text(f"🎱 {fallback}")
        return

    try:
        prompt = (
            "Answer this like a playful magic 8-ball. "
            "Keep it under 20 words. Question: "
            f"{question}"
        )
        answer = await asyncio.to_thread(_call_groq_fun, prompt)
        await update.message.reply_text(f"🎱 {answer}")
    except Exception as e:
        logger.warning("Groq 8ball command failed: %s", e)
        await update.message.reply_text(f"🎱 {fallback}")


def _luck_result(key: str):
    rng = _daily_rng("luck", key)
    score = rng.randint(0, 100)
    for tier in FATE_TIERS:
        lo, hi = tier["range"]
        if lo <= score <= hi:
            return score, tier["name"], rng.choice(tier["messages"])

    return score, "🌤️ Neutral", "Just another day."


async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    # Try to get a stable user_id from a mention or reply
    seed_key = None
    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            seed_key = str(entity.user.id)
            target = _display_user(entity.user)
            break

    if seed_key is None:
        # Fall back to display name for @username mentions (no user object available)
        target = _target_from_mention_or_sender(update, context)
        # If it's the sender themselves, use their user_id for stability
        if target == _display_user(update.effective_user):
            seed_key = str(update.effective_user.id)
        else:
            seed_key = _normalize_target(target)

    score, tier, message_text = _luck_result(seed_key)

    await message.reply_text(
        f"🍀 Daily Luck — {target}\n"
        f"Tier: {tier}\n"
        f"Score: {score}/100\n"
        f"{message_text}"
    )


async def fateboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    board = await asyncio.to_thread(get_fate_board, update.effective_chat.id, today_str)

    if not board:
        await update.message.reply_text(
            "⚠️ No fate scores yet today. Tell people to use /fate first!"
        )
        return

    users = sorted(
        board.values(),
        key=lambda item: item["score"],
        reverse=True,
    )

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏅 *Today's Fateboard*\n"]
    for index, item in enumerate(users[:10], start=1):
        rank_icon = medals[index - 1] if index <= 3 else f"{index}."
        s = item["score"]
        if s == 999:
            bar = "⚡ MAX"
        elif s == -999:
            bar = "💀 MIN"
        else:
            filled = round(max(0, min(s, 100)) / 10)
            bar = "█" * filled + "░" * (10 - filled)
        lines.append(
            f"{rank_icon} *{item['name']}* — {item['tier']}\n"
            f"    Score: `{s}`  [{bar}]"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def curse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(CURSE_LINES).format(target=target))


async def bless_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(BLESS_LINES).format(target=target))


# ---------------------------------------------
# /decide — instant single-command pick
# ---------------------------------------------
async def decide_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: /decide option1, option2, option3\n"
            "Example: /decide pizza, burger, sushi"
        )
        return

    options = [o.strip() for o in text.split(",") if o.strip()]
    if len(options) < 2:
        await update.message.reply_text(
            "Give me at least 2 options separated by commas.\n"
            "Example: /decide sleep, study, both"
        )
        return

    chosen = random.choice(options)
    verdict = random.choice(VERDICT_LINES)
    await update.message.reply_text(f"🎯 {chosen}\n{verdict}")


# ---------------------------------------------
# /poll — native Telegram poll
# ---------------------------------------------
async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text or ":" not in text:
        await update.message.reply_text(
            "Usage: /poll question: option1, option2, option3\n"
            "Example: /poll Where to makan?: McD, KFC, Mamak"
        )
        return

    question, opts_text = text.split(":", 1)
    question = question.strip()
    options = [o.strip() for o in opts_text.split(",") if o.strip()]

    if not question:
        await update.message.reply_text("The question can't be empty.")
        return
    if len(options) < 2:
        await update.message.reply_text("Give at least 2 options separated by commas.")
        return

    options = options[:10]  # Telegram max is 10
    await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=question[:300],
        options=[o[:100] for o in options],
        is_anonymous=False,
    )


# ---------------------------------------------
# /remind — one-shot personal reminder
# Supports: 10s, 10sec, 10secs, 10second, 10seconds,
#           10m, 10min, 10mins, 10minute, 10minutes,
#           10h, 10hr, 10hrs, 10hour, 10hours
# Usage: /remind 10m take a break  OR  /remind in 10min check oven
# ---------------------------------------------
_REMIND_RE = re.compile(
    r"(?:in\s+)?(\d+)\s*"
    r"(s(?:ec(?:ond)?s?)?|m(?:in(?:ute)?s?)?|h(?:(?:ou)?rs?)?)",
    re.IGNORECASE,
)

_UNIT_MAP = {
    "s": 1, "se": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "mi": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}


def _parse_remind_seconds(unit_str: str) -> int:
    key = unit_str.lower()
    # Try exact match first, then prefix scan
    if key in _UNIT_MAP:
        return _UNIT_MAP[key]
    for k, v in _UNIT_MAP.items():
        if key.startswith(k):
            return v
    return 60  # fallback to minutes


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
            "`/remind 10m take a break`\n"
            "`/remind 2h check dinner`\n"
            "`/remind 30sec drink water`",
            parse_mode="Markdown",
        )
        return

    amount = int(match.group(1))
    unit_str = match.group(2)
    per_unit = _parse_remind_seconds(unit_str)
    seconds = amount * per_unit

    # Build a clean human-readable label
    if per_unit == 1:
        label = f"{amount} second{'s' if amount != 1 else ''}"
    elif per_unit == 60:
        label = f"{amount} minute{'s' if amount != 1 else ''}"
    else:
        label = f"{amount} hour{'s' if amount != 1 else ''}"

    if seconds < 5:
        await update.message.reply_text("⚠️ Minimum reminder time is 5 seconds.")
        return
    if seconds > 86400:
        await update.message.reply_text("⚠️ Maximum reminder time is 24 hours.")
        return

    # Everything after the time expression is the reminder body
    reminder_text = text[match.end():].strip().lstrip("to").strip()
    if not reminder_text:
        reminder_text = "You asked me to remind you of something!"

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_mention = user.mention_html() if user else "Hey"

    async def _fire(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Reminder for {user_mention}!\n{reminder_text}",
            parse_mode="HTML",
        )

    context.application.job_queue.run_once(_fire, when=seconds, chat_id=chat_id)
    await update.message.reply_text(f"⏰ Got it! I'll remind you in *{label}*.", parse_mode="Markdown")


# ---------------------------------------------
# /quote — save a quote by replying; /quotes — fetch one; /deletequote <n> — remove
# ---------------------------------------------
async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    replied = message.reply_to_message
    saved_by = _display_user(update.effective_user)

    if replied and replied.from_user and replied.text:
        author = _display_user(replied.from_user)
        text = replied.text
    else:
        await message.reply_text(
            "💬 *How to save a quote:*\n"
            "Reply to any message with /quote to save it to the archive.",
            parse_mode="Markdown",
        )
        return

    if not text:
        await message.reply_text("The quote can't be empty!")
        return

    count = save_quote(update.effective_chat.id, author, text, saved_by)
    await message.reply_text(
        f'💬 Saved!\n*"{text}"* — {author}\n_#{count} in this chat_',
        parse_mode="Markdown",
    )


async def quotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    quotes = await asyncio.to_thread(get_all_quotes, chat_id)
    if not quotes:
        await update.message.reply_text(
            "⚠️ No quotes saved yet. Reply to any message with /quote to start the archive!"
        )
        return

    # Start at a random index
    index = random.randrange(len(quotes))
    await _send_quote_page(update.message.reply_text, chat_id, quotes, index)


async def _send_quote_page(send_fn, chat_id: int, quotes: list, index: int) -> None:
    total = len(quotes)
    q = quotes[index]
    text = (
        f'💬 *"{q["text"]}"*\n'
        f'— {q["author"]}\n'
        f'_(saved by {q["saved_by"]}) · #{index + 1}/{total}_'
    )
    prev_idx = (index - 1) % total
    next_idx = (index + 1) % total
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀ Prev", callback_data=f"quote:{chat_id}:{prev_idx}"),
        InlineKeyboardButton(f"{index + 1}/{total}", callback_data="quote:noop"),
        InlineKeyboardButton("Next ▶", callback_data=f"quote:{chat_id}:{next_idx}"),
    ]])
    await send_fn(text, parse_mode="Markdown", reply_markup=keyboard)


async def quotes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "quote:noop":
        return
    _, chat_id_str, idx_str = data.split(":")
    chat_id = int(chat_id_str)
    index = int(idx_str)
    quotes = await asyncio.to_thread(get_all_quotes, chat_id)
    if not quotes:
        await query.edit_message_text("⚠️ No quotes found.")
        return
    index = index % len(quotes)
    total = len(quotes)
    q = quotes[index]
    text = (
        f'💬 *"{q["text"]}"*\n'
        f'— {q["author"]}\n'
        f'_(saved by {q["saved_by"]}) · #{index + 1}/{total}_'
    )
    prev_idx = (index - 1) % total
    next_idx = (index + 1) % total
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀ Prev", callback_data=f"quote:{chat_id}:{prev_idx}"),
        InlineKeyboardButton(f"{index + 1}/{total}", callback_data="quote:noop"),
        InlineKeyboardButton("Next ▶", callback_data=f"quote:{chat_id}:{next_idx}"),
    ]])
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass


async def deletequote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        total = get_quote_count(chat_id)
        await update.message.reply_text(
            f"Usage: `/deletequote <number>`\n"
            f"There are currently *{total}* quote(s) saved.\n"
            "Use /quotes to browse them.",
            parse_mode="Markdown",
        )
        return

    index = int(context.args[0])
    success, msg = delete_quote(chat_id, index)
    await update.message.reply_text(msg)




# ---------------------------------------------
# /mvp — daily random MVP, picks from all chat members
# ---------------------------------------------
MVP_LINES = [
    "The data is in. The vibe is certified.",
    "Chosen by the algorithm. No debates.",
    "Today's main character. Uncontested.",
    "The group would not be the same without this one.",
    "Carrying the group energy on their back. Respect.",
    "Statistically, the most needed person in this chat today.",
    "The universe picked. We just announced it.",
    "Undefeated. Unbothered. MVP.",
]


async def mvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"mvp:{chat_id}:{today_str}")

    # Try to get the full member list via admins + seen users combined
    candidate_pool: dict[str, str] = {}  # {user_id_str: name}

    # Pull admins — always available via API
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for member in admins:
            u = member.user
            if not u.is_bot:
                name = u.first_name or u.username or str(u.id)
                candidate_pool[str(u.id)] = name
                # Also keep seen_users up to date (non-blocking)
                asyncio.create_task(
                    asyncio.to_thread(track_seen_user, chat_id, u.id, name)
                )
    except Exception as e:
        logger.warning("Could not fetch admins for mvp in chat %s: %s", chat_id, e)

    # Merge with seen_users (non-admins who've used any command)
    seen = await asyncio.to_thread(get_seen_users, chat_id)
    candidate_pool.update(seen)

    if not candidate_pool:
        await update.message.reply_text(
            "⚠️ Not enough members tracked yet. Have people use a command first!"
        )
        return

    winner_id = rng.choice(list(candidate_pool.keys()))
    winner_name = candidate_pool[winner_id]

    await update.message.reply_text(
        f"🏆 *Today's MVP — {winner_name}*\n"
        f"{rng.choice(MVP_LINES)}",
        parse_mode="Markdown",
    )



# ---------------------------------------------
# /hot — hot or not rating for anything
# ---------------------------------------------
HOT_VERDICTS = [
    (15,  "🧊 Absolutely not. Ice cold."),
    (35,  "😬 Not great. The vibes said no."),
    (55,  "🤔 Debatable. The jury is split."),
    (75,  "🔥 Lowkey hot. Solid choice."),
    (90,  "🌶️ Very hot. The group approves."),
    (100, "💥 MAXIMUM HOT. Undeniably elite."),
]


async def hot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: /hot <anything>\nExample: /hot sleeping through alarms"
        )
        return

    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"hot:{_normalize_target(text)}:{today_str}")
    score = rng.randint(0, 100)
    fallback = next(v for limit, v in HOT_VERDICTS if score <= limit)

    if groq_client:
        try:
            prompt = (
                f"Rate '{text}' in one short punchy sentence. "
                f"The score is {score}/100 — match that energy. "
                "Be funny. Plain text only. No intro."
            )
            verdict = await asyncio.to_thread(_call_groq_fun, prompt)
        except Exception as e:
            logger.warning("Groq hot command failed: %s", e)
            verdict = fallback
    else:
        verdict = fallback

    await update.message.reply_text(
        f"🌡️ *Hot or Not — {text}*\n"
        f"Score: {score}/100\n"
        f"{verdict}",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# Main
# ---------------------------------------------
def main() -> None:
    keep_alive()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(16)
        .connect_timeout(10.0)
        .read_timeout(10.0)
        .write_timeout(10.0)
        .post_init(restore_jobs)
        .build()
    )

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
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(quotes_callback, pattern=r"^quote:"))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(countdown_conv)
    app.add_handler(choose_conv)
    app.add_handler(CommandHandler("listcountdown", list_countdown))
    app.add_handler(CommandHandler("removecountdown", remove_countdown_cmd))
    app.add_handler(CommandHandler("fate", fate_command))
    app.add_handler(CommandHandler("ship", ship_command))
    app.add_handler(CommandHandler("roast", roast_command))
    app.add_handler(CommandHandler("compliment", compliment_command))
    app.add_handler(CommandHandler("vibecheck", vibecheck_command))
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("truth", truth_command))
    app.add_handler(CommandHandler("dare", dare_command))
    app.add_handler(CommandHandler("wouldyourather", would_you_rather_command))
    app.add_handler(CommandHandler("coinflip", coinflip_command))
    app.add_handler(CommandHandler("8ball", eightball_command))
    app.add_handler(CommandHandler("luck", luck_command))
    app.add_handler(CommandHandler("fateboard", fateboard_command))
    app.add_handler(CommandHandler("curse", curse_command))
    app.add_handler(CommandHandler("bless", bless_command))
    app.add_handler(CommandHandler("decide", decide_command))
    app.add_handler(CommandHandler("poll", poll_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("quote", quote_command))
    app.add_handler(CommandHandler("quotes", quotes_command))
    app.add_handler(CommandHandler("deletequote", deletequote_command))
    app.add_handler(CommandHandler("mvp", mvp_command))
    app.add_handler(CommandHandler("hot", hot_command))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()