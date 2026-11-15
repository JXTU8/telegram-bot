"""
handlers/misc.py — backward-compatible import facade (no logic lives here).

STRUCTURAL CHANGE (refactor):
    This file used to be a 790-line catch-all for every "miscellaneous" command.
    It has been split by purpose into:

        handlers/general.py   /start, /help, /cancel
        handlers/log.py       /log (release notes)
        handlers/bug.py       /bug, /banbug, /unbanbug
        handlers/stats.py     /leaderboard, /recap, /stats, /profile
        handlers/admin.py     /status, /ban family, /say, /id, /cmdstats
        handlers/tracking.py  background trackers, error handler, timeout

    New code should import from those modules directly. This shim only re-exports
    the old names so any external script or REPL that did
    ``from handlers.misc import x`` keeps working. It can be deleted once no
    callers remain.
"""

from handlers.admin import (
    ban_command, banlist_command, cmdstats_command, id_command, say_command,
    status_command, unban_command,
)
from handlers.general import (
    cancel_command, help_callback, help_command, start_command,
)
from handlers.log import log_command
from handlers.stats import (
    leaderboard_command, profile_command, recap_command, stats_command,
)
from handlers.tracking import (
    command_stats_tracker, conversation_timeout, error_handler,
    seen_user_tracker,
)

__all__ = [
    "start_command", "help_command", "help_callback", "log_command",
    "stats_command", "profile_command", "status_command",
    "seen_user_tracker", "error_handler", "conversation_timeout",
    "cancel_command", "leaderboard_command", "recap_command",
    "ban_command", "unban_command", "banlist_command", "say_command",
    "id_command", "cmdstats_command", "command_stats_tracker",
]
