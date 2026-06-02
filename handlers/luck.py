"""
handlers/luck.py
────────────────
Luck system: /luck, /luckboard, /streak, and deprecated /fate stubs.
"""

import asyncio
import logging
import random
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import TIMEZONE
from constants import (
    FATE_TIERS, FATE_EXTREME_LUCKY_MESSAGES, FATE_EXTREME_UNLUCKY_MESSAGES,
    _SPECIAL_SCORE_CASES,
)
from stores.luck_store import (
    save_fate_entry, get_fate_board,
    update_fate_streak, get_fate_streak,
    delete_old_fateboard_keys,
    increment_luck_checks,
)
from stores.user_store import track_seen_user
from stores.birthday_store import get_all_birthdays

from helpers import (
    _display_user, _mentioned_target, _normalize_target, _today,
    _escape_md,
    BOT_OWNER_ID, _is_owner, owner_only, _arg_text,
)
from config import env_int

logger = logging.getLogger(__name__)


def _log_background_failure(label: str):
    def _callback(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("%s failed: %s", label, exc)
    return _callback


FATE_LUCKY_ID = env_int("FATE_LUCKY_ID", 0)
FATE_UNLUCKY_ID = env_int("FATE_UNLUCKY_ID", 0)


# ── Core luck engine ──────────────────────────────────────────────────────────

def _get_fate_by_seed(seed: str):
    """Compute daily luck for any string seed (user_id str or normalised username)."""
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    rng = random.Random(f"{seed}:{today_str}")
    extreme_roll = rng.randint(1, 100)  # ~1% chance of extreme result (was 1/30 ≈ 3.3%)
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


# Fix 13: _BIRTHDAY_SCORE must be defined BEFORE _score_display uses it.
# Previously it was declared after the function — fragile even though Python
# resolves names at call-time, not definition-time.
_BIRTHDAY_SCORE = 101  # sentinel — outside 0-100 range, never in _SPECIAL_SCORE_CASES


def _score_display(score: int) -> str:
    if score == 999:
        return "999 ⚡ MAXIMUM"
    if score == -999:
        return "-999 💀 MINIMUM"
    if score == _BIRTHDAY_SCORE:
        return "🎂 MAX"
    filled = round(max(0, min(score, 100)) / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{score}/100  [{bar}]"


def _apply_special_luck(score, tier, luck_msg, target_name, today, seed=""):
    """Apply brainrot special cases on top of the base luck result.

    Birthday overrides (score == _BIRTHDAY_SCORE) are always preserved —
    day specials like New Year cannot clobber a birthday.
    """
    day_note = ""
    is_april_fools = False

    # Birthday takes total priority — skip all day/score overrides
    if score == _BIRTHDAY_SCORE:
        return score, tier, luck_msg, day_note, is_april_fools

    # New Year (Jan 1) — always max luck
    if today.month == 1 and today.day == 1:
        return (
            999, "🎆 NEW YEAR",
            "New year. Same you. But the luck reset so technically fresh start. "
            "The universe gives everyone max luck today. Enjoy it while it lasts.",
            "", False,
        )

    # April Fools — keep real score, set flag for reveal
    if today.month == 4 and today.day == 1:
        is_april_fools = True

    # Score-based specials (only for normal 0-100 scores)
    if score in _SPECIAL_SCORE_CASES and score not in (999, -999):
        tier, luck_msg = _SPECIAL_SCORE_CASES[score]

    return score, tier, luck_msg, day_note, is_april_fools


def _luck_result_text(target_name, tier, score, luck_msg,
                      streak_line="", day_note="", checking_other=False,
                      username_mention=False) -> str:
    target_safe = _escape_md(target_name)
    parts = [
        f"🍀 *Daily Luck — {target_safe}*\n"
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
        parts.append("\n\n_⚠️ Their score won't appear on /luckboard until they run /luck themselves._")
        # Fix 11: username-only mentions can't trigger the birthday override because
        # the bot has no user_id to look up — surface this clearly.
        if username_mention:
            parts.append(
                "\n_🎂 Birthday check skipped — have them use /luck directly for birthday luck._"
            )
    else:
        parts.append("\n\n_Scores reset daily at midnight MYT · /streak to track your run_")
    return "".join(parts)


# ── /luck ─────────────────────────────────────────────────────────────────────

async def luck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user

    target_user_id = None
    target_name = None
    checking_other = False
    # Fix 11: track whether we resolved via @username (no user_id available)
    is_username_mention = False

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
            target_user_id = None
            checking_other = True
            is_username_mention = True   # Fix 11: @username path — no user_id
        else:
            target_user_id = user.id
            target_name = user.first_name or user.username or "You"
            checking_other = False

    if target_user_id is not None:
        score, tier, luck_msg = _get_fate(target_user_id)
    else:
        score, tier, luck_msg = _get_fate_by_seed(_normalize_target(target_name))

    today = _today()
    today_str = today.strftime("%Y-%m-%d")
    seed = str(target_user_id) if target_user_id else _normalize_target(target_name)

    # Birthday override — applies whenever we have the actual user ID (own or other).
    # Fix 4: was incorrectly restricted to self-luck only; text_mention gives us
    # the real user ID for others too, so the override should fire for them as well.
    # Fix 11: @username mentions (is_username_mention=True) skip this block because
    # target_user_id is None — the note in _luck_result_text tells the user why.
    if target_user_id:
        bdays = await asyncio.to_thread(get_all_birthdays, update.effective_chat.id)
        bday = bdays.get(str(target_user_id))
        if bday and bday.get("day") == today.day and bday.get("month") == today.month:
            score = _BIRTHDAY_SCORE
            tier = "🎂 BIRTHDAY LEGEND"
            luck_msg = (
                "It's their birthday! The universe has no choice but to comply. "
                "Maximum luck, no exceptions. They earned this one."
                if checking_other else
                "It's your birthday. The universe has no choice but to comply. "
                "Maximum luck, no exceptions. You earned this one."
            )

    score, tier, luck_msg, day_note, is_april_fools = _apply_special_luck(
        score, tier, luck_msg, target_name, today, seed=seed
    )

    streak_line = ""
    if not checking_other:
        if score >= 65 or score in (999, _BIRTHDAY_SCORE):
            tier_category = "lucky"
        elif score <= 35 or score == -999:
            tier_category = "unlucky"
        else:
            tier_category = "neutral"

        t1 = asyncio.create_task(
            asyncio.to_thread(_remember_fate, update.effective_chat.id, target_user_id, target_name, score, tier)
        )
        t1.add_done_callback(_log_background_failure("_remember_fate"))
        t2 = asyncio.create_task(
            asyncio.to_thread(track_seen_user, update.effective_chat.id, target_user_id, target_name)
        )
        t2.add_done_callback(_log_background_failure("track_seen_user"))

        t3 = asyncio.create_task(
            asyncio.to_thread(increment_luck_checks, target_user_id)
        )
        t3.add_done_callback(_log_background_failure("increment_luck_checks"))

        old_streak, old_cat = await asyncio.to_thread(get_fate_streak, target_user_id)
        streak = await asyncio.to_thread(_update_streak_sync, target_user_id, today_str, tier_category)

        if old_streak >= 2 and streak == 1 and old_cat != "neutral":
            broken_icon = "🔥" if old_cat == "lucky" else "💀"
            streak_line = (
                f"\n{broken_icon} *Your {old_streak}-day {old_cat} streak has ended.*"
            )
        elif streak >= 2:
            if tier_category == "lucky":
                streak_line = f"\n🔥 *Lucky streak: {streak} days in a row!*"
            elif tier_category == "unlucky":
                streak_line = f"\n💀 *Unlucky streak: {streak} days in a row...*"

    real_text = _luck_result_text(
        target_name, tier, score, luck_msg,
        streak_line=streak_line, day_note=day_note,
        checking_other=checking_other,
        username_mention=is_username_mention,  # Fix 11
    )

    if is_april_fools:
        fake_rng = random.Random(f"aprilfools:{seed}:{today_str}")
        fake_score = fake_rng.randint(0, 8)
        fake_tier = "💀 CURSED"
        fake_msg = fake_rng.choice(FATE_TIERS[0]["messages"])
        fake_text = _luck_result_text(target_name, fake_tier, fake_score, fake_msg)
        sent = await message.reply_text(fake_text, parse_mode="Markdown")
        await asyncio.sleep(3)
        await sent.edit_text(f"🎭 *April Fools!*\n\n{real_text}", parse_mode="Markdown")
    else:
        await message.reply_text(real_text, parse_mode="Markdown")


# ── /luckboard ────────────────────────────────────────────────────────────────

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
    total = len(board)
    lines = [f"🍀 *Today's Luckboard* _({total} checked)_\n"]
    for i, (uid, item) in enumerate(sorted_items[:10], 1):
        rank_icon = medals[i - 1] if i <= 3 else f"{i}."
        s = item["score"]
        streak_count, streak_cat = streak_map.get(str(uid), (0, "neutral"))
        streak_badge = ""
        if streak_count >= 2:
            streak_badge = (
                f" 🔥×{streak_count}" if streak_cat == "lucky"
                else f" 💀×{streak_count}" if streak_cat == "unlucky"
                else ""
            )
        lines.append(
            f"{rank_icon} *{_escape_md(item['name'])}*{streak_badge} — {item['tier']}\n"
            f"    Score: `{_score_display(s)}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /streak ───────────────────────────────────────────────────────────────────

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
            f"📊 *Streak — {_escape_md(target)}*\nNo active streak yet. Use /luck to start one!",
            parse_mode="Markdown",
        )
        return
    icon, label = (
        ("🔥", "Lucky streak") if category == "lucky"
        else ("💀", "Unlucky streak") if category == "unlucky"
        else ("😐", "Neutral streak")
    )
    await message.reply_text(
        f"📊 *Streak — {_escape_md(target)}*\n"
        f"{icon} {label}: *{streak} day{'s' if streak != 1 else ''}* in a row",
        parse_mode="Markdown",
    )


# ── /lucktest (owner-only) ────────────────────────────────────────────────────

@owner_only
async def lucktest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if raw_score not in (999, -999):
        score = max(0, min(100, raw_score))
    else:
        score = raw_score

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

    target_name = _display_user(update.effective_user)
    today = _today()

    final_score, final_tier, final_msg, day_note, _ = _apply_special_luck(
        score, tier, luck_msg, target_name, today, seed=str(update.effective_user.id)
    )

    result_text = _luck_result_text(target_name, final_tier, final_score, final_msg, day_note=day_note)

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


# ── Deprecated stubs ──────────────────────────────────────────────────────────

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
