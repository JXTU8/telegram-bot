"""
countdown_manager.py
────────────────────
Countdown store backed by Upstash Redis.
Data persists across restarts and redeployments.

Redis key structure:
  countdowns:<chat_id>  →  JSON dict of all countdowns for that chat
  {
    "Exam": {
      "target_date": "2025-12-31",
      "reminder_hour": 8,
      "reminder_minute": 30,
      "created_by": 123456789
    },
    ...
  }
"""

import json
import logging
import os
from datetime import date
from typing import Optional

from upstash_redis import Redis

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Redis client
# ─────────────────────────────────────────────
redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────
def _rkey(chat_id: int) -> str:
    return f"countdowns:{chat_id}"


def _load_chat(chat_id: int) -> dict:
    try:
        data = redis.get(_rkey(chat_id))
        if data is None:
            return {}
        if isinstance(data, dict):
            return data
        return json.loads(data)
    except Exception as e:
        logger.error("Redis read error for chat %s: %s", chat_id, e)
        return {}


def _save_chat(chat_id: int, data: dict) -> None:
    try:
        redis.set(_rkey(chat_id), json.dumps(data))
    except Exception as e:
        logger.error("Redis write error for chat %s: %s", chat_id, e)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def add_countdown(
    chat_id: int,
    name: str,
    target_date: date,
    hour: int,
    minute: int,
    created_by: int,
) -> None:
    """Add or overwrite a named countdown for a chat."""
    data = _load_chat(chat_id)
    data[name] = {
        "target_date": target_date.isoformat(),
        "reminder_hour": hour,
        "reminder_minute": minute,
        "created_by": created_by,
    }
    _save_chat(chat_id, data)


def get_countdown(chat_id: int, name: str) -> Optional[dict]:
    """Return a single countdown entry or None."""
    return _load_chat(chat_id).get(name)


def get_all_countdowns(chat_id: int) -> dict:
    """Return all countdowns for a chat."""
    return _load_chat(chat_id)


def remove_countdown(chat_id: int, name: str) -> bool:
    """Remove a named countdown. Returns True if it existed."""
    data = _load_chat(chat_id)
    if name in data:
        del data[name]
        _save_chat(chat_id, data)
        return True
    return False


def countdown_exists(chat_id: int, name: str) -> bool:
    return name in _load_chat(chat_id)


def get_all_chats() -> dict:
    """
    Return all countdowns across all chats.
    Used to restore reminder jobs on startup.
    """
    try:
        keys = redis.keys("countdowns:*")
        if not keys:
            return {}

        result = {}
        for key in keys:
            chat_id = int(key.split(":")[1])
            data = redis.get(key)
            if data:
                if isinstance(data, dict):
                    result[chat_id] = data
                else:
                    result[chat_id] = json.loads(data)
        return result
    except Exception as e:
        logger.error("Redis get_all_chats error: %s", e)
        return {}