"""
config.py
─────────
Every environment-derived setting lives here, and nowhere else.

STRUCTURAL CHANGE (refactor):
    Settings used to be scattered: owner IDs in ``helpers.py``, fate overrides
    in ``handlers/luck.py``, AI keys/models in ``handlers/ai.py``, PORT and
    RENDER_URL in ``keep_alive.py``. Centralising them means one place to look
    when a deployment misbehaves, and modules no longer need to import ``os``
    just to read a variable.

    The bot VERSION constant was removed. It is now derived from the newest
    entry in CHANGELOG.md via ``changelog.current_version()``, so publishing a
    release can no longer leave the version out of sync with the notes.
"""

import os
import sys

import pytz


# ── Shared helper ─────────────────────────────────────────────────────────────

def env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on missing/junk."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ── Required credentials ─────────────────────────────────────────────────────

TOKEN: str = os.getenv("BOT_TOKEN", "")
if not TOKEN:
    sys.exit("❌ BOT_TOKEN environment variable is not set. Exiting.")


# ── Timezone (MYT, GMT+8) ────────────────────────────────────────────────────

TIMEZONE = pytz.timezone("Asia/Kuala_Lumpur")


# ── Owner / moderation ───────────────────────────────────────────────────────
# Re-exported by helpers.py because several handlers import them from there.

BOT_OWNER_ID: int = env_int("BOT_OWNER_ID", 0)
BOT_OWNER_USERNAMES: set[str] = {
    username.strip().lstrip("@").casefold()
    for username in os.getenv("BOT_OWNER_USERNAME", "").replace(",", " ").split()
    if username.strip()
}


# ── Countdown defaults (24-hour, MYT) ────────────────────────────────────────

DEFAULT_REMINDER_HOUR: int = env_int("DEFAULT_REMINDER_HOUR", 12)
DEFAULT_REMINDER_MINUTE: int = env_int("DEFAULT_REMINDER_MINUTE", 0)


# ── Deterministic luck overrides ─────────────────────────────────────────────

FATE_LUCKY_ID: int = env_int("FATE_LUCKY_ID", 0)
FATE_UNLUCKY_ID: int = env_int("FATE_UNLUCKY_ID", 0)


# ── AI + web search ──────────────────────────────────────────────────────────

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
SEARCH_CACHE_TTL_SECONDS: int = max(0, env_int("SEARCH_CACHE_TTL_SECONDS", 900))
AI_THREAD_WORKERS: int = max(2, env_int("AI_THREAD_WORKERS", 3))


# ── Hosting (Render keep-alive) ──────────────────────────────────────────────

PORT: int = env_int("PORT", 8080)
RENDER_URL: str = os.getenv("RENDER_URL", "").rstrip("/")
