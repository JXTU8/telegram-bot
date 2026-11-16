# Countdown Bot — Release Notes

<!--
==========================================================================
 HOW TO PUBLISH AN UPDATE  (this file IS the /log command)
==========================================================================

 There is no Python code to edit. Just add a new section at the TOP of the
 list of releases below. Newest entry always wins: /log shows it, and /status
 reports its version number.

 1. Copy the TEMPLATE block below.
 2. Paste it directly ABOVE the current newest `## v...` heading.
 3. Change the version (and date). Write your bullet points.
 4. Save. On a local run the bot picks up the change on the next /log call;
    on Render it goes live after the deploy.

 Supported format (everything except the version is optional):

     ## v1.0.3 — 2026-11-20 — Short title for this release
     ### Added
     - One bullet point per change
     - Backticks work: /log renders `code` inline
     ### Fixed
     - Another bullet point

 Rules of thumb:
   * A `## vX.Y.Z` line starts a release. Keep the `v` + numbers; the rest of
     the line is the date/title, shown as a subtitle.
   * `### Heading` starts a bold section. Every non-blank line under it becomes
     a bullet; `- ` and `* ` prefixes are optional and stripped automatically.
   * Blank lines are preserved as spacing.
   * Anything inside <!-- ... --> is ignored, so the template stays inert.
   * Don't use raw * or _ for emphasis — Telegram Markdown is applied by the
     bot. Backtick code spans are safe.
   * Older releases are never deleted, so /log all and /log 1.0.1 keep working.

==========================================================================
-->

<!-- TEMPLATE — copy from the line below up to the closing marker
## v1.0.3 — 2026-11-20 — Short title for this release

### Added
- Something new

### Fixed
- Something that broke
-->
## v1.0.4 — 2026-11-16 — /ask error message

### Added
- There will be more error message for /ask if any error occur.

## v1.0.3 — 2026-11-15 — Global bug reporting

### Added
- `/bug <description>` — report a bug straight to the developer from any group or DM
- Reports arrive with the description, username, a copyable user ID and the chat name
- One-time notice introducing `/bug`, shown on the first command in each group
- `/bug` usage is now listed in `/help`; 

### Changed
- `/bug` is rate-limited to one report
- Background trackers now share one handler so command stats actually record usage

## v1.0.2 — 2026-11-14 — Changelog-driven /log and codebase cleanup

### Added
- `/log` now reads its notes from `CHANGELOG.md` — edit one plain-text file, no code changes
- `/log all` lists every release, `/log 1.0.1` opens a specific version, `/log help` shows the authoring guide
- The version reported by `/status` is now taken from the newest entry in this file

### Changed
- `handlers/misc.py` was split into `general.py`, `stats.py`, `admin.py` and `tracking.py`
- Command wiring moved out of `bot.py` into a declarative table in `handlers/registry.py`
- All environment variables are now read in one place: `config.py`

### Fixed
- `/luck` no longer crashes on a leftover undefined variable
- The number-guessing game no longer defines two separate reveal timers

## v1.0.1 — 2026-11-10 — Gemini upgrade

### What's new
- `/ask` now uses Gemini Flash
- Google-search context remains powered by Serper
- Faster AI replies with automatic-function calls disabled
- AI requests now time out safely instead of getting stuck
