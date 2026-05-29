"""
stores/mvp_store.py
───────────────────
MVP persistence.
Redis keys:
  mvp_daily:<chat_id>:<YYYY-MM-DD>  →  JSON dict for today's winner
  mvp_wins:<chat_id>                →  JSON dict {user_id_str: {name, wins, last_won}}
"""

import json
import logging

from db import redis
from stores._utils import _decode_dict

logger = logging.getLogger(__name__)

_MVP_DAILY_TTL = 60 * 60 * 36


def _daily_key(chat_id: int, date_str: str) -> str:
    return f"mvp_daily:{chat_id}:{date_str}"


def _wins_key(chat_id: int) -> str:
    return f"mvp_wins:{chat_id}"


def get_today_mvp(chat_id: int, date_str: str) -> dict:
    try:
        return _decode_dict(redis.get(_daily_key(chat_id, date_str)))
    except Exception as e:
        logger.error("Redis mvp daily read error for chat %s: %s", chat_id, e)
        return {}


def save_mvp_win(chat_id: int, date_str: str, user_id: str, name: str) -> dict:
    """Save today's MVP once and increment their all-time win count."""
    user_id = str(user_id)
    daily = {"user_id": user_id, "name": name, "date": date_str}
    try:
        daily_key = _daily_key(chat_id, date_str)
        daily_json = json.dumps(daily, separators=(",", ":"))
        if hasattr(redis, "setnx"):
            created = redis.setnx(daily_key, daily_json)
            if not created:
                existing = _decode_dict(redis.get(daily_key))
                return existing or daily
            redis.expire(daily_key, _MVP_DAILY_TTL)
        else:
            existing = _decode_dict(redis.get(daily_key))
            if existing:
                return existing
            redis.set(daily_key, daily_json, ex=_MVP_DAILY_TTL)

        wins_key = _wins_key(chat_id)
        if hasattr(redis, "eval"):
            lua = """
            local board = {}
            local raw = redis.call('GET', KEYS[1])
            if raw then board = cjson.decode(raw) end
            local entry = board[ARGV[1]] or {name = ARGV[2], wins = 0, last_won = ''}
            entry['name'] = ARGV[2]
            entry['wins'] = tonumber(entry['wins'] or 0) + 1
            entry['last_won'] = ARGV[3]
            board[ARGV[1]] = entry
            redis.call('SET', KEYS[1], cjson.encode(board))
            return 1
            """
            redis.eval(lua, 1, wins_key, user_id, name, date_str)
        else:
            board = _decode_dict(redis.get(wins_key))
            entry = board.get(user_id, {"name": name, "wins": 0, "last_won": ""})
            entry["name"] = name
            entry["wins"] = int(entry.get("wins", 0)) + 1
            entry["last_won"] = date_str
            board[user_id] = entry
            redis.set(wins_key, json.dumps(board, separators=(",", ":")))
        return daily
    except Exception as e:
        logger.error("Redis mvp save error for chat %s user %s: %s", chat_id, user_id, e)
        return daily


def get_mvp_board(chat_id: int, limit: int = 10) -> list:
    try:
        board = _decode_dict(redis.get(_wins_key(chat_id)))
        rows = [
            {"user_id": uid, **entry}
            for uid, entry in board.items()
            if isinstance(entry, dict)
        ]
        rows.sort(key=lambda item: (-int(item.get("wins", 0)), item.get("last_won", "")))
        return rows[:limit]
    except Exception as e:
        logger.error("Redis mvp board read error for chat %s: %s", chat_id, e)
        return []


def get_user_mvp_stats(chat_id: int, user_id: int) -> dict:
    try:
        board = _decode_dict(redis.get(_wins_key(chat_id)))
        return _decode_dict(board.get(str(user_id)))
    except Exception as e:
        logger.error("Redis mvp stats read error for chat %s user %s: %s", chat_id, user_id, e)
        return {}
