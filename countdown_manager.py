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


def _decode_chat_data(data) -> dict:
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def _load_chat(chat_id: int) -> dict:
    try:
        return _decode_chat_data(redis.get(_rkey(chat_id)))
    except Exception as e:
        logger.error("Redis read error for chat %s: %s", chat_id, e)
        return {}


def _save_chat(chat_id: int, data: dict) -> None:
    try:
        redis.set(_rkey(chat_id), json.dumps(data, separators=(",", ":")))
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
            key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            try:
                chat_id = int(key_name.split(":", 1)[1])
            except (IndexError, ValueError):
                logger.warning("Skipping unexpected Redis key: %s", key)
                continue

            result[chat_id] = _decode_chat_data(redis.get(key))
        return result
    except Exception as e:
        logger.error("Redis get_all_chats error: %s", e)
        return {}


# ─────────────────────────────────────────────
# Fateboard persistence (per chat, per day)
# Redis key: fateboard:<chat_id>:<YYYY-MM-DD>
# TTL: 2 days so it auto-cleans itself
# ─────────────────────────────────────────────
_FATEBOARD_TTL = 60 * 60 * 48  # 48 hours


def _fb_key(chat_id: int, date_str: str) -> str:
    return f"fateboard:{chat_id}:{date_str}"


def save_fate_entry(
    chat_id: int,
    date_str: str,
    user_id: int,
    name: str,
    score: int,
    tier: str,
) -> None:
    """Upsert one user's fate result into today's board for this chat."""
    key = _fb_key(chat_id, date_str)
    try:
        try:
            raw = redis.get(key)
        except Exception:
            raw = None
        board = _decode_chat_data(raw) if raw is not None else {}
        board[str(user_id)] = {"name": name, "score": score, "tier": tier}
        # Atomic write + TTL in a single REST call (no separate expire)
        redis.set(key, json.dumps(board, separators=(",", ":")), ex=_FATEBOARD_TTL)
        logger.info("Fateboard saved for chat %s user %s score %s", chat_id, user_id, score)
    except Exception as e:
        logger.error("Redis fateboard write error for chat %s: %s", chat_id, e)


def get_fate_board(chat_id: int, date_str: str) -> dict:
    """Return {user_id_str: {name, score, tier}} for today's board."""
    key = _fb_key(chat_id, date_str)
    try:
        raw = redis.get(key)
        if raw is None:
            return {}
        return _decode_chat_data(raw)
    except Exception as e:
        logger.error("Redis fateboard read error for chat %s: %s", chat_id, e)
        return {}


# ─────────────────────────────────────────────
# Quote archive (per chat)
# Redis key: quotes:<chat_id>  →  JSON list of quote dicts
# Capped at 100 quotes per chat, no TTL (persistent)
# ─────────────────────────────────────────────
_QUOTES_MAX = 100


def _quotes_key(chat_id: int) -> str:
    return f"quotes:{chat_id}"


def save_quote(chat_id: int, author_name: str, text: str, saved_by_name: str) -> int:
    """Append a quote. Returns the new total count."""
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_chat_data(raw) if raw else []
        if not isinstance(quotes, list):
            quotes = []
        quotes.append({"author": author_name, "text": text, "saved_by": saved_by_name})
        quotes = quotes[-_QUOTES_MAX:]
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return len(quotes)
    except Exception as e:
        logger.error("Redis quote save error for chat %s: %s", chat_id, e)
        return 0


def get_random_quote(chat_id: int) -> Optional[dict]:
    """Return a random quote dict (with 1-based 'index' key) or None."""
    import random as _random
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        if not raw:
            return None
        quotes = _decode_chat_data(raw)
        if not isinstance(quotes, list) or not quotes:
            return None
        idx = _random.randrange(len(quotes))
        q = dict(quotes[idx])
        q["index"] = idx + 1  # 1-based for display
        return q
    except Exception as e:
        logger.error("Redis quote read error for chat %s: %s", chat_id, e)
        return None


def get_all_quotes(chat_id: int) -> list:
    """Return all quotes as a list."""
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        if not raw:
            return []
        quotes = _decode_chat_data(raw)
        return quotes if isinstance(quotes, list) else []
    except Exception as e:
        logger.error("Redis quotes list error for chat %s: %s", chat_id, e)
        return []


def delete_quote(chat_id: int, index: int) -> tuple:
    """
    Delete quote by 1-based index.
    Returns (success: bool, message: str).
    """
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_chat_data(raw) if raw else []
        if not isinstance(quotes, list) or not quotes:
            return False, "❌ No quotes saved yet."
        if index < 1 or index > len(quotes):
            return False, f"❌ Quote #{index} doesn't exist. There are {len(quotes)} quote(s)."
        removed = quotes.pop(index - 1)
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        preview = removed["text"][:60] + ("…" if len(removed["text"]) > 60 else "")
        return True, f'🗑️ Deleted quote #{index}: *"{preview}"* — {removed["author"]}'
    except Exception as e:
        logger.error("Redis quote delete error for chat %s: %s", chat_id, e)
        return False, "❌ Something went wrong while deleting the quote."


def get_quote_count(chat_id: int) -> int:
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        if not raw:
            return 0
        quotes = _decode_chat_data(raw)
        return len(quotes) if isinstance(quotes, list) else 0
    except Exception:
        return 0


# ─────────────────────────────────────────────
# Ship pair leaderboard (per chat)
# Redis key: ship_pairs:<chat_id>  →  JSON dict
# {pair_key: {label_a, label_b, score}}
# ─────────────────────────────────────────────
def _ship_pairs_key(chat_id: int) -> str:
    return f"ship_pairs:{chat_id}"


def save_ship_pair(
    chat_id: int, pair_key: str, label_a: str, label_b: str, score: float
) -> None:
    key = _ship_pairs_key(chat_id)
    try:
        raw = redis.get(key)
        pairs = _decode_chat_data(raw) if raw else {}
        pairs[pair_key] = {"label_a": label_a, "label_b": label_b, "score": score}
        redis.set(key, json.dumps(pairs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis ship pair save error for chat %s: %s", chat_id, e)


def get_top_ship_pairs(chat_id: int, limit: int = 5) -> list:
    key = _ship_pairs_key(chat_id)
    try:
        raw = redis.get(key)
        if not raw:
            return []
        pairs = _decode_chat_data(raw)
        return sorted(pairs.values(), key=lambda x: x["score"], reverse=True)[:limit]
    except Exception as e:
        logger.error("Redis ship pairs read error for chat %s: %s", chat_id, e)
        return []


# ─────────────────────────────────────────────
# Fate streak (per user)
# Redis key: fate_streak:<user_id>  →  {date, streak, category}
# category: "lucky" | "unlucky" | "neutral"
# TTL: 49 hours — resets if you miss a day
# ─────────────────────────────────────────────
_STREAK_TTL = 60 * 60 * 49  # 49 hours


def _streak_key(user_id: int) -> str:
    return f"fate_streak:{user_id}"


def update_fate_streak(user_id: int, date_str: str, tier_category: str) -> int:
    """Update streak and return the new count."""
    from datetime import date as _date, timedelta
    key = _streak_key(user_id)
    try:
        raw = redis.get(key)
        data = _decode_chat_data(raw) if raw else {}

        today = _date.fromisoformat(date_str)
        yesterday_str = (today - timedelta(days=1)).isoformat()

        if data.get("date") == date_str:
            # Already logged today, return existing streak
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
    """Return (streak_count, category). streak_count=0 if none."""
    key = _streak_key(user_id)
    try:
        raw = redis.get(key)
        if not raw:
            return 0, "neutral"
        data = _decode_chat_data(raw)
        return data.get("streak", 0), data.get("category", "neutral")
    except Exception as e:
        logger.error("Redis streak read error for user %s: %s", user_id, e)
        return 0, "neutral"


# ─────────────────────────────────────────────
# Seen users (per chat) — for /mvp
# Redis key: seen_users:<chat_id>  →  {user_id_str: name}
# ─────────────────────────────────────────────
def _seen_key(chat_id: int) -> str:
    return f"seen_users:{chat_id}"


def track_seen_user(chat_id: int, user_id: int, name: str) -> None:
    key = _seen_key(chat_id)
    try:
        raw = redis.get(key)
        users = _decode_chat_data(raw) if raw else {}
        users[str(user_id)] = name
        redis.set(key, json.dumps(users, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis seen user track error for chat %s: %s", chat_id, e)


def get_seen_users(chat_id: int) -> dict:
    key = _seen_key(chat_id)
    try:
        raw = redis.get(key)
        return _decode_chat_data(raw) if raw else {}
    except Exception as e:
        logger.error("Redis seen users read error for chat %s: %s", chat_id, e)
        return {}