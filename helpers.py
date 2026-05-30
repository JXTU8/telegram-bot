"""
helpers.py
──────────
Shared utility functions, conversation states, and owner config.
Imported by every handler — keep this free of handler-specific logic.
"""

import logging
import os
import random
from datetime import date, datetime
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import TIMEZONE, env_int

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
ASK_NAME, ASK_DATE, ASK_TIME = range(3)
ASK_DECISION, ASK_OPTIONS = range(3, 5)
ASK_EDIT_FIELD, ASK_EDIT_VALUE = range(5, 7)
CONV_TIMEOUT = 90

# ── Owner config ──────────────────────────────────────────────────────────────
BOT_OWNER_ID = env_int("BOT_OWNER_ID", 0)
BOT_OWNER_USERNAMES = {
    username.strip().lstrip("@").casefold()
    for username in os.getenv("BOT_OWNER_USERNAME", "").replace(",", " ").split()
    if username.strip()
}


# ── Owner helpers ─────────────────────────────────────────────────────────────

def _is_owner(user) -> bool:
    """Return True if the given Telegram user is the bot owner."""
    return (BOT_OWNER_ID and user.id == BOT_OWNER_ID) or (
        bool(user.username)
        and user.username.casefold() in BOT_OWNER_USERNAMES
    )


def owner_only(func):
    """
    Decorator for command handlers that restricts access to the bot owner only.
    Silently returns (no reply) for non-owners, and logs the attempt.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_owner(user):
            logger.info(
                "owner_only: rejected %s (id=%s) for %s",
                user.username or "no-username", user.id, func.__name__,
            )
            return
        return await func(update, context)
    return wrapper


def requires_message(func):
    """
    Decorator that silently skips the handler if update.message is None.
    Prevents AttributeError crashes from edited messages, channel posts,
    or any other update type that does not carry a .message object.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        return await func(update, context)
    return wrapper


# ── User display ──────────────────────────────────────────────────────────────
_INVISIBLE_NAME_CHARS = str.maketrans(
    "",
    "",
    "\u180e\u200b\u200c\u200d\u2060\u2800\u3164\ufeff",
)


def _has_visible_text(text: str) -> bool:
    return bool(str(text or "").translate(_INVISIBLE_NAME_CHARS).strip())


def _display_name_or_id(name: str, user_id) -> str:
    return str(name).strip() if _has_visible_text(name) else str(user_id)


def _display_user(user) -> str:
    if _has_visible_text(getattr(user, "first_name", "")):
        return user.first_name.strip()
    if _has_visible_text(getattr(user, "username", "")):
        return user.username.strip()
    return str(user.id)


def _arg_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args).strip()


def _normalize_target(target: str) -> str:
    return target.strip().lstrip("@").casefold()


def _daily_rng(label: str, *parts):
    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    seed = ":".join(str(part) for part in (label, today_str, *parts))
    return random.Random(seed)


def _mentioned_target(update: Update, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    message = update.message
    if not message:
        return ""

    ignored_usernames = set()
    ignored_user_ids = set()
    if context:
        bot_username = getattr(context.bot, "username", None)
        bot_id = getattr(context.bot, "id", None)
        if bot_username:
            ignored_usernames.add(bot_username.casefold())
        if bot_id:
            ignored_user_ids.add(bot_id)

    for entity in message.entities or []:
        if entity.type != "bot_command":
            continue
        command_text = message.parse_entity(entity)
        if "@" in command_text:
            ignored_usernames.add(command_text.split("@", 1)[1].casefold())

    for entity in message.entities or []:
        if entity.type not in ("mention", "text_mention"):
            continue
        if getattr(entity, "user", None):
            if entity.user.id in ignored_user_ids:
                continue
            return _display_user(entity.user)
        mention = message.parse_entity(entity)
        if _normalize_target(mention) in ignored_usernames:
            continue
        return mention

    return ""


def _target_from_mention_or_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    return _mentioned_target(update, context) or _display_user(update.effective_user)


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _days_label(days: int) -> str:
    if days == 0:
        return "🎉 Today is the day!"
    if days < 0:
        return f"⏰ {abs(days)} day(s) overdue"
    return f"📅 {days} day(s) remaining"


def _job_name(chat_id: int, name: str) -> str:
    return f"{chat_id}::{name}"


# ── Message tracking (for conversation cleanup) ───────────────────────────────
def _track(context: ContextTypes.DEFAULT_TYPE, *messages) -> None:
    ids: list = context.user_data.setdefault("_cd_msg_ids", [])
    for msg in messages:
        if msg is not None:
            ids.append((msg.chat_id, msg.message_id))


async def _delete_tracked(context: ContextTypes.DEFAULT_TYPE) -> None:
    for chat_id, msg_id in context.user_data.pop("_cd_msg_ids", []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


# ── Admin check ───────────────────────────────────────────────────────────────
async def _is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(
            update.effective_chat.id, update.effective_user.id
        )
        return member.status in ("administrator", "creator")
    except Exception:
        return False


# ── Markdown escaping ─────────────────────────────────────────────────────────
def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in user-supplied text."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text