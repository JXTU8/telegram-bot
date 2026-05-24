import logging
import pytz
import os
import sys

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Shared integer env-var helper
# ─────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s must be an integer. Using %s.", name, default)
        return default


# ─────────────────────────────────────────────
# Bot token — set via environment variable
# ─────────────────────────────────────────────
TOKEN: str = os.getenv("BOT_TOKEN", "")
if not TOKEN:
    sys.exit("❌ BOT_TOKEN environment variable is not set. Exiting.")

# ─────────────────────────────────────────────
# Malaysia timezone (MYT, GMT+8)
# ─────────────────────────────────────────────
TIMEZONE = pytz.timezone("Asia/Kuala_Lumpur")

# ─────────────────────────────────────────────
# Daily reminder time (24-hour, MYT)
# ─────────────────────────────────────────────
REMINDER_HOUR   = 12
REMINDER_MINUTE = 0