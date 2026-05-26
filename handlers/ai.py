"""
handlers/ai.py
──────────────
Groq AI client, Serper web search, /ask command, and /choose flow.
Other handlers that need AI (roast, 8ball, hot…) import _call_groq_fun from here.
"""

import asyncio
import logging
import os
import random
import threading
import time
from collections import OrderedDict

import requests
from groq import Groq
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from config import env_int
from constants import THINKING_MESSAGES, VERDICT_LINES
from helpers import _track, _delete_tracked, ASK_DECISION, ASK_OPTIONS, CONV_TIMEOUT

logger = logging.getLogger(__name__)

# ── Groq client ───────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq AI ready.")
else:
    groq_client = None
    logger.warning("GROQ_API_KEY not set — /ask, /roast, /compliment, /8ball, /hot will use fallbacks.")

# ── Serper web search + LRU cache ────────────────────────────────────────────

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"
SERPER_SESSION = requests.Session()

SEARCH_CACHE_TTL_SECONDS = max(0, env_int("SEARCH_CACHE_TTL_SECONDS", 900))
SEARCH_CACHE_MAX_ITEMS = 128
MAX_ASK_LENGTH = 500

# OrderedDict gives O(1) LRU eviction (move_to_end + popitem(last=False))
_SEARCH_CACHE: OrderedDict = OrderedDict()
_search_cache_lock = threading.Lock()


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _get_cached_search(query: str):
    key = _cache_key(query)
    with _search_cache_lock:
        if key not in _SEARCH_CACHE:
            return None
        cached_at, result = _SEARCH_CACHE[key]
        if time.monotonic() - cached_at <= SEARCH_CACHE_TTL_SECONDS:
            # Move to end (most recently used)
            _SEARCH_CACHE.move_to_end(key)
            return result
        # Expired — evict
        del _SEARCH_CACHE[key]
        return None


def _set_cached_search(query: str, result: str) -> None:
    key = _cache_key(query)
    with _search_cache_lock:
        if key in _SEARCH_CACHE:
            _SEARCH_CACHE.move_to_end(key)
        elif len(_SEARCH_CACHE) >= SEARCH_CACHE_MAX_ITEMS:
            # Evict least recently used (first item) — O(1)
            _SEARCH_CACHE.popitem(last=False)
        _SEARCH_CACHE[key] = (time.monotonic(), result)


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


def _call_groq(question: str, search_context: str, history: list | None = None) -> str:
    system_msg = (
        "You are a helpful assistant in an ongoing conversation. "
        "Answer concisely in plain text only. "
        "No markdown formatting, no bullet symbols, no headers. "
        "Use the web search results below if relevant, otherwise use your own knowledge."
    )
    user_msg = question
    if search_context:
        user_msg = f"Web search results for context:\n{search_context}\n\nQuestion: {question}"

    messages = [{"role": "system", "content": system_msg}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    chat = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
    )
    return chat.choices[0].message.content.strip()


# ── Ask conversation context (Redis-backed, TTL 6 h) ─────────────────────────
# Key: ask_ctx:<chat_id>:<message_id>  →  JSON list of {role, content} dicts
# Stored after every bot /ask reply so follow-ups can load the full history.

_ASK_CTX_TTL = 6 * 3600   # 6 hours
_ASK_PREFIX  = "🤖 Q: "   # sentinel to detect bot /ask replies


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


def _call_groq_fun(prompt: str) -> str:
    """Short, playful AI response used by roast, compliment, 8ball, hot."""
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
            "_💡 Tip: reply to my answer and use /ask again to follow up!_",
            parse_mode="Markdown",
        )
        return

    question = " ".join(context.args)
    if len(question) > MAX_ASK_LENGTH:
        await update.message.reply_text(
            f"⚠️ Question too long. Please keep it under {MAX_ASK_LENGTH} characters."
        )
        return

    # ── Detect reply-chaining: load history from the replied-to bot message ──
    history: list | None = None
    replied = update.message.reply_to_message
    chat_id = update.effective_chat.id

    if (
        replied
        and replied.from_user
        and replied.from_user.id == context.bot.id
        and replied.text
        and replied.text.startswith(_ASK_PREFIX)
    ):
        history = await asyncio.to_thread(
            _load_ask_context, chat_id, replied.message_id
        )
        if history:
            logger.info(
                "ask follow-up from user %s (depth %s, prev msg %s)",
                update.effective_user.id, len(history) // 2, replied.message_id,
            )

    thinking_msg = await update.message.reply_text("🤖 Searching and thinking...")

    try:
        # Always search — follow-ups benefit from fresh web context too
        search_context = await asyncio.to_thread(_search_web, question)
        answer = await asyncio.to_thread(_call_groq, question, search_context, history)

        # ── Persist updated history for the next follow-up (cap at 3 exchanges) ──
        prior = history or []
        new_history = (prior + [
            {"role": "user",      "content": question},
            {"role": "assistant", "content": answer},
        ])[-6:]   # keep last 3 exchanges (6 turns)

        max_len = 3900
        header = f"🤖 Q: {question}\n\n"
        first_chunk_limit = max(1, max_len - len(header))
        first_chunk = answer[:first_chunk_limit]
        remaining_answer = answer[first_chunk_limit:]

        await thinking_msg.edit_text(header + first_chunk)
        await asyncio.to_thread(_save_ask_context, chat_id, thinking_msg.message_id, new_history)

        for i in range(0, len(remaining_answer), max_len):
            overflow_msg = await thinking_msg.reply_text(remaining_answer[i:i + max_len])
            await asyncio.to_thread(
                _save_ask_context, chat_id, overflow_msg.message_id, new_history
            )

    except Exception as e:
        logger.error("Ask error: %s", e)
        await thinking_msg.edit_text("❌ Something went wrong with the AI. Please try again later.")


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
    # Do NOT use update.message.reply_text here — that message was just deleted,
    # and Telegram will reject the reply_to_message_id, silently killing the rest
    # of this function. Send a fresh message instead.
    thinking_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=thinking
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
        await context.bot.send_message(chat_id=update.effective_chat.id, text=result_text)
    context.user_data.clear()
    return ConversationHandler.END