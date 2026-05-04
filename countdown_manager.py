"""
countdown_manager.py
────────────────────
Supports multiple named countdowns per group/chat.
Each countdown has its own name, target date, and reminder time.
Data is persisted to a JSON file so it survives restarts.

Structure:
{
  "chat_id": {
    "countdown_name": {
      "target_date": "YYYY-MM-DD",
      "reminder_hour": 8,
      "reminder_minute": 0,
      "created_by": user_id
    },
    ...
  },
  ...
}
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_FILE = Path("data/countdowns.json")


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────
def _load() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load countdowns: %s", e)
        return {}


def _save(data: dict) -> None:
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.error("Failed to save countdowns: %s", e)


def _ckey(chat_id: int) -> str:
    return str(chat_id)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def add_countdown(
    chat_id: int,
    name: str,
    target_date: date,
    hour: int,
    minute: int,
    created_by: int,
) -> None:
    """Add or overwrite a named countdown for a chat."""
    data = _load()
    chat = data.setdefault(_ckey(chat_id), {})
    chat[name] = {
        "target_date": target_date.isoformat(),
        "reminder_hour": hour,
        "reminder_minute": minute,
        "created_by": created_by,
    }
    _save(data)


def get_countdown(chat_id: int, name: str) -> Optional[dict]:
    """Return a single countdown entry dict or None."""
    return _load().get(_ckey(chat_id), {}).get(name)


def get_all_countdowns(chat_id: int) -> dict[str, dict]:
    """Return all countdowns for a chat."""
    return _load().get(_ckey(chat_id), {})


def remove_countdown(chat_id: int, name: str) -> bool:
    """Remove a named countdown. Returns True if it existed."""
    data = _load()
    chat = data.get(_ckey(chat_id), {})
    if name in chat:
        del chat[name]
        data[_ckey(chat_id)] = chat
        _save(data)
        return True
    return False


def countdown_exists(chat_id: int, name: str) -> bool:
    return name in _load().get(_ckey(chat_id), {})


def get_all_chats() -> dict:
    """Return entire data store (used to restore jobs on startup)."""
    return _load()