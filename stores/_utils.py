"""
stores/_utils.py
────────────────
Shared decode helpers used by all store modules.
"""

import json
from typing import Optional


def _decode_dict(data) -> dict:
    """Safely decode a Redis value expected to be a JSON object (dict)."""
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        result = json.loads(data)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _decode_list(data) -> list:
    """Safely decode a Redis value expected to be a JSON array (list)."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        result = json.loads(data)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _key_to_chat_id(key) -> Optional[int]:
    """Parse a Redis key of the form 'prefix:<chat_id>' and return the chat_id int."""
    key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
    try:
        return int(key_name.split(":", 1)[1])
    except (IndexError, ValueError):
        return None