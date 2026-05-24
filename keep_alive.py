"""
keep_alive.py
─────────────
Spins up a tiny Flask web server so Render sees an active HTTP service
and doesn't spin it down. The bot pings itself every 14 minutes via
the RENDER_URL environment variable to prevent the free-tier sleep.
"""

import logging
import os
import threading

import requests
from flask import Flask

from config import env_int

logger = logging.getLogger(__name__)

app = Flask(__name__)
_http_session = requests.Session()
_started = False
_start_lock = threading.Lock()


@app.route("/")
def index():
    return "✅ Bot is alive!", 200


@app.route("/health")
def health():
    return "ok", 200


def _self_ping() -> None:
    """Ping our own URL every 14 minutes to prevent Render free-tier sleep."""
    import time

    url = os.getenv("RENDER_URL", "").rstrip("/")
    if not url:
        logger.warning(
            "RENDER_URL not set — self-ping disabled. "
            "Set it to your Render service URL (e.g. https://my-bot.onrender.com)"
        )
        return

    ping_url = f"{url}/health"
    logger.info("Self-ping enabled → %s every 14 min", ping_url)

    while True:
        time.sleep(14 * 60)  # 14 minutes
        try:
            resp = _http_session.get(ping_url, timeout=10)
            logger.info("Self-ping OK (%s)", resp.status_code)
        except Exception as e:
            logger.warning("Self-ping failed: %s", e)


def keep_alive() -> None:
    """Start the Flask server + self-ping in background threads."""
    global _started

    with _start_lock:
        if _started:
            return
        _started = True

    port = env_int("PORT", 8080)

    # Web server thread
    server_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    server_thread.start()
    logger.info("Keep-alive server started on port %s", port)

    # Self-ping thread
    ping_thread = threading.Thread(target=_self_ping, daemon=True)
    ping_thread.start()