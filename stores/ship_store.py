"""
stores/ship_store.py
────────────────────
Ship pair persistence — rolling 48-hour window.
Redis key: ship_pairs:<chat_id>:<bucket>  →  JSON dict  (TTL auto)
"""

import json
import logging
import time as _time

from db import redis
from stores._utils import _decode_dict

logger = logging.getLogger(__name__)

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
        pairs = _decode_dict(redis.get(key))
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


def get_user_ship_stats(chat_id: int, name: str) -> dict:
    """
    Return ship stats for a display name within the current 48-hour window.
    Keys: appearances (int), best_score (float), best_partner (str).
    """
    key = _ship_pairs_key(chat_id)
    try:
        pairs      = _decode_dict(redis.get(key))
        name_norm  = name.strip().casefold()
        appearances  = 0
        best_score   = 0.0
        best_partner = ""
        for pair in pairs.values():
            la = pair.get("label_a", "").strip().casefold()
            lb = pair.get("label_b", "").strip().casefold()
            if la == name_norm or lb == name_norm:
                appearances += 1
                score = float(pair.get("score", 0))
                if score > best_score:
                    best_score   = score
                    best_partner = pair.get("label_b", "") if la == name_norm else pair.get("label_a", "")
        return {"appearances": appearances, "best_score": best_score, "best_partner": best_partner}
    except Exception as e:
        logger.error("Redis user ship stats error for chat %s: %s", chat_id, e)
        return {"appearances": 0, "best_score": 0.0, "best_partner": ""}