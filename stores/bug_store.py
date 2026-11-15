"""
stores/bug_store.py
───────────────────
Persistent state for the global /bug reporting system.

Redis keys:
  bug_banned_users       → JSON list of user_id ints (no TTL, until /unbanbug)
  bug_announced_chats    → Redis set of chat IDs that already saw the notice
  bug_cooldown:<user_id> → short-lived key with EX (rate-limit window)

STRUCTURAL DESIGN NOTE:
    All three checks are *atomic Redis operations*, not read-then-write.
    ``SET NX EX`` claims a cooldown slot and ``SADD`` claims the announcement in
    one round-trip. With ``concurrent_updates(True)`` two simultaneous commands
    from the same user/group cannot both slip through a check performed in
    Python — that race is the classic way a rate limiter gets bypassed.
"""

import json
import logging

from db import redis
from stores._utils import _decode_list

logger = logging.getLogger(__name__)

_BUG_BAN_KEY = "bug_banned_users"
_ANNOUNCED_KEY = "bug_announced_chats"
_COOLDOWN_PREFIX = "bug_cooldown:"


# ── /banbug & /unbanbug ──────────────────────────────────────────────────────

def _banned_ids() -> set[int]:
    """Decode the ban list, tolerating missing or corrupted data."""
    return {int(uid) for uid in _decode_list(redis.get(_BUG_BAN_KEY))}


def get_bug_banned_users() -> set[int]:
    """Return every user blocked from /bug."""
    try:
        return _banned_ids()
    except Exception as e:
        logger.error("Redis bug-ban list read error: %s", e)
        return set()


def is_bug_banned(user_id: int) -> bool:
    try:
        return user_id in _banned_ids()
    except Exception as e:
        logger.error("Redis bug-ban check error for user %s: %s", user_id, e)
        # Fail closed: if moderation state is unavailable, don't let a possibly
        # abusive reporter through.
        return True


def ban_bug_user(user_id: int) -> bool:
    """Block /bug for a user. Returns True if newly banned."""
    try:
        banned = _banned_ids()
        if user_id in banned:
            return False
        banned.add(user_id)
        redis.set(_BUG_BAN_KEY, json.dumps(sorted(banned), separators=(",", ":")))
        logger.info("Blocked user_id=%s from /bug", user_id)
        return True
    except Exception as e:
        logger.error("Redis bug-ban error for user %s: %s", user_id, e)
        return False


def unban_bug_user(user_id: int) -> bool:
    """Restore /bug for a user. Returns True if they were blocked."""
    try:
        banned = _banned_ids()
        if user_id not in banned:
            return False
        banned.discard(user_id)
        redis.set(_BUG_BAN_KEY, json.dumps(sorted(banned), separators=(",", ":")))
        logger.info("Unblocked user_id=%s from /bug", user_id)
        return True
    except Exception as e:
        logger.error("Redis bug-unban error for user %s: %s", user_id, e)
        return False


# ── Rate limiting ────────────────────────────────────────────────────────────

def claim_bug_cooldown(user_id: int, seconds: int) -> int:
    """
    Atomically claim one report slot for a user.

    Returns:
        0   → allowed (a fresh window starts now)
        > 0 → blocked; seconds left before they may report again
       -1   → Redis failed; caller should refuse the report (fail closed)
    """
    key = f"{_COOLDOWN_PREFIX}{user_id}"
    try:
        if redis.set(key, "1", nx=True, ex=seconds):
            return 0
        remaining = redis.ttl(key)
        return int(remaining) if isinstance(remaining, int) and remaining > 0 else seconds
    except Exception as e:
        logger.error("Redis bug cooldown claim failed for user %s: %s", user_id, e)
        return -1


def release_bug_cooldown(user_id: int) -> None:
    """Undo a claim when delivery failed, so the reporter can try again."""
    try:
        redis.delete(f"{_COOLDOWN_PREFIX}{user_id}")
    except Exception as e:
        logger.warning("Redis bug cooldown release failed for user %s: %s", user_id, e)


# ── One-time group announcement ──────────────────────────────────────────────

def claim_bug_announcement(chat_id: int) -> bool:
    """
    Return True exactly once per group (the first time a command is seen).

    The chat ID is added to a persistent Redis set, so the notice survives
    restarts and redeploys. SADD returns the number of *new* members, which
    doubles as an atomic "was I first?" check.
    """
    try:
        return bool(redis.sadd(_ANNOUNCED_KEY, str(chat_id)))
    except Exception as e:
        logger.error("Redis bug-announcement claim failed for chat %s: %s", chat_id, e)
        # Fail closed: better to skip one notice than to repeat it forever.
        return False


def get_announced_chats() -> set[int]:
    """All groups that have already been shown the /bug notice (for /status)."""
    try:
        chats = set()
        for member in (redis.smembers(_ANNOUNCED_KEY) or []):
            value = member.decode() if isinstance(member, (bytes, bytearray)) else str(member)
            try:
                chats.add(int(value))
            except ValueError:
                continue
        return chats
    except Exception as e:
        logger.error("Redis bug-announcement set read error: %s", e)
        return set()
