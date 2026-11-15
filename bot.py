"""
bot.py
──────
Entry point. Build the Application, wire up the handlers, start polling.

STRUCTURAL CHANGE (refactor):
    This file used to hold the ban gate, two wrapper factories, all three
    ConversationHandlers and 35 individual add_handler calls. All of that
    plumbing now lives in ``handlers/registry.py``; this file is deliberately
    small so startup behaviour can be read in one screen.

    To add a command: write the handler in the appropriate handlers/ module,
    then add one line to ``_command_handlers()`` in handlers/registry.py.
    You should not need to touch this file.
"""

import logging

from telegram import Update
from telegram.ext import ApplicationBuilder

from config import TOKEN
from handlers.registry import on_startup, register_handlers
from keep_alive import keep_alive

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    keep_alive()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(16)
        .connect_timeout(10.0)
        .read_timeout(10.0)
        .write_timeout(10.0)
        .post_init(on_startup)
        .build()
    )

    register_handlers(app)

    logger.info("Bot is running...")
    # Pass allowed_updates explicitly so Telegram resets any server-side filter
    # cached from a previous run. When omitted, Telegram silently reuses the
    # last value — if that ever excluded callback_query, every inline button
    # goes undelivered regardless of handler registration.
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
