"""
handlers/ai.py
──────────────
Gemini AI client, Serper web search, /ask command, and /choose flow.
Other handlers that need AI (roast, 8ball, hot…) import _call_gemini_fun from here.

Search policy:
  - Always enabled for all chats, but a search failure never fails the answer.

STRUCTURAL CHANGE (error handling):
    /ask errors are no longer classified by guessing substrings out of str(e).
    All classification lives in handlers/ask_errors.py, keyed primarily on the
    HTTP status code, so a 429/503 "the model is overloaded" is reported as
    high demand rather than a bad model name. This module now also detects
    safety blocks, degrades gracefully when search is down, and splits long
    answers using UTF-16-aware chunks that stay under Telegram's 4096 limit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests
from google import genai
from google.genai import types
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

# STRUCTURAL CHANGE (refactor): keys, model name, cache TTL and worker count are
# now plain values from config.py rather than os.getenv calls in this module.
from config import (
    AI_THREAD_WORKERS, GEMINI_API_KEY, GEMINI_MODEL, SEARCH_CACHE_TTL_SECONDS,
    SERPER_API_KEY,
)
from constants import THINKING_MESSAGES, VERDICT_LINES
from handlers.ask_errors import (
    AskEmptyAnswer, AskSafetyBlocked, EMPTY_PROMPT_MESSAGE, classify_ask_error,
)
from helpers import (
    _track, _delete_tracked, ASK_DECISION, ASK_OPTIONS, CONV_TIMEOUT,
    _thread_kwargs,
)

logger = logging.getLogger(__name__)

# ── Gemini client ─────────────────────────────────────────────────────────────

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Gemini AI ready with model %s.", GEMINI_MODEL)
else:
    gemini_client = None
    logger.warning("GEMINI_API_KEY not set — /ask, /roast, /compliment, /8ball, /hot will use fallbacks.")

# ── Serper web search + LRU cache ────────────────────────────────────────────

SERPER_URL = "https://google.serper.dev/search"
_serper_local = threading.local()
_AI_EXECUTOR = ThreadPoolExecutor(
    max_workers=AI_THREAD_WORKERS,
    thread_name_prefix="ai-io",
)

SEARCH_CACHE_MAX_ITEMS = 128
MAX_ASK_LENGTH = 1000
_SERPER_TIMEOUT = (2, 5)
_SEARCH_TIMEOUT_SECONDS = 12.0   # web search is optional; never hold up the answer
_GEMINI_TIMEOUT_SECONDS = 45.0

# Telegram hard limit is 4096 characters per message. We cap at 3900 UTF-16
# units so emoji-heavy answers (2 units each) still fit with room to spare.
TELEGRAM_MAX_CHARS = 4096
_ASK_SAFE_CHARS = 3900
_MAX_HEADER_CHARS = 800

_SEARCH_CACHE: OrderedDict = OrderedDict()
_search_cache_lock = threading.Lock()


async def _run_ai_io(func, *args, timeout: float = _GEMINI_TIMEOUT_SECONDS):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_AI_EXECUTOR, lambda: func(*args)),
        timeout=timeout,
    )


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
            timeout=_SERPER_TIMEOUT,
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


# ── Shared Gemini base function ───────────────────────────────────────────────

def _gemini_complete(
    contents: list,
    system_instruction: str,
    max_tokens: int = 2048,
) -> str:
    """Single text-only Gemini call shared by all public AI helpers."""
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=max_tokens,
            # Gemini 3.x models use thinking_level (not thinking_budget).
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            automatic_continuation=False,
            http_options=types.HttpOptions(
                timeout=45_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ),
    )
    # A safety-filtered response usually has NO text at all, and reading
    # ``.text`` can itself raise. Inspect the metadata first so the user gets
    # "blocked by safety" rather than a vague "something went wrong".
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason and str(block_reason) not in ("", "None", "0", "BLOCK_REASON_UNSPECIFIED"):
        raise AskSafetyBlocked(str(block_reason))

    candidates = getattr(response, "candidates", None) or []
    blocked_finish = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION")
    for candidate in candidates:
        finish_reason = str(getattr(candidate, "finish_reason", "") or "").upper()
        if any(marker in finish_reason for marker in blocked_finish):
            raise AskSafetyBlocked(finish_reason)

    try:
        text = (response.text or "").strip()
    except Exception as e:
        # Some SDK versions raise instead of returning "" for blocked content.
        if any(marker in str(e).casefold() for marker in ("safety", "blocked", "finish_reason")):
            raise AskSafetyBlocked(str(e)) from e
        text = ""
    if not text:
        raise AskEmptyAnswer("Gemini returned no text")
    return text


def _call_gemini(question: str, search_context: str, history: list | None = None) -> str:
    system_msg = (
        "You are a knowledgeable, reliable assistant in a Telegram group chat. "
        "Your default mode is calm, clear, and informative — give accurate, well-reasoned answers. "
        "Always answer in natural English, even when the user writes in another language, unless they explicitly ask for a different language. "
        "You understand Gen Z slang, internet culture, and Malaysian context, so you can read the room. "
        "If the question is lighthearted, absurd, or clearly asking for banter, bring the humour naturally — "
        "but never force jokes onto serious questions. "
        "Keep answers concise. Plain text only, absolutely no markdown, no bullet points, no headers. "
        "Use the web search results below if relevant, otherwise use your own knowledge."
    )
    user_msg = question
    if search_context:
        user_msg = f"Web search results for context:\n{search_context}\n\nQuestion: {question}"
    contents = []
    if history:
        for message in history:
            role = "model" if message.get("role") == "assistant" else "user"
            content = str(message.get("content", "")).strip()
            if content:
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=content)]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))
    return _gemini_complete(contents, system_msg)


def _call_gemini_fun(prompt: str) -> str:
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
    system_instruction = (
        "You write short, playful Telegram group chat content. "
        "Plain text only. No markdown. Keep it friendly, funny, and safe. "
        f"Write in a {style} style this time. "
        "Give only the answer, no intro, no label, no explanation of your style."
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    return _gemini_complete(contents, system_instruction, max_tokens=512)


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


def _clear_ask_cooldown(user_id: int) -> None:
    """Release the slot after a transient failure so a retry isn't blocked."""
    with _ask_cooldowns_lock:
        _ASK_COOLDOWNS.pop(user_id, None)


# ── Ask conversation context (Redis-backed, TTL 6 h) ─────────────────────────

_ASK_CTX_TTL = 6 * 3600
_ASK_PREFIX          = "🤖 Q: "
_ASK_OVERFLOW_PREFIX = "🤖 ↪ "


def _ask_ctx_key(chat_id: int, message_id: int) -> str:
    return f"ask_ctx:{chat_id}:{message_id}"


def _save_ask_context(chat_id: int, message_id: int, history: list) -> None:
    # Imported lazily so the text helpers above remain unit-testable without
    # Redis credentials being present.
    from db import redis
    try:
        redis.set(
            _ask_ctx_key(chat_id, message_id),
            json.dumps(history, separators=(",", ":")),
            ex=_ASK_CTX_TTL,
        )
    except Exception as e:
        logger.warning("ask_ctx save failed for msg %s: %s", message_id, e)


def _load_ask_context(chat_id: int, message_id: int) -> list | None:
    # See _save_ask_context: Redis is only needed when a conversation is used.
    from db import redis
    try:
        raw = redis.get(_ask_ctx_key(chat_id, message_id))
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning("ask_ctx load failed for msg %s: %s", message_id, e)
        return None


# ── Telegram-safe message building (pure, unit-testable) ─────────────────────

def _utf16_len(text: str) -> int:
    """Telegram counts UTF-16 code units; an emoji takes two, not one."""
    return len(text.encode("utf-16-le")) // 2


def _take_utf16(text: str, limit: int) -> tuple[str, int]:
    """Return (prefix, consumed_code_points) with UTF-16 length <= limit."""
    units = 0
    count = 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            break
        units += width
        count += 1
    return text[:count], count


def _answer_header(question: str, history: list | None) -> str:
    original_q = None
    if history:
        original_q = next(
            (h.get("content") for h in history if h.get("role") == "user"), None
        )
    if original_q and original_q != question:
        header = f"{_ASK_PREFIX}{original_q}\n↩️ {question}\n\n"
    else:
        header = f"{_ASK_PREFIX}{question}\n\n"
    # A 1000-character question must not eat the space reserved for the answer.
    if _utf16_len(header) > _MAX_HEADER_CHARS:
        prefix, _ = _take_utf16(header, _MAX_HEADER_CHARS - 4)
        header = prefix + "…\n\n"
    return header


def build_answer_messages(question: str, history: list | None, answer: str) -> list[str]:
    """
    Split an answer into Telegram-safe messages.

    Every returned string is <= _ASK_SAFE_CHARS UTF-16 units (well under the
    hard 4096-character API limit). The first keeps the "🤖 Q:" header; the rest
    carry the "🤖 ↪ " marker so replying to any chunk still continues the thread.
    """
    header = _answer_header(question, history)
    first_piece, used = _take_utf16(
        answer, max(1, _ASK_SAFE_CHARS - _utf16_len(header))
    )
    messages = [header + first_piece]

    remaining = answer[used:]
    overflow_budget = _ASK_SAFE_CHARS - _utf16_len(_ASK_OVERFLOW_PREFIX)
    while remaining:
        piece, used = _take_utf16(remaining, overflow_budget)
        if not piece:
            break
        messages.append(_ASK_OVERFLOW_PREFIX + piece)
        remaining = remaining[used:]
    return messages


# ── Failure reporting ────────────────────────────────────────────────────────

async def _report_ask_failure(thinking_msg, failure, exc, *, user_id=None) -> None:
    """Show one accurate message; never leak raw SDK errors into the chat."""
    if failure.silent:
        logger.debug("ask: %s (no user-facing message)", failure.category)
        return
    level = logging.WARNING if failure.retryable else logging.ERROR
    logger.log(level, "ask failed [%s]: %s", failure.category, exc)
    try:
        await thinking_msg.edit_text(failure.message)
    except Exception as e:
        logger.warning("ask: could not display the error message: %s", e)
    if failure.retryable and user_id is not None:
        # Don't make "please try again in a minute" collide with the 3 s cooldown.
        _clear_ask_cooldown(user_id)


# ── Shared ask processing ─────────────────────────────────────────────────────

async def _process_ask(
    question: str,
    history: list | None,
    chat_id: int,
    reply_target,
    context: ContextTypes.DEFAULT_TYPE,
    use_search: bool = True,
    user_id: int | None = None,
) -> None:
    """Core logic shared by /ask and the plain-text follow-up handler."""
    try:
        thinking_msg = await reply_target.reply_text(
            "🤖 Searching and thinking..." if use_search else "🤖 Thinking..."
        )
    except Exception as e:
        logger.warning("ask: could not post the thinking message: %s", e)
        return

    # Web search is optional context: a Serper timeout or network failure must
    # not turn into a failed /ask. Simply answer without search results.
    search_context = ""
    if use_search:
        try:
            search_context = await _run_ai_io(
                _search_web, question, timeout=_SEARCH_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.warning("ask: web search unavailable, answering without it: %s", e)

    try:
        answer = (await _run_ai_io(_call_gemini, question, search_context, history)).strip()
        if not answer:
            raise AskEmptyAnswer("Gemini returned no text")
    except Exception as e:
        await _report_ask_failure(
            thinking_msg, classify_ask_error(e), e, user_id=user_id
        )
        return

    new_history = ((history or []) + [
        {"role": "user",      "content": question},
        {"role": "assistant", "content": answer},
    ])[-6:]
    messages = build_answer_messages(question, history, answer)

    try:
        await thinking_msg.edit_text(messages[0])
    except Exception as e:
        failure = classify_ask_error(e)
        if failure.silent:
            # "Message is not modified" means the answer is already there;
            # carry on with any remaining chunks instead of dropping them.
            logger.debug("ask: first message already contained the answer")
        else:
            await _report_ask_failure(
                thinking_msg, failure, e, user_id=user_id
            )
            return

    await asyncio.to_thread(
        _save_ask_context, chat_id, thinking_msg.message_id, new_history
    )

    # Overflow chunks are delivered one at a time. If one fails we say the answer
    # was cut short, instead of silently stopping halfway through.
    for text in messages[1:]:
        try:
            overflow_msg = await thinking_msg.reply_text(text)
            await asyncio.to_thread(
                _save_ask_context, chat_id, overflow_msg.message_id, new_history
            )
        except Exception as e:
            failure = classify_ask_error(e)
            logger.warning("ask: overflow message failed [%s]: %s", failure.category, e)
            if not failure.silent:
                try:
                    await thinking_msg.reply_text(failure.message)
                except Exception as notify_exc:
                    logger.warning("ask: could not report truncation: %s", notify_exc)
            break


def _is_ask_bot_message(text: str | None) -> bool:
    """True if the text looks like a /ask answer (initial or overflow)."""
    if not text:
        return False
    return text.startswith(_ASK_PREFIX) or text.startswith(_ASK_OVERFLOW_PREFIX)


# ── /ask command ──────────────────────────────────────────────────────────────

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not gemini_client:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up the `GEMINI_API_KEY`."
        )
        return
    question = " ".join(context.args or []).strip()
    # Empty, whitespace-only or emoji/punctuation-only prompts should never be
    # sent to Gemini (that would produce a confusing 400). Give a clear hint.
    if not question or not any(char.isalnum() for char in question):
        await update.message.reply_text(
            EMPTY_PROMPT_MESSAGE + "\n\n"
            "💡 Tip: just reply to any of my answers to follow up — no command needed."
        )
        return

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

    await _process_ask(
        question, history, chat_id, update.message, context,
        use_search=True, user_id=user_id,
    )


# ── Plain-text reply follow-up handler ───────────────────────────────────────

async def ask_followup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires when a user sends a plain-text reply (no /ask command) directly to a
    bot /ask answer (initial or overflow chunk).
    """
    if not gemini_client:
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
    if not question or not any(char.isalnum() for char in question):
        return  # a bare "?" or emoji isn't a question worth an API call
    if len(question) > MAX_ASK_LENGTH:
        await message.reply_text(
            f"⚠️ That follow-up is too long. Please keep it under {MAX_ASK_LENGTH} characters."
        )
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
    await _process_ask(
        question, history, chat_id, message, context,
        use_search=True, user_id=user_id,
    )


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