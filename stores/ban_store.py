"""
stores/ban_store.py
───────────────────
Bot-wide ban list persistence.
Redis key: banned_users  →  JSON list of user_id ints (global, not per-chat)
No TTL — bans are permanent until explicitly lifted with /unban.
"""

import json
import logging

from db import redis
from stores._utils import _decode_list

logger = logging.getLogger(__name__)

_BAN_KEY = "banned_users"


def get_banned_users() -> set:
    """Return the full set of banned user IDs."""
    try:
        return set(int(uid) for uid in _decode_list(redis.get(_BAN_KEY)))
    except Exception as e:
        logger.error("Redis ban list read error: %s", e)
        return set()


def is_banned(user_id: int) -> bool:
    """Return True if the user is currently banned."""
    try:
        return user_id in set(int(uid) for uid in _decode_list(redis.get(_BAN_KEY)))
    except Exception as e:
        logger.error("Redis ban check error for user %s: %s", user_id, e)
        return False


def ban_user(user_id: int) -> bool:
    """Add to ban list. Returns True if newly banned, False if already was."""
    try:
        banned = set(int(uid) for uid in _decode_list(redis.get(_BAN_KEY)))
        if user_id in banned:
            return False
        banned.add(user_id)
        redis.set(_BAN_KEY, json.dumps(sorted(banned), separators=(",", ":")))
        logger.info("Banned user_id=%s", user_id)
        return True
    except Exception as e:
        logger.error("Redis ban error for user %s: %s", user_id, e)
        return False


def unban_user(user_id: int) -> bool:
    """Remove from ban list. Returns True if was banned, False if wasn't."""
    try:
        banned = set(int(uid) for uid in _decode_list(redis.get(_BAN_KEY)))
        if user_id not in banned:
            return False
        banned.discard(user_id)
        redis.set(_BAN_KEY, json.dumps(sorted(banned), separators=(",", ":")))
        logger.info("Unbanned user_id=%s", user_id)
        return True
    except Exception as e:
        logger.error("Redis unban error for user %s: %s", user_id, e)
        return False