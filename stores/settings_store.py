"""
stores/settings_store.py
────────────────────────
Persistent reminder destination settings.
"""

import json
import logging

from db import redis
from stores._utils import _decode_dict

logger = logging.getLogger(__name__)

_DESTINATIONS_KEY = "settings:reminder_destinations"
_SELECTED_KEY = "settings:selected_reminder_destination"


def _dest_id(chat_id: int, thread_id=None) -> str:
    return f"{chat_id}:{thread_id if thread_id is not None else 'main'}"


def _split_dest_id(dest_id: str) -> tuple[int, int | None] | None:
    try:
        chat_id_str, thread_str = str(dest_id).split(":", 1)
        return int(chat_id_str), None if thread_str == "main" else int(thread_str)
    except (TypeError, ValueError):
        return None


def remember_reminder_destination(chat_id: int, title: str, thread_id=None) -> None:
    try:
        destinations = _decode_dict(redis.get(_DESTINATIONS_KEY))
        did = _dest_id(chat_id, thread_id)
        label = title or str(chat_id)
        if thread_id is not None:
            label = f"{label} · topic {thread_id}"
        destinations[did] = {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "label": label,
        }
        redis.set(_DESTINATIONS_KEY, json.dumps(destinations, separators=(",", ":")))
    except Exception as e:
        logger.error("Redis settings destination save error for chat %s: %s", chat_id, e)


def get_reminder_destinations() -> list[dict]:
    try:
        destinations = _decode_dict(redis.get(_DESTINATIONS_KEY))
        return sorted(destinations.values(), key=lambda item: str(item.get("label", "")))
    except Exception as e:
        logger.error("Redis settings destination read error: %s", e)
        return []


def set_selected_reminder_destination(chat_id: int, thread_id=None) -> None:
    try:
        redis.set(_SELECTED_KEY, _dest_id(chat_id, thread_id))
    except Exception as e:
        logger.error("Redis selected reminder destination save error: %s", e)


def get_selected_reminder_destination() -> dict | None:
    try:
        selected = redis.get(_SELECTED_KEY)
        if isinstance(selected, (bytes, bytearray)):
            selected = selected.decode("utf-8")
        parsed = _split_dest_id(selected)
        if parsed is None:
            return None
        chat_id, thread_id = parsed
        destinations = _decode_dict(redis.get(_DESTINATIONS_KEY))
        data = destinations.get(_dest_id(chat_id, thread_id), {})
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "label": data.get("label") or str(chat_id),
        }
    except Exception as e:
        logger.error("Redis selected reminder destination read error: %s", e)
        return None
