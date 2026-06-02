"""
stores/stats_store.py
─────────────────────
Command usage stats persistence.
Redis keys:
  cmd_stats                      → JSON dict {command: total_count}  (no TTL — lifetime total)
  cmd_stats_today:<YYYY-MM-DD>   → JSON dict {command: count}        (TTL 8 days)
"""

import json
import logging
from datetime import datetime

from db import redis
from _utils import _decode_dict

logger = logging.getLogger(__name__)

_CMD_STATS_KEY     = "cmd_stats"
_CMD_STATS_DAY_TTL = 60 * 60 * 24 * 8   # 8 days


def _today_key() -> str:
    from config import TIMEZONE
    return f"cmd_stats_today:{datetime.now(TIMEZONE).strftime('%Y-%m-%d')}"


def increment_cmd_stat(command: str) -> None:
    """Increment both the lifetime and today's usage counter for a command."""
    try:
        # ── Lifetime total ────────────────────────────────────────────────────
        stats = _decode_dict(redis.get(_CMD_STATS_KEY))
        stats[command] = int(stats.get(command, 0)) + 1
        redis.set(_CMD_STATS_KEY, json.dumps(stats, separators=(",", ":")))
        # ── Daily bucket ──────────────────────────────────────────────────────
        today_key = _today_key()
        daily = _decode_dict(redis.get(today_key))
        daily[command] = int(daily.get(command, 0)) + 1
        redis.set(today_key, json.dumps(daily, separators=(",", ":")), ex=_CMD_STATS_DAY_TTL)
    except Exception as e:
        logger.error("Redis cmd stats error for '%s': %s", command, e)


def get_cmd_stats() -> dict:
    """Return lifetime command usage counts {command: total}."""
    try:
        return _decode_dict(redis.get(_CMD_STATS_KEY))
    except Exception as e:
        logger.error("Redis cmd stats read error: %s", e)
        return {}


def get_cmd_stats_today() -> dict:
    """Return today's command usage counts {command: count}."""
    try:
        return _decode_dict(redis.get(_today_key()))
    except Exception as e:
        logger.error("Redis cmd stats today read error: %s", e)
        return {}
"""
stores/stats_store.py
---------------------
Command usage counter persistence.
Redis keys:
  cmdstats:lifetime            -> Redis hash {command: count}
  cmdstats:<YYYY-MM-DD>        -> Redis hash {command: count} (TTL 32 days)
"""

import logging
from datetime import datetime

from config import TIMEZONE
from db import redis

logger = logging.getLogger(__name__)

_LIFETIME_KEY = "cmdstats:lifetime"
_DAILY_KEY_PREFIX = "cmdstats:"
_DAILY_TTL = 60 * 60 * 24 * 32


def _today_key() -> str:
    return f"{_DAILY_KEY_PREFIX}{datetime.now(TIMEZONE).strftime('%Y-%m-%d')}"


def _normalise_command(command: str) -> str:
    return command.strip().lstrip("/").split("@", 1)[0].lower()


def _decode_hash_counts(data) -> dict:
    if not isinstance(data, dict):
        return {}

    counts = {}
    for key, value in data.items():
        name = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
        try:
            counts[name] = int(value)
        except (TypeError, ValueError):
            continue
    return counts


def increment_cmd_stat(command: str) -> int:
    """Increment lifetime and today's usage count for a command."""
    cmd = _normalise_command(command)
    if not cmd:
        return 0

    today_key = _today_key()
    try:
        total = int(redis.hincrby(_LIFETIME_KEY, cmd, 1))
        redis.hincrby(today_key, cmd, 1)
        redis.expire(today_key, _DAILY_TTL)
        return total
    except Exception as e:
        logger.error("Redis command stats increment error for /%s: %s", cmd, e)
        return 0


def get_cmd_stats() -> dict:
    """Return lifetime command usage counts."""
    try:
        return _decode_hash_counts(redis.hgetall(_LIFETIME_KEY))
    except Exception as e:
        logger.error("Redis command stats read error: %s", e)
        return {}


def get_cmd_stats_today() -> dict:
    """Return today's command usage counts."""
    try:
        return _decode_hash_counts(redis.hgetall(_today_key()))
    except Exception as e:
        logger.error("Redis daily command stats read error: %s", e)
        return {}
