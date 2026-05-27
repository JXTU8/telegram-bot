"""
stores/birthday_store.py
────────────────────────
Birthday persistence.
Redis key: birthdays:<chat_id>  →  JSON dict {user_id_str: {name, day, month}}
No TTL — birthdays are permanent until deleted by the user.
"""

import json
import logging

from db import redis
from stores._utils import _decode_dict, _key_to_chat_id

logger = logging.getLogger(__name__)


def _birthday_key(chat_id: int) -> str:
    return f"birthdays:{chat_id}"


def save_birthday(chat_id: int, user_id: int, name: str, day: int, month: int) -> None:
    key = _birthday_key(chat_id)
    try:
        data = _decode_dict(redis.get(key))
        data[str(user_id)] = {"name": name, "day": day, "month": month}
        redis.set(key, json.dumps(data, separators=(",", ":")))
        logger.info("Birthday saved user=%s chat=%s %02d/%02d", user_id, chat_id, day, month)
    except Exception as e:
        logger.error("Redis birthday save error for chat %s: %s", chat_id, e)


def get_all_birthdays(chat_id: int) -> dict:
    """Return {user_id_str: {name, day, month}} for this chat."""
    try:
        return _decode_dict(redis.get(_birthday_key(chat_id)))
    except Exception as e:
        logger.error("Redis birthday read error for chat %s: %s", chat_id, e)
        return {}


def delete_birthday(chat_id: int, user_id: int) -> bool:
    """Delete a user's birthday entry. Returns True if it existed."""
    key = _birthday_key(chat_id)
    try:
        data = _decode_dict(redis.get(key))
        if str(user_id) not in data:
            return False
        del data[str(user_id)]
        redis.set(key, json.dumps(data, separators=(",", ":")))
        logger.info("Birthday deleted user=%s chat=%s", user_id, chat_id)
        return True
    except Exception as e:
        logger.error("Redis birthday delete error for chat %s: %s", chat_id, e)
        return False


def get_all_birthday_chats() -> dict:
    """
    Return {chat_id: {user_id_str: {name, day, month}}} across all chats.
    Uses SCAN (non-blocking) + mget for efficiency.
    """
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="birthdays:*", count=100)
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
        logger.error("Redis get_all_birthday_chats error: %s", e)
        return {}