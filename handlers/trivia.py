"""
handlers/trivia.py
──────────────────
/trivia [topic]  — AI-generated group trivia game, first correct answer wins.
/triviaboard     — all-time trivia leaderboard for this chat.
"""

import asyncio
import json as _json
import logging
import os
import random
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from helpers import _display_user, _escape_md, _arg_text

logger = logging.getLogger(__name__)

# ── Game config ───────────────────────────────────────────────────────────────

_TRIVIA_TIMEOUT   = 30   # seconds before auto-reveal
_ACTIVE_TRIVIA: dict = {}  # chat_id → game state dict

_TRIVIA_CATEGORIES = [
    "general knowledge", "pop culture", "science", "history",
    "geography", "Malaysian culture and food", "sports",
    "technology", "movies and TV shows", "music", "animals",
    "space and astronomy", "video games",
]

_WIN_LINES = [
    "🧠 Big brain energy.", "🏆 Undefeated.", "🎯 Couldn't miss.",
    "⚡ Too fast.", "🔥 On a different level.", "💯 Scholar behaviour.",
]
_TIMEOUT_LINES = [
    "Nobody? Seriously?", "The group has failed.", "Touch grass, then try again.",
    "The answer was right there.", "Collective knowledge: zero.",
]


# ── Question generation ───────────────────────────────────────────────────────

async def _generate_question(topic: str = "") -> dict | None:
    """
    Ask Groq for a trivia question and return a validated dict, or None on failure.
    Expected JSON shape:
      {"question": "...", "options": {"A":"...","B":"...","C":"...","D":"..."}, "answer":"B", "fact":"..."}
    """
    try:
        from ai import groq_client, _groq_complete
    except ImportError:
        return None
    if not groq_client:
        return None

    category = topic.strip() or random.choice(_TRIVIA_CATEGORIES)
    system = (
        "You are a trivia question generator. "
        "Return ONLY a single valid JSON object — no markdown fences, no extra text:\n"
        '{"question":"...","options":{"A":"...","B":"...","C":"...","D":"..."},'
        '"answer":"A","fact":"..."}\n\n'
        "Rules:\n"
        "• answer must be exactly one of: A, B, C, D\n"
        "• fact is one interesting sentence about the correct answer\n"
        "• medium difficulty, fun and accessible\n"
        "• options must all be plausible (no obviously wrong choices)"
    )
    try:
        raw = await asyncio.to_thread(
            _groq_complete,
            [
                {"role": "system", "content": system},
                {"role": "user",   "content": f"Generate a trivia question about: {category}"},
            ],
            220,
            0.8,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        data = _json.loads(cleaned)
        required = ("question", "options", "answer", "fact")
        if not all(k in data for k in required):
            logger.warning("Trivia: missing keys in Groq response")
            return None
        if data["answer"] not in data.get("options", {}):
            logger.warning("Trivia: answer key not in options dict")
            return None
        return data
    except Exception as e:
        logger.warning("Trivia question generation failed: %s", e)
        return None


# ── Keyboard builders ─────────────────────────────────────────────────────────

def _active_keyboard(chat_id: int, qid: str, options: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{letter}. {text}", callback_data=f"trivia:{chat_id}:{qid}:{letter}")]
        for letter, text in options.items()
    ])


def _disabled_keyboard(options: dict, correct: str) -> InlineKeyboardMarkup:
    """Render the final keyboard with ✅ on the correct option."""
    buttons = []
    for letter, text in options.items():
        prefix = "✅ " if letter == correct else "      "
        buttons.append([InlineKeyboardButton(f"{prefix}{letter}. {text}", callback_data="trivia:noop")])
    return InlineKeyboardMarkup(buttons)


# ── /trivia ───────────────────────────────────────────────────────────────────

async def trivia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if chat_id in _ACTIVE_TRIVIA:
        await update.message.reply_text(
            "⚠️ A question is already active! Answer it first — or wait for the timer."
        )
        return

    try:
        from ai import groq_client
    except ImportError:
        groq_client = None
    if not groq_client:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up `GROQ_API_KEY`."
        )
        return

    topic     = _arg_text(context)
    category  = topic.strip() or "General"
    thinking  = await update.message.reply_text("🧠 Generating a question...")

    data = await _generate_question(topic)
    if not data:
        try:
            await thinking.edit_text("❌ Failed to generate a question. Please try again.")
        except TelegramError:
            pass
        return

    qid = os.urandom(4).hex()
    _ACTIVE_TRIVIA[chat_id] = {
        "qid":         qid,
        "data":        data,
        "category":    category,
        "answered_by": None,
    }

    question_text = (
        f"🧠 *Trivia — {_escape_md(category)}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{_escape_md(data['question'])}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_First correct answer wins!  ⏱ {_TRIVIA_TIMEOUT}s_"
    )
    keyboard = _active_keyboard(chat_id, qid, data["options"])

    try:
        sent = await thinking.edit_text(question_text, parse_mode="Markdown", reply_markup=keyboard)
        _ACTIVE_TRIVIA[chat_id]["message_id"] = sent.message_id
    except TelegramError as e:
        logger.warning("Trivia: failed to post question: %s", e)
        _ACTIVE_TRIVIA.pop(chat_id, None)
        return

    # ── Auto-reveal timer ─────────────────────────────────────────────────────
    async def _on_timeout(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _qid=qid) -> None:
        game = _ACTIVE_TRIVIA.get(_cid)
        if not game or game["qid"] != _qid or game.get("answered_by"):
            return
        _ACTIVE_TRIVIA.pop(_cid, None)
        d      = game["data"]
        letter = d["answer"]
        try:
            await ctx.bot.edit_message_text(
                chat_id=_cid,
                message_id=game["message_id"],
                text=(
                    f"⏰ *Time's up!* {random.choice(_TIMEOUT_LINES)}\n\n"
                    f"✅ Answer: *{letter}. {_escape_md(d['options'][letter])}*\n"
                    f"📖 _{_escape_md(d['fact'])}_"
                ),
                parse_mode="Markdown",
                reply_markup=_disabled_keyboard(d["options"], letter),
            )
        except TelegramError as e:
            logger.warning("Trivia timeout edit failed: %s", e)

    context.application.job_queue.run_once(
        _on_timeout,
        when=_TRIVIA_TIMEOUT,
        chat_id=chat_id,
        name=f"trivia_timeout:{chat_id}",
    )
    logger.info("Trivia started in chat %s  qid=%s  topic=%s", chat_id, qid, category)


# ── Callback handler ──────────────────────────────────────────────────────────

async def trivia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "trivia:noop":
        return

    parts = query.data.split(":", 3)
    if len(parts) != 4:
        return
    _, chat_id_str, qid, chosen = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return

    game = _ACTIVE_TRIVIA.get(chat_id)
    if not game or game["qid"] != qid:
        await query.answer("⏰ This question has already ended.", show_alert=True)
        return
    if game.get("answered_by"):
        await query.answer("⚠️ Already answered by someone else!", show_alert=True)
        return

    data    = game["data"]
    correct = data["answer"]
    user    = update.effective_user
    name    = _display_user(user)

    if chosen == correct:
        # ── Correct ───────────────────────────────────────────────────────────
        game["answered_by"] = user.id
        _ACTIVE_TRIVIA.pop(chat_id, None)

        from stores.trivia_store import record_trivia_win
        wins = await asyncio.to_thread(record_trivia_win, chat_id, user.id, name)

        correct_text = data["options"][correct]
        win_line     = random.choice(_WIN_LINES)
        try:
            await query.edit_message_text(
                f"✅ *{_escape_md(name)}* got it!  {win_line}\n\n"
                f"*{correct}. {_escape_md(correct_text)}*\n"
                f"📖 _{_escape_md(data['fact'])}_\n\n"
                f"🏆 {_escape_md(name)} — *{wins}* trivia win{'s' if wins != 1 else ''}",
                parse_mode="Markdown",
                reply_markup=_disabled_keyboard(data["options"], correct),
            )
        except TelegramError as e:
            logger.warning("Trivia win edit failed: %s", e)
    else:
        # ── Wrong ─────────────────────────────────────────────────────────────
        await query.answer("❌ Wrong! Keep trying.", show_alert=True)


# ── /triviaboard ──────────────────────────────────────────────────────────────

async def triviaboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from stores.trivia_store import get_trivia_board
    chat_id = update.effective_chat.id
    rows    = await asyncio.to_thread(get_trivia_board, chat_id, 10)
    if not rows:
        await update.message.reply_text(
            "🧠 No trivia wins yet!\nStart a game with /trivia."
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🧠 *Trivia Leaderboard*\n"]
    for i, row in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name  = _escape_md(str(row.get("name", row.get("user_id", "?"))))
        wins  = int(row.get("wins", 0))
        lines.append(f"{medal} *{name}* — `{wins}` win{'s' if wins != 1 else ''}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
