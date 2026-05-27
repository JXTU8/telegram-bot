"""
stores/countdown_store.py
─────────────────────────
Countdown persistence.
Redis key: countdowns:<chat_id>  →  JSON dict of countdowns
"""

import json
import logging
from datetime import date
from typing import Optional

from db import redis
from stores._utils import _decode_dict, _key_to_chat_id

logger = logging.getLogger(__name__)

_CODE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def _rkey(chat_id: int) -> str:
    return f"countdowns:{chat_id}"


def _gen_code(existing_codes: set) -> str:
    """Generate a unique 3-char alphanumeric code."""
    import random as _random
    for _ in range(200):
        code = "".join(_random.choices(_CODE_CHARS, k=3))
        if code not in existing_codes:
            return code
    return "".join(_random.choices(_CODE_CHARS, k=4))


def _load_chat(chat_id: int) -> dict:
    try:
        return _decode_dict(redis.get(_rkey(chat_id)))
    except Exception as e:
        logger.error("Redis read error for chat %s: %s", chat_id, e)
        return {}


def _save_chat(chat_id: int, data: dict) -> None:
    try:
        redis.set(_rkey(chat_id), json.dumps(data, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis write error for chat %s: %s", chat_id, e)


def add_countdown(
    chat_id: int,
    name: str,
    target_date: date,
    hour: int,
    minute: int,
    created_by: int,
) -> str:
    """Add or overwrite a named countdown. Returns the short code."""
    data = _load_chat(chat_id)
    existing_codes = {v.get("code", "") for v in data.values()}
    existing_code = data.get(name, {}).get("code", "")
    code = existing_code or _gen_code(existing_codes)
    data[name] = {
        "target_date": target_date.isoformat(),
        "reminder_hour": hour,
        "reminder_minute": minute,
        "created_by": created_by,
        "code": code,
    }
    _save_chat(chat_id, data)
    return code


def get_countdown(chat_id: int, name: str) -> Optional[dict]:
    return _load_chat(chat_id).get(name)


def get_all_countdowns(chat_id: int) -> dict:
    return _load_chat(chat_id)


def remove_countdown(chat_id: int, name: str) -> bool:
    data = _load_chat(chat_id)
    if name in data:
        del data[name]
        _save_chat(chat_id, data)
        return True
    return False


def countdown_exists(chat_id: int, name: str) -> bool:
    return name in _load_chat(chat_id)


def get_countdown_by_code(chat_id: int, code: str) -> Optional[str]:
    code = code.lower()
    for name, entry in _load_chat(chat_id).items():
        if entry.get("code", "").lower() == code:
            return name
    return None


def get_countdown_creator(chat_id: int, name: str) -> Optional[int]:
    entry = _load_chat(chat_id).get(name)
    return entry.get("created_by") if entry else None


def get_all_chats() -> dict:
    """Return all countdowns across all chats (used to restore reminder jobs on startup)."""
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="countdowns:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return {}
        values = redis.mget(*keys)
        result = {}
        for key, raw in zip(keys, values):
            chat_id = _key_to_chat_id(key)
            if chat_id is not None:
                result[chat_id] = _decode_dict(raw)
        return result
    except Exception as e:
        logger.error("Redis get_all_chats error: %s", e)
        return {}