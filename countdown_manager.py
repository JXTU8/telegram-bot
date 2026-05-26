"""
countdown_manager.py
────────────────────
Countdown store backed by Upstash Redis.
Data persists across restarts and redeployments.

Redis key structure:
  countdowns:<chat_id>              →  JSON dict of countdowns for that chat
  luckboard:<chat_id>:<YYYY-MM-DD>  →  JSON dict of luck scores  (TTL 25 h)
  quotes:<chat_id>                  →  JSON list of quote dicts
  ship_pairs:<chat_id>:<bucket>     →  JSON dict of ship pair scores (TTL auto)
  fate_streak:<user_id>             →  JSON dict of streak data  (TTL 49 h)
  seen_users:<chat_id>              →  JSON dict of {user_id: name} (TTL refreshed)
  remind_count:<user_id>            →  int  (TTL 25 h)
  remind_jobs:<chat_id>             →  JSON list of one-shot reminder dicts
  remind_claimed:<job_id>           →  "1"  (TTL 2 h) — prevents double-fire on restart
  birthdays:<chat_id>               →  JSON dict of {user_id: {name, day, month}}
"""

import json
import logging
import os
import sys
import time as _time
from datetime import date
from typing import Optional

from upstash_redis import Redis

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Redis client — fail fast with a clear message
# ─────────────────────────────────────────────
try:
    redis = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
except KeyError as _missing:
    sys.exit(f"❌ Redis env var {_missing} is not set. Exiting.")
except Exception as _e:
    sys.exit(f"❌ Failed to initialise Redis client: {_e}")


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────
def _rkey(chat_id: int) -> str:
    return f"countdowns:{chat_id}"


def _decode_dict(data) -> dict:
    """Safely decode a Redis value that is expected to be a JSON object (dict)."""
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        result = json.loads(data)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _decode_list(data) -> list:
    """Safely decode a Redis value that is expected to be a JSON array (list)."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        result = json.loads(data)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# Keep for legacy call sites that haven't been migrated yet
def _decode_chat_data(data):
    """Legacy decoder — prefers dict. Migrate callers to _decode_dict/_decode_list."""
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
        return _decode_dict(redis.get(_rkey(chat_id)))
    except Exception as e:
        logger.error("Redis read error for chat %s: %s", chat_id, e)
        return {}


def _save_chat(chat_id: int, data: dict) -> None:
    try:
        redis.set(_rkey(chat_id), json.dumps(data, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis write error for chat %s: %s", chat_id, e)


def _key_to_chat_id(key) -> Optional[int]:
    """Parse a Redis key of the form 'prefix:<chat_id>' and return the chat_id int."""
    key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
    try:
        return int(key_name.split(":", 1)[1])
    except (IndexError, ValueError):
        logger.warning("Skipping unexpected Redis key: %s", key_name)
        return None


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
        # Batch fetch all values in one round-trip
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
        # Delete all in a single command instead of one at a time
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
    """Upsert one user's luck result into today's luckboard."""
    key = _lb_key(chat_id, date_str)
    try:
        raw = redis.get(key)
        board = _decode_dict(raw)
        board[str(user_id)] = {"name": name, "score": score, "tier": tier}
        redis.set(key, json.dumps(board, separators=(",", ":")), ex=_LUCKBOARD_TTL)
        logger.info("Luckboard saved chat=%s user=%s score=%s", chat_id, user_id, score)
    except Exception as e:
        logger.error("Redis luckboard write error for chat %s: %s", chat_id, e)


def get_fate_board(chat_id: int, date_str: str) -> dict:
    """Return {user_id_str: {name, score, tier}} for today's luckboard."""
    key = _lb_key(chat_id, date_str)
    try:
        return _decode_dict(redis.get(key))
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
        quotes = _decode_list(raw)
        # Dedup: reject exact same text from the same author
        text_norm = text.strip().casefold()
        if any(
            q.get("text", "").strip().casefold() == text_norm
            and q.get("author", "") == author
            for q in quotes
        ):
            return -1
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
        return len(_decode_list(redis.get(key)))
    except Exception:
        return 0


def get_all_quotes(chat_id: int) -> list:
    key = _quotes_key(chat_id)
    try:
        return _decode_list(redis.get(key))
    except Exception as e:
        logger.error("Redis quotes read error for chat %s: %s", chat_id, e)
        return []


def delete_quote(chat_id: int, index: int) -> tuple:
    key = _quotes_key(chat_id)
    try:
        raw = redis.get(key)
        quotes = _decode_list(raw)
        if not (1 <= index <= len(quotes)):
            return False, f"⚠️ Invalid number. There are {len(quotes)} quote(s)."
        removed = quotes.pop(index - 1)
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return True, f'✅ Deleted quote #{index}: *"{removed["text"]}"* — {removed["author"]}'
    except Exception as e:
        logger.error("Redis quote delete error for chat %s: %s", chat_id, e)
        return False, "❌ Failed to delete quote."


# ─────────────────────────────────────────────
# Ship pairs (per chat) — rolling 48-hour window
# ─────────────────────────────────────────────
_SHIP_PAIRS_WINDOW = 48 * 3600


def _ship_pairs_key(chat_id: int) -> str:
    bucket = int(_time.time()) // _SHIP_PAIRS_WINDOW
    return f"ship_pairs:{chat_id}:{bucket}"


def get_shipboard_reset_time() -> int:
    now = int(_time.time())
    bucket = now // _SHIP_PAIRS_WINDOW
    return (bucket + 1) * _SHIP_PAIRS_WINDOW - now


def save_ship_pair(
    chat_id: int, pair_key: str, label_a: str, label_b: str, score: float
) -> None:
    key = _ship_pairs_key(chat_id)
    try:
        raw = redis.get(key)
        pairs = _decode_dict(raw)
        pairs[pair_key] = {"label_a": label_a, "label_b": label_b, "score": score}
        ttl = get_shipboard_reset_time() + 3600
        redis.set(key, json.dumps(pairs, separators=(",", ":")), ex=ttl)
    except Exception as e:
        logger.error("Redis ship pair save error for chat %s: %s", chat_id, e)


def get_top_ship_pairs(chat_id: int, limit: int = 5) -> list:
    key = _ship_pairs_key(chat_id)
    try:
        pairs = _decode_dict(redis.get(key))
        if not pairs:
            return []
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
        data = _decode_dict(raw)

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
        data = _decode_dict(redis.get(key))
        return data.get("streak", 0), data.get("category", "neutral")
    except Exception as e:
        logger.error("Redis streak read error for user %s: %s", user_id, e)
        return 0, "neutral"


# ─────────────────────────────────────────────
# Seen users (per chat) — for /mvp, /toss
# TTL: 90 days, refreshed on every write.
# Capped at 500 users (oldest entry evicted).
# ─────────────────────────────────────────────
_SEEN_USERS_TTL = 60 * 60 * 24 * 90   # 90 days
_SEEN_USERS_CAP = 500


def _seen_key(chat_id: int) -> str:
    return f"seen_users:{chat_id}"


def track_seen_user(chat_id: int, user_id: int, name: str) -> None:
    key = _seen_key(chat_id)
    try:
        raw = redis.get(key)
        users = _decode_dict(raw)
        users[str(user_id)] = name
        # Evict oldest entries when over cap (dict insertion order preserved in Python 3.7+)
        if len(users) > _SEEN_USERS_CAP:
            evict_count = len(users) - _SEEN_USERS_CAP
            for old_key in list(users.keys())[:evict_count]:
                del users[old_key]
        redis.set(key, json.dumps(users, separators=(",", ":")), ex=_SEEN_USERS_TTL)
    except Exception as e:
        logger.error("Redis seen user track error for chat %s: %s", chat_id, e)


def get_seen_users(chat_id: int) -> dict:
    key = _seen_key(chat_id)
    try:
        return _decode_dict(redis.get(key))
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
    """
    Atomically decrement the reminder count, flooring at 0.
    Uses a Lua script so the check-and-decrement is a single atomic operation.
    """
    key = _remind_count_key(user_id)
    lua_script = """
    local v = tonumber(redis.call('GET', KEYS[1]))
    if v and v > 0 then
        return redis.call('DECR', KEYS[1])
    end
    return 0
    """
    try:
        redis.eval(lua_script, 1, key)
    except Exception as e:
        logger.error("Redis remind decr error for user %s: %s", user_id, e)


# ─────────────────────────────────────────────
# Remind job persistence (survives restarts)
# Redis key: remind_jobs:<chat_id>  →  JSON list
# ─────────────────────────────────────────────
def _remind_jobs_key(chat_id: int) -> str:
    return f"remind_jobs:{chat_id}"


def _remind_claim_key(job_id: str) -> str:
    return f"remind_claimed:{job_id}"


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
        jobs = _decode_list(raw)
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
        jobs = _decode_list(raw)
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remind job delete error for chat %s: %s", chat_id, e)


def try_claim_remind_job(job_id: str) -> bool:
    """
    Atomically claim a remind job so only one scheduler closure fires it.
    Returns True if this caller successfully claimed it (SETNX), False if
    another closure already claimed it.
    """
    key = _remind_claim_key(job_id)
    try:
        claimed = redis.setnx(key, "1")
        if claimed:
            redis.expire(key, 2 * 3600)  # auto-expire after 2 h
        return bool(claimed)
    except Exception as e:
        logger.error("Redis remind claim error for job %s: %s", job_id, e)
        # On Redis failure, allow the fire (better to double-remind than miss)
        return True


def get_user_remind_jobs(chat_id: int, user_id: int) -> list:
    """Return still-pending remind jobs for a specific user in a chat."""
    key = _remind_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
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
    Uses mget for a single Redis round-trip.
    """
    try:
        keys = redis.keys("remind_jobs:*")
        if not keys:
            return {}
        values = redis.mget(*keys)
        cutoff = _time.time() - 10 * 60
        result = {}
        for key, raw in zip(keys, values):
            chat_id = _key_to_chat_id(key)
            if chat_id is None:
                continue
            jobs = _decode_list(raw)
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
        data = _decode_dict(raw)
        data[str(user_id)] = {"name": name, "day": day, "month": month}
        redis.set(key, json.dumps(data, separators=(",", ":")))
        logger.info("Birthday saved user=%s chat=%s %02d/%02d", user_id, chat_id, day, month)
    except Exception as e:
        logger.error("Redis birthday save error for chat %s: %s", chat_id, e)


def get_all_birthdays(chat_id: int) -> dict:
    """Return {user_id_str: {name, day, month}} for this chat."""
    key = _birthday_key(chat_id)
    try:
        return _decode_dict(redis.get(key))
    except Exception as e:
        logger.error("Redis birthday read error for chat %s: %s", chat_id, e)
        return {}


def get_all_birthday_chats() -> dict:
    """
    Return {chat_id: {user_id_str: {name, day, month}}} across all chats.
    Uses mget for a single Redis round-trip.
    """
    try:
        keys = redis.keys("birthdays:*")
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