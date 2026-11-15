"""
changelog.py
────────────
Reads and parses ``CHANGELOG.md`` — the single source of truth for /log notes
and for the bot version shown by /status.

STRUCTURAL CHANGE (refactor):
    Release notes used to be hard-coded as an f-string inside
    ``handlers/misc.py`` while the version lived separately in ``config.py``.
    Publishing an update meant touching Python code in two places and
    hand-escaping Telegram Markdown. Now the notes are plain text in one file
    and this module turns them into structured data.

This module deliberately depends on the standard library only. It sits at the
bottom of the dependency graph, so ``config.py``, handlers and tests can all
import it without pulling in Telegram, Flask or Redis.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolved from this file's location, not the process working directory, so the
# bot behaves the same whether it is started from the repo root or elsewhere.
CHANGELOG_PATH: Path = Path(__file__).resolve().parent / "CHANGELOG.md"
CHANGELOG_FILENAME: str = CHANGELOG_PATH.name

# Used only when the file is missing/unreadable, so /status never crashes.
FALLBACK_VERSION = "0.0.0"

# ── Data model ────────────────────────────────────────────────────────────────
# One Release = one "## vX.Y.Z" block. A Section = one "### Heading" plus its
# following lines. Keeping the shape explicit makes rendering in handlers/log.py
# a straight loop instead of more string surgery.

@dataclass
class Section:
    heading: str = ""
    lines: list[str] = field(default_factory=list)


@dataclass
class Release:
    version: str
    date: str = ""
    title: str = ""
    sections: list[Section] = field(default_factory=list)


# ── Parsing ───────────────────────────────────────────────────────────────────

# Accept -, en-dash, em-dash, | or : as separators in a release heading, and
# make the date/title optional. "## v1.0.2 — 2026-11-15 — Title" and
# "## v1.0.2" both parse cleanly.
_HEADING_RE = re.compile(
    r"^##\s+v?(?P<version>\d+(?:\.\d+)*)\s*(?:[-–—|:]\s*(?P<rest>.*?)\s*)?$"
)
_SUBTITLE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s*(?:[-–—|:]\s*(?P<title>.*?)\s*)?$"
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def parse_markdown(text: str) -> tuple[Release, ...]:
    """
    Parse changelog markdown into newest-first releases.

    Anything before the first "## v..." heading (title, how-to guide, inert
    template) is ignored. HTML comments are stripped first, so the copy/paste
    template can contain a real-looking release heading without being parsed.
    """
    text = _COMMENT_RE.sub("", text)

    releases: list[Release] = []
    current: Release | None = None
    section: Section | None = None

    def commit_section() -> None:
        """Store the open section, dropping trailing blank lines."""
        nonlocal section
        if section is None or current is None:
            return
        while section.lines and section.lines[-1] == "":
            section.lines.pop()
        if section.heading or section.lines:
            current.sections.append(section)
        section = None

    def commit_release() -> None:
        nonlocal current
        commit_section()
        if current is not None:
            releases.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # A level-2 heading opens a new release.
        if line.startswith("## "):
            commit_release()
            match = _HEADING_RE.match(line)
            if not match:
                # Malformed heading: skip it rather than guessing a version.
                logger.warning("Skipping malformed changelog heading: %r", line)
                continue
            date, title = "", ""
            rest = match.group("rest") or ""
            subtitle = _SUBTITLE_RE.match(rest)
            if subtitle:
                date = subtitle.group("date") or ""
                title = subtitle.group("title") or ""
            else:
                title = rest
            current = Release(version=match.group("version"), date=date, title=title)
            continue

        # Ignore the preamble and any other non-release content.
        if current is None:
            continue

        if line.startswith("### "):
            commit_section()
            section = Section(heading=line[4:].strip())
            continue

        if line.startswith("#"):
            # Stray level-1 / level-4 headings are not part of the format.
            continue

        if section is None:
            section = Section()

        if not line:
            # Collapse repeated blanks, but keep one as a visual separator.
            if section.lines and section.lines[-1] != "":
                section.lines.append("")
            continue

        # "- item" and "* item" both become a bullet; plain lines stay as-is.
        if line.startswith("- ") or line.startswith("* "):
            line = line[2:].strip()
        section.lines.append(line)

    commit_release()
    return tuple(releases)


# ── Loading (with hot reload) ─────────────────────────────────────────────────
# STRUCTURAL CHANGE: the old /log text was frozen at import time. Here the
# parsed result is cached against the file's mtime/size, so editing
# CHANGELOG.md takes effect on the very next /log call without a restart.

_cache_lock = threading.Lock()
_cached_stat: tuple[int, int] | None = None
_cached_releases: tuple[Release, ...] = ()


def load_releases(path: Path | str | None = None) -> tuple[Release, ...]:
    """
    Return newest-first releases from the bundled CHANGELOG.md.

    Passing an explicit ``path`` bypasses the cache (used by tests). A missing
    or unreadable file yields an empty tuple so callers can show a friendly
    message instead of raising.
    """
    global _cached_stat, _cached_releases

    if path is not None:
        try:
            return parse_markdown(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Could not read changelog %s: %s", path, exc)
            return ()

    try:
        stat = CHANGELOG_PATH.stat()
    except OSError as exc:
        logger.warning("CHANGELOG.md is missing/unreadable: %s", exc)
        return ()

    fingerprint = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if _cached_stat == fingerprint:
            return _cached_releases
        try:
            releases = parse_markdown(CHANGELOG_PATH.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Could not read CHANGELOG.md: %s", exc)
            return _cached_releases
        _cached_stat, _cached_releases = fingerprint, releases
        return releases


def current_version() -> str:
    """Version of the newest release; also what /status reports."""
    releases = load_releases()
    return releases[0].version if releases else FALLBACK_VERSION


def normalise_version(value: str) -> str:
    """Accept 'v1.0.1', 'V1.0.1', '1.0.1' or '@1.0.1' from a command argument."""
    return value.strip().lstrip("@").lstrip("vV")


def find_release(version: str, releases: tuple[Release, ...] | None = None) -> Release | None:
    """Look up a release by version. Falls back to a unique prefix match."""
    releases = load_releases() if releases is None else releases
    wanted = normalise_version(version)
    if not wanted:
        return None
    for release in releases:
        if release.version == wanted:
            return release
    matches = [r for r in releases if r.version.startswith(f"{wanted}.")]
    return matches[0] if len(matches) == 1 else None
