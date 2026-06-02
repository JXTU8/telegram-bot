"""
stores/luck_store.py
────────────────────
Luck board and streak persistence.
Redis keys:
  luckboard:<chat_id>:<YYYY-MM-DD>  →  JSON dict  (TTL 25 h)
  fate_streak:<user_id>             →  JSON dict  (TTL 49 h)
"""

import json
import logging

from db import redis
from stores._utils import _decode_dict, _key_to_chat_id

logger = logging.getLogger(__name__)

_LUCKBOARD_TTL = 60 * 60 * 25   # 25 hours
_STREAK_TTL    = 60 * 60 * 49   # 49 hours


# ── Luckboard ─────────────────────────────────────────────────────────────────

def _lb_key(chat_id: int, date_str: str) -> str:
    return f"luckboard:{chat_id}:{date_str}"


def delete_old_fateboard_keys() -> int:
    """Delete all legacy fateboard:* keys. Called once on startup."""
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="fateboard:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return 0
        redis.delete(*keys)
        logger.info("Deleted %s legacy fateboard key(s) from Redis.", len(keys))
        return len(keys)
    except Exception as e:
        logger.error("Redis delete_old_fateboard_keys error: %s", e)
        return 0


def save_fate_entry(
    chat_id: int,
    date_str: str,
    user_id: int,
    name: str,
    score: int,
    tier: str,
) -> None:
    key = _lb_key(chat_id, date_str)
    try:
        board = _decode_dict(redis.get(key))
        board[str(user_id)] = {"name": name, "score": score, "tier": tier}
        redis.set(key, json.dumps(board, separators=(",", ":")), ex=_LUCKBOARD_TTL)
    except Exception as e:
        logger.error("Redis luckboard write error for chat %s: %s", chat_id, e)


def get_fate_board(chat_id: int, date_str: str) -> dict:
    key = _lb_key(chat_id, date_str)
    try:
        return _decode_dict(redis.get(key))
    except Exception as e:
        logger.error("Redis luckboard read error for chat %s: %s", chat_id, e)
        return {}


# ── Streak ────────────────────────────────────────────────────────────────────

def _streak_key(user_id: int) -> str:
    return f"fate_streak:{user_id}"


def update_fate_streak(user_id: int, date_str: str, tier_category: str) -> int:
    """Update streak and return the new count."""
    from datetime import date as _date, timedelta
    key = _streak_key(user_id)
    try:
        data = _decode_dict(redis.get(key))
        today = _date.fromisoformat(date_str)
        yesterday_str = (today - timedelta(days=1)).isoformat()

        if data.get("date") == date_str:
            return data.get("streak", 1)
        elif data.get("date") == yesterday_str and data.get("category") == tier_category:
            streak = data.get("streak", 1) + 1
        else:
            streak = 1

        new_data = {"date": date_str, "streak": streak, "category": tier_category}
        redis.set(key, json.dumps(new_data, separators=(",", ":")))
        redis.expire(key, _STREAK_TTL)
        return streak
    except Exception as e:
        logger.error("Redis streak update error for user %s: %s", user_id, e)
        return 1


def get_fate_streak(user_id: int) -> tuple:
    """Return (streak_count, category). streak_count=0 if no active streak."""
    key = _streak_key(user_id)
    try:
        data = _decode_dict(redis.get(key))
        return data.get("streak", 0), data.get("category", "neutral")
    except Exception as e:
        logger.error("Redis streak read error for user %s: %s", user_id, e)
        return 0, "neutral"


# ── Lifetime luck check counter ───────────────────────────────────────────────

def _luck_checks_key(user_id: int) -> str:
    return f"luck_checks:{user_id}"


def increment_luck_checks(user_id: int) -> int:
    """Increment and return the lifetime /luck check count for a user."""
    key = _luck_checks_key(user_id)
    try:
        return int(redis.incr(key))
    except Exception as e:
        logger.error("Redis luck checks incr error for user %s: %s", user_id, e)
        return 0


def get_luck_check_count(user_id: int) -> int:
    """Return the lifetime /luck check count for a user (0 if never checked)."""
    key = _luck_checks_key(user_id)
    try:
        raw = redis.get(key)
        return int(raw) if raw is not None else 0
    except Exception as e:
        logger.error("Redis luck checks read error for user %s: %s", user_id, e)
        return 0