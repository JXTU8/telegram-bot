"""
Tests for the changelog-driven /log.

These cover the part that matters most for day-to-day maintenance: adding a
section to CHANGELOG.md must immediately change what /log shows, without any
Python edits and without a restart.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.com")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "dummy")

import changelog
from changelog import (
    find_release, load_releases, normalise_version, parse_markdown,
)
from handlers.log import (
    _chunks, _escape_body, log_command, render_index, render_release,
)

_SAMPLE = """\
# Title / preamble is ignored

<!--
## v9.9.9 — template inside a comment must not be parsed
-->

## v1.2.0 — 2026-11-15 — Big release

### Added
- `/log all` works
* Star bullets also work
A plain line becomes a bullet too

### Fixed
- One bug

## v1.1.0 | 2026-11-01 | Older release
### What's new
- Older stuff

## v1.0.0
- First release
"""


class ChangelogParserTests(unittest.TestCase):
    def test_bundled_file_parses_newest_first(self):
        releases = load_releases()
        self.assertGreaterEqual(len(releases), 2)
        self.assertEqual(releases[0].version, changelog.current_version())
        self.assertEqual([r.version for r in releases],
                         sorted((r.version for r in releases),
                                key=lambda v: tuple(map(int, v.split("."))), reverse=True))

    def test_template_inside_html_comment_is_ignored(self):
        self.assertNotIn("v1.0.3", [r.version for r in load_releases()])

    def test_preamble_sections_and_bullets(self):
        releases = parse_markdown(_SAMPLE)
        self.assertEqual([r.version for r in releases], ["1.2.0", "1.1.0", "1.0.0"])
        newest = releases[0]
        self.assertEqual(newest.date, "2026-11-15")
        self.assertEqual(newest.title, "Big release")
        self.assertEqual([s.heading for s in newest.sections], ["Added", "Fixed"])
        # Bullets lose their "- "/"* " prefix; trailing blank lines are trimmed.
        self.assertEqual(newest.sections[0].lines, [
            "`/log all` works", "Star bullets also work",
            "A plain line becomes a bullet too",
        ])
        self.assertEqual(newest.sections[1].lines, ["One bug"])

    def test_separator_variants_and_optional_date(self):
        releases = parse_markdown(_SAMPLE)
        self.assertEqual((releases[1].date, releases[1].title),
                         ("2026-11-01", "Older release"))
        self.assertEqual(releases[2].date, "")
        self.assertEqual(releases[2].title, "")

    def test_malformed_heading_does_not_create_a_release(self):
        releases = parse_markdown("## not-a-version\n- nope\n\n## v2.0.0\n- yes")
        self.assertEqual([r.version for r in releases], ["2.0.0"])

    def test_version_helpers(self):
        releases = parse_markdown(_SAMPLE)
        self.assertEqual(normalise_version("  @v1.1.0 "), "1.1.0")
        self.assertEqual(find_release("v1.1.0", releases).version, "1.1.0")
        self.assertEqual(find_release("1.0", releases).version, "1.0.0")
        self.assertIsNone(find_release("9.9", releases))
        self.assertIsNone(find_release("", releases))

    def test_editing_the_file_is_picked_up_without_a_restart(self):
        """The cache is keyed on mtime+size, so a save invalidates it."""
        original_path = changelog.CHANGELOG_PATH
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text("## v1.0.0\n- first\n", encoding="utf-8")
            changelog.CHANGELOG_PATH = path
            try:
                self.assertEqual(changelog.current_version(), "1.0.0")
                path.write_text("## v2.0.0\n- second\n", encoding="utf-8")
                # Windows filesystems can have coarse mtime granularity.
                os.utime(path, (10**9, 10**9))
                self.assertEqual(changelog.current_version(), "2.0.0")
            finally:
                changelog.CHANGELOG_PATH = original_path
                changelog._cached_stat = None
                changelog._cached_releases = ()


class LogRenderingTests(unittest.TestCase):
    def setUp(self):
        self.releases = parse_markdown(_SAMPLE)

    def test_render_release_shows_version_heading_and_bullets(self):
        text = render_release(self.releases[0])
        self.assertIn("*Countdown Bot — v1.2.0*", text)
        self.assertIn("*Added*", text)
        self.assertIn("• `/log all` works", text)
        self.assertIn("/log all for older releases", text)

    def test_render_index_lists_every_version(self):
        text = render_index(self.releases)
        for release in self.releases:
            self.assertIn(f"*v{release.version}*", text)

    def test_escape_body_keeps_code_spans_and_escapes_control_chars(self):
        self.assertEqual(
            _escape_body("Use `/log` and a_b *c* [x]"),
            "Use `/log` and a\\_b \\*c\\* \\[x]",
        )
        self.assertEqual(_escape_body("unmatched ` tick"), "unmatched \\` tick")

    def test_chunks_stay_within_limit_and_preserve_text(self):
        text = "\n".join(f"line {i}: " + "x" * 80 for i in range(200))
        chunks = _chunks(text, limit=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertEqual("\n".join(chunks), text)

    def test_chunks_hard_split_a_single_overlong_line(self):
        chunks = _chunks("z" * 1200, limit=500)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertEqual("".join(chunks), "z" * 1200)


class _FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMessage()


class _FakeContext:
    def __init__(self, args):
        self.args = args


class LogCommandTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, args):
        update = _FakeUpdate()
        await log_command(update, _FakeContext(args))
        return "\n".join(text for text, _ in update.message.sent)

    async def test_no_args_shows_newest_release(self):
        text = await self._run([])
        self.assertIn("Countdown Bot — v", text)
        self.assertIn("•", text)

    async def test_all_lists_history(self):
        self.assertIn("Release History", await self._run(["all"]))

    async def test_specific_version(self):
        self.assertIn("v1.0.1", await self._run(["v1.0.1"]))

    async def test_unknown_version_is_friendly(self):
        self.assertIn("No notes for", await self._run(["9.9.9"]))

    async def test_help_shows_authoring_guide(self):
        text = await self._run(["help"])
        self.assertIn("CHANGELOG.md", text)
        self.assertIn("## v1.0.3", text)

    async def test_markdown_parse_mode_is_always_set(self):
        update = _FakeUpdate()
        await log_command(update, _FakeContext([]))
        self.assertTrue(all(kwargs.get("parse_mode") == "Markdown"
                            for _, kwargs in update.message.sent))


if __name__ == "__main__":
    unittest.main()
