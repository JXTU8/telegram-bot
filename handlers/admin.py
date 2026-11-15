"""
handlers/admin.py
─────────────────
Owner-only tools: /status, /ban, /unban, /banlist, /say, /id, /cmdstats.

STRUCTURAL CHANGE (refactor):
    Extracted from ``handlers/misc.py``. Every public function here is guarded
    by the ``@owner_only`` decorator (except /id, which is public), which makes
    the trust boundary visible at a glance. /status now reports the version via
    ``changelog.current_version()`` instead of a hard-coded config constant.
"""

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from changelog import current_version
from config import BUG_ADMIN_ID, SERPER_API_KEY, TIMEZONE
from db import redis
from helpers import (
    BOT_OWNER_ID, _display_user, _escape_md, _is_owner, owner_only,
)
from stores.ban_store import ban_user, get_banned_users, unban_user
from stores.bug_store import get_announced_chats, get_bug_banned_users
from stores.stats_store import get_cmd_stats, get_cmd_stats_today

logger = logging.getLogger(__name__)


# ── /status ───────────────────────────────────────────────────────────────────

def _env_status(name: str) -> str:
    return "set" if os.getenv(name) else "missing"


def _redis_health() -> tuple:
    try:
        if hasattr(redis, "ping"):
            redis.ping()
        else:
            redis.get("__healthcheck__")
        return True, "ok"
    except Exception as e:
        return False, str(e)


@owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    redis_ok, redis_msg = await asyncio.to_thread(_redis_health)
    try:
        job_count = len(context.application.job_queue.jobs())
    except Exception:
        job_count = -1

    # Imported lazily: the Gemini client is created at handlers.ai import time.
    from handlers.ai import gemini_client

    redis_detail = _escape_md(redis_msg[:120].replace("`", "").replace("\n", " "))
    banned_count = len(await asyncio.to_thread(get_banned_users))
    bug_bans, announced_chats = await asyncio.gather(
        asyncio.to_thread(get_bug_banned_users),
        asyncio.to_thread(get_announced_chats),
    )

    lines = [
        "🧪 *Bot Status*",
        f"Version: *{current_version()}*",
        f"Redis: *{'ok' if redis_ok else 'error'}*",
        f"Redis detail: `{redis_detail}`",
        f"Job queue: *{'ok' if context.application.job_queue else 'missing'}*",
        f"Scheduled jobs: *{job_count if job_count >= 0 else 'unknown'}*",
        f"Banned users: *{banned_count}*",
        f"Bug reports →: *{BUG_ADMIN_ID or 'not configured'}*",
        f"Bug-report bans: *{len(bug_bans)}*",
        f"Groups announced: *{len(announced_chats)}*",
        f"BOT\\_TOKEN: *{_env_status('BOT_TOKEN')}*",
        f"Redis URL/token: *{_env_status('UPSTASH_REDIS_REST_URL')}/{_env_status('UPSTASH_REDIS_REST_TOKEN')}*",
        f"Gemini: *{'ready' if gemini_client else 'missing key'}*",
        f"Serper: *{'ready' if SERPER_API_KEY else 'missing key'}*",
        f"Timezone: *{_escape_md(TIMEZONE.zone)}*",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /ban, /unban, /banlist ───────────────────────────────────────────────────

def _resolve_ban_target(update: Update) -> tuple:
    """
    Resolve a ban target from the command message.
    Priority:
      1. text_mention entity (Telegram's mention picker) — has a real user ID
      2. Reply to any message — use the sender's ID
      3. Bare numeric ID passed as the first argument
    Returns (user_id: int | None, display: str).
    """
    message = update.message

    for entity in (message.entities or []):
        if entity.type == "text_mention" and getattr(entity, "user", None):
            user = entity.user
            return user.id, _display_user(user)

    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, _display_user(user)

    args = (message.text or "").split()[1:]
    if args and args[0].lstrip("-").isdigit():
        uid = int(args[0])
        return uid, str(uid)

    return None, ""


@owner_only
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ban — block a user from using any bot command or interaction.
    Usage:
      /ban <user_id>           — by numeric ID
      /ban (reply to message)  — ban whoever sent that message
      /ban @mention            — use Telegram's mention picker (not typed @name)
    """
    user_id, display = _resolve_ban_target(update)
    if user_id is None:
        await update.message.reply_text(
            "Usage:\n"
            "• `/ban <user_id>` — e.g. `/ban 123456789`\n"
            "• Reply to any of their messages + `/ban`\n"
            "• `/ban` with a mention from Telegram's picker\n\n"
            "_Typed @usernames won't work — use the picker or a reply._",
            parse_mode="Markdown",
        )
        return

    # Never allow banning the owner.
    if user_id == BOT_OWNER_ID or _is_owner(
        type("_U", (), {"id": user_id, "username": "", "is_bot": False})()
    ):
        await update.message.reply_text("⚠️ Cannot ban the bot owner.")
        return

    newly_banned = await asyncio.to_thread(ban_user, user_id)
    name_safe = _escape_md(display)
    if newly_banned:
        await update.message.reply_text(
            f"🔨 *{name_safe}* (`{user_id}`) has been banned from using this bot.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ *{name_safe}* (`{user_id}`) is already banned.",
            parse_mode="Markdown",
        )


@owner_only
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore bot access for a previously banned user (same targeting as /ban)."""
    user_id, display = _resolve_ban_target(update)
    if user_id is None:
        await update.message.reply_text(
            "Usage:\n"
            "• `/unban <user_id>`\n"
            "• Reply to any of their messages + `/unban`",
            parse_mode="Markdown",
        )
        return

    was_banned = await asyncio.to_thread(unban_user, user_id)
    name_safe = _escape_md(display)
    if was_banned:
        await update.message.reply_text(
            f"✅ *{name_safe}* (`{user_id}`) has been unbanned.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ *{name_safe}* (`{user_id}`) wasn't banned.",
            parse_mode="Markdown",
        )


@owner_only
async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all currently banned user IDs."""
    banned = await asyncio.to_thread(get_banned_users)
    if not banned:
        await update.message.reply_text("✅ No users are currently banned.")
        return
    ids = "\n".join(f"• `{uid}`" for uid in sorted(banned))
    await update.message.reply_text(
        f"🔨 *Banned users ({len(banned)}):*\n{ids}",
        parse_mode="Markdown",
    )


# ── /say, /id ────────────────────────────────────────────────────────────────

@owner_only
async def say_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /say <chat_id> <message>
    /say <chat_id> <thread_id> <message>
    Example: /say -1003861255064 Hello everyone!
    Example: /say -1003861255064 123 Hello topic!
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "`/say <chat_id> <message>`\n"
            "`/say <chat_id> <thread_id> <message>`\n\n"
            "Example: `/say -1003861255064 Hello!`\n"
            "Example: `/say -1003861255064 123 Hello topic!`",
            parse_mode="Markdown",
        )
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid chat ID. Must be a number.")
        return

    # Second arg is a thread_id only when it is numeric and a message follows.
    thread_id = None
    if len(context.args) >= 3 and context.args[1].lstrip("-").isdigit():
        thread_id = int(context.args[1])
        text = " ".join(context.args[2:])
    else:
        text = " ".join(context.args[1:])

    if not text:
        await update.message.reply_text("⚠️ Message can't be empty.")
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=thread_id,
        )
        reply = f"✅ Sent to `{chat_id}`"
        if thread_id:
            reply += f" (thread `{thread_id}`)"
        await update.message.reply_text(reply + ".", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: `{e}`", parse_mode="Markdown")


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    thread_id = getattr(update.message, "message_thread_id", None)
    text = f"💬 Chat ID: `{chat_id}`"
    if thread_id:
        text += f"\n🧵 Thread ID: `{thread_id}`"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /cmdstats ────────────────────────────────────────────────────────────────

@owner_only
async def cmdstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show lifetime and today's command usage, sorted by popularity."""
    lifetime, today = await asyncio.gather(
        asyncio.to_thread(get_cmd_stats),
        asyncio.to_thread(get_cmd_stats_today),
    )
    if not lifetime:
        await update.message.reply_text("📊 No command usage data yet.")
        return

    sorted_all = sorted(lifetime.items(), key=lambda x: x[1], reverse=True)
    total_uses = sum(lifetime.values())
    today_total = sum(today.values())

    lines = [
        "📊 *Command Usage Stats*\n",
        f"Lifetime total: *{total_uses}* uses  ·  Today: *{today_total}*\n",
    ]
    for i, (cmd, count) in enumerate(sorted_all[:20], 1):
        today_count = today.get(cmd, 0)
        today_tag = f" _(+{today_count} today)_" if today_count else ""
        lines.append(f"{i}. `/{cmd}` — *{count}*{today_tag}")

    if len(sorted_all) > 20:
        lines.append(f"\n_...and {len(sorted_all) - 20} more commands_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
