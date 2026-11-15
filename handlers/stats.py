"""
handlers/stats.py
─────────────────
Read-only reporting commands: /leaderboard, /recap, /stats, /profile.

STRUCTURAL CHANGE (refactor):
    Extracted verbatim from the old ``handlers/misc.py``. These handlers all
    share the same shape — fan out several Redis reads with asyncio.gather,
    tolerate individual failures, then format a Markdown report — so keeping
    them together makes that pattern obvious. They now own their own store
    imports instead of importing everything through a catch-all module.
"""

import asyncio
import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import TIMEZONE
from helpers import _days_label, _display_name_or_id, _display_user, _escape_md
from handlers.luck import _get_fate
from stores.countdown_store import get_all_countdowns
from stores.luck_store import get_fate_board, get_fate_streak, get_luck_check_count
from stores.ship_store import (
    get_shipboard_reset_time, get_top_ship_pairs, get_user_ship_stats,
)
from stores.user_store import get_seen_users

logger = logging.getLogger(__name__)

_MEDALS = ["🥇", "🥈", "🥉"]


# ── /leaderboard ──────────────────────────────────────────────────────────────

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    luck_board, pairs = await asyncio.gather(
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 3),
        return_exceptions=True,
    )
    for value in (luck_board, pairs):
        if isinstance(value, Exception):
            logger.error("leaderboard_command gather error: %s", value)

    lines = [f"🏆 *Group Leaderboard* — {today_str}\n"]

    lines.append("🍀 *Today's Luck*")
    board = luck_board if isinstance(luck_board, dict) else {}
    if board:
        sorted_board = sorted(board.items(), key=lambda kv: kv[1]["score"], reverse=True)
        for i, (_, item) in enumerate(sorted_board[:3], 1):
            medal = _MEDALS[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{medal} *{_escape_md(item['name'])}* — {item['tier']} `{item['score']}`")
    else:
        lines.append("_No checks yet today. Use /luck!_")

    lines.append("\n💞 *Top Ships*")
    ship_list = pairs if isinstance(pairs, list) else []
    if ship_list:
        for i, pair in enumerate(ship_list, 1):
            medal = _MEDALS[i - 1] if i <= 3 else f"{i}."
            a = _escape_md(pair["label_a"])
            b = _escape_md(pair["label_b"])
            lines.append(f"{medal} *{a}* × *{b}* `{pair['score']:.1f}%`")
    else:
        lines.append("_No ships yet. Use /ship!_")

    lines.append("\n_Use /luckboard · /shipboard for full lists_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /recap ────────────────────────────────────────────────────────────────────

async def recap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    (
        luck_board, pairs, seen, countdowns,
    ) = await asyncio.gather(
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_all_countdowns, chat_id),
        return_exceptions=True,
    )

    board      = luck_board if isinstance(luck_board, dict) else {}
    ship_list  = pairs      if isinstance(pairs,      list) else []
    seen_users = seen       if isinstance(seen,       dict) else {}
    cd_dict    = countdowns if isinstance(countdowns, dict) else {}

    lines = [f"📋 *Daily Recap — {today_str}*\n"]

    if board:
        sorted_board = sorted(board.items(), key=lambda kv: kv[1]["score"], reverse=True)
        luckiest = sorted_board[0][1]
        unluckiest = sorted_board[-1][1]
        active = len(board)
        lines.append(
            f"🍀 *Luckiest:* {_escape_md(luckiest['name'])} — {luckiest['tier']} `{luckiest['score']}`"
        )
        if active > 1:
            lines.append(
                f"💀 *Unluckiest:* {_escape_md(unluckiest['name'])} — {unluckiest['tier']} `{unluckiest['score']}`"
            )
        lines.append(f"👥 *Luck checks today:* {active}")
    else:
        lines.append("🍀 *Luck:* _Nobody checked yet — use /luck_")

    if ship_list:
        p = ship_list[0]
        a = _escape_md(p["label_a"])
        b = _escape_md(p["label_b"])
        lines.append(f"💞 *Top Ship:* {a} × {b} `{p['score']:.1f}%`")
    else:
        lines.append("💞 *Ships:* _None yet — use /ship_")

    today_date = datetime.now(TIMEZONE).date()
    next_countdown = None
    min_days = float("inf")
    for cd_name, entry in cd_dict.items():
        try:
            td = date.fromisoformat(entry["target_date"])
            days_left = (td - today_date).days
            if 0 <= days_left < min_days:
                min_days = days_left
                next_countdown = (cd_name, days_left)
        except (ValueError, KeyError):
            pass

    if next_countdown:
        cd_name, days_left = next_countdown
        lines.append(f"⏱ *Next Countdown:* {_escape_md(cd_name)} — _{_days_label(days_left)}_")
    else:
        lines.append("⏱ *Countdowns:* _None active — use /addcountdown_")

    lines.append(f"👤 *Members tracked:* {len(seen_users)}")
    lines.append("\n_Run /leaderboard for the full rankings_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /stats ────────────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    results = await asyncio.gather(
        asyncio.to_thread(get_seen_users, chat_id),
        asyncio.to_thread(get_top_ship_pairs, chat_id, 1),
        asyncio.to_thread(get_fate_board, chat_id, today_str),
        asyncio.to_thread(get_all_countdowns, chat_id),
        return_exceptions=True,
    )
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("stats_command gather[%s] failed: %s", i, result)
    seen_users = results[0] if not isinstance(results[0], Exception) else {}
    top_pairs  = results[1] if not isinstance(results[1], Exception) else []
    board      = results[2] if not isinstance(results[2], Exception) else {}
    countdowns = results[3] if not isinstance(results[3], Exception) else {}

    reset_secs = await asyncio.to_thread(get_shipboard_reset_time)

    member_count = len(seen_users)
    countdown_count = len(countdowns)
    reset_h = reset_secs // 3600
    reset_m = (reset_secs % 3600) // 60

    if top_pairs:
        p = top_pairs[0]
        ship_line = f"{_escape_md(p['label_a'])} × {_escape_md(p['label_b'])} `{p['score']:.1f}%`"
    else:
        ship_line = "No ships yet this cycle"

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
        f"⏱ Active countdowns: *{countdown_count}*",
        "\n🍀 *Today's Luck*",
        f"Checks: *{luck_count}*",
        f"Luckiest: {lucky_line}",
        f"\n💞 *Ships* _(resets in {reset_h}h {reset_m}m)_",
        f"Top pair: {ship_line}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /profile ──────────────────────────────────────────────────────────────────

def _profile_target(update: Update):
    """Return (user, username_only) for a text_mention / @mention / sender."""
    message = update.message
    for entity in message.entities or []:
        if entity.type == "text_mention" and getattr(entity, "user", None):
            return entity.user, None
        if entity.type == "mention":
            return None, message.parse_entity(entity)
    return update.effective_user, None


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    target_user, username_only = _profile_target(update)
    if username_only and target_user is None:
        await update.message.reply_text(
            "⚠️ I can only show profiles for direct mentions or yourself.\n"
            "Try tapping the user from Telegram's mention picker, or use /profile for your own stats."
        )
        return

    user_id = target_user.id
    name = _display_user(target_user)
    today = datetime.now(TIMEZONE).date()

    streak, ship_stats, luck_checks = await asyncio.gather(
        asyncio.to_thread(get_fate_streak, user_id),
        asyncio.to_thread(get_user_ship_stats, chat_id, name),
        asyncio.to_thread(get_luck_check_count, user_id),
    )

    streak_count, streak_cat = streak
    streak_line = f"{streak_count} day(s) {streak_cat}" if streak_count else "No active streak"
    seen = await asyncio.to_thread(get_seen_users, chat_id)
    seen_name = _display_name_or_id(seen.get(str(user_id), name), user_id)

    # Ship stats cover the current 48-hour window only.
    ship_appearances = ship_stats.get("appearances", 0) if isinstance(ship_stats, dict) else 0
    best_score = ship_stats.get("best_score", 0.0) if isinstance(ship_stats, dict) else 0.0
    best_partner = ship_stats.get("best_partner", "") if isinstance(ship_stats, dict) else ""
    if ship_appearances > 0 and best_partner:
        ship_line = f"*{ship_appearances}* _(best: {_escape_md(best_partner)} `{best_score:.1f}%`)_"
    elif ship_appearances > 0:
        ship_line = f"*{ship_appearances}*"
    else:
        ship_line = "*0* _(not shipped yet this cycle)_"

    total_luck_checks = luck_checks if isinstance(luck_checks, int) else 0

    luck_score, luck_tier, _ = _get_fate(user_id)
    if luck_score == 999:
        luck_display = "999 ⚡ MAXIMUM"
    elif luck_score == -999:
        luck_display = "-999 💀 MINIMUM"
    else:
        luck_display = f"{luck_score}/100"

    await update.message.reply_text(
        "\n".join([
            f"👤 *Profile — {_escape_md(seen_name)}*",
            f"🍀 Today's luck: *{_escape_md(luck_tier)}* (`{luck_display}`)",
            f"📈 Luck streak: *{_escape_md(streak_line)}*",
            f"🎯 Total luck checks: *{total_luck_checks}*",
            f"💞 Ships this cycle: {ship_line}",
            f"📆 Today: *{today.isoformat()} MYT*",
        ]),
        parse_mode="Markdown",
    )
