"""
bot.py
──────
Entry point. Registers all handlers and starts the bot.
All logic lives in handlers/ — edit this file only when adding/removing commands.
"""

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, TypeHandler, filters,
)

from config import TOKEN, TIMEZONE
from keep_alive import keep_alive
from stores.luck_store import delete_old_fateboard_keys
from stores.ban_store import is_banned

# ── Handlers ──────────────────────────────────────────────────────────────────
from handlers.misc import (
    start_command, help_command, help_callback,
    stats_command, profile_command, status_command,
    seen_user_tracker, error_handler, conversation_timeout,
    cancel_command, leaderboard_command, recap_command,
    ban_command, unban_command, banlist_command,say_command,
    threadid_command
)
from handlers.countdown import (
    add_countdown_start, received_name, received_date, received_time,
    editcountdown_start, received_edit_field, received_edit_value,
    cancel, list_countdown, remove_countdown_cmd,
    restore_jobs,
)
from handlers.ai import (
    ask_command, ask_followup_handler,
    choose_start, received_decision, received_options,
)
from handlers.luck import (
    luck_command, luckboard_command, streak_command, lucktest_command,
    fate_command, fateboard_command,
)
from handlers.fun import (
    ship_command, shipboard_command,
    roast_command, compliment_command,
    vibecheck_command, rank_command,
    truth_command, dare_command, would_you_rather_command,
    coinflip_command, eightball_command,
    curse_command, bless_command,
    mvp_command, mvpboard_command, hot_command,
    decide_command, poll_command, toss_command,
    game_command, game_guess_handler,
)
from handlers.reminders import (
    remind_command, cancelremind_command, cancelremind_callback,
    remindall_command, restore_remind_jobs, restore_remindall_jobs,
)
from handlers.birthdays import (
    birthday_command, addbirthday_command, deletebirthday_command,
    birthday_check_job,
)
from handlers.quotes import (
    quote_command, quotes_command, quotes_callback,
    deletequote_command,
)

# ── Conversation states ───────────────────────────────────────────────────────
from helpers import (
    ASK_NAME, ASK_DATE, ASK_TIME,
    ASK_DECISION, ASK_OPTIONS,
    ASK_EDIT_FIELD, ASK_EDIT_VALUE,
    CONV_TIMEOUT,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Ban gate ──────────────────────────────────────────────────────────────────

async def ban_gate(update: Update, context) -> None:
    from telegram.ext import ApplicationHandlerStop
    from helpers import _is_owner
    user = update.effective_user
    if user and not user.is_bot:
        if _is_owner(user):
            return
        banned = await asyncio.to_thread(is_banned, user.id)
        if banned:
            raise ApplicationHandlerStop


# ── Startup hook ──────────────────────────────────────────────────────────────

async def on_startup(app) -> None:
    """Runs once after the bot connects. Restores all persistent jobs from Redis."""
    try:
        await asyncio.to_thread(delete_old_fateboard_keys)
    except Exception as e:
        logger.error("on_startup: delete_old_fateboard_keys failed: %s", e)

    for restore_fn, label in [
        (lambda: restore_jobs(app),           "countdown jobs"),
        (lambda: restore_remind_jobs(app),    "remind jobs"),
        (lambda: restore_remindall_jobs(app), "remindall jobs"),
    ]:
        try:
            await restore_fn()
        except Exception as e:
            logger.error("on_startup: restoring %s failed: %s", label, e)

    midnight = datetime.now(TIMEZONE).replace(
        hour=0, minute=1, second=0, microsecond=0
    ).timetz()
    app.job_queue.run_daily(birthday_check_job, time=midnight)
    logger.info("on_startup complete — all jobs restored.")


# ── Main ──────────────────────────────────────────────────────────────────────

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

    # ── Helper: only fire CommandHandlers on real messages, not edits/channel posts ──
    def _cmd(name, handler):
        return CommandHandler(name, handler, filters=filters.UpdateType.MESSAGE)

    # ── Ban gate — must be registered first, runs before everything else ──────
    app.add_handler(TypeHandler(Update, ban_gate), group=-1)

    # ── Conversation handlers ─────────────────────────────────────────────────
    countdown_conv = ConversationHandler(
        entry_points=[CommandHandler("addcountdown", add_countdown_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_date)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    choose_conv = ConversationHandler(
        entry_points=[CommandHandler("choose", choose_start)],
        states={
            ASK_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_decision)],
            ASK_OPTIONS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("editcountdown", editcountdown_start)],
        states={
            ASK_EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_field)],
            ASK_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_edit_value)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conversation_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
    )

    # ── Callback handlers (most specific patterns first) ──────────────────────
    app.add_handler(CallbackQueryHandler(help_callback,         pattern=r"^help:"))
    app.add_handler(CallbackQueryHandler(quotes_callback,       pattern=r"^quote:"))
    app.add_handler(CallbackQueryHandler(cancelremind_callback, pattern=r"^cancelremind:"))

    # ── Conversation handlers ─────────────────────────────────────────────────
    app.add_handler(countdown_conv)
    app.add_handler(choose_conv)
    app.add_handler(edit_conv)

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(_cmd("start",           start_command))
    app.add_handler(_cmd("help",            help_command))
    app.add_handler(_cmd("cancel",          cancel_command))
    app.add_handler(_cmd("ask",             ask_command))
    app.add_handler(_cmd("listcountdown",   list_countdown))
    app.add_handler(_cmd("removecountdown", remove_countdown_cmd))
    app.add_handler(_cmd("fate",            fate_command))
    app.add_handler(_cmd("luck",            luck_command))
    app.add_handler(_cmd("luckboard",       luckboard_command))
    app.add_handler(_cmd("fateboard",       fateboard_command))
    app.add_handler(_cmd("streak",          streak_command))
    app.add_handler(_cmd("ship",            ship_command))
    app.add_handler(_cmd("shipboard",       shipboard_command))
    app.add_handler(_cmd("roast",           roast_command))
    app.add_handler(_cmd("compliment",      compliment_command))
    app.add_handler(_cmd("vibecheck",       vibecheck_command))
    app.add_handler(_cmd("rank",            rank_command))
    app.add_handler(_cmd("truth",           truth_command))
    app.add_handler(_cmd("dare",            dare_command))
    app.add_handler(_cmd("wouldyourather",  would_you_rather_command))
    app.add_handler(_cmd("coinflip",        coinflip_command))
    app.add_handler(_cmd("8ball",           eightball_command))
    app.add_handler(_cmd("curse",           curse_command))
    app.add_handler(_cmd("bless",           bless_command))
    app.add_handler(_cmd("decide",          decide_command))
    app.add_handler(_cmd("poll",            poll_command))
    app.add_handler(_cmd("toss",            toss_command))
    app.add_handler(_cmd("game",            game_command))
    app.add_handler(_cmd("birthday",        birthday_command))
    app.add_handler(_cmd("addbirthday",     addbirthday_command))
    app.add_handler(_cmd("deletebirthday",  deletebirthday_command))
    app.add_handler(_cmd("remind",          remind_command))
    app.add_handler(_cmd("cancelremind",    cancelremind_command))
    app.add_handler(_cmd("remindall",       remindall_command))
    app.add_handler(_cmd("quote",           quote_command))
    app.add_handler(_cmd("quotes",          quotes_command))
    app.add_handler(_cmd("deletequote",     deletequote_command))
    app.add_handler(_cmd("mvp",             mvp_command))
    app.add_handler(_cmd("mvpboard",        mvpboard_command))
    app.add_handler(_cmd("hot",             hot_command))
    app.add_handler(_cmd("stats",           stats_command))
    app.add_handler(_cmd("leaderboard",     leaderboard_command))
    app.add_handler(_cmd("recap",           recap_command))
    app.add_handler(_cmd("profile",         profile_command))
    app.add_handler(_cmd("status",          status_command))
    app.add_handler(_cmd("lucktest",        lucktest_command))
    app.add_handler(_cmd("ban",             ban_command))
    app.add_handler(_cmd("unban",           unban_command))
    app.add_handler(_cmd("banlist",         banlist_command))
    app.add_handler(_cmd("say", say_command))
    app.add_handler(_cmd("threadid", threadid_command))

    # ── Message handlers (group 0) ────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, ask_followup_handler))

    # ── Message handlers (group 1) ────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.ALL, seen_user_tracker), group=1)

    # ── Message handlers (group 2) ────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, game_guess_handler), group=2)

    app.add_error_handler(error_handler)

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()