"""
handlers/ai.py
──────────────
Groq AI client, Serper web search, /ask command, and /choose flow.
Other handlers that need AI (roast, 8ball, hot…) import _call_groq_fun from here.

Search policy:
  - Always enabled for all chats.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests
from groq import Groq
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from config import env_int
from constants import THINKING_MESSAGES, VERDICT_LINES
from helpers import (
    _track, _delete_tracked, ASK_DECISION, ASK_OPTIONS, CONV_TIMEOUT,
    _thread_kwargs,
)

logger = logging.getLogger(__name__)

# ── Groq client ───────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq AI ready.")
else:
    groq_client = None
    logger.warning("GROQ_API_KEY not set — /ask, /roast, /compliment, /8ball, /hot will use fallbacks.")

# ── Serper web search + LRU cache ────────────────────────────────────────────

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"
_serper_local = threading.local()
_AI_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, env_int("AI_THREAD_WORKERS", 3)),
    thread_name_prefix="ai-io",
)

SEARCH_CACHE_TTL_SECONDS = max(0, env_int("SEARCH_CACHE_TTL_SECONDS", 900))
SEARCH_CACHE_MAX_ITEMS = 128
MAX_ASK_LENGTH = 500

_SEARCH_CACHE: OrderedDict = OrderedDict()
_search_cache_lock = threading.Lock()


async def _run_ai_io(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_AI_EXECUTOR, lambda: func(*args))


def _serper_session() -> requests.Session:
    session = getattr(_serper_local, "session", None)
    if session is None:
        session = requests.Session()
        _serper_local.session = session
    return session


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _get_cached_search(query: str):
    key = _cache_key(query)
    with _search_cache_lock:
        if key not in _SEARCH_CACHE:
            return None
        cached_at, result = _SEARCH_CACHE[key]
        if time.monotonic() - cached_at <= SEARCH_CACHE_TTL_SECONDS:
            _SEARCH_CACHE.move_to_end(key)
            return result
        del _SEARCH_CACHE[key]
        return None


def _set_cached_search(query: str, result: str) -> None:
    key = _cache_key(query)
    with _search_cache_lock:
        if key in _SEARCH_CACHE:
            _SEARCH_CACHE.move_to_end(key)
        elif len(_SEARCH_CACHE) >= SEARCH_CACHE_MAX_ITEMS:
            _SEARCH_CACHE.popitem(last=False)
        _SEARCH_CACHE[key] = (time.monotonic(), result)


def _search_web(query: str) -> str:
    if not SERPER_API_KEY:
        return ""
    cached = _get_cached_search(query)
    if cached is not None:
        return cached
    try:
        resp = _serper_session().post(
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


# ── Shared Groq base function ─────────────────────────────────────────────────

def _groq_complete(messages: list, max_tokens: int = 1024, temperature: float = 0.7) -> str:
    """Single low-level call to Groq. Shared by all public helpers."""
    chat = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return chat.choices[0].message.content.strip()


def _call_groq(question: str, search_context: str, history: list | None = None) -> str:
    system_msg = (
        "You are a knowledgeable, reliable assistant in a Malaysian Telegram group chat. "
        "Your default mode is calm, clear, and informative — give accurate, well-reasoned answers. "
        "You understand Gen Z slang, internet culture, and local Malaysian context, so you can read the room. "
        "If the question is lighthearted, absurd, or clearly asking for banter, bring the humour naturally — "
        "but never force jokes onto serious questions. "
        "Keep answers concise. Plain text only, absolutely no markdown, no bullet points, no headers. "
        "Use the web search results below if relevant, otherwise use your own knowledge."
    )
    user_msg = question
    if search_context:
        user_msg = f"Web search results for context:\n{search_context}\n\nQuestion: {question}"
    messages = [{"role": "system", "content": system_msg}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})
    return _groq_complete(messages, max_tokens=1024, temperature=0.7)


def _call_groq_fun(prompt: str) -> str:
    """
    Short, playful AI response used by roast, compliment, 8ball, hot.
    A random style word is injected every call so repeated uses on the
    same target never produce the same answer.
    """
    spice_words = [
        "unhinged", "dramatic", "deadpan", "chaotic", "poetic", "corporate",
        "Shakespearean", "brainrot", "sarcastic", "wholesome", "absurd",
        "conspiracy theorist", "sports commentator", "disappointed parent",
        "motivational coach who gave up", "overly formal", "sleepy",
        "telenovela villain", "nature documentary narrator", "infomercial host",
        "passive aggressive", "enthusiastically confused", "retired superhero",
    ]
    style = random.choice(spice_words)
    return _groq_complete(
        messages=[
            {
                "role": "system",
                "content": (
                    "You write short, playful Telegram group chat content. "
                    "Plain text only. No markdown. Keep it friendly, funny, and safe. "
                    f"Write in a {style} style this time. "
                    "Give only the answer, no intro, no label, no explanation of your style."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=120,
        temperature=0.95,
    )


# ── Per-user ask rate limiting ────────────────────────────────────────────────

_ASK_COOLDOWNS: dict = {}
_ASK_COOLDOWN_SECONDS: float = 3.0
_ASK_COOLDOWNS_MAX = 5_000
_ask_cooldowns_lock = threading.Lock()


def _ask_on_cooldown(user_id: int) -> float:
    """Return remaining cooldown seconds (0.0 = clear)."""
    with _ask_cooldowns_lock:
        remaining = _ASK_COOLDOWN_SECONDS - (time.monotonic() - _ASK_COOLDOWNS.get(user_id, 0))
        return max(0.0, remaining)


def _set_ask_cooldown(user_id: int) -> None:
    with _ask_cooldowns_lock:
        now = time.monotonic()
        expired = [
            uid for uid, last_used in _ASK_COOLDOWNS.items()
            if now - last_used > _ASK_COOLDOWN_SECONDS
        ]
        for uid in expired:
            del _ASK_COOLDOWNS[uid]
        if len(_ASK_COOLDOWNS) >= _ASK_COOLDOWNS_MAX:
            evict = _ASK_COOLDOWNS_MAX // 10
            for k in list(_ASK_COOLDOWNS)[:evict]:
                del _ASK_COOLDOWNS[k]
        _ASK_COOLDOWNS[user_id] = now


# ── Ask conversation context (Redis-backed, TTL 6 h) ─────────────────────────

_ASK_CTX_TTL = 6 * 3600
_ASK_PREFIX          = "🤖 Q: "
_ASK_OVERFLOW_PREFIX = "🤖 ↪ "


def _ask_ctx_key(chat_id: int, message_id: int) -> str:
    return f"ask_ctx:{chat_id}:{message_id}"


def _save_ask_context(chat_id: int, message_id: int, history: list) -> None:
    import json
    try:
        from db import redis
        redis.set(
            _ask_ctx_key(chat_id, message_id),
            json.dumps(history, separators=(",", ":")),
            ex=_ASK_CTX_TTL,
        )
    except Exception as e:
        logger.warning("ask_ctx save failed for msg %s: %s", message_id, e)


def _load_ask_context(chat_id: int, message_id: int) -> list | None:
    import json
    try:
        from db import redis
        raw = redis.get(_ask_ctx_key(chat_id, message_id))
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning("ask_ctx load failed for msg %s: %s", message_id, e)
        return None


# ── Shared ask processing ─────────────────────────────────────────────────────

async def _process_ask(
    question: str,
    history: list | None,
    chat_id: int,
    reply_target,
    context: ContextTypes.DEFAULT_TYPE,
    use_search: bool = True,
) -> None:
    """Core logic shared by /ask and the plain-text follow-up handler."""
    thinking_msg = await reply_target.reply_text(
        "🤖 Searching and thinking..." if use_search else "🤖 Thinking..."
    )
    answer_displayed = False
    try:
        search_context = await _run_ai_io(_search_web, question) if use_search else ""
        answer = await _run_ai_io(_call_groq, question, search_context, history)

        prior = history or []
        new_history = (prior + [
            {"role": "user",      "content": question},
            {"role": "assistant", "content": answer},
        ])[-6:]

        max_len = 3900
        if history:
            original_q = next((h["content"] for h in history if h["role"] == "user"), None)
            if original_q and original_q != question:
                header = f"🤖 Q: {original_q}\n↩️ {question}\n\n"
            else:
                header = f"🤖 Q: {question}\n\n"
        else:
            header = f"🤖 Q: {question}\n\n"
        first_chunk_limit = max(1, max_len - len(header))
        first_chunk = answer[:first_chunk_limit]
        remaining_answer = answer[first_chunk_limit:]

        await thinking_msg.edit_text(header + first_chunk)
        answer_displayed = True
        await asyncio.to_thread(_save_ask_context, chat_id, thinking_msg.message_id, new_history)

        for i in range(0, len(remaining_answer), max_len):
            overflow_msg = await thinking_msg.reply_text(
                _ASK_OVERFLOW_PREFIX + remaining_answer[i:i + max_len]
            )
            await asyncio.to_thread(
                _save_ask_context, chat_id, overflow_msg.message_id, new_history
            )
    except Exception as e:
        logger.error("Ask error: %s", e)
        if not answer_displayed:
            await thinking_msg.edit_text("❌ Something went wrong with the AI. Please try again later.")


def _is_ask_bot_message(text: str | None) -> bool:
    """True if the text looks like a /ask answer (initial or overflow)."""
    if not text:
        return False
    return text.startswith(_ASK_PREFIX) or text.startswith(_ASK_OVERFLOW_PREFIX)


# ── /ask command ──────────────────────────────────────────────────────────────

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not groq_client:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up the `GROQ_API_KEY`."
        )
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/ask <your question>`\n_(e.g. `/ask what is tung tung tung sahur?`)_\n\n"
            "_💡 Tip: just reply to my answer to follow up — no command needed!_",
            parse_mode="Markdown",
        )
        return

    question = " ".join(context.args)
    if len(question) > MAX_ASK_LENGTH:
        await update.message.reply_text(
            f"⚠️ Question too long. Please keep it under {MAX_ASK_LENGTH} characters."
        )
        return

    user_id = update.effective_user.id
    remaining = _ask_on_cooldown(user_id)
    if remaining:
        await update.message.reply_text(
            f"⚠️ Please wait {remaining:.1f}s before asking again."
        )
        return
    _set_ask_cooldown(user_id)

    chat_id = update.effective_chat.id
    replied = update.message.reply_to_message
    history = None

    if (
        replied
        and replied.from_user
        and replied.from_user.id == context.bot.id
        and _is_ask_bot_message(replied.text)
    ):
        history = await asyncio.to_thread(_load_ask_context, chat_id, replied.message_id)

    await _process_ask(question, history, chat_id, update.message, context, use_search=True)


# ── Plain-text reply follow-up handler ───────────────────────────────────────

async def ask_followup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires when a user sends a plain-text reply (no /ask command) directly to a
    bot /ask answer (initial or overflow chunk).
    """
    if not groq_client:
        return

    message = update.message
    if not message or not message.text:
        return
    if update.effective_user and update.effective_user.id == context.bot.id:
        return

    replied = message.reply_to_message
    if (
        not replied
        or not replied.from_user
        or replied.from_user.id != context.bot.id
        or not _is_ask_bot_message(replied.text)
    ):
        return

    question = message.text.strip()
    if not question or len(question) > MAX_ASK_LENGTH:
        return

    user_id = update.effective_user.id
    remaining = _ask_on_cooldown(user_id)
    if remaining:
        await message.reply_text(f"⚠️ Please wait {remaining:.1f}s before asking again.")
        return
    _set_ask_cooldown(user_id)

    chat_id = update.effective_chat.id
    history = await asyncio.to_thread(_load_ask_context, chat_id, replied.message_id)

    if history is None:
        await message.reply_text(
            "⚠️ This conversation has expired (older than 6 hours). "
            "Start a new one with `/ask <question>`.",
            parse_mode="Markdown",
        )
        return

    logger.info(
        "ask_followup from user %s (depth %s, prev msg %s)",
        update.effective_user.id, len(history) // 2, replied.message_id,
    )
    await _process_ask(question, history, chat_id, message, context, use_search=True)


# ── /choose flow ──────────────────────────────────────────────────────────────

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
    decision = context.user_data.get("decision", "your decision")
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
    thinking_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=thinking,
        **_thread_kwargs(update),
    )
    await asyncio.sleep(2)
    result_text = (
        f"🎯 Decision: {decision}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ The answer is... {chosen}!\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 Odds:\n{percentage_lines}\n\n{verdict}"
    )
    try:
        await thinking_msg.edit_text(result_text)
    except TelegramError as e:
        logger.warning("Choose edit failed, sending fresh result: %s", e)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=result_text,
            **_thread_kwargs(update),
        )
    context.user_data.clear()
    return ConversationHandler.END
