"""
handlers/bug.py
───────────────
Global bug reporting: /bug, plus the owner moderation tools /banbug and
/unbanbug.

STRUCTURAL CHANGE (new feature):
    /bug is available to *everyone*, in private chats and in any group. Reports
    are forwarded to ``BUG_ADMIN_ID`` (falls back to ``BOT_OWNER_ID``) with the
    reporter's identity in copyable inline-code, so the developer can paste the
    ID straight into a ban command. All moderation state lives in
    ``stores/bug_store.py`` (Redis), not in process memory — cooldowns and bans
    therefore survive a restart or a Render redeploy.

    Flow: ban check → description validation → atomic cooldown claim → forward.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from config import BUG_ADMIN_ID, BUG_COOLDOWN_SECONDS, TIMEZONE
from helpers import _display_user, _escape_md, owner_only
from stores.bug_store import (
    ban_bug_user, claim_bug_cooldown, is_bug_banned, release_bug_cooldown,
    unban_bug_user,
)

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_LENGTH = 1000


# ── Admin-facing message ──────────────────────────────────────────────────────

def build_bug_report(
    description: str,
    *,
    display_name: str,
    username: str,
    user_id: int,
    chat_label: str,
    chat_id: int,
    timestamp: str,
) -> str:
    """
    Format a report for the developer.

    User-supplied strings are Markdown-escaped; the user ID and chat ID are
    deliberately wrapped in backticks so Telegram renders them as tappable,
    easy-to-copy inline code. The ready-made ban commands are included for the
    same reason.
    """
    who = _escape_md(f"@{username}") if username else "_(no username)_"
    return "\n".join([
        "🐞 *New Bug Report*",
        "",
        "📝 *Description*",
        _escape_md(description),
        "",
        f"👤 *From:* {_escape_md(display_name)} — {who}",
        f"🆔 *User ID:* `{user_id}`",
        f"💬 *Chat:* {_escape_md(chat_label)} · `{chat_id}`",
        f"🕐 *When:* {timestamp} MYT",
        "",
        f"🚫 Block /bug: `/banbug {user_id}`",
        f"🔨 Ban from bot: `/ban {user_id}`",
    ])


def _chat_label(update: Update) -> str:
    chat = update.effective_chat
    if chat is None:
        return "Unknown chat"
    if chat.type == "private":
        return "Private chat"
    return chat.title or f"Group {chat.id}"


# ── /bug ─────────────────────────────────────────────────────────────────────

async def bug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a report from any chat and forward it to the developer."""
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or user.is_bot:
        return

    # Check configuration first so a failed report never consumes a cooldown.
    if not BUG_ADMIN_ID:
        await message.reply_text(
            "⚠️ Bug reporting isn't set up yet. Ask the bot owner to set "
            "`BUG_ADMIN_ID` (or `BOT_OWNER_ID`).",
            parse_mode="Markdown",
        )
        return

    # Silently ignore users blocked with /banbug — no feedback loop for spammers.
    if await asyncio.to_thread(is_bug_banned, user.id):
        logger.info("bug_command: dropped report from blocked user %s", user.id)
        return

    description = " ".join(context.args or []).strip()
    if not description:
        await message.reply_text(
            "🐞 *Report a Bug*\n\n"
            "Usage: `/bug <what went wrong>`\n"
            "_e.g. `/bug /luck shows 0 after midnight`_\n\n"
            "Works here, in any group, or in a private chat with me.",
            parse_mode="Markdown",
        )
        return

    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.reply_text(
            f"⚠️ Please keep reports under {MAX_DESCRIPTION_LENGTH} characters."
        )
        return

    outcome = await asyncio.to_thread(claim_bug_cooldown, user.id, BUG_COOLDOWN_SECONDS)
    if outcome < 0:
        # Redis is unavailable: fail closed rather than opening a spam window.
        await message.reply_text(
            "⚠️ Reporting is temporarily unavailable. Please try again shortly."
        )
        return
    if outcome > 0:
        minutes, seconds = divmod(outcome, 60)
        wait = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        await message.reply_text(
            f"⏳ Please wait {wait} before sending another bug report."
        )
        return

    report = build_bug_report(
        description,
        display_name=_display_user(user),
        username=user.username or "",
        user_id=user.id,
        chat_label=_chat_label(update),
        chat_id=chat.id,
        timestamp=datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M"),
    )

    try:
        await context.bot.send_message(
            chat_id=BUG_ADMIN_ID, text=report, parse_mode="Markdown",
        )
    except Exception as e:
        # Delivery failed (bad admin ID, bot blocked, Markdown rejected...).
        # Give the cooldown slot back so the reporter can retry.
        logger.warning("bug_command: could not forward to %s: %s", BUG_ADMIN_ID, e)
        await asyncio.to_thread(release_bug_cooldown, user.id)
        await message.reply_text(
            "⚠️ Couldn't deliver your report just now. Please try again in a moment."
        )
        return

    await message.reply_text("✅ Thanks! Your bug report was sent to the developer.")
    logger.info("bug report forwarded: user=%s chat=%s", user.id, chat.id)


# ── /banbug & /unbanbug (owner only) ─────────────────────────────────────────

def _resolve_bug_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept a numeric ID, a text_mention, or a reply — same rules as /ban."""
    message = update.message
    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            return entity.user.id
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if context.args and context.args[0].lstrip("-").isdigit():
        return int(context.args[0])
    return None


@owner_only
async def banbug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block a user from submitting /bug reports."""
    user_id = _resolve_bug_target(update, context)
    if user_id is None:
        await update.message.reply_text(
            "Usage: `/banbug <user_id>`\n"
            "_Or reply to one of their messages with /banbug._",
            parse_mode="Markdown",
        )
        return
    blocked = await asyncio.to_thread(ban_bug_user, user_id)
    if blocked:
        await update.message.reply_text(
            f"🚫 `{user_id}` can no longer use /bug.", parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ `{user_id}` was already blocked from /bug.", parse_mode="Markdown",
        )


@owner_only
async def unbanbug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore /bug access for a previously blocked user."""
    user_id = _resolve_bug_target(update, context)
    if user_id is None:
        await update.message.reply_text(
            "Usage: `/unbanbug <user_id>`\n"
            "_Or reply to one of their messages with /unbanbug._",
            parse_mode="Markdown",
        )
        return
    restored = await asyncio.to_thread(unban_bug_user, user_id)
    if restored:
        await update.message.reply_text(
            f"✅ `{user_id}` can use /bug again.", parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ `{user_id}` wasn't blocked from /bug.", parse_mode="Markdown",
        )
