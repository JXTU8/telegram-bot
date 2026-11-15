"""
handlers/log.py
───────────────
``/log`` — public release notes rendered from ``CHANGELOG.md``.

STRUCTURAL CHANGE (refactor):
    The old /log was a 12-line f-string buried in ``handlers/misc.py`` with the
    bullet points, the version and the Telegram Markdown all mixed together.
    Publishing an update meant editing Python source, escaping Markdown by hand
    and separately bumping ``config.VERSION``.

    Now the *content* lives in CHANGELOG.md, the *parsing* in changelog.py, and
    this module only handles presentation + command dispatch. To publish an
    update you edit one plain-text file — see the guide at the top of it.

Supported forms:
    /log            newest release
    /log all        version history (also: /log list, /log history)
    /log 1.0.1      one specific release (also accepts v1.0.1)
    /log help       short authoring guide for whoever maintains the notes
"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from changelog import (
    CHANGELOG_FILENAME,
    Release,
    find_release,
    load_releases,
)

logger = logging.getLogger(__name__)

# Telegram caps a message at 4096 chars. Staying below it leaves headroom for
# Markdown entities and keeps long release notes readable as separate messages.
_MESSAGE_LIMIT = 3900

# Markdown v1 requires _, * and [ to be escaped in user-authored text. Backtick
# code spans are intentionally preserved so notes can show `commands` inline.
_CODE_SPAN_RE = re.compile(r"(`[^`]*`)")

_HISTORY_ALIASES = frozenset({"all", "list", "history"})


def _escape_body(text: str) -> str:
    """Escape Markdown control characters, leaving backtick code spans intact."""
    pieces = _CODE_SPAN_RE.split(text)
    return "".join(
        piece if index % 2 else _escape_plain(piece)
        for index, piece in enumerate(pieces)
    )


def _escape_plain(text: str) -> str:
    # Backticks are escaped too: an unmatched backtick in a note would make
    # Telegram reject the whole message. Matched code spans were already split
    # out by _escape_body and are passed through untouched.
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


def _chunks(text: str, limit: int = _MESSAGE_LIMIT) -> list[str]:
    """Split rendered text into <=limit-char messages, preferring line breaks."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        # Hard-split a pathological single line so no chunk can exceed the cap.
        parts = [line[i:i + limit] for i in range(0, len(line), limit)] or [""]
        for part in parts:
            extra = len(part) + (1 if current else 0)
            if current and length + extra > limit:
                chunks.append("\n".join(current))
                current, length = [], 0
                extra = len(part)
            current.append(part)
            length += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def _subtitle(release: Release) -> str:
    return " · ".join(part for part in (release.date, release.title) if part)


def render_release(release: Release, *, footer: bool = True) -> str:
    """Turn one parsed Release into Telegram Markdown v1 text."""
    lines = [f"📢 *Countdown Bot — v{release.version}*"]
    subtitle = _subtitle(release)
    if subtitle:
        lines.append(f"_{_escape_body(subtitle)}_")
    lines.append("")

    for section in release.sections:
        if section.heading:
            lines.append(f"*{_escape_body(section.heading)}*")
        for line in section.lines:
            lines.append(f"• {_escape_body(line)}" if line else "")
        lines.append("")

    if footer:
        lines.append("_/log all for older releases · /help for all commands_")
    return "\n".join(lines).rstrip()


def render_index(releases: tuple[Release, ...]) -> str:
    """Compact, tap-friendly list of every published version."""
    lines = ["📢 *Release History*\n"]
    for release in releases:
        subtitle = _subtitle(release)
        suffix = f" — {_escape_body(subtitle)}" if subtitle else ""
        lines.append(f"• *v{release.version}*{suffix}")
    lines.append("")
    lines.append(f"_Latest: /log · One version: /log {releases[0].version}_")
    return "\n".join(lines)


_LOG_HELP = (
    "🛠 *Updating /log notes*\n\n"
    f"Edit `{CHANGELOG_FILENAME}` at the project root — no code to change.\n\n"
    "1️⃣ Copy its TEMPLATE block and paste it above the newest `## v...` heading.\n"
    "2️⃣ Set the version and date, then write bullet points under `###` headings.\n"
    "3️⃣ Save. A local bot picks it up on the next /log; Render needs a deploy.\n\n"
    "*Heading format*\n"
    "`## v1.0.3 — 2026-11-20 — Short title`\n"
    "`### Added`\n"
    "`- Something new`\n\n"
    "_Newest entry wins · /log all lists older releases._"
)


async def _reply(update: Update, text: str) -> None:
    """Send text as one or more Markdown messages, respecting Telegram's cap."""
    chunks = _chunks(text)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")
    if len(chunks) > 1:
        logger.debug("/log sent %s message(s)", len(chunks))


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch /log to the newest release, the index, a version or the guide."""
    # reload on every call: load_releases() re-parses only when the file changed.
    releases = load_releases()
    token = (context.args[0].strip().lower() if context.args else "")

    if token == "help":
        await _reply(update, _LOG_HELP)
        return

    if not releases:
        await _reply(
            update,
            "📢 No release notes yet.\n"
            f"_Add a `## v...` section to `{CHANGELOG_FILENAME}` to publish one._",
        )
        return

    if token in _HISTORY_ALIASES:
        await _reply(update, render_index(releases))
        return

    if token:
        release = find_release(token, releases)
        if release is None:
            await _reply(
                update,
                f"🔎 No notes for *v{_escape_plain(token)}*.\n"
                "_Use /log all to see every published version._",
            )
            return
        await _reply(update, render_release(release))
        return

    await _reply(update, render_release(releases[0]))
