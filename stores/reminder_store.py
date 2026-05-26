"""
stores/reminder_store.py
────────────────────────
Reminder job persistence and per-user count cap.
Redis keys:
  remind_count:<user_id>   →  int           (TTL 25 h)
  remind_jobs:<chat_id>    →  JSON list
  remind_claimed:<job_id>  →  "1"           (TTL 2 h, prevents double-fire)
"""

import json
import logging
import os
import time as _time

from db import redis
from stores._utils import _decode_list

logger = logging.getLogger(__name__)

_REMIND_COUNT_TTL = 60 * 60 * 25   # 25 hours


# ── Per-user reminder count ───────────────────────────────────────────────────

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
    try:
        raw = redis.get(_remind_count_key(user_id))
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


def decrement_remind_count(user_id: int) -> None:
    """Atomically decrement the reminder count, flooring at 0 via Lua script."""
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


# ── Remind job persistence ────────────────────────────────────────────────────

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
        jobs = _decode_list(redis.get(key))
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
    key = _remind_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remind job delete error for chat %s: %s", chat_id, e)


def try_claim_remind_job(job_id: str) -> bool:
    """
    Atomically claim a remind job so only one scheduler closure fires it.
    Returns True if successfully claimed, False if already claimed.
    """
    key = _remind_claim_key(job_id)
    try:
        claimed = redis.setnx(key, "1")
        if claimed:
            redis.expire(key, 2 * 3600)
        return bool(claimed)
    except Exception as e:
        logger.error("Redis remind claim error for job %s: %s", job_id, e)
        return True  # on Redis failure, allow the fire rather than miss it


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
    Drops jobs more than 10 minutes overdue. Uses mget for one round-trip.
    """
    from stores._utils import _key_to_chat_id
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
            valid = [j for j in _decode_list(raw) if j.get("fire_at", 0) > cutoff]
            if valid:
                result[chat_id] = valid
        return result
    except Exception as e:
        logger.error("Redis get_all_remind_jobs error: %s", e)
        return {}