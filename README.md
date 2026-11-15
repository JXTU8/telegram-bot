# 🤖 Countdown Bot

A Telegram group bot for countdowns, AI-powered commands, daily luck, ships, games, and group summaries.

---

## ✨ Features

| Category | Highlights |
|---|---|
| ⏱ **Countdowns** | Multi-step creation wizard, daily reminders, short codes, auto-cleanup of expired entries |
| 🤖 **AI (Gemini + Serper)** | `/ask` with web search + 6-hour conversation memory, AI roasts/compliments/8ball |
| 🍀 **Daily Luck** | Deterministic daily scores, tier system, streaks, April Fools reveal, New Year max-luck |
| 🎮 **Games** | Number guessing game with 60 s inactivity reveal, higher/lower hints |
| 💞 **Ships** | Deterministic daily compatibility scores, 48-hour rolling leaderboard |
| 📊 **Summary** | `/recap` (luck extremes, top ship, next countdown), `/leaderboard`, `/stats`, `/profile` |
| 🎲 **Decisions** | `/choose` wizard, `/decide`, `/coinflip` |
| 🎉 **Fun** | `/truth`, `/dare`, `/wouldyourather`, `/vibecheck`, `/curse`, `/bless`, `/hot` |
| 📢 **Release notes** | `/log`, `/log all`, `/log <version>` — edited by writing one plain-text file |
| 🐞 **Bug reports** | `/bug` from any chat → forwarded to the developer, rate-limited, with `/banbug` moderation and a one-time group notice |

---

## 🛠️ Tech Stack

- **Runtime** — Python 3.11+
- **Bot framework** — [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v20 (async, job queue)
- **AI** — [Google Gemini](https://ai.google.dev/) (default model `gemini-3.5-flash-lite`, override with `GEMINI_MODEL`)
- **Web search** — [Serper](https://serper.dev/) (Google results, LRU cached)
- **Database** — [Upstash Redis](https://upstash.com/) (serverless, REST)
- **Release notes** — plain `CHANGELOG.md` (no database, no Python edits)
- **Web server** — Flask (keep-alive endpoint for Render free tier)
- **Deployment** — [Render](https://render.com/) (free web service)

---

## ⚙️ Environment Variables

All of them are read in one place: `config.py`.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `UPSTASH_REDIS_REST_URL` | ✅ | Upstash Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | ✅ | Upstash Redis auth token |
| `GEMINI_API_KEY` | ⚠️ | Enables `/ask`, `/roast`, `/compliment`, `/8ball`, `/hot` |
| `SERPER_API_KEY` | ⚠️ | Enables web search in `/ask` |
| `BOT_OWNER_ID` | ⚠️ | Telegram user ID of the bot owner (unlocks `/status`, `/lucktest`, `/ban`) |
| `BOT_OWNER_USERNAME` | ⚠️ | Username(s) of the bot owner, comma-separated |
| `BUG_ADMIN_ID` | ⚠️ | Where `/bug` reports are sent (your user ID, or a staff group ID). Falls back to `BOT_OWNER_ID` |
| `BUG_COOLDOWN_SECONDS` | ➖ | Seconds between reports from one user (default: `600`, minimum `60`) |
| `RENDER_URL` | ⚠️ | Your Render service URL — enables the self-ping keep-alive |
| `PORT` | ➖ | Port for the Flask keep-alive server (default: `8080`) |
| `DEFAULT_REMINDER_HOUR` | ➖ | Default countdown reminder hour in MYT (default: `12`) |
| `DEFAULT_REMINDER_MINUTE` | ➖ | Default countdown reminder minute (default: `0`) |
| `FATE_LUCKY_ID` | ➖ | Force a user to always roll max luck |
| `FATE_UNLUCKY_ID` | ➖ | Force a user to always roll min luck |
| `SEARCH_CACHE_TTL_SECONDS` | ➖ | How long to cache Serper results (default: `900`) |
| `GEMINI_MODEL` | ➖ | Gemini model to use (default: `gemini-3.5-flash-lite`) |
| `AI_THREAD_WORKERS` | ➖ | Threads for blocking AI I/O (default: `3`) |

> 📌 The **bot version is not an env var**. It is read from the newest heading in
> `CHANGELOG.md`, so `/log` and `/status` can never disagree — see the next section.

---

## 🚀 Deployment (Render)

1. **Fork / clone** this repository.
2. Create a new **Web Service** on Render, connect your repo.
3. Set **Build Command**: `pip install -r requirements.txt`
4. Set **Start Command**: `python bot.py`
5. Add all required environment variables in the Render dashboard.
6. Set `RENDER_URL` to `https://<your-service-name>.onrender.com` to keep the free tier awake.

### Local development

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Export the variables listed above (BOT_TOKEN + the two Redis ones are mandatory)
export BOT_TOKEN=...
export UPSTASH_REDIS_REST_URL=...
export UPSTASH_REDIS_REST_TOKEN=...
export BOT_OWNER_ID=123456789      # unlocks /status, /banbug, ...
export BUG_ADMIN_ID=123456789      # optional: where /bug reports land (defaults to BOT_OWNER_ID)

# 3. Run
python bot.py
```

### Tests

```bash
python -m unittest discover -s tests -v
```

---

## 📖 Commands

### ⏱ Countdown
| Command | Description |
|---|---|
| `/addcountdown` | Add a new countdown (3-step wizard) |
| `/listcountdown` | View all active countdowns |
| `/editcountdown <code>` | Edit a countdown's date or reminder time |
| `/removecountdown <code>` | Delete a countdown |

### 🤖 AI
| Command | Description |
|---|---|
| `/ask <question>` | Ask AI anything, with optional web search context |
| `/8ball <question>` | Magic 8-ball (AI-powered) |
| `/hot <anything>` | Rate anything out of 100 |

> 💡 Reply to any `/ask` answer to continue the conversation without typing `/ask` again.

### 🍀 Daily Luck
| Command | Description |
|---|---|
| `/luck` | Check your daily luck score |
| `/luck @user` | Check another user's luck |
| `/luckboard` | Today's luck leaderboard with streak badges |
| `/streak` | View your current luck streak |

### 🎮 Games & Fun
| Command | Description |
|---|---|
| `/game` | Start a number guessing game (1–100) |
| `/ship @user1 @user2` | Compatibility percentage |
| `/shipboard` | Top ship pairs (resets every 48 h) |
| `/roast @user` | AI-generated personalised roast |
| `/compliment @user` | AI-generated compliment |
| `/vibecheck` | Group mood score |
| `/truth` | Random truth question |
| `/dare` | Random dare |
| `/wouldyourather` | Random would-you-rather |
| `/coinflip` | Heads or tails |
| `/curse @user` | Daily curse |
| `/bless @user` | Daily blessing |

### 🎲 Decisions
| Command | Description |
|---|---|
| `/choose` | Interactive decision wizard |
| `/decide opt1, opt2` | Instant random pick |

### 📊 Summary
| Command | Description |
|---|---|
| `/recap` | Today's full group summary (luck, ships, countdown) |
| `/leaderboard` | Combined luck + ships leaderboard |
| `/stats` | Group activity overview |
| `/profile` | Your bot profile in this chat |

### ⚙️ Other
| Command | Description |
|---|---|
| `/start` | Introduction and quick-start |
| `/help` | Paginated help menu by category |
| `/log` | Latest release notes |
| `/log all` | List every released version |
| `/log 1.0.1` | Notes for one specific version (`v1.0.1` also works) |
| `/log help` | In-chat guide for editing the notes |
| `/bug <description>` | Report a bug to the developer — works in any group or DM, one report per 10 min |
| `/cancel` | Cancel an active setup wizard |
| `/status` | Bot health check (owner only) |
| `/lucktest <score>` | Preview a luck card without saving (owner only) |
| `/banbug <user_id>` | Block a user from `/bug` (owner only) |
| `/unbanbug <user_id>` | Restore their `/bug` access (owner only) |

---

## 🐞 Bug reporting (`/bug`)

Anyone can run `/bug <description>` in a group **or** in a private chat. The bot
forwards a formatted report to `BUG_ADMIN_ID` (or `BOT_OWNER_ID` if unset):

```
🐞 New Bug Report

📝 Description
<whatever they typed>

👤 From: Alice — @alice
🆔 User ID: `123456789`          ← tap to copy
💬 Chat: My Group · `-1001234567`
🕐 When: 2026-11-15 15:22 MYT

🚫 Block /bug: `/banbug 123456789`
🔨 Ban from bot: `/ban 123456789`
```

- **Rate limit:** one report per user every `BUG_COOLDOWN_SECONDS` (default 10
  min). The window is a Redis `SET NX EX` key, so it survives restarts and cannot
  be bypassed by two simultaneous messages. Failures fail **closed**.
- **Moderation:** `/banbug <user_id>` / `/unbanbug <user_id>` (owner only). Both
  also accept a reply to the user's message or a mention from Telegram's picker.
  Bans are stored in Redis (`bug_banned_users`) and checked before anything else.
  Blocked reporters are ignored silently.
- **First-time group notice:** on the very first command in a group, the bot
  posts a one-time introduction to `/bug`. The chat ID is added to the persistent
  Redis set `bug_announced_chats` with an atomic `SADD`, so the notice can never
  repeat — even across redeploys or concurrent commands.
- `/status` shows the destination ID, the number of blocked reporters and how
  many groups have been announced.

> ⚠️ If neither `BUG_ADMIN_ID` nor `BOT_OWNER_ID` is set, `/bug` replies that
> reporting is not configured instead of silently dropping reports.

---

## 📝 Publishing an update (`/log`)

**One file. No Python. No Markdown escaping.** Open `CHANGELOG.md`, copy the
`TEMPLATE` comment block near the top, and paste it **above the newest `## v...`
heading**:

```markdown
## v1.0.3 — 2026-11-20 — Short title for this release

### Added
- Something new (`/code` spans are safe)

### Fixed
- Something that broke
```

Then save. That's the whole workflow.

| You write | The bot does |
|---|---|
| A new `## vX.Y.Z` section at the top | `/log` shows it as the latest release |
| The version number | `/status` reports it, and the `/log` header uses it — no `VERSION` constant to bump |
| `### Added` / `### Fixed` | Renders as a bold section |
| `- item` (or `* item`, or a plain line) | Renders as a `•` bullet |
| Older `## v...` sections left in place | `/log all` and `/log 1.0.1` keep working |
| Backticks around `code` | Preserved as inline code; other Markdown characters are auto-escaped |

Notes:

- Everything before the first `## v...` heading, and everything inside
  `<!-- ... -->`, is ignored — that's why the how-to guide and TEMPLATE can
  live in the same file.
- The date and title are optional. `-`, `–`, `—`, `|` and `:` all work as
  separators.
- Very long notes are split into several Telegram messages automatically.
- **Hot reload:** a locally running bot re-reads the file on the next `/log`
  call (the cache is keyed on file mtime + size). On Render, deploy/restart to
  pick up the new file.
- If the file is missing or empty, `/log` shows a friendly placeholder instead
  of crashing, and `/status` falls back to version `0.0.0`.

---

## 🏗️ Architecture

```
bot.py                  ← entry point ONLY: build app + run_polling
config.py               ← every env var, timezone, owner/fate settings
constants.py            ← all static text (tiers, messages, quotes)
helpers.py              ← shared utilities (display, escape, admin check, RNG)
changelog.py            ← stdlib-only CHANGELOG.md parser + hot-reload cache
CHANGELOG.md            ← 📝 THE /log CONTENT — edit this to publish an update
db.py                   ← Upstash Redis client
keep_alive.py           ← Flask server + self-ping thread

handlers/
  registry.py           ← the wiring diagram: ban gate, conversations, command table
  general.py            ← /start, /help, /cancel
  log.py                ← /log rendering + subcommands (reads changelog.py)
  bug.py                ← /bug, /banbug, /unbanbug (report forwarding + moderation)
  stats.py              ← /leaderboard, /recap, /stats, /profile
  admin.py              ← /status, /ban family, /say, /id, /cmdstats
  tracking.py           ← ONE group-1 passive tracker, one-time notice, error handler
  ai.py                 ← Gemini client, Serper search, /ask, /choose
  countdown.py          ← /addcountdown wizard, daily reminders
  fun.py                ← ship, roast, game, and other fun commands
  luck.py               ← luck engine, /luck, /luckboard, /streak, /lucktest
  misc.py               ← compatibility facade only (re-exports old names, no logic)

stores/
  countdown_store.py    ← Redis: countdowns:<chat_id>
  luck_store.py         ← Redis: luckboard:<chat_id>:<date>, fate_streak:<uid>
  ship_store.py         ← Redis: ship_pairs:<chat_id>:<bucket> (48 h TTL)
  user_store.py         ← Redis: seen_users:<chat_id> (90-day TTL)
  stats_store.py        ← Redis: cmdstats:* hashes
  ban_store.py          ← Redis: banned_users (global list)
  bug_store.py          ← Redis: bug bans, cooldown windows, announced-group set
  _utils.py             ← shared Redis value decoders
```

### What changed in the refactor

| Before | After | Why |
|---|---|---|
| `/log` text hard-coded as an f-string in `misc.py`, version separate in `config.py` | `CHANGELOG.md` → `changelog.py` → `handlers/log.py` | One file to edit, no code or Markdown escaping, no version drift |
| `handlers/misc.py` = 790 lines of unrelated commands | Split into `general` / `log` / `stats` / `admin` / `tracking` | Each file has one job; `misc.py` is now a documented compatibility shim |
| `bot.py` held the ban gate, wrappers, 3 conversations and 37 `add_handler` calls | `handlers/registry.py` owns all wiring; `bot.py` is ~50 lines | Adding a command is a one-line edit; startup is readable in one screen |
| `os.getenv` calls scattered across `helpers.py`, `luck.py`, `ai.py`, `keep_alive.py` | All env reads in `config.py` | One place to audit a deployment |
| Duplicate background-failure callback and two copies of the game reveal timer | `helpers.log_background_failure` + one `job.data`-driven reveal callback | Less copy-paste, fewer divergent bug fixes |
| `/luck` referenced an undefined `is_username_mention` | Unused argument removed | `/luck` no longer raises `NameError` |
| Two separate handlers in group 1 (`filters.ALL` first) | One `passive_tracker` dispatching all three background jobs | PTB runs only **one handler per group** — command stats never used to fire; now they do, and the one-time `/bug` notice is reliable |

### Key design decisions

- **All persistence in Redis** — no SQL, no file system for live data. Upstash's free tier is enough for hundreds of groups. Release notes are the one exception: they are versioned plain text, committed with the code.
- **Async throughout** — blocking store calls are wrapped in `asyncio.to_thread`. The bot stays responsive under load.
- **Deterministic daily randomness** — luck and ship scores use `random.Random(seed)` seeded by `user_id + date`, so results are consistent within a day but change the next.
- **Job persistence** — countdown reminders survive bot restarts via Redis + `restore_jobs` on startup.
- **Handler groups matter** — the ban gate is group `-1`, the single passive tracker group `1`, game guesses group `2`. `handlers/registry.py` documents and preserves that ordering.
- **Atomic moderation state** — `/bug` cooldowns (`SET NX EX`), bans (JSON list) and announced groups (`SADD`) all live in Redis, so restarts and concurrent updates cannot reset a limit or duplicate a notice.

---

## 📝 License

MIT — feel free to fork, extend, and deploy your own version.

---

*Built with ❤️ using python-telegram-bot, Gemini, and Upstash Redis.*
