"""
handlers/fun.py
───────────────
Fun commands: ship, roast, compliment, vibecheck, rank, truth, dare,
wouldyourather, coinflip, 8ball, curse, bless, mvp, hot, toss, decide,
poll, game, roastmax.
"""

import asyncio
import logging
import random
import time as _time
from datetime import datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import TIMEZONE
from constants import (
    VERDICT_LINES,
    SHIP_OWNER_BLOCK_LINES, SHIP_TIER_LINES, BOT_SHIP_REFUSALS,
    ROAST_LINES, COMPLIMENT_LINES, VIBE_TIERS,
    TRUTH_QUESTIONS, DARE_PROMPTS, WOULD_YOU_RATHER_PROMPTS,
    EIGHT_BALL_ANSWERS, CURSE_LINES, BLESS_LINES, MVP_LINES, HOT_VERDICTS,
    TOSS_VERDICTS, PREDICT_FALLBACK_LINES,
)
from stores.ship_store import save_ship_pair, get_top_ship_pairs, get_shipboard_reset_time
from stores.user_store import track_seen_user, get_seen_users
from stores.mvp_store import get_today_mvp, save_mvp_win, get_mvp_board
from handlers.ai import groq_client, _call_groq_fun, _call_groq_roastmax, is_roastmax_allowed
from helpers import (
    _display_user, _arg_text, _normalize_target, _daily_rng,
    _mentioned_target, _target_from_mention_or_sender, _escape_md,
    _display_name_or_id,
    BOT_OWNER_ID, BOT_OWNER_USERNAMES, _is_owner,
)

logger = logging.getLogger(__name__)

# ── Telegram poll limits ──────────────────────────────────────────────────────
_POLL_MAX_OPTIONS = 10
_POLL_MAX_QUESTION_LEN = 300
_POLL_MAX_OPTION_LEN = 100

_POLL_COOLDOWNS: dict = {}
_POLL_COOLDOWN_SECONDS: float = 30.0
_POLL_COOLDOWNS_MAX = 5_000


# ── Ship helpers ──────────────────────────────────────────────────────────────

def _purge_poll_cooldowns(now: float) -> None:
    expired = [
        key for key, last_used in _POLL_COOLDOWNS.items()
        if now - last_used >= _POLL_COOLDOWN_SECONDS
    ]
    for key in expired:
        del _POLL_COOLDOWNS[key]


def _log_background_failure(label: str):
    def _callback(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("%s failed: %s", label, exc)
    return _callback


def _ship_target(label, user_id=None, username="", explicit_username=False):
    return {"label": label, "user_id": user_id,
            "username": username.strip().lstrip("@").casefold(),
            "explicit_username": explicit_username}


def _ship_mentions_from_message(update: Update, bot_username: str = "", bot_id: int = 0) -> list:
    message = update.message
    if not message:
        return []
    bot_username_norm = bot_username.casefold().lstrip("@")
    targets = []
    for entity in message.entities or []:
        if entity.type == "text_mention" and getattr(entity, "user", None):
            if bot_id and entity.user.id == bot_id:
                continue
            targets.append(_ship_target(_display_user(entity.user), user_id=entity.user.id,
                                        username=entity.user.username or "",
                                        explicit_username=bool(entity.user.username)))
        elif entity.type == "mention":
            mention = message.parse_entity(entity)
            if bot_username_norm and mention.lstrip("@").casefold() == bot_username_norm:
                continue
            targets.append(_ship_target(mention, username=mention, explicit_username=True))
    return targets


def _bot_mentioned_in_ship(update: Update, bot_username: str, bot_id: int) -> bool:
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
    if not message or not message.reply_to_message:
        return False
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
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    for limit, messages in SHIP_TIER_LINES:
        if score <= limit:
            rng = random.Random(f"shipcomment:{seed}:{today_str}:{limit}")
            return rng.choice(messages)
    rng = random.Random(f"shipcomment:{seed}:{today_str}:100")
    return rng.choice(SHIP_TIER_LINES[-1][1])


# ── Roast target resolver ─────────────────────────────────────────────────────

def _resolve_roast_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    """
    Resolve the roast target and any message context.

    Priority:
      1. @mention or text_mention in the command message
      2. Reply to someone else's message (uses that message's text as context)
      3. Sender themselves (self-roast)

    Returns (target_name: str, msg_context: str)
    """
    message = update.message
    replied = message.reply_to_message if message else None

    # 1. Explicit mention in the command
    mentioned = _mentioned_target(update, context)
    if mentioned:
        # If they also replied, grab the replied message text as bonus context
        msg_context = ""
        if replied and replied.text:
            msg_context = replied.text[:300]
        return mentioned, msg_context

    # 2. Reply to someone else's message — target is the replied-to author
    if replied and replied.from_user and not replied.from_user.is_bot:
        if replied.from_user.id != update.effective_user.id:
            target = _display_user(replied.from_user)
            msg_context = (replied.text or "")[:300]
            return target, msg_context

    # 3. Self-roast
    return _display_user(update.effective_user), ""


# ── /ship ─────────────────────────────────────────────────────────────────────

async def ship_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            task.add_done_callback(_log_background_failure("track_seen_user"))
    pair_key = f"{normalized[0]}:{normalized[1]}"
    task = asyncio.create_task(asyncio.to_thread(save_ship_pair, chat_id, pair_key, target_a, target_b, score))
    task.add_done_callback(_log_background_failure("save_ship_pair"))
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


# ── /roast ────────────────────────────────────────────────────────────────────

async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, msg_context = _resolve_roast_target(update, context)
    fallback = random.choice(ROAST_LINES).format(target=target)
    if not groq_client:
        await update.message.reply_text(fallback)
        return
    try:
        context_line = (
            f' They just said: "{msg_context[:200]}".'
            if msg_context else ""
        )
        prompt = (
            f"Write one short, playful, friendly roast for someone named '{target}' "
            f"in a Telegram group.{context_line} "
            "Use their name. Keep it funny and harmless, not mean. One sentence only."
        )
        await update.message.reply_text(await asyncio.to_thread(_call_groq_fun, prompt))
    except Exception as e:
        logger.warning("Groq roast failed: %s", e)
        await update.message.reply_text(fallback)


# ── /roastmax ─────────────────────────────────────────────────────────────────

async def roastmax_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Savage comedy-roast command, restricted to authorised users only.
    Authorised user IDs are read from the ROASTMAX_ALLOWED_IDS env var.
    Supports @mention, reply-to-message (with message context), or self-roast.
    """
    user_id = update.effective_user.id

    if not is_roastmax_allowed(user_id):
        return

    if not groq_client:
        await update.message.reply_text(
            "⚠️ AI is not configured. Ask the admin to set up `GROQ_API_KEY`."
        )
        return

    target, msg_context = _resolve_roast_target(update, context)

    try:
        result = await asyncio.to_thread(_call_groq_roastmax, target, msg_context)
        await update.message.reply_text(f"🔥 {result}")
    except Exception as e:
        logger.warning("Groq roastmax failed: %s", e)
        await update.message.reply_text(
            f"💀 The roast machine broke but {_escape_md(target)} should still feel bad about themselves.",
            parse_mode="Markdown",
        )


# ── /compliment ───────────────────────────────────────────────────────────────

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


# ── /vibecheck ────────────────────────────────────────────────────────────────

async def vibecheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rng = _daily_rng("vibecheck", update.effective_chat.id)
    score = rng.randint(0, 100)
    mood = next(msg for limit, msg in VIBE_TIERS if score <= limit)
    await update.message.reply_text(f"📡 Vibe Check\nGroup mood: {score}/100\n{mood}")


# ── /rank ─────────────────────────────────────────────────────────────────────

def _parse_rank_text(text: str):
    title, item_text = ("Random Ranking", text)
    if ":" in text:
        title, item_text = [p.strip() for p in text.split(":", 1)]
        title = title or "Random Ranking"
    if "," in item_text:
        items = [i.strip() for i in item_text.split(",") if i.strip()]
    else:
        items = [i.strip() for i in item_text.split() if i.strip()]
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


# ── /truth, /dare, /wouldyourather ───────────────────────────────────────────

async def truth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = random.choice(TRUTH_QUESTIONS)
    await update.message.reply_text(f"🧃 *Truth*\n\n{q}", parse_mode="Markdown")


async def dare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = random.choice(DARE_PROMPTS)
    await update.message.reply_text(f"🎬 *Dare*\n\n{d}", parse_mode="Markdown")


async def would_you_rather_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = random.choice(WOULD_YOU_RATHER_PROMPTS)
    await update.message.reply_text(f"⚖️ *Would You Rather*\n\n{q}", parse_mode="Markdown")


# ── /coinflip ─────────────────────────────────────────────────────────────────

async def coinflip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🪙 Flipping...")
    await asyncio.sleep(1)
    result = random.choice(["Heads", "Tails"])
    icon = "🌕" if result == "Heads" else "🌑"
    await msg.edit_text(f"🪙 *Coinflip*\n\n{icon} *{result}*", parse_mode="Markdown")


# ── /8ball ────────────────────────────────────────────────────────────────────

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


# ── /curse & /bless ───────────────────────────────────────────────────────────

async def curse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(CURSE_LINES).format(target=target))


async def bless_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = _target_from_mention_or_sender(update, context)
    await update.message.reply_text(random.choice(BLESS_LINES).format(target=target))


# ── /mvp ─────────────────────────────────────────────────────────────────────

async def mvp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    saved_winner = await asyncio.to_thread(get_today_mvp, chat_id, today_str)
    if saved_winner:
        winner_name = _display_name_or_id(saved_winner.get("name", ""), saved_winner.get("user_id", "unknown"))
        await update.message.reply_text(
            f"🏆 *Today's MVP — {_escape_md(winner_name)}*\n{random.choice(MVP_LINES)}",
            parse_mode="Markdown",
        )
        return

    rng = random.Random(f"mvp:{chat_id}:{today_str}")
    candidate_pool: dict = {}
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for member in admins:
            u = member.user
            if not u.is_bot:
                name = _display_user(u)
                candidate_pool[str(u.id)] = name
                task = asyncio.create_task(asyncio.to_thread(track_seen_user, chat_id, u.id, name))
                task.add_done_callback(_log_background_failure("track_seen_user"))
    except Exception as e:
        logger.warning("Could not fetch admins for mvp in chat %s: %s", chat_id, e)
    seen = await asyncio.to_thread(get_seen_users, chat_id)
    for uid, name in seen.items():
        safe_name = _display_name_or_id(name, uid)
        if uid not in candidate_pool or candidate_pool[uid] == str(uid):
            candidate_pool[uid] = safe_name
    if not candidate_pool:
        await update.message.reply_text("⚠️ Not enough members tracked yet. Have people use a command first!")
        return
    winner_id = rng.choice(list(candidate_pool.keys()))
    winner_name = _display_name_or_id(candidate_pool[winner_id], winner_id)
    await asyncio.to_thread(save_mvp_win, chat_id, today_str, winner_id, winner_name)
    await update.message.reply_text(
        f"🏆 *Today's MVP — {_escape_md(winner_name)}*\n{rng.choice(MVP_LINES)}",
        parse_mode="Markdown",
    )


async def mvpboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await asyncio.to_thread(get_mvp_board, update.effective_chat.id, 10)
    if not rows:
        await update.message.reply_text("🏆 No MVP wins recorded yet. Use /mvp to crown today's winner.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *MVP Board*\n"]
    for i, row in enumerate(rows, 1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        name = _display_name_or_id(row.get("name", ""), row.get("user_id", "unknown"))
        wins = int(row.get("wins", 0))
        last_won = row.get("last_won", "unknown")
        lines.append(
            f"{rank} *{_escape_md(name)}* — `{wins}` win{'s' if wins != 1 else ''}\n"
            f"   Last: {_escape_md(last_won)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /hot ──────────────────────────────────────────────────────────────────────

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
        f"🌡️ *Hot or Not — {_escape_md(text)}*\nScore: {score}/100\n{verdict}",
        parse_mode="Markdown",
    )


# ── /predict ──────────────────────────────────────────────────────────────────

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _arg_text(context)
    if not text:
        await update.message.reply_text(
            "Usage: `/predict <scenario>`\n"
            "Examples:\n"
            "• `/predict will I pass my exam`\n"
            "• `/predict who wins the group trivia tonight`\n"
            "• `/predict should I sleep early`",
            parse_mode="Markdown",
        )
        return
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng       = random.Random(f"predict:{_normalize_target(text)}:{today_str}")
    fallback  = rng.choice(PREDICT_FALLBACK_LINES)
    if not groq_client:
        await update.message.reply_text(
            f"🔮 *Prediction*\n\n_{_escape_md(text)}_\n\n{fallback}",
            parse_mode="Markdown",
        )
        return
    try:
        prompt = (
            f"Give a short, dramatic, entertaining prediction for this scenario: '{text}'. "
            "Be confident and creative. Max 2 sentences. "
            "No markdown, no bullet points, plain text only."
        )
        result = await asyncio.to_thread(_call_groq_fun, prompt)
        await update.message.reply_text(
            f"🔮 *Prediction*\n\n_{_escape_md(text)}_\n\n{result}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Groq predict failed: %s", e)
        await update.message.reply_text(
            f"🔮 *Prediction*\n\n_{_escape_md(text)}_\n\n{fallback}",
            parse_mode="Markdown",
        )


# ── /decide ───────────────────────────────────────────────────────────────────

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


# ── /poll ─────────────────────────────────────────────────────────────────────

async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id  = update.effective_user.id
    chat_id  = update.effective_chat.id
    ck = (chat_id, user_id)
    now = _time.monotonic()
    _purge_poll_cooldowns(now)
    remaining = _POLL_COOLDOWN_SECONDS - (now - _POLL_COOLDOWNS.get(ck, 0))
    if remaining > 0:
        await update.message.reply_text(
            f"⚠️ Please wait {remaining:.0f}s before sending another poll."
        )
        return
    if len(_POLL_COOLDOWNS) >= _POLL_COOLDOWNS_MAX:
        evict = _POLL_COOLDOWNS_MAX // 10
        for old_key in list(_POLL_COOLDOWNS)[:evict]:
            del _POLL_COOLDOWNS[old_key]
    _POLL_COOLDOWNS[ck] = now

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

    warnings = []
    if len(options) > _POLL_MAX_OPTIONS:
        warnings.append(
            f"⚠️ Only the first {_POLL_MAX_OPTIONS} options will be used "
            f"(you gave {len(options)})."
        )
    if len(question) > _POLL_MAX_QUESTION_LEN:
        warnings.append(f"⚠️ Question trimmed to {_POLL_MAX_QUESTION_LEN} characters.")
    truncated_opts = [o for o in options[:_POLL_MAX_OPTIONS] if len(o) > _POLL_MAX_OPTION_LEN]
    if truncated_opts:
        warnings.append(
            f"⚠️ {len(truncated_opts)} option(s) trimmed to {_POLL_MAX_OPTION_LEN} characters."
        )
    if warnings:
        await update.message.reply_text("\n".join(warnings))

    try:
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question[:_POLL_MAX_QUESTION_LEN],
            options=[o[:_POLL_MAX_OPTION_LEN] for o in options[:_POLL_MAX_OPTIONS]],
            is_anonymous=False,
        )
    except TelegramError as e:
        logger.warning("send_poll failed in chat %s: %s", chat_id, e)
        await update.message.reply_text(
            "⚠️ I couldn't create that poll here. Check my poll permission and try again."
        )


# ── /toss ─────────────────────────────────────────────────────────────────────

async def toss_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    targets = []
    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            if not entity.user.is_bot:
                targets.append(_display_user(entity.user))
        elif entity.type == "mention":
            mention = message.parse_entity(entity)
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
        f"🎰 *The Pick*\n\n➡️ *{_escape_md(chosen)}*\n\n_{random.choice(TOSS_VERDICTS)}_",
        parse_mode="Markdown",
    )


# ── /game — Number guessing ────────────────────────────────────────────────────

_ACTIVE_GAMES: dict = {}
_GAME_RANGE = (1, 100)
_GAME_TIMEOUT_SECONDS = 60


async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a number guessing game in the group."""
    chat_id = update.effective_chat.id
    if chat_id in _ACTIVE_GAMES:
        lo, hi = _GAME_RANGE
        await update.message.reply_text(
            f"🎮 A game is already running!\nJust type a number between *{lo}* and *{hi}* to guess.",
            parse_mode="Markdown",
        )
        return

    lo, hi = _GAME_RANGE
    number   = random.randint(lo, hi)
    job_name = f"game_reveal:{chat_id}"
    _ACTIVE_GAMES[chat_id] = {"number": number, "job_name": job_name, "timer_version": 1}

    async def _reveal(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _n=number, _version=1) -> None:
        game = _ACTIVE_GAMES.get(_cid)
        if game and game.get("timer_version") == _version:
            _ACTIVE_GAMES.pop(_cid, None)
            await ctx.bot.send_message(
                chat_id=_cid,
                text=(
                    f"⏰ *Time's up!* Nobody guessed it.\n"
                    f"The number was *{_n}*. Better luck next time!\n\n"
                    f"_Start a new game with /game_"
                ),
                parse_mode="Markdown",
            )

    context.application.job_queue.run_once(
        _reveal, when=_GAME_TIMEOUT_SECONDS, name=job_name, chat_id=chat_id
    )
    await update.message.reply_text(
        f"🎮 *Number Guessing Game!*\n\n"
        f"I'm thinking of a number between *{lo}* and *{hi}*.\n"
        f"Type a number to guess — I'll say 📈 Higher or 📉 Lower!\n\n"
        f"_No guesses for {_GAME_TIMEOUT_SECONDS}s and I'll reveal the answer._",
        parse_mode="Markdown",
    )


async def game_guess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Process number guesses for an active game. Registered in handler group 2
    so it runs alongside group-0 handlers without blocking or being blocked by them.
    """
    chat_id = update.effective_chat.id
    if chat_id not in _ACTIVE_GAMES:
        return

    text = (update.message.text or "").strip()

    if not text.lstrip("-").isdigit():
        return

    try:
        guess = int(text)
    except ValueError:
        return

    lo, hi = _GAME_RANGE
    if not (lo <= guess <= hi):
        await update.message.reply_text(f"⚠️ Guess must be between {lo} and {hi}!")
        return

    game     = _ACTIVE_GAMES[chat_id]
    number   = game["number"]
    job_name = game["job_name"]
    game["timer_version"] = int(game.get("timer_version", 0)) + 1
    timer_version = game["timer_version"]
    user_name = _display_user(update.effective_user)

    for job in context.application.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    if guess == number:
        _ACTIVE_GAMES.pop(chat_id, None)
        await update.message.reply_text(
            f"🎉 *{_escape_md(user_name)} got it!* The number was *{number}*! 🏆\n\n"
            f"_Start another round with /game_",
            parse_mode="Markdown",
        )
        return

    hint = "📈 Higher!" if guess < number else "📉 Lower!"
    await update.message.reply_text(hint)

    async def _reveal(ctx: ContextTypes.DEFAULT_TYPE, _cid=chat_id, _n=number, _version=timer_version) -> None:
        game = _ACTIVE_GAMES.get(_cid)
        if game and game.get("timer_version") == _version:
            _ACTIVE_GAMES.pop(_cid, None)
            await ctx.bot.send_message(
                chat_id=_cid,
                text=(
                    f"⏰ *Time's up!* Nobody guessed it.\n"
                    f"The number was *{_n}*. Better luck next time!\n\n"
                    f"_Start a new game with /game_"
                ),
                parse_mode="Markdown",
            )

    context.application.job_queue.run_once(
        _reveal, when=_GAME_TIMEOUT_SECONDS, name=job_name, chat_id=chat_id
    )