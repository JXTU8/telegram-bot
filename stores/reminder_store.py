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

_REMIND_COUNT_TTL = 60 * 60 * 24 * 366


# ── Per-user reminder count ───────────────────────────────────────────────────

def _remind_count_key(user_id: int) -> str:
    return f"remind_count:{user_id}"


def increment_remind_count(user_id: int) -> int:
    key = _remind_count_key(user_id)
    try:
        new_val = redis.incr(key)
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
    key = _remind_count_key(user_id)
    try:
        raw = redis.get(key)
        v = int(raw) if raw is not None else 0
        if v > 0:
            redis.decr(key)
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
    job_id = os.urandom(4).hex()
    key = _remind_jobs_key(chat_id)
    try:
        job = {
            "job_id": job_id,
            "user_id": user_id,
            "user_mention_html": user_mention_html,
            "text": text,
            "fire_at": fire_at,
        }
        jobs = _decode_list(redis.get(key))
        jobs.append(job)
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
        return job_id
    except Exception as e:
        logger.error("Redis remind job save error for chat %s: %s", chat_id, e)
        return ""


def delete_remind_job(chat_id: int, job_id: str) -> None:
    key = _remind_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remind job delete error for chat %s: %s", chat_id, e)


def try_claim_remind_job(job_id: str) -> bool:
    key = _remind_claim_key(job_id)
    try:
        claimed = redis.setnx(key, "1")
        if claimed:
            redis.expire(key, 2 * 3600)
        return bool(claimed)
    except Exception as e:
        logger.error("Redis remind claim error for job %s: %s", job_id, e)
        return True


def get_user_remind_jobs(chat_id: int, user_id: int) -> list:
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


def get_remind_job(chat_id: int, user_id: int, job_id: str) -> dict:
    key = _remind_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
        for job in jobs:
            if job.get("user_id") == user_id and job.get("job_id") == job_id:
                return job
        return {}
    except Exception as e:
        logger.error("Redis remind job lookup error for chat %s job %s: %s", chat_id, job_id, e)
        return {}


def get_all_remind_jobs() -> dict:
    from stores._utils import _key_to_chat_id
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="remind_jobs:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
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


def get_all_remind_jobs_for_restore() -> dict:
    from stores._utils import _key_to_chat_id
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="remind_jobs:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return {}
        values = redis.mget(*keys)
        result = {}
        for key, raw in zip(keys, values):
            chat_id = _key_to_chat_id(key)
            if chat_id is None:
                continue
            valid = [j for j in _decode_list(raw) if j.get("fire_at") is not None]
            if valid:
                result[chat_id] = valid
        return result
    except Exception as e:
        logger.error("Redis get_all_remind_jobs_for_restore error: %s", e)
        return {}


# ── Remindall (group reminder) job persistence ────────────────────────────────

def _remindall_jobs_key(chat_id: int) -> str:
    return f"remindall_jobs:{chat_id}"


def save_remindall_job(chat_id: int, set_by: str, text: str, fire_at: float) -> str:
    job_id = os.urandom(4).hex()
    key = _remindall_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
        jobs.append({
            "job_id": job_id,
            "set_by": set_by,
            "text": text,
            "fire_at": fire_at,
        })
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
        return job_id
    except Exception as e:
        logger.error("Redis remindall save error for chat %s: %s", chat_id, e)
        return ""


def delete_remindall_job(chat_id: int, job_id: str) -> None:
    key = _remindall_jobs_key(chat_id)
    try:
        jobs = _decode_list(redis.get(key))
        jobs = [j for j in jobs if j.get("job_id") != job_id]
        redis.set(key, json.dumps(jobs, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis remindall delete error for chat %s: %s", chat_id, e)


def get_all_remindall_jobs() -> dict:
    from stores._utils import _key_to_chat_id
    try:
        keys = []
        cursor = 0
        while True:
            cursor, batch = redis.scan(cursor, match="remindall_jobs:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
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
        logger.error("Redis get_all_remindall_jobs error: %s", e)
        return {}