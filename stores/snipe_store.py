"""
stores/snipe_store.py
─────────────────────
Stores recent plain-text messages per chat for /snipe.
Redis key: snipe_log:<chat_id>  →  JSON list (capped at 50, TTL 2 hours)

Only plain text messages are stored — no media, no commands.
Each entry: {user_id, name, text, timestamp}
"""

import json
import logging
import time

from db import redis
from stores._utils import _decode_list

logger = logging.getLogger(__name__)

_SNIPE_KEY_PREFIX = "snipe_log"
_SNIPE_TTL        = 2 * 3600   # 2 hours
_SNIPE_CAP        = 50         # max messages stored per chat


def _snipe_key(chat_id: int) -> str:
    return f"{_SNIPE_KEY_PREFIX}:{chat_id}"


def save_snipe_message(chat_id: int, user_id: int, name: str, text: str) -> None:
    """Append a plain-text message to the snipe log for this chat."""
    key = _snipe_key(chat_id)
    try:
        messages = _decode_list(redis.get(key))
        messages.append({
            "user_id":   user_id,
            "name":      name,
            "text":      text,
            "timestamp": time.time(),
        })
        # Keep only the most recent _SNIPE_CAP messages
        if len(messages) > _SNIPE_CAP:
            messages = messages[-_SNIPE_CAP:]
        redis.set(key, json.dumps(messages, separators=(",", ":")), ex=_SNIPE_TTL)
    except Exception as e:
        logger.error("Redis snipe save error for chat %s: %s", chat_id, e)


def get_snipe_messages(chat_id: int) -> list:
    """
    Return stored messages for this chat, most recent first.
    Each item: {user_id, name, text, timestamp}
    """
    key = _snipe_key(chat_id)
    try:
        messages = _decode_list(redis.get(key))
        return list(reversed(messages))
    except Exception as e:
        logger.error("Redis snipe read error for chat %s: %s", chat_id, e)
        return []


def clear_snipe_log(chat_id: int) -> None:
    """Wipe the snipe log for this chat."""
    try:
        redis.delete(_snipe_key(chat_id))
    except Exception as e:
        logger.error("Redis snipe clear error for chat %s: %s", chat_id, e)