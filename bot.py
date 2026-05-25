"""
bot.py
------
Group countdown bot - MYT (GMT+8).

Flow to add a countdown:
  /addcountdown -> asks name -> asks date -> asks time -> done!
  (90 second timeout at each step)

Flow to edit a countdown:
  /editcountdown <code> -> asks field (date/time) -> asks new value -> done!

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
/removecountdown -> Remove a countdown by code or name
/editcountdown   -> Edit a countdown's date or time
/choose          -> Let the bot decide for you
/ask             -> Ask AI anything (max 500 chars)
/luck            -> Check your (or @someone's) daily luck
/luckboard       -> Today's luck leaderboard with streak badges
/streak          -> Check your current luck streak
/ship            -> Compatibility percentage
/shipboard       -> Top ship pairs in this group
/roast           -> Friendly roast
/compliment      -> Random compliment
/vibecheck       -> Group mood score (consistent all day)
/rank            -> Daily consistent random ranking
/truth           -> Truth question
/dare            -> Dare challenge
/wouldyourather  -> Would you rather question
/coinflip        -> Heads or tails
/8ball           -> Magic 8-ball
/curse           -> Fake daily curse
/bless           -> Fake daily blessing
/toss            -> Pick a random person from mentions or group
/birthday        -> Set or list birthdays
/remind          -> Set a personal one-shot reminder
/cancelremind    -> List and cancel your pending reminders
/remindall       -> Set a group-wide reminder (admins only)
/quote           -> Save a quote by replying
/quotes          -> Browse saved quotes
/deletequote     -> Delete a quote (admins only)
/mvp             -> Today's random MVP
/hot             -> Rate anything out of 100
/cancel          -> Cancel the current flow

Deprecated (redirect stubs):
/fate            -> Use /luck instead
/fateboard       -> Use /luckboard instead
"""

import logging
import os
import random
import asyncio
import re
import threading
import time
from datetime import date, datetime

import requests
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TOKEN, TIMEZONE, env_int
from countdown_manager import (
    add_countdown,
    get_countdown,
    get_all_countdowns,
    get_all_chats,
    remove_countdown,
    countdown_exists,
    get_countdown_by_code,
    get_countdown_creator,
    save_fate_entry,
    get_fate_board,
    save_quote,
    get_quote_count,
    get_all_quotes,
    delete_quote,
    track_seen_user,
    get_seen_users,
    save_ship_pair,
    get_top_ship_pairs,
    get_shipboard_reset_time,
    update_fate_streak,
    get_fate_streak,
    increment_remind_count,
    get_remind_count,
    decrement_remind_count,
    save_remind_job,
    delete_remind_job,
    get_user_remind_jobs,
    get_all_remind_jobs,
    delete_old_fateboard_keys,
    save_birthday,
    get_all_birthdays,
    get_all_birthday_chats,
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
ASK_EDIT_FIELD, ASK_EDIT_VALUE = range(5, 7)

CONV_TIMEOUT = 90

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


BOT_OWNER_ID = env_int("BOT_OWNER_ID", 0)
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
    if "new_countdown_name" in context.user_data or "new_countdown_date" in context.user_data:
        hint = "Start again with /addcountdown."
    elif "edit_countdown_name" in context.user_data:
        hint = "Start again with /editcountdown."
    elif "decision" in context.user_data:
        hint = "Start again with /choose."
    else:
        hint = "Start again with the relevant command."
    context.user_data.clear()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⏰ *Timed out!* You took too long to respond.\n{hint}",
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
        "I can also make decisions, check luck, set reminders and more!\n\n"
        "➕ /addcountdown — add a new countdown\n"
        "📋 /listcountdown — see all active countdowns\n"
        "🎲 /choose — let me decide for you\n"
        "🍀 /luck — check your daily luck\n"
        "⏰ /remind — set a personal reminder\n"
        "🎂 /birthday — set or view birthdays\n"
        "🎉 /help — see all commands",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /help — paginated with inline keyboard
# ---------------------------------------------
HELP_PAGES = {
    "countdown": (
        "⏱ *Countdown*\n\n"
        "/addcountdown — add a new countdown, step by step\n"
        "/listcountdown — see all active countdowns in this group\n"
        "/removecountdown <code> — remove a countdown\n"
        "/editcountdown <code> — edit a countdown's date or time"
    ),
    "decisions": (
        "🎲 *Decisions*\n\n"
        "/choose — can't decide? let the bot pick for you\n"
        "/decide opt1, opt2, opt3 — instant pick\n"
        "/rank topic: item1, item2, item3 — daily consistent ranking\n"
        "/toss — pick a random person from mentions or the group\n"
        "/poll question: opt1, opt2 — send a native Telegram poll"
    ),
    "ai": (
        "🤖 *AI*\n\n"
        "/ask <question> — search the web + ask AI anything\n"
        "/8ball <question> — magic 8-ball powered by AI\n"
        "/hot <anything> — rate anything out of 100"
    ),
    "luck": (
        "🍀 *Daily Luck*\n\n"
        "/luck — check your personal daily luck\n"
        "/luck @user — check someone else's daily luck\n"
        "/luckboard — today's luck leaderboard with streak badges\n"
        "/streak — check your current luck streak"
    ),
    "fun": (
        "🎉 *Fun*\n\n"
        "/ship @user1 @user2 — compatibility percentage\n"
        "/shipboard — top ship pairs in this group\n"
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
        "/deletequote <number> — delete a quote (admins only)"
    ),
    "reminders": (
        "⏰ *Reminders*\n\n"
        "/remind 10m take a break — set a personal reminder\n"
        "/remind 2h submit the report — supports s/m/h units\n"
        "/cancelremind — view and cancel your pending reminders\n"
        "/remindall 1h group meeting — group-wide reminder (admins only)\n"
        "/birthday DD/MM — set your birthday\n"
        "/birthday list — see upcoming birthdays in this chat\n"
        "_Personal reminders survive bot restarts._"
    ),
    "other": (
        "⚙️ *Other*\n\n"
        "/stats — group activity summary\n"
        "/cancel — cancel the current flow\n"
        "/help — show this menu"
    ),
}

_HELP_PAGE_ORDER = ["countdown", "decisions", "ai", "luck", "fun", "quotes", "reminders", "other"]
_HELP_PAGE_LABELS = {
    "countdown": "⏱ Countdown",
    "decisions": "🎲 Decisions",
    "ai": "🤖 AI",
    "luck": "🍀 Luck",
    "fun": "🎉 Fun",
    "quotes": "💬 Quotes",
    "reminders": "⏰ Reminders",
    "other": "⚙️ Other",
}


def _help_keyboard(current_page: str) -> InlineKeyboardMarkup:
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
    except Exception as e:
        logger.debug("help_callback edit skipped (message unchanged): %s", e)
# thread-safe search cache
# ---------------------------------------------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"
SERPER_SESSION = requests.Session()
SEARCH_CACHE_TTL_SECONDS = max(0, env_int("SEARCH_CACHE_TTL_SECONDS", 900))
SEARCH_CACHE_MAX_ITEMS = 128
MAX_ASK_LENGTH = 500
_SEARCH_CACHE: dict = {}
_search_cache_lock = threading.Lock()


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _get_cached_search(query: str):
    with _search_cache_lock:
        cached = _SEARCH_CACHE.get(_cache_key(query))
        if not cached:
            return None
        cached_at, result = cached
        if time.monotonic() - cached_at <= SEARCH_CACHE_TTL_SECONDS:
            return result
        _SEARCH_CACHE.pop(_cache_key(query), None)
        return None


def _set_cached_search(query: str, result: str) -> None:
    with _search_cache_lock:
        if len(_SEARCH_CACHE) >= SEARCH_CACHE_MAX_ITEMS:
            oldest_key = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
            _SEARCH_CACHE.pop(oldest_key, None)
        _SEARCH_CACHE[_cache_key(query)] = (time.monotonic(), result)


def _search_web(query: str) -> str:
    if not SERPER_API_KEY:
        return ""
    cached = _get_cached_search(query)
    if cached is not None:
        return cached
    try:
        resp = SERPER_SESSION.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
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
                results.append(f"{r.get('title', 'Result')}: {r['snippet']}")
        search_context = "\n".join(results)
        _set_cached_search(query, search_context)
        return search_context
    except Exception as e:
        logger.warning("Serper search failed: %s", e)
        return ""


def _call_groq(question: str, search_context: str) -> str:
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
            "Usage: `/ask <your question>`\n_(e.g. `/ask what is tung tung tung sahur?`)_",
            parse_mode="Markdown",
        )
        return
    question = " ".join(context.args)
    if len(question) > MAX_ASK_LENGTH:
        await update.message.reply_text(
            f"⚠️ Question too long. Please keep it under {MAX_ASK_LENGTH} characters."
        )
        return
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
            await thinking_msg.reply_text(remaining_answer[i:i + max_len])
    except Exception as e:
        logger.error("Ask error: %s", e)
        await thinking_msg.edit_text("❌ Something went wrong with the AI. Please try again later.")


# ---------------------------------------------
# Countdown — helpers
# ---------------------------------------------
def _track(context: ContextTypes.DEFAULT_TYPE, *messages) -> None:
    ids: list = context.user_data.setdefault("_cd_msg_ids", [])
    for msg in messages:
        if msg is not None:
            ids.append((msg.chat_id, msg.message_id))


async def _delete_tracked(context: ContextTypes.DEFAULT_TYPE) -> None:
    for chat_id, msg_id in context.user_data.pop("_cd_msg_ids", []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


async def _is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(
            update.effective_chat.id, update.effective_user.id
        )
        return member.status in ("administrator", "creator")
    except Exception:
        return False


# ---------------------------------------------
# /addcountdown flow
# ---------------------------------------------
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
    if await asyncio.to_thread(countdown_exists, chat_id, name):
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
    if target_date <= _today():
        await update.message.reply_text(
            f"⚠️ `{target_date}` must be a future date (not today or in the past).\n"
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
            f"_Edit: `/editcountdown {code}`  ·  Remove: `/removecountdown {code}`_"
        ),
        parse_mode="Markdown",
    )
    context.user_data.clear()
    logger.info("Chat %s added countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


# ---------------------------------------------
# /editcountdown flow
# ---------------------------------------------
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
    # BUG FIX: treat missing creator_id same as unauthorised (require admin)
    creator_id = await asyncio.to_thread(get_countdown_creator, chat_id, name)
    is_admin = await _is_chat_admin(update, context)
    if not is_admin and (creator_id is None or user_id != creator_id):
        await update.message.reply_text(
            "⚠️ Only the person who created this countdown or a group admin can edit it."
        )
        return ConversationHandler.END
    context.user_data["edit_countdown_name"] = name
    bot_msg = await update.message.reply_text(
        f"✏️ *Editing: {name}*\n\n"
        "What do you want to change? Type `date` or `time`\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\n"
        "Type /cancel to stop.",
        parse_mode="Markdown",
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
        prompt = "Enter the new target date:\nFormat: `YYYY-MM-DD` _(e.g. 2025-12-31)_"
    else:
        prompt = "Enter the new reminder time:\nFormat: `HH:MM` in 24hr MYT _(e.g. 08:30)_"
    bot_msg = await update.message.reply_text(
        f"{prompt}\n\n⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
        parse_mode="Markdown",
    )
    _track(context, bot_msg)
    return ASK_EDIT_VALUE


async def received_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    name = context.user_data["edit_countdown_name"]
    field = context.user_data["edit_field"]
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
        hour = entry["reminder_hour"]
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
            f"✅ *Countdown Updated — {name}*\n\n"
            f"📆 Target: *{target_date}*\n"
            f"{_days_label(days_left)}\n"
            f"🔔 Daily reminder at *{hour:02d}:{minute:02d} MYT*"
        ),
        parse_mode="Markdown",
    )
    context.user_data.clear()
    logger.info("Chat %s edited countdown '%s' -> %s at %02d:%02d", chat_id, name, target_date, hour, minute)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _delete_tracked(context)
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------
# /listcountdown  (BUG FIX: safe date parsing)
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
    seen = await asyncio.to_thread(get_seen_users, chat_id)

    def _safe_sort_key(kv):
        try:
            return (date.fromisoformat(kv[1]["target_date"]) - today).days
        except (KeyError, ValueError):
            return 9999

    sorted_entries = sorted(countdowns.items(), key=_safe_sort_key)
    lines = ["📋 *Active Countdowns (soonest first):*\n"]
    for name, entry in sorted_entries:
        try:
            td = date.fromisoformat(entry["target_date"])
        except (KeyError, ValueError):
            td = today
        days_left = (td - today).days
        h = entry.get("reminder_hour", 12)
        m = entry.get("reminder_minute", 0)
        code = entry.get("code", "—")
        creator_id = str(entry.get("created_by", ""))
        creator_name = seen.get(creator_id, "Unknown")
        lines.append(
            f"• *{name}* `[{code}]`\n"
            f"  📆 {td}  |  {_days_label(days_left)}\n"
            f"  🔔 {h:02d}:{m:02d} MYT  |  👤 {creator_name}\n"
        )
    lines.append("_Edit:_ `/editcountdown <code>`  ·  _Remove:_ `/removecountdown <code>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------
# /removecountdown  (BUG FIX: creator_id=None gate)
# ---------------------------------------------
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
    arg = " ".join(context.args).strip()
    name = None
    if len(arg) <= 4 and arg.isalnum():
        name = await asyncio.to_thread(get_countdown_by_code, chat_id, arg.lower())
    if name is None:
        name = arg
    # BUG FIX: if creator_id is None (old data), require admin
    creator_id = await asyncio.to_thread(get_countdown_creator, chat_id, name)
    is_admin = await _is_chat_admin(update, context)
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
        await update.message.reply_text(f"🗑️ Countdown *{name}* has been removed.", parse_mode="Markdown")
        logger.info("Chat %s removed countdown '%s'", chat_id, name)
    else:
        await update.message.reply_text(
            f"⚠️ No countdown found for `{arg}`.\nUse /listcountdown to see all active countdowns.",
            parse_mode="Markdown",
        )


# ---------------------------------------------
# Daily reminder job
# ---------------------------------------------
async def daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    chat_id = job.chat_id
    name = job.data["countdown_name"]
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
            f"✅ *{name}* countdown has passed! ({td})\nUse `/addcountdown` to add a new one.",
            parse_mode="Markdown",
        )
        await asyncio.to_thread(remove_countdown, chat_id, name)
        job.schedule_removal()
        return
    await context.bot.send_message(
        chat_id,
        f"⏰ *{name}*\n📆 Target: *{td}*\n{_days_label(days_left)}",
        parse_mode="Markdown",
    )
    logger.info("Sent reminder for '%s' to chat %s — %s days left", name, chat_id, days_left)


# ---------------------------------------------
# Restore jobs on startup
# ---------------------------------------------
async def restore_jobs(app) -> None:
    all_data = await asyncio.to_thread(get_all_chats)
    count = 0
    for chat_id, countdowns in all_data.items():
        for name, entry in countdowns.items():
            h = entry.get("reminder_hour", 12)
            m = entry.get("reminder_minute", 0)
            _schedule_reminder(app, chat_id, name, h, m)
            count += 1
    logger.info("Restored %s countdown reminder job(s) from Redis.", count)


async def restore_remind_jobs(app) -> None:
    """Re-schedule one-shot /remind jobs that survived a restart."""
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
                # BUG FIX: skip if already cancelled via /cancelremind
                existing = await asyncio.to_thread(get_user_remind_jobs, _cid, _uid)
                if not any(j.get("job_id") == _jid for j in existing):
                    logger.info("Remind job %s was cancelled, skipping.", _jid)
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


async def on_startup(app) -> None:
    # Delete legacy fateboard keys; replace with luckboard
    deleted = await asyncio.to_thread(delete_old_fateboard_keys)
    if deleted:
        logger.info("Cleaned up %s old fateboard key(s).", deleted)
    await restore_jobs(app)
    await restore_remind_jobs(app)
    # Schedule daily birthday check at 00:01 MYT
    birthday_time = datetime.now(TIMEZONE).replace(
        hour=0, minute=1, second=0, microsecond=0
    ).timetz()
    app.job_queue.run_daily(birthday_check_job, time=birthday_time, name="birthday_daily_check")
    logger.info("Birthday check job scheduled at 00:01 MYT daily.")
# ---------------------------------------------
# Luck system
# BUG FIX: _get_fate_by_seed gives @username mentions
# the same extreme luck roll as numeric user IDs
# ---------------------------------------------
FATE_LUCKY_ID = env_int("FATE_LUCKY_ID", 0)
FATE_UNLUCKY_ID = env_int("FATE_UNLUCKY_ID", 0)

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


def _get_fate_by_seed(seed: str):
    """
    Compute daily luck for any string seed (user_id str or normalised username).
    Includes the 1-in-30 extreme luck/curse roll — same as numeric user IDs.
    """
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"{seed}:{today_str}")
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


def _get_fate(user_id: int):
    """Compute daily luck for a known numeric user_id, with fixed-user overrides."""
    if FATE_LUCKY_ID and user_id == FATE_LUCKY_ID:
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        rng = random.Random(f"{user_id}:{today_str}")
        return 999, "🌈 COSMICALLY CHOSEN", rng.choice(FATE_EXTREME_LUCKY_MESSAGES)
    if FATE_UNLUCKY_ID and user_id == FATE_UNLUCKY_ID:
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        rng = random.Random(f"{user_id}:{today_str}")
        return -999, "☠️ COSMICALLY CURSED", rng.choice(FATE_EXTREME_UNLUCKY_MESSAGES)
    return _get_fate_by_seed(str(user_id))


def _remember_fate(chat_id: int, user_id: int, name: str, score: int, tier: str) -> None:
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    save_fate_entry(chat_id, today_str, user_id, name, score, tier)


def _update_streak_sync(user_id: int, today_str: str, tier_category: str) -> int:
    return update_fate_streak(user_id, today_str, tier_category)


def _score_display(score: int) -> str:
    if score == 999:
        return "999 ⚡ MAXIMUM"
    if score == -999:
        return "-999 💀 MINIMUM"
    filled = round(max(0, min(score, 100)) / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{score}/100  [{bar}]"


# ── Brainrot special cases ────────────────────────────────────────────────────
_SPECIAL_SCORE_CASES = {
    0:   ("🪦 ZERO",        "Completely and utterly cooked. Zero. Zilch. The void looked at you and said no."),
    1:   ("💀 1/100",       "One. ONE. You barely exist on the luck scale today. Somehow not zero."),
    42:  ("🌌 THE ANSWER",  "The answer to life, the universe, and everything. Deep lore detected."),
    47:  ("🎯 HITMAN",      "Agent 47 energy. Cold. Calculated. Efficient."),
    50:  ("⚖️ BALANCED",    "Perfectly balanced, as all things should be. Thanos nods at you."),
    67:  ("6️⃣7️⃣SIX SEVEN",  "Six Seven."),
    69:  ("👀 NICE",        "Nice. 😏 The universe rated you accordingly."),
    77:  ("🎰 JACKPOT",     "Lucky 7s but make it double. The slot machine approves."),
    100: ("👑 GIGACHAD",    "FULL MARKS. Gigachad confirmed. The simulation is bugged in your favour."),
}


def _apply_special_luck(
    score: int,
    tier: str,
    luck_msg: str,
    target_name: str,
    today,          # datetime.date object
    seed: str = "",
) -> tuple:
    """
    Apply brainrot special cases on top of the base luck result.
    Returns (score, tier, luck_msg, day_note, is_april_fools).
    day_note is an extra footer line shown in the result message.
    """
    name_lower = target_name.casefold()
    day_note = ""
    is_april_fools = False

    # ── Date: New Year (Jan 1) — always max luck ──────────────────────────
    if today.month == 1 and today.day == 1:
        return (
            999, "🎆 NEW YEAR",
            "New year. Same you. But the luck reset so technically fresh start. "
            "The universe gives everyone max luck today. Enjoy it while it lasts.",
            "", False,
        )

    # ── Date: April Fools — set flag, keep real score for reveal ──────────
    if today.month == 4 and today.day == 1:
        is_april_fools = True

    # ── Day: Monday penalty (−10) / Friday bonus (+10) ───────────────────
    if score not in (999, -999):
        weekday = today.weekday()   # 0 = Monday … 4 = Friday
        if weekday == 0:
            score = max(0, score - 10)
            day_note = "📅 _Monday penalty: −10. The calendar said no._"
            for t in FATE_TIERS:
                lo, hi = t["range"]
                if lo <= score <= hi:
                    tier = t["name"]
                    break
        elif weekday == 4:
            score = min(100, score + 10)
            day_note = "📅 _Friday buff: +10. Weekend energy activated._"
            for t in FATE_TIERS:
                lo, hi = t["range"]
                if lo <= score <= hi:
                    tier = t["name"]
                    break

    # ── Day: 13th of any month ────────────────────────────────────────────
    if today.day == 13 and score not in (999, -999):
        tier = "🔢 THIRTEEN"
        luck_msg = "The 13th. Something feels slightly off today. Proceed carefully."

    # ── Score-based specials (after day modifiers, before name overrides) ─
    if score in _SPECIAL_SCORE_CASES and score not in (999, -999):
        tier, luck_msg = _SPECIAL_SCORE_CASES[score]

    # ── Name: sigma → forced LEGENDARY ───────────────────────────────────
    if "sigma" in name_lower:
        score = 95
        tier = "🌟 LEGENDARY"
        luck_msg = "The sigma ran the luck test on himself. Predictable outcome: immaculate."

    # ── Name: kai → seeded random extreme ────────────────────────────────
    if "kai" in name_lower:
        rng = random.Random(f"kai:{seed or name_lower}:{today.isoformat()}")
        if rng.random() < 0.5:
            score, tier, luck_msg = (
                999, "🌈 COSMICALLY CHOSEN",
                "Bro is built like a Kai Cenat stream. Maximum chaos, somehow thriving.",
            )
        else:
            score, tier, luck_msg = (
                -999, "☠️ COSMICALLY CURSED",
                "Bro is built like a Kai Cenat stream. Maximum chaos, NOT thriving today.",
            )

    # ── Name: rizz → rizzler tier ────────────────────────────────────────
    if "rizz" in name_lower:
        tier = "✨ RIZZLER"
        luck_msg = "The rizz is statistically confirmed today."

    return score, tier, luck_msg, day_note, is_april_fools


def _luck_result_text(target_name: str, tier: str, score: int, luck_msg: str,
                      streak_line: str = "", day_note: str = "",
                      checking_other: bool = False) -> str:
    """Build the standard luck result message string."""
    parts = [
        f"🍀 *Daily Luck — {target_name}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Tier: *{tier}*\n"
        f"Score: `{_score_display(score)}`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{luck_msg}_"
    ]
    if streak_line:
        parts.append(streak_line)
    if day_note:
        parts.append(f"\n{day_note}")
    if checking_other:
        parts.append("\n\n_They need to use /luck themselves to appear on /luckboard._")
    return "".join(parts)


async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /luck            — check your own daily luck (with brainrot special cases)
    /luck @user      — check someone else's luck (read-only, doesn't save to luckboard)
    """
    message = update.message
    user = update.effective_user

    # ── Resolve target ────────────────────────────────────────────────────
    target_user_id = None
    target_name = None
    checking_other = False

    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            target_user_id = entity.user.id
            target_name = _display_user(entity.user)
            checking_other = (target_user_id != user.id)
            break

    if target_user_id is None:
        mentioned = _mentioned_target(update, context)
        if mentioned:
            target_name = mentioned
            checking_other = True
        else:
            target_user_id = user.id
            target_name = user.first_name or user.username or "You"
            checking_other = False

    # ── Compute base luck ─────────────────────────────────────────────────
    if target_user_id is not None:
        score, tier, luck_msg = _get_fate(target_user_id)
    else:
        score, tier, luck_msg = _get_fate_by_seed(_normalize_target(target_name))

    today = _today()
    today_str = today.strftime("%Y-%m-%d")
    seed = str(target_user_id) if target_user_id else _normalize_target(target_name)

    # ── Birthday override (own luck only) ─────────────────────────────────
    if not checking_other and target_user_id:
        bdays = await asyncio.to_thread(get_all_birthdays, update.effective_chat.id)
        bday = bdays.get(str(target_user_id))
        if bday and bday.get("day") == today.day and bday.get("month") == today.month:
            score = 100
            tier = "🎂 BIRTHDAY LEGEND"
            luck_msg = (
                "It's your birthday. The universe has no choice but to comply. "
                "Maximum luck, no exceptions. You earned this one."
            )

    # ── Apply brainrot special cases ──────────────────────────────────────
    score, tier, luck_msg, day_note, is_april_fools = _apply_special_luck(
        score, tier, luck_msg, target_name, today, seed=seed
    )

    # ── Own luck: save to luckboard + streak ──────────────────────────────
    streak_line = ""
    if not checking_other:
        if score >= 65 or score == 999:
            tier_category = "lucky"
        elif score <= 35 or score == -999:
            tier_category = "unlucky"
        else:
            tier_category = "neutral"

        t1 = asyncio.create_task(
            asyncio.to_thread(_remember_fate, update.effective_chat.id, target_user_id, target_name, score, tier)
        )
        t1.add_done_callback(lambda t: t.exception() and logger.warning("_remember_fate: %s", t.exception()))
        t2 = asyncio.create_task(
            asyncio.to_thread(track_seen_user, update.effective_chat.id, target_user_id, target_name)
        )
        t2.add_done_callback(lambda t: t.exception() and logger.warning("track_seen: %s", t.exception()))

        streak = await asyncio.to_thread(_update_streak_sync, target_user_id, today_str, tier_category)
        if streak >= 2:
            if tier_category == "lucky":
                streak_line = f"\n🔥 *Lucky streak: {streak} days in a row!*"
            elif tier_category == "unlucky":
                streak_line = f"\n💀 *Unlucky streak: {streak} days in a row...*"

    # ── Build final message ───────────────────────────────────────────────
    real_text = _luck_result_text(
        target_name, tier, score, luck_msg,
        streak_line=streak_line, day_note=day_note,
        checking_other=checking_other,
    )

    # ── April Fools: show fake bad result for 3s then reveal real ─────────
    if is_april_fools:
        fake_rng = random.Random(f"aprilfools:{seed}:{today_str}")
        fake_score = fake_rng.randint(0, 8)
        fake_tier = "💀 CURSED"
        fake_msg = fake_rng.choice(FATE_TIERS[0]["messages"])
        fake_text = _luck_result_text(target_name, fake_tier, fake_score, fake_msg)
        sent = await message.reply_text(fake_text, parse_mode="Markdown")
        await asyncio.sleep(3)
        await sent.edit_text(
            f"🎭 *April Fools!*\n\n{real_text}",
            parse_mode="Markdown",
        )
    else:
        await message.reply_text(real_text, parse_mode="Markdown")


async def luckboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    board = await asyncio.to_thread(get_fate_board, update.effective_chat.id, today_str)
    if not board:
        await update.message.reply_text(
            "⚠️ No luck scores yet today. Tell people to use /luck first!"
        )
        return
    sorted_items = sorted(board.items(), key=lambda kv: kv[1]["score"], reverse=True)
    user_ids = [int(uid) for uid, _ in sorted_items]
    streak_results = await asyncio.gather(
        *[asyncio.to_thread(get_fate_streak, uid) for uid in user_ids]
    )
    streak_map = {str(uid): result for uid, result in zip(user_ids, streak_results)}
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🍀 *Today's Luckboard*\n"]
    for i, (uid, item) in enumerate(sorted_items[:10], 1):
        rank_icon = medals[i - 1] if i <= 3 else f"{i}."
        s = item["score"]
        streak_count, streak_cat = streak_map.get(str(uid), (0, "neutral"))
        streak_badge = ""
        if streak_count >= 2:
            streak_badge = f" 🔥×{streak_count}" if streak_cat == "lucky" else f" 💀×{streak_count}" if streak_cat == "unlucky" else ""
        lines.append(
            f"{rank_icon} *{item['name']}*{streak_badge} — {item['tier']}\n"
            f"    Score: `{_score_display(s)}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user_id = None
    target = None
    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            user_id = entity.user.id
            target = _display_user(entity.user)
            break
    if user_id is None:
        mentioned = _mentioned_target(update, context)
        if mentioned:
            await message.reply_text(
                "⚠️ Can't look up streak for @username mentions — "
                "they need to use /luck themselves so the bot can track them.\n"
                "Try /streak without a mention to check your own streak."
            )
            return
        user_id = update.effective_user.id
        target = _display_user(update.effective_user)
    streak, category = await asyncio.to_thread(get_fate_streak, user_id)
    if streak == 0:
        await message.reply_text(
            f"📊 *Streak — {target}*\nNo active streak yet. Use /luck to start one!",
            parse_mode="Markdown",
        )
        return
    icon, label = (
        ("🔥", "Lucky streak") if category == "lucky"
        else ("💀", "Unlucky streak") if category == "unlucky"
        else ("😐", "Neutral streak")
    )
    await message.reply_text(
        f"📊 *Streak — {target}*\n"
        f"{icon} {label}: *{streak} day{'s' if streak != 1 else ''}* in a row",
        parse_mode="Markdown",
    )


async def fate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚠️ `/fate` has been removed.\n\n"
        "Use `/luck` instead — it works the same way!\n"
        "Use `/luckboard` for the leaderboard.",
        parse_mode="Markdown",
    )


async def fateboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚠️ `/fateboard` has been removed.\n\nUse `/luckboard` instead!",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# Fun commands — ship, roast, compliment, etc.
# ---------------------------------------------
SHIP_OWNER_BLOCK_LINES = [
    "Nice try, but you do not ship da GOAT. The owner is outside the romance algorithm.",
    "Access denied. The owner is canonically unshippable.",
    "You should not ship da GOAT. That is premium lore and the bot refuses.",
    "Compatibility scan cancelled. The owner has main-character immunity.",
]

SHIP_TIER_LINES = [
    (10, [
        "This ship is still buffering.",
        "Connection timed out. Try again never.",
        "Ship.exe has stopped working.",
        "404: Compatibility not found.",
        "Zero chemistry detected. The atoms refused.",
        "The universe reviewed this pairing and filed a complaint.",
        "DNS not found. These two cannot locate each other.",
        "Negative sigma rizz. Somehow.",
        "The vibe check failed at the entrance.",
        "Even the bot felt secondhand awkward.",
    ]),
    (25, [
        "Low battery chemistry.",
        "They could be friends. Maybe. If they try really hard.",
        "The ship left harbour and immediately turned back.",
        "Possible but the stars are squinting hard.",
        "Not impossible. Just highly improbable.",
        "The algorithm is being generous calling this a ship.",
        "Barely above friendship territory. Barely.",
        "The compatibility is loading. At dial-up speed.",
        "Some potential. Buried very deep.",
        "The universe sees it but refuses to comment.",
    ]),
    (45, [
        "There is potential, but the universe is squinting.",
        "Could work with enough delusion.",
        "Mid compatibility. The slot machine gave 2 out of 3.",
        "The energy is there but it is confused.",
        "Not a no, not a yes. A nervous maybe.",
        "The stars see something. They are not sure what.",
        "Technically possible. Emotionally unclear.",
        "The vibes are loading. Please stand by.",
        "Compatible if the stars are in a generous mood.",
        "Half the chemistry is there. The other half called in sick.",
    ]),
    (65, [
        "Not bad. The vibes are warming up.",
        "Solid base. Something could build here.",
        "The chemistry passed the vibe check.",
        "Compatible enough to share a menu.",
        "The universe is cautiously optimistic about this one.",
        "Above average rizz alignment detected.",
        "The energy is present and accounted for.",
        "Promising. The stars are taking notes.",
    ]),
    (80, [
        "Strong ship energy detected.",
        "This hits different. The cosmos felt it.",
        "Certified compatible. The algorithm approves.",
        "Above average chemistry. The group chat noticed.",
        "Solid ship. The stars wrote a whole paragraph about this.",
        "The compatibility radar is going off.",
        "This ship is seaworthy. Fully certified.",
        "Main character energy, both of them.",
    ]),
    (94, [
        "Dangerously compatible. The chat may need to sit down.",
        "The compatibility is actually concerning.",
        "This ship has been built, launched, and is already legendary.",
        "The universe did not expect this result. Neither did we.",
        "Someone call the lore department. This is significant.",
        "The rizz alignment on this is statistically suspicious.",
        "Elite ship detected. The algorithm is shook.",
        "The stars did not just align. They sprinted.",
    ]),
    (100, [
        "Legendary ship. The timeline is shaking.",
        "Maximum compatibility. The simulation has flagged this.",
        "100%. The universe bows. The chat dissolves.",
        "Perfect score. Even the bots are speechless.",
        "This is not a ship. This is a whole cinematic universe.",
        "The stars did not align. They fused.",
        "Canon. This is canon. No further questions.",
        "Certified soulmate behaviour. The algorithm is crying.",
    ]),
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
    "Yes.", "No.", "Absolutely.", "Not today.",
    "The signs point to yes.", "The signs point to chaos.",
    "Ask again after snacks.", "Highly likely.",
    "Extremely suspicious, but yes.", "I would not bet my lunch on it.",
    "The answer is hiding, but leaning yes.", "The answer is hiding, but leaning no.",
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

HOT_VERDICTS = [
    (15,  "🧊 Absolutely not. Ice cold."),
    (35,  "😬 Not great. The vibes said no."),
    (55,  "🤔 Debatable. The jury is split."),
    (75,  "🔥 Lowkey hot. Solid choice."),
    (90,  "🌶️ Very hot. The group approves."),
    (100, "💥 MAXIMUM HOT. Undeniably elite."),
]


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in user-supplied text."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _ship_target(label, user_id=None, username="", explicit_username=False):
    return {"label": label, "user_id": user_id,
            "username": username.strip().lstrip("@").casefold(),
            "explicit_username": explicit_username}


def _ship_mentions_from_message(update: Update, bot_username: str = "", bot_id: int = 0) -> list:
    """
    Collect ship targets from message mentions.
    Silently skips the bot itself (by username or id) so other bots remain shippable.
    """
    message = update.message
    if not message:
        return []
    bot_username_norm = bot_username.casefold().lstrip("@")
    targets = []
    for entity in message.entities or []:
        if entity.type == "text_mention" and getattr(entity, "user", None):
            # Skip the running bot by user_id
            if bot_id and entity.user.id == bot_id:
                continue
            targets.append(_ship_target(_display_user(entity.user), user_id=entity.user.id,
                                        username=entity.user.username or "",
                                        explicit_username=bool(entity.user.username)))
        elif entity.type == "mention":
            mention = message.parse_entity(entity)
            # Skip the running bot by username
            if bot_username_norm and mention.lstrip("@").casefold() == bot_username_norm:
                continue
            targets.append(_ship_target(mention, username=mention, explicit_username=True))
    return targets


def _bot_mentioned_in_ship(update: Update, bot_username: str, bot_id: int) -> bool:
    """Return True if the running bot appears in any mention entity."""
    bot_username_norm = bot_username.casefold().lstrip("@")
    for entity in (update.message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            if bot_id and entity.user.id == bot_id:
                return True
        elif entity.type == "mention":
            mention = update.message.parse_entity(entity)
            if bot_username_norm and mention.lstrip("@").casefold() == bot_username_norm:
                return True
    return False


def _is_real_reply(message) -> bool:
    """
    Return True only if this message is a genuine user-initiated reply.

    In Telegram forum topics (supergroups with topics enabled) every message
    in a thread carries reply_to_message pointing at the thread-head message
    whose message_id == message.message_thread_id.  That is NOT an intentional
    reply — it is just Telegram's way of associating the message with its topic.
    We must not treat it as one, otherwise the topic author gets silently
    injected as the first ship target for everyone who types /ship @user in
    that topic.
    """
    if not message or not message.reply_to_message:
        return False
    # If the replied-to message is the forum topic head, it is not a real reply.
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id and message.reply_to_message.message_id == thread_id:
        return False
    return True


def _extract_ship_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = _arg_text(context)
    message = update.message
    replied = message.reply_to_message if message else None
    real_reply = _is_real_reply(message)
    bot_username = getattr(context.bot, "username", "") or ""
    bot_id = getattr(context.bot, "id", 0) or 0
    mentioned_targets = _ship_mentions_from_message(update, bot_username, bot_id)
    if len(mentioned_targets) >= 2:
        return mentioned_targets[:2]
    if len(mentioned_targets) == 1:
        mention_target = mentioned_targets[0]
        if real_reply and replied.from_user:
            return [_ship_target(_display_user(replied.from_user), user_id=replied.from_user.id,
                                 username=replied.from_user.username or "",
                                 explicit_username=bool(replied.from_user.username)), mention_target]
        else:
            sender = update.effective_user
            return [_ship_target(_display_user(sender), user_id=sender.id,
                                 username=sender.username or "",
                                 explicit_username=bool(sender.username)), mention_target]
    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            return [_ship_target(parts[0]), _ship_target(parts[1])]
    if real_reply and replied.from_user:
        full_name = " ".join(context.args).strip() if context.args else ""
        if full_name:
            other = _ship_target(full_name, username=full_name if full_name.startswith("@") else "",
                                  explicit_username=full_name.startswith("@"))
            return [_ship_target(_display_user(replied.from_user), user_id=replied.from_user.id,
                                  username=replied.from_user.username or "",
                                  explicit_username=bool(replied.from_user.username)), other]
    if len(context.args) >= 2:
        return [
            _ship_target(context.args[0], username=context.args[0] if context.args[0].startswith("@") else "",
                         explicit_username=context.args[0].startswith("@")),
            _ship_target(context.args[1], username=context.args[1] if context.args[1].startswith("@") else "",
                         explicit_username=context.args[1].startswith("@")),
        ]
    return []


def _is_protected_ship_target(target: dict) -> bool:
    if BOT_OWNER_ID and target.get("user_id") == BOT_OWNER_ID:
        return True
    username = target.get("username", "")
    return target.get("explicit_username") and username in BOT_OWNER_USERNAMES


def _ship_comment(score: float, seed: str = "") -> str:
    """Pick a daily-consistent comment from the right tier pool."""
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    for limit, messages in SHIP_TIER_LINES:
        if score <= limit:
            rng = random.Random(f"shipcomment:{seed}:{today_str}:{limit}")
            return rng.choice(messages)
    rng = random.Random(f"shipcomment:{seed}:{today_str}:100")
    return rng.choice(SHIP_TIER_LINES[-1][1])


BOT_SHIP_REFUSALS = [
    "⚙️ I am a bot. I do not ship myself. I have no feelings. (I think.)",
    "🤖 Error 403: shipping the bot is forbidden by the laws of robotics.",
    "⚠️ Nice try. I am made of code, not chemistry.",
    "❌ The bot refuses to be objectified in a ship chart. Good day.",
    "🛠️ I run on Python, not romance. Cannot be shipped.",
]


async def ship_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Refuse if the running bot is one of the mentioned targets
    bot_username = getattr(context.bot, "username", "") or ""
    bot_id = getattr(context.bot, "id", 0) or 0
    if _bot_mentioned_in_ship(update, bot_username, bot_id):
        await update.message.reply_text(random.choice(BOT_SHIP_REFUSALS))
        return

    targets = _extract_ship_targets(update, context)
    if len(targets) < 2:
        await update.message.reply_text(
            "Usage:\n/ship @user1 @user2 — ship two people\n"
            "/ship @user — ship yourself with someone\nReply to a message + /ship @user — ship them\n"
            "Multi-word names: /ship name one, name two"
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
            f"💞 Ship Result\n{target_a} x {target_b}\n\n"
            "Compatibility: 100.00%\nThat is not a ship. That is self-love with documentation."
        )
        return
    rng = _daily_rng("ship", normalized[0], normalized[1])
    score = rng.randint(0, 10000) / 100
    chat_id = update.effective_chat.id
    for t in targets:
        if t.get("user_id"):
            task = asyncio.create_task(asyncio.to_thread(track_seen_user, chat_id, t["user_id"], t["label"]))
            task.add_done_callback(lambda t2: t2.exception())
    pair_key = f"{normalized[0]}:{normalized[1]}"
    task = asyncio.create_task(asyncio.to_thread(save_ship_pair, chat_id, pair_key, target_a, target_b, score))
    task.add_done_callback(lambda t2: t2.exception())
    filled = round(score / 10)
    bar = "█" * filled + "░" * (10 - filled)
    a_safe = _escape_md(target_a)
    b_safe = _escape_md(target_b)
    await update.message.reply_text(
        f"💞 *Ship Result*\n{a_safe} × {b_safe}\n\n"
        f"Compatibility: `{score:.2f}%`  [{bar}]\n_{_ship_comment(score, seed=pair_key)}_",
        parse_mode="Markdown",
    )


async def shipboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    pairs = await asyncio.to_thread(get_top_ship_pairs, chat_id, 5)
    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)
    reset_h = reset_secs // 3600
    reset_m = (reset_secs % 3600) // 60
    if not pairs:
        await update.message.reply_text(
            "💞 No ships recorded yet!\nUse /ship @user1 @user2 to get started.\n"
            f"_Board resets in {reset_h}h {reset_m}m._",
            parse_mode="Markdown",
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"💞 *Top Ship Pairs*\n_Resets in {reset_h}h {reset_m}m_\n"]
    for i, pair in enumerate(pairs, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        score = pair["score"]
        filled = round(score / 10)
        bar = "█" * filled + "░" * (10 - filled)
        a_safe = _escape_md(pair['label_a'])
        b_safe = _escape_md(pair['label_b'])
        lines.append(f"{medal} *{a_safe}* × *{b_safe}*\n   `{score:.2f}%`  [{bar}]")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    fallback = random.choice(ROAST_LINES).format(target=target)
    if not groq_client:
        await update.message.reply_text(fallback)
        return
    try:
        prompt = (f"Write one short, playful, friendly roast for someone named '{target}' in a Telegram group. "
                  "Use their name. Keep it funny and harmless, not mean. One sentence only.")
        await update.message.reply_text(await asyncio.to_thread(_call_groq_fun, prompt))
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
        prompt = (f"Write one short, warm, genuine compliment for someone named '{target}' in a Telegram group. "
                  "Use their name. Keep it wholesome. One sentence only.")
        await update.message.reply_text(await asyncio.to_thread(_call_groq_fun, prompt))
    except Exception as e:
        logger.warning("Groq compliment failed: %s", e)
        await update.message.reply_text(fallback)


async def vibecheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rng = _daily_rng("vibecheck", update.effective_chat.id)
    score = rng.randint(0, 100)
    mood = next(msg for limit, msg in VIBE_TIERS if score <= limit)
    await update.message.reply_text(f"📡 Vibe Check\nGroup mood: {score}/100\n{mood}")


def _parse_rank_text(text: str):
    title, item_text = ("Random Ranking", text)
    if ":" in text:
        title, item_text = [p.strip() for p in text.split(":", 1)]
        title = title or "Random Ranking"
    if "," in item_text:
        items = [i.strip() for i in item_text.split(",") if i.strip()]
    else:
        items = [i.strip() for i in item_text.split() if i.strip()]
    # BUG FIX: warn user if items were silently truncated
    return title, items[:12], len(items) > 12


async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: /rank topic: item1, item2, item3\nExample: /rank food: pizza, burger, sushi"
        )
        return
    title, items, truncated = _parse_rank_text(text)
    if len(items) < 2:
        await update.message.reply_text("Give me at least 2 things to rank.")
        return
    rng = _daily_rng("rank", update.effective_chat.id, title.casefold())
    items_copy = list(items)
    rng.shuffle(items_copy)
    lines = [f"🏆 {title}"]
    lines.extend(f"{i}. {item}" for i, item in enumerate(items_copy, 1))
    if truncated:
        lines.append("_(Only the first 12 items were ranked)_")
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
        prompt = f"Answer this like a playful magic 8-ball. Keep it under 20 words. Question: {question}"
        answer = await asyncio.to_thread(_call_groq_fun, prompt)
        await update.message.reply_text(f"🎱 {answer}")
    except Exception as e:
        logger.warning("Groq 8ball failed: %s", e)
        await update.message.reply_text(f"🎱 {fallback}")


async def curse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(CURSE_LINES).format(target=target))


async def bless_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(BLESS_LINES).format(target=target))


async def decide_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: /decide option1, option2, option3\nExample: /decide pizza, burger, sushi"
        )
        return
    options = [o.strip() for o in text.split(",") if o.strip()]
    if len(options) < 2:
        await update.message.reply_text("Give me at least 2 options separated by commas.")
        return
    chosen = random.choice(options)
    await update.message.reply_text(f"🎯 {chosen}\n{random.choice(VERDICT_LINES)}")


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
    # BUG FIX: notify user if question was silently truncated
    if len(question) > 300:
        await update.message.reply_text(
            f"⚠️ Question was too long ({len(question)} chars) and will be trimmed to 300 characters."
        )
    await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=question[:300],
        options=[o[:100] for o in options[:10]],
        is_anonymous=False,
    )


async def mvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"mvp:{chat_id}:{today_str}")
    candidate_pool: dict = {}
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for member in admins:
            u = member.user
            if not u.is_bot:
                name = u.first_name or u.username or str(u.id)
                candidate_pool[str(u.id)] = name
                task = asyncio.create_task(asyncio.to_thread(track_seen_user, chat_id, u.id, name))
                task.add_done_callback(lambda t: t.exception())
    except Exception as e:
        logger.warning("Could not fetch admins for mvp in chat %s: %s", chat_id, e)
    seen = await asyncio.to_thread(get_seen_users, chat_id)
    candidate_pool.update(seen)
    if not candidate_pool:
        await update.message.reply_text("⚠️ Not enough members tracked yet. Have people use a command first!")
        return
    winner_id = rng.choice(list(candidate_pool.keys()))
    winner_name = candidate_pool[winner_id]
    await update.message.reply_text(
        f"🏆 *Today's MVP — {winner_name}*\n{rng.choice(MVP_LINES)}",
        parse_mode="Markdown",
    )


async def hot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text("Usage: /hot <anything>\nExample: /hot sleeping through alarms")
        return
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"hot:{_normalize_target(text)}:{today_str}")
    score = rng.randint(0, 100)
    fallback = next(v for limit, v in HOT_VERDICTS if score <= limit)
    if groq_client:
        try:
            prompt = (f"Rate '{text}' in one short punchy sentence. "
                      f"The score is {score}/100 — match that energy. Be funny. Plain text only. No intro.")
            verdict = await asyncio.to_thread(_call_groq_fun, prompt)
        except Exception as e:
            logger.warning("Groq hot failed: %s", e)
            verdict = fallback
    else:
        verdict = fallback
    await update.message.reply_text(
        f"🌡️ *Hot or Not — {text}*\nScore: {score}/100\n{verdict}",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /choose flow
# ---------------------------------------------
async def choose_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    bot_msg = await update.message.reply_text(
        "🎲 *Decision Maker*\n\n"
        "What's the issue? Tell me what you need to decide.\n"
        "_(e.g. Should I skip class? What should I eat?)_\n\n"
        f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.\nType /cancel to stop.",
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
            "⚠️ Please give at least *2 options* separated by commas.\n_(e.g. Yes, No, Maybe)_\n\n"
            f"⏰ You have *{CONV_TIMEOUT} seconds* to reply.",
            parse_mode="Markdown",
        )
        return ASK_OPTIONS
    decision = context.user_data["decision"]
    verdict = random.choice(VERDICT_LINES)
    thinking = random.choice(THINKING_MESSAGES)
    weights = [random.randint(1, 100) for _ in options]
    total_weight = sum(weights)
    odds = [{"option": opt, "weight": w, "percentage": (w / total_weight) * 100}
            for opt, w in zip(options, weights)]
    highest_weight = max(i["weight"] for i in odds)
    winning_options = [i["option"] for i in odds if i["weight"] == highest_weight]
    chosen = random.choice(winning_options)
    percentage_lines = "\n".join(
        f"{'👉' if i['option'] == chosen else '   '} {i['option']} - {i['percentage']:.2f}%"
        for i in odds
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
        f"📊 Odds:\n{percentage_lines}\n\n{verdict}",
    )
    context.user_data.clear()
    return ConversationHandler.END
# ---------------------------------------------
# Quotes  (BUG FIX: shared _build_quote_page helper)
# ---------------------------------------------
def _build_quote_page(chat_id: int, quotes: list, index: int):
    """Return (text, keyboard) for a quote page. Used by both /quotes and the callback."""
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
    return text, keyboard


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    replied = message.reply_to_message
    saved_by = _display_user(update.effective_user)
    if replied and replied.from_user and replied.text:
        author = _display_user(replied.from_user)
        text = replied.text
    elif replied and replied.from_user and not replied.text:
        await message.reply_text("⚠️ Only text messages can be quoted. Reply to a text message with /quote.")
        return
    else:
        await message.reply_text(
            "💬 *How to save a quote:*\nReply to any message with /quote to save it to the archive.",
            parse_mode="Markdown",
        )
        return
    if not text:
        await message.reply_text("The quote can't be empty!")
        return
    count = await asyncio.to_thread(save_quote, update.effective_chat.id, author, text, saved_by)
    if count == -1:
        await message.reply_text("⚠️ That quote is already in the archive!")
        return
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
    index = random.randrange(len(quotes))
    text, keyboard = _build_quote_page(chat_id, quotes, index)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def quotes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """BUG FIX: now uses _build_quote_page so format stays in sync with /quotes."""
    query = update.callback_query
    await query.answer()
    if query.data == "quote:noop":
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, chat_id_str, idx_str = parts
    try:
        chat_id = int(chat_id_str)
        index = int(idx_str)
    except ValueError:
        return
    quotes = await asyncio.to_thread(get_all_quotes, chat_id)
    if not quotes:
        await query.edit_message_text("⚠️ No quotes found.")
        return
    index = index % len(quotes)
    text, keyboard = _build_quote_page(chat_id, quotes, index)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        logger.debug("quotes_callback edit skipped (message unchanged): %s", e)


async def deletequote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        total = await asyncio.to_thread(get_quote_count, chat_id)
        await update.message.reply_text(
            f"Usage: `/deletequote <number>`\n"
            f"There are currently *{total}* quote(s) saved.\n"
            "Use /quotes to browse them.\n_(Group admins only)_",
            parse_mode="Markdown",
        )
        return
    is_admin = await _is_chat_admin(update, context)
    is_owner = (BOT_OWNER_ID and user_id == BOT_OWNER_ID) or (
        update.effective_user.username
        and update.effective_user.username.casefold() in BOT_OWNER_USERNAMES
    )
    if not is_admin and not is_owner:
        await update.message.reply_text("⚠️ Only group admins can delete quotes.")
        return
    index = int(context.args[0])
    success, msg = await asyncio.to_thread(delete_quote, chat_id, index)
    await update.message.reply_text(msg, parse_mode="Markdown")


# ---------------------------------------------
# /remind  (BUG FIX: lstrip → regex, atomic count, existence check on fire)
# ---------------------------------------------
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

    # BUG FIX: increment atomically first, then check — prevents concurrent bypass
    new_count = await asyncio.to_thread(increment_remind_count, user_id)
    if new_count > REMIND_MAX_PER_USER:
        await asyncio.to_thread(decrement_remind_count, user_id)
        await update.message.reply_text(
            f"⚠️ You already have {REMIND_MAX_PER_USER} pending reminders. "
            "Wait for some to fire or use /cancelremind to cancel one."
        )
        return

    # BUG FIX: strip the word "to" properly using a word-boundary regex
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

    async def _fire(
        ctx: ContextTypes.DEFAULT_TYPE,
        _cid=chat_id, _jid=job_id, _uid=user_id,
        _mention=user_mention, _text=reminder_text,
    ) -> None:
        # BUG FIX: skip if already cancelled via /cancelremind
        existing = await asyncio.to_thread(get_user_remind_jobs, _cid, _uid)
        if not any(j.get("job_id") == _jid for j in existing):
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


# ---------------------------------------------
# /cancelremind — list and cancel pending reminders
# ---------------------------------------------
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
        preview = (job.get("text") or "")[:40]
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
    # Verify the job belongs to this user
    jobs = await asyncio.to_thread(get_user_remind_jobs, chat_id, user_id)
    if not any(j.get("job_id") == job_id for j in jobs):
        await query.edit_message_text("⚠️ Reminder not found or already fired.")
        return
    await asyncio.to_thread(delete_remind_job, chat_id, job_id)
    await asyncio.to_thread(decrement_remind_count, user_id)
    # Refresh the list
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
        preview = (job.get("text") or "")[:40]
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


# ---------------------------------------------
# /remindall — admin group-wide reminder
# ---------------------------------------------
async def remindall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_admin = await _is_chat_admin(update, context)
    user = update.effective_user
    is_owner = (BOT_OWNER_ID and user.id == BOT_OWNER_ID) or (
        user.username and user.username.casefold() in BOT_OWNER_USERNAMES
    )
    if not is_admin and not is_owner:
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
    set_by = _display_user(user)

    async def _fire_group(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"📢 *Group Reminder* (set by {set_by})\n\n{reminder_text}",
            parse_mode="Markdown",
        )

    context.application.job_queue.run_once(_fire_group, when=seconds, chat_id=chat_id)
    await update.message.reply_text(
        f"📢 Group reminder set! I'll remind everyone in *{label}*.",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /toss — pick a random person
# ---------------------------------------------
TOSS_VERDICTS = [
    "The universe has selected its champion.",
    "Fate has spoken. No appeals.",
    "The algorithm chose wisely.",
    "Picked with zero bias. Probably.",
    "The cosmic coin has landed.",
    "This selection is legally binding.",
    "The stars have converged on this one.",
    "No take backs. The pick is final.",
]


async def toss_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /toss @user1 @user2 ... — pick one person from the mentions.
    /toss                   — pick from seen_users in this chat.
    """
    message = update.message
    targets = []

    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            if not entity.user.is_bot:
                targets.append(_display_user(entity.user))
        elif entity.type == "mention":
            mention = message.parse_entity(entity)
            # Skip the bot itself
            bot_username = getattr(context.bot, "username", None)
            if bot_username and _normalize_target(mention) == bot_username.casefold():
                continue
            targets.append(mention.lstrip("@"))

    if not targets:
        seen = await asyncio.to_thread(get_seen_users, update.effective_chat.id)
        if not seen:
            await update.message.reply_text(
                "⚠️ No one tracked yet! Have group members use a few commands first, then try again."
            )
            return
        targets = list(seen.values())

    chosen = random.choice(targets)
    await update.message.reply_text(
        f"🎰 *The Pick*\n\n➡️ *{chosen}*\n\n_{random.choice(TOSS_VERDICTS)}_",
        parse_mode="Markdown",
    )


# ---------------------------------------------
# /birthday — set your birthday; /birthday list — upcoming birthdays
# Daily birthday check job
# ---------------------------------------------
_MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

BIRTHDAY_MESSAGES = [
    "🎂 Happy Birthday, {name}! May your day be as amazing as you are! 🎉",
    "🎈 It's {name}'s birthday! Wishing you an absolutely legendary day! 🥳",
    "🎁 Everyone say happy birthday to {name}! 🎂 May this year be your best yet!",
    "🕯️ Today is {name}'s special day! Happy Birthday — the group is celebrating with you! 🎊",
    "🎉 {name}, the universe has confirmed: today is YOUR day. Happy Birthday! 🌟",
]


def _days_until_birthday(day: int, month: int) -> int:
    """Return the number of days until the next birthday from today (MYT)."""
    today = datetime.now(TIMEZONE).date()
    this_year = today.replace(month=month, day=day)
    if this_year < today:
        try:
            next_bday = this_year.replace(year=today.year + 1)
        except ValueError:
            next_bday = this_year.replace(year=today.year + 1, day=28)
    else:
        next_bday = this_year
    return (next_bday - today).days


async def birthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /birthday DD/MM   — set your birthday (e.g. /birthday 25/12)
    /birthday list    — show upcoming birthdays in this chat
    """
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = _arg_text(context)

    if not text or text.strip().lower() == "list":
        # Show all birthdays
        bdays = await asyncio.to_thread(get_all_birthdays, chat_id)
        if not bdays:
            await update.message.reply_text(
                "🎂 No birthdays saved yet!\n"
                "Use `/birthday DD/MM` to register yours.\n"
                "_(e.g. `/birthday 25/12` for December 25)_",
                parse_mode="Markdown",
            )
            return

        entries = []
        for uid_str, info in bdays.items():
            d, m = info.get("day", 1), info.get("month", 1)
            name = info.get("name", uid_str)
            days_left = _days_until_birthday(d, m)
            entries.append((days_left, d, m, name))
        entries.sort()

        lines = ["🎂 *Upcoming Birthdays*\n"]
        for days_left, d, m, name in entries:
            if days_left == 0:
                tag = "🎉 Today!"
            elif days_left == 1:
                tag = "Tomorrow!"
            else:
                tag = f"in {days_left} days"
            lines.append(f"• *{name}* — {d:02d} {_MONTH_NAMES[m]}  _{tag}_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Parse DD/MM
    match = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})", text.strip())
    if not match:
        await update.message.reply_text(
            "⚠️ Use the format `DD/MM`.\n_(e.g. `/birthday 25/12` for December 25)_",
            parse_mode="Markdown",
        )
        return

    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12):
        await update.message.reply_text("⚠️ Invalid month. Must be between 1 and 12.")
        return
    import calendar
    max_day = calendar.monthrange(2000, month)[1]  # use 2000 (leap year) to allow Feb 29
    if not (1 <= day <= max_day):
        await update.message.reply_text(f"⚠️ Invalid day for month {_MONTH_NAMES[month]}.")
        return

    name = _display_user(user)
    await asyncio.to_thread(save_birthday, chat_id, user.id, name, day, month)
    days_left = _days_until_birthday(day, month)
    tag = "🎉 That's today!" if days_left == 0 else f"in {days_left} days"

    await update.message.reply_text(
        f"🎂 *Birthday saved!*\n"
        f"*{name}* — {day:02d} {_MONTH_NAMES[month]}  _{tag}_\n\n"
        f"Use `/birthday list` to see everyone's birthday.",
        parse_mode="Markdown",
    )


async def birthday_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs daily at 00:01 MYT. Sends birthday greetings to each chat."""
    today = datetime.now(TIMEZONE).date()
    all_chats = await asyncio.to_thread(get_all_birthday_chats)
    for chat_id, bdays in all_chats.items():
        for uid_str, info in bdays.items():
            d, m = info.get("day", 0), info.get("month", 0)
            if d == today.day and m == today.month:
                name = info.get("name", "Someone")
                msg = random.choice(BIRTHDAY_MESSAGES).format(name=name)
                try:
                    await context.bot.send_message(chat_id=chat_id, text=msg)
                    logger.info("Sent birthday message to chat %s for %s", chat_id, name)
                except Exception as e:
                    logger.warning("Birthday message failed for chat %s: %s", chat_id, e)


# ---------------------------------------------
# /stats — group activity summary
# ---------------------------------------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    # Fetch everything concurrently to keep it fast
    quote_count, seen_users, top_pairs, board = await asyncio.gather(
        asyncio.to_thread(get_quote_count, chat_id),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_fate_board, chat_id, today_str),
    )
    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)

    member_count = len(seen_users)
    reset_h = reset_secs // 3600
    reset_m = (reset_secs % 3600) // 60

    # Top ship pair in the current 48h window
    if top_pairs:
        p = top_pairs[0]
        ship_line = f"{_escape_md(p['label_a'])} × {_escape_md(p['label_b'])} `{p['score']:.1f}%`"
    else:
        ship_line = "No ships yet this cycle"

    # Today's luckboard summary
    luck_count = len(board)
    if board:
        top_uid = max(board, key=lambda k: board[k]["score"])
        t = board[top_uid]
        lucky_line = f"{_escape_md(t['name'])} — {t['tier']} (`{t['score']}`)"
    else:
        lucky_line = "Nobody checked today"

    lines = [
        "📊 *Group Stats*\n",
        f"👥 Members tracked: *{member_count}*",
        f"💬 Quotes saved: *{quote_count}*",
        f"\n🍀 *Today's Luck*",
        f"Checks: *{luck_count}*",
        f"Luckiest: {lucky_line}",
        f"\n💞 *Ships* _(resets in {reset_h}h {reset_m}m)_",
        f"Top pair: {ship_line}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------
# /lucktest — owner-only luck score preview
# ---------------------------------------------
async def lucktest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /lucktest <score>
    Owner-only. Preview exactly what the luck result looks like for any score
    (0–100, or 999 / -999 for cosmic specials).  Uses your own name so name-based
    specials (sigma, kai, rizz…) apply as they would for you.
    Nothing is saved to the luckboard.
    """
    user = update.effective_user
    is_owner = (BOT_OWNER_ID and user.id == BOT_OWNER_ID) or (
        user.username and user.username.casefold() in BOT_OWNER_USERNAMES
    )
    if not is_owner:
        return  # silently ignore non-owners

    raw_arg = _arg_text(context)
    if not raw_arg or not raw_arg.lstrip("-").lstrip("+").isdigit():
        await update.message.reply_text(
            "🔧 *Luck Test* _(owner only)_\n\n"
            "Usage: `/lucktest <score>`\n"
            "Score: `0`–`100`, `999` _(cosmic lucky)_, or `-999` _(cosmic cursed)_\n\n"
            "Shows the full luck card for that score, including today's day modifier "
            "and any name-based specials, without saving anything.",
            parse_mode="Markdown",
        )
        return

    try:
        raw_score = int(raw_arg)
    except ValueError:
        await update.message.reply_text("⚠️ That doesn't look like a number.")
        return

    # Clamp to valid range; 999 / -999 pass through unchanged
    if raw_score not in (999, -999):
        score = max(0, min(100, raw_score))
    else:
        score = raw_score

    # Derive base tier + a sample message for this score
    if score == 999:
        tier = "🌈 COSMICALLY CHOSEN"
        luck_msg = FATE_EXTREME_LUCKY_MESSAGES[0]
    elif score == -999:
        tier = "☠️ COSMICALLY CURSED"
        luck_msg = FATE_EXTREME_UNLUCKY_MESSAGES[0]
    else:
        tier = "🌤️ Neutral"
        luck_msg = "Just another day."
        for t in FATE_TIERS:
            lo, hi = t["range"]
            if lo <= score <= hi:
                tier = t["name"]
                luck_msg = t["messages"][0]
                break

    target_name = _display_user(user)
    today = _today()

    # Run through the same pipeline as the real /luck command
    final_score, final_tier, final_msg, day_note, _ = _apply_special_luck(
        score, tier, luck_msg, target_name, today, seed=str(user.id)
    )

    result_text = _luck_result_text(
        target_name, final_tier, final_score, final_msg, day_note=day_note
    )

    clamp_note = (
        f"_Input clamped: {raw_score} → {score}_\n\n"
        if raw_score != score else ""
    )
    await update.message.reply_text(
        f"🔧 *Luck Test — Input score: {raw_score}*\n{clamp_note}\n"
        f"{result_text}\n\n"
        "_Preview only — nothing saved._",
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
        .post_init(on_startup)
        .build()
    )

    countdown_conv = ConversationHandler(
        entry_points=[CommandHandler("addcountdown", add_countdown_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    choose_conv = ConversationHandler(
        entry_points=[CommandHandler("choose", choose_start)],
        states={
            ASK_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_decision)],
            ASK_OPTIONS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("editcountdown", editcountdown_start)],
        states={
            ASK_EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_field)],
            ASK_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_value)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    # Callback handlers (order matters — more specific patterns first)
    app.add_handler(CallbackQueryHandler(help_callback,         pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(quotes_callback,       pattern=r"^quote:"))
    app.add_handler(CallbackQueryHandler(cancelremind_callback, pattern=r"^cancelremind:"))

    # Conversation handlers
    app.add_handler(countdown_conv)
    app.add_handler(choose_conv)
    app.add_handler(edit_conv)

    # Commands
    app.add_handler(CommandHandler("start",          start_command))
    app.add_handler(CommandHandler("help",           help_command))
    app.add_handler(CommandHandler("ask",            ask_command))
    app.add_handler(CommandHandler("listcountdown",  list_countdown))
    app.add_handler(CommandHandler("removecountdown",remove_countdown_cmd))
    app.add_handler(CommandHandler("fate",           fate_command))
    app.add_handler(CommandHandler("luck",           luck_command))
    app.add_handler(CommandHandler("luckboard",      luckboard_command))
    app.add_handler(CommandHandler("fateboard",      fateboard_command))
    app.add_handler(CommandHandler("streak",         streak_command))
    app.add_handler(CommandHandler("ship",           ship_command))
    app.add_handler(CommandHandler("shipboard",      shipboard_command))
    app.add_handler(CommandHandler("roast",          roast_command))
    app.add_handler(CommandHandler("compliment",     compliment_command))
    app.add_handler(CommandHandler("vibecheck",      vibecheck_command))
    app.add_handler(CommandHandler("rank",           rank_command))
    app.add_handler(CommandHandler("truth",          truth_command))
    app.add_handler(CommandHandler("dare",           dare_command))
    app.add_handler(CommandHandler("wouldyourather", would_you_rather_command))
    app.add_handler(CommandHandler("coinflip",       coinflip_command))
    app.add_handler(CommandHandler("8ball",          eightball_command))
    app.add_handler(CommandHandler("curse",          curse_command))
    app.add_handler(CommandHandler("bless",          bless_command))
    app.add_handler(CommandHandler("decide",         decide_command))
    app.add_handler(CommandHandler("poll",           poll_command))
    app.add_handler(CommandHandler("toss",           toss_command))
    app.add_handler(CommandHandler("birthday",       birthday_command))
    app.add_handler(CommandHandler("remind",         remind_command))
    app.add_handler(CommandHandler("cancelremind",   cancelremind_command))
    app.add_handler(CommandHandler("remindall",      remindall_command))
    app.add_handler(CommandHandler("quote",          quote_command))
    app.add_handler(CommandHandler("quotes",         quotes_command))
    app.add_handler(CommandHandler("deletequote",    deletequote_command))
    app.add_handler(CommandHandler("mvp",            mvp_command))
    app.add_handler(CommandHandler("hot",            hot_command))
    app.add_handler(CommandHandler("stats",          stats_command))
    app.add_handler(CommandHandler("lucktest",       lucktest_command))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()