"""
stores/trivia_store.py
──────────────────────
Trivia win tracking.
Redis key: trivia_wins:<chat_id>  →  JSON dict {user_id_str: {name, wins}}
No TTL — wins are cumulative and permanent.
"""

import json
import logging

from db import redis
from _utils import _decode_dict

logger = logging.getLogger(__name__)


def _wins_key(chat_id: int) -> str:
    return f"trivia_wins:{chat_id}"


def record_trivia_win(chat_id: int, user_id: int, name: str) -> int:
    """Increment a user's trivia win count. Returns the new total."""
    key = _wins_key(chat_id)
    try:
        board  = _decode_dict(redis.get(key))
        uid    = str(user_id)
        entry  = board.get(uid, {"name": name, "wins": 0})
        entry["name"] = name
        entry["wins"] = int(entry.get("wins", 0)) + 1
        board[uid] = entry
        redis.set(key, json.dumps(board, separators=(",", ":")))
        return entry["wins"]
    except Exception as e:
        logger.error("Redis trivia win error for chat %s user %s: %s", chat_id, user_id, e)
        return 0


def get_trivia_board(chat_id: int, limit: int = 10) -> list:
    """Return top trivia winners sorted by win count descending."""
    try:
        board = _decode_dict(redis.get(_wins_key(chat_id)))
        rows  = [
            {"user_id": uid, "name": entry.get("name", uid), "wins": int(entry.get("wins", 0))}
            for uid, entry in board.items()
            if isinstance(entry, dict)
        ]
        rows.sort(key=lambda r: -r["wins"])
        return rows[:limit]
    except Exception as e:
        logger.error("Redis trivia board read error for chat %s: %s", chat_id, e)
        return []


def get_user_trivia_wins(chat_id: int, user_id: int) -> int:
    """Return a user's total trivia wins in this chat."""
    try:
        board = _decode_dict(redis.get(_wins_key(chat_id)))
        return int(board.get(str(user_id), {}).get("wins", 0))
    except Exception as e:
        logger.error("Redis trivia user wins error for chat %s user %s: %s", chat_id, user_id, e)
        return 0
