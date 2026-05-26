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

import requests
from groq import Groq
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

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

# ── Serper web search + cache ─────────────────────────────────────────────────

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"
SERPER_SESSION = requests.Session()

from config import env_int
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