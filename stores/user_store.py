"""
stores/user_store.py
────────────────────
Seen-user tracking per chat (used by /mvp, /toss, /stats).
Redis key: seen_users:<chat_id>  →  JSON dict {user_id_str: name}
TTL: 90 days, refreshed on every write. Capped at 500 users.
"""

import json
import logging

from db import redis
from stores._utils import _decode_dict

logger = logging.getLogger(__name__)

_SEEN_USERS_TTL = 60 * 60 * 24 * 90   # 90 days
_SEEN_USERS_CAP = 500


def _seen_key(chat_id: int) -> str:
    return f"seen_users:{chat_id}"


def track_seen_user(chat_id: int, user_id: int, name: str) -> None:
    key = _seen_key(chat_id)
    try:
        users = _decode_dict(redis.get(key))
        users[str(user_id)] = name
        # Evict oldest entries when over cap (insertion order preserved in Python 3.7+)
        if len(users) > _SEEN_USERS_CAP:
            evict_count = len(users) - _SEEN_USERS_CAP
            for old_key in list(users.keys())[:evict_count]:
                del users[old_key]
        redis.set(key, json.dumps(users, separators=(",", ":")), ex=_SEEN_USERS_TTL)
    except Exception as e:
        logger.error("Redis seen user track error for chat %s: %s", chat_id, e)


def get_seen_users(chat_id: int) -> dict:
    try:
        return _decode_dict(redis.get(_seen_key(chat_id)))
    except Exception as e:
        logger.error("Redis seen users read error for chat %s: %s", chat_id, e)
        return {}