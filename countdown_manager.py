"""
countdown_manager.py
────────────────────
Countdown store backed by Upstash Redis.
Data persists across restarts and redeployments.

Redis key structure:
  countdowns:<chat_id>     →  JSON dict of countdowns for that chat
  luckboard:<chat_id>:<YYYY-MM-DD>  →  JSON dict of luck scores (replaces fateboard:*)
  quotes:<chat_id>         →  JSON list of quote dicts
  ship_pairs:<chat_id>     →  JSON dict of ship pair scores
  fate_streak:<user_id>    →  JSON dict of streak data
  seen_users:<chat_id>     →  JSON dict of {user_id: name}
  remind_count:<user_id>   →  int, TTL 25 h
  remind_jobs:<chat_id>    →  JSON list of one-shot reminder dicts
  birthdays:<chat_id>      →  JSON dict of {user_id: {name, day, month}}
"""

import json
import logging
import os
import time as _time
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


def _decode_chat_data(data):
    """Safely decode Redis response to dict or list."""
    if data is None:
        return {}
    if isinstance(data, (dict, list)):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return {}


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
# Countdowns — public API
# ─────────────────────────────────────────────
_CODE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def _gen_code(existing_codes: set) -> str:
    """Generate a unique 3-char alphanumeric code."""
    import random as _random
    for _ in range(200):
        code = "".join(_random.choices(_CODE_CHARS, k=3))
        if code not in existing_codes:
            return code
    return "".join(_random.choices(_CODE_CHARS, k=4))


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
# Luckboard (replaces fateboard — keys migrated on startup)
# Redis key: luckboard:<chat_id>:<YYYY-MM-DD>
# TTL: 25 hours
# ─────────────────────────────────────────────
_LUCKBOARD_TTL = 60 * 60 * 25  # 25 hours


def _lb_key(chat_id: int, date_str: str) -> str:
    return f"luckboard:{chat_id}:{date_str}"


def delete_old_fateboard_keys() -> int:
    """Delete all legacy fateboard:* keys. Called once on startup."""
    try:
        keys = redis.keys("fateboard:*")
        if not keys:
            return 0
        for key in keys:
            try:
                redis.delete(key)
            except Exception as e:
                logger.warning("Could not delete fateboard key %s: %s", key, e)
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
    """Upsert one user's luck result into today's luckboard."""
    key = _lb_key(chat_id, date_str)
    try:
        try:
            raw = redis.get(key)
        except Exception:
            raw = None
        board = _decode_chat_data(raw) if raw is not None else {}
        board[str(user_id)] = {"name": name, "score": score, "tier": tier}
        redis.set(key, json.dumps(board, separators=(",", ":")), ex=_LUCKBOARD_TTL)
        logger.info("Luckboard saved chat=%s user=%s score=%s", chat_id, user_id, score)
    except Exception as e:
        logger.error("Redis luckboard write error for chat %s: %s", chat_id, e)


def get_fate_board(chat_id: int, date_str: str) -> dict:
    """Return {user_id_str: {name, score, tier}} for today's luckboard."""
    key = _lb_key(chat_id, date_str)
    try:
        raw = redis.get(key)
        if raw is None:
            return {}
        return _decode_chat_data(raw)
    except Exception as e:
        logger.error("Redis luckboard read error for chat %s: %s", chat_id, e)
        return {}


# ─────────────────────────────────────────────
# Quote archive (per chat) — capped at 100
# ─────────────────────────────────────────────
_QUOTE_CAP = 100


def _quotes_key(chat_id: int) -> str:
    return f"quotes:{chat_id}"


def save_quote(chat_id: int, author: str, text: str, saved_by: str) -> int:
    """
    Append a quote.  Returns the new total count, or -1 if an identical
    (text + author) quote already exists in the archive.
    """
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_chat_data(raw) if raw else []
        if not isinstance(quotes, list):
            quotes = []
        # Dedup: reject exact same text from the same author
        text_norm = text.strip().casefold()
        if any(
            q.get("text", "").strip().casefold() == text_norm
            and q.get("author", "") == author
            for q in quotes
        ):
            return -1  # caller should inform the user it's a duplicate
        quotes.append({"author": author, "text": text, "saved_by": saved_by})
        if len(quotes) > _QUOTE_CAP:
            quotes = quotes[-_QUOTE_CAP:]
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return len(quotes)
    except Exception as e:
        logger.error("Redis quote save error for chat %s: %s", chat_id, e)
        return 0


def get_quote_count(chat_id: int) -> int:
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_chat_data(raw) if raw else []
        return len(quotes) if isinstance(quotes, list) else 0
    except Exception:
        return 0


def get_all_quotes(chat_id: int) -> list:
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        if not raw:
            return []
        quotes = _decode_chat_data(raw)
        return quotes if isinstance(quotes, list) else []
    except Exception as e:
        logger.error("Redis quotes read error for chat %s: %s", chat_id, e)
        return []


def delete_quote(chat_id: int, index: int) -> tuple:
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_chat_data(raw) if raw else []
        if not isinstance(quotes, list) or not (1 <= index <= len(quotes)):
            total = len(quotes) if isinstance(quotes, list) else 0
            return False, f"⚠️ Invalid number. There are {total} quote(s)."
        removed = quotes.pop(index - 1)
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return True, f'✅ Deleted quote #{index}: *"{removed["text"]}"* — {removed["author"]}'
    except Exception as e:
        logger.error("Redis quote delete error for chat %s: %s", chat_id, e)
        return False, "❌ Failed to delete quote."


# ─────────────────────────────────────────────
# Ship pairs (per chat) — rolling 48-hour window
# Key rotates every 48 h so the board auto-resets.
# Redis key: ship_pairs:<chat_id>:<bucket>
# where bucket = unix_epoch // 172800
# ─────────────────────────────────────────────
_SHIP_PAIRS_WINDOW = 48 * 3600  # 48 hours in seconds


def _ship_pairs_key(chat_id: int) -> str:
    bucket = int(_time.time()) // _SHIP_PAIRS_WINDOW
    return f"ship_pairs:{chat_id}:{bucket}"


def get_shipboard_reset_time() -> int:
    """Return seconds until the current 48-hour ship window resets."""
    now = int(_time.time())
    bucket = now // _SHIP_PAIRS_WINDOW
    return (bucket + 1) * _SHIP_PAIRS_WINDOW - now


def save_ship_pair(
    chat_id: int, pair_key: str, label_a: str, label_b: str, score: float
) -> None:
    key = _ship_pairs_key(chat_id)
    try:
        raw = redis.get(key)
        pairs = _decode_chat_data(raw) if raw else {}
        pairs[pair_key] = {"label_a": label_a, "label_b": label_b, "score": score}
        # TTL = remaining seconds in this window + 1 h grace so the key never
        # disappears while the window is still active.
        ttl = get_shipboard_reset_time() + 3600
        redis.set(key, json.dumps(pairs, separators=(",", ":")), ex=ttl)
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
# Fate/luck streak (per user) — TTL 49 h
# ─────────────────────────────────────────────
_STREAK_TTL = 60 * 60 * 49


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
# Seen users (per chat) — for /mvp, /toss
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


# ─────────────────────────────────────────────
# Per-user reminder count (spam cap, TTL 25 h)
# ─────────────────────────────────────────────
_REMIND_COUNT_TTL = 60 * 60 * 25


def _remind_count_key(user_id: int) -> str:
    return f"remind_count:{user_id}"


def increment_remind_count(user_id: int) -> int:
    """Atomically increment and return the new count. Sets TTL on first increment."""
    key = _remind_count_key(user_id)
    try:
        new_val = redis.incr(key)
        if new_val == 1:
            redis.expire(key, _REMIND_COUNT_TTL)
        return new_val
    except Exception as e:
        logger.error("Redis remind count error for user %s: %s", user_id, e)
        return 1


def get_remind_count(user_id: int) -> int:
    key = _remind_count_key(user_id)
    try:
        raw = redis.get(key)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


def decrement_remind_count(user_id: int) -> None:
    key = _remind_count_key(user_id)
    try:
        if get_remind_count(user_id) > 0:
            redis.decr(key)
    except Exception as e:
        logger.error("Redis remind decr error for user %s: %s", user_id, e)


# ─────────────────────────────────────────────
# Remind job persistence (survives restarts)
# Redis key: remind_jobs:<chat_id>  →  JSON list
# ─────────────────────────────────────────────
def _remind_jobs_key(chat_id: int) -> str:
    return f"remind_jobs:{chat_id}"


def save_remind_job(
    chat_id: int,
    user_id: int,
    user_mention_html: str,
    text: str,
    fire_at: float,
) -> str:
    """Persist a remind job. Returns a unique job_id string."""
    job_id = os.urandom(4).hex()
    key = _remind_jobs_key(chat_id)
    try:
        raw = redis.get(key)
        jobs = _decode_chat_data(raw) if raw else []
        if not isinstance(jobs, list):
            jobs = []
        jobs.append({
            "job_id": job_id,
            "user_id": user_id,
            "user_mention_html": user_mention_html,
            "text": text,
            "fire_at": fire_at,
        })
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remind job save error for chat %s: %s", chat_id, e)
    return job_id


def delete_remind_job(chat_id: int, job_id: str) -> None:
    """Remove a fired or cancelled remind job."""
    key = _remind_jobs_key(chat_id)
    try:
        raw = redis.get(key)
        jobs = _decode_chat_data(raw) if raw else []
        if not isinstance(jobs, list):
            return
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remind job delete error for chat %s: %s", chat_id, e)


def get_user_remind_jobs(chat_id: int, user_id: int) -> list:
    """Return still-pending remind jobs for a specific user in a chat."""
    key = _remind_jobs_key(chat_id)
    try:
        raw = redis.get(key)
        jobs = _decode_chat_data(raw) if raw else []
        if not isinstance(jobs, list):
            return []
        now = _time.time()
        return [
            j for j in jobs
            if j.get("user_id") == user_id and j.get("fire_at", 0) > now
        ]
    except Exception as e:
        logger.error("Redis user remind jobs error for chat %s: %s", chat_id, e)
        return []


def get_all_remind_jobs() -> dict:
    """
    Return {chat_id: [job_dicts]} for all chats with pending remind jobs.
    Drops jobs more than 10 minutes overdue.
    """
    try:
        keys = redis.keys("remind_jobs:*")
        if not keys:
            return {}
        cutoff = _time.time() - 10 * 60
        result = {}
        for key in keys:
            key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            try:
                chat_id = int(key_name.split(":", 1)[1])
            except (IndexError, ValueError):
                logger.warning("Skipping unexpected Redis key: %s", key)
                continue
            raw = redis.get(key)
            jobs = _decode_chat_data(raw) if raw else []
            if not isinstance(jobs, list):
                continue
            valid = [j for j in jobs if j.get("fire_at", 0) > cutoff]
            if valid:
                result[chat_id] = valid
        return result
    except Exception as e:
        logger.error("Redis get_all_remind_jobs error: %s", e)
        return {}


# ─────────────────────────────────────────────
# Birthdays (per chat)
# Redis key: birthdays:<chat_id>  →  {user_id_str: {name, day, month}}
# No TTL — birthdays are permanent until the user updates them.
# ─────────────────────────────────────────────
def _birthday_key(chat_id: int) -> str:
    return f"birthdays:{chat_id}"


def save_birthday(chat_id: int, user_id: int, name: str, day: int, month: int) -> None:
    key = _birthday_key(chat_id)
    try:
        raw = redis.get(key)
        data = _decode_chat_data(raw) if raw else {}
        data[str(user_id)] = {"name": name, "day": day, "month": month}
        redis.set(key, json.dumps(data, separators=(",", ":")))
        logger.info("Birthday saved user=%s chat=%s %02d/%02d", user_id, chat_id, day, month)
    except Exception as e:
        logger.error("Redis birthday save error for chat %s: %s", chat_id, e)


def get_all_birthdays(chat_id: int) -> dict:
    """Return {user_id_str: {name, day, month}} for this chat."""
    key = _birthday_key(chat_id)
    try:
        raw = redis.get(key)
        return _decode_chat_data(raw) if raw else {}
    except Exception as e:
        logger.error("Redis birthday read error for chat %s: %s", chat_id, e)
        return {}


def get_all_birthday_chats() -> dict:
    """Return {chat_id: {user_id_str: {name, day, month}}} across all chats."""
    try:
        keys = redis.keys("birthdays:*")
        if not keys:
            return {}
        result = {}
        for key in keys:
            key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            try:
                chat_id = int(key_name.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            result[chat_id] = _decode_chat_data(redis.get(key))
        return result
    except Exception as e:
        logger.error("Redis get_all_birthday_chats error: %s", e)
        return {}