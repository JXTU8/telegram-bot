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
        if hasattr(redis, "eval"):
            lua = """
            local quotes = {}
            local raw = redis.call('GET', KEYS[1])
            if raw then quotes = cjson.decode(raw) end
            local text_norm = string.lower(ARGV[2])
            for _, q in ipairs(quotes) do
                if string.lower(q['text'] or '') == text_norm and (q['author'] or '') == ARGV[1] then
                    return -1
                end
            end
            table.insert(quotes, cjson.decode(ARGV[3]))
            while #quotes > tonumber(ARGV[4]) do
                table.remove(quotes, 1)
            end
            redis.call('SET', KEYS[1], cjson.encode(quotes))
            return #quotes
            """
            quote = {"author": author, "text": text, "saved_by": saved_by}
            return int(redis.eval(
                lua, 1, key, author, text.strip().casefold(),
                json.dumps(quote, separators=(",", ":")),
                str(_QUOTE_CAP),
            ))
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


def get_user_quote_counts(chat_id: int, display_name: str) -> tuple:
    """Return (authored_count, saved_count) for a display name in this chat."""
    try:
        quotes = _decode_list(redis.get(_quotes_key(chat_id)))
        name_norm = display_name.strip().casefold()
        authored = sum(1 for q in quotes if q.get("author", "").strip().casefold() == name_norm)
        saved = sum(1 for q in quotes if q.get("saved_by", "").strip().casefold() == name_norm)
        return authored, saved
    except Exception as e:
        logger.error("Redis user quote count error for chat %s: %s", chat_id, e)
        return 0, 0


def get_all_quotes(chat_id: int) -> list:
    try:
        return _decode_list(redis.get(_quotes_key(chat_id)))
    except Exception as e:
        logger.error("Redis quotes read error for chat %s: %s", chat_id, e)
        return []


def delete_quote(chat_id: int, index: int) -> tuple:
    key = _quotes_key(chat_id)
    try:
        if hasattr(redis, "eval"):
            lua = """
            local quotes = {}
            local raw = redis.call('GET', KEYS[1])
            if raw then quotes = cjson.decode(raw) end
            local idx = tonumber(ARGV[1])
            if idx < 1 or idx > #quotes then
                return cjson.encode({ok = false, count = #quotes})
            end
            local removed = table.remove(quotes, idx)
            redis.call('SET', KEYS[1], cjson.encode(quotes))
            return cjson.encode({ok = true, text = removed['text'] or '', author = removed['author'] or ''})
            """
            result = redis.eval(lua, 1, key, str(index))
            data = json.loads(result.decode("utf-8") if isinstance(result, bytes) else result)
            if not data.get("ok"):
                return False, f"⚠️ Invalid number. There are {data.get('count', 0)} quote(s)."
            return True, f'✅ Deleted quote #{index}: "{data["text"]}" — {data["author"]}'
        quotes = _decode_list(redis.get(key))
        if not (1 <= index <= len(quotes)):
            return False, f"⚠️ Invalid number. There are {len(quotes)} quote(s)."
        removed = quotes.pop(index - 1)
        redis.set(key, json.dumps(quotes, separators=(",", ":")))
        return True, f'✅ Deleted quote #{index}: "{removed["text"]}" — {removed["author"]}'
    except Exception as e:
        logger.error("Redis quote delete error for chat %s: %s", chat_id, e)
        return False, "❌ Failed to delete quote."
