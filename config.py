import pytz
import os
import sys

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