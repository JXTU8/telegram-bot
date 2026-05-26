"""
stores/quote_store.py
─────────────────────
Quote archive persistence.
Redis key: quotes:<chat_id>  →  JSON list, capped at 100
"""

import json
import logging

from db import redis
from stores._utils import _decode_list

logger = logging.getLogger(__name__)

_QUOTE_CAP = 100


def _quotes_key(chat_id: int) -> str:
    return f"quotes:{chat_id}"


def save_quote(chat_id: int, author: str, text: str, saved_by: str) -> int:
    """
    Append a quote. Returns the new total count,
    or -1 if an identical (text + author) quote already exists.
    """
    key = _quotes_key(chat_id)
    try:
        quotes = _decode_list(redis.get(key))
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
    try:
        return len(_decode_list(redis.get(_quotes_key(chat_id))))
    except Exception:
        return 0


def get_all_quotes(chat_id: int) -> list:
    try:
        return _decode_list(redis.get(_quotes_key(chat_id)))
    except Exception as e:
        logger.error("Redis quotes read error for chat %s: %s", chat_id, e)
        return []


def delete_quote(chat_id: int, index: int) -> tuple:
    key = _quotes_key(chat_id)
    try:
        quotes = _decode_list(redis.get(key))
        if not (1 <= index <= len(quotes)):
            return False, f"⚠️ Invalid number. There are {len(quotes)} quote(s)."
        removed = quotes.pop(index - 1)
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return True, f'✅ Deleted quote #{index}: *"{removed["text"]}"* — {removed["author"]}'
    except Exception as e:
        logger.error("Redis quote delete error for chat %s: %s", chat_id, e)
        return False, "❌ Failed to delete quote."