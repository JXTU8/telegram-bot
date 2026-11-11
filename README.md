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

---

## 🛠️ Tech Stack

- **Runtime** — Python 3.11+
- **Bot framework** — [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v20 (async, job queue)
- **AI** — [Google Gemini](https://ai.google.dev/) (`gemini-3.8-flash`) for fast free-tier inference
- **Web search** — [Serper](https://serper.dev/) (Google results, LRU cached)
- **Database** — [Upstash Redis](https://upstash.com/) (serverless, REST)
- **Web server** — Flask (keep-alive endpoint for Render free tier)
- **Deployment** — [Render](https://render.com/) (free web service)

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `UPSTASH_REDIS_REST_URL` | ✅ | Upstash Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | ✅ | Upstash Redis auth token |
| `GEMINI_API_KEY` | ⚠️ | Enables `/ask`, `/roast`, `/compliment`, `/8ball`, `/hot` |
| `SERPER_API_KEY` | ⚠️ | Enables web search in `/ask` |
| `BOT_OWNER_ID` | ⚠️ | Telegram user ID of the bot owner (unlocks `/status`, `/lucktest`) |
| `BOT_OWNER_USERNAME` | ⚠️ | Username(s) of the bot owner, comma-separated |
| `RENDER_URL` | ⚠️ | Your Render service URL — enables the self-ping keep-alive |
| `DEFAULT_REMINDER_HOUR` | ➖ | Default countdown reminder hour in MYT (default: `12`) |
| `DEFAULT_REMINDER_MINUTE` | ➖ | Default countdown reminder minute (default: `0`) |
| `FATE_LUCKY_ID` | ➖ | Force a user to always roll max luck |
| `FATE_UNLUCKY_ID` | ➖ | Force a user to always roll min luck |
| `SEARCH_CACHE_TTL_SECONDS` | ➖ | How long to cache Serper results (default: `900`) |
| `GEMINI_MODEL` | ➖ | Gemini model to use (default: `gemini-3.8-flash`) |

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

# 2. Copy and fill in your environment variables
cp .env.example .env   # or export them manually

# 3. Run
python bot.py
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
| `/log` | Latest bot update notes (`v1.0.0`) |
| `/cancel` | Cancel an active setup wizard |
| `/status` | Bot health check (owner only) |
| `/lucktest <score>` | Preview a luck card without saving (owner only) |

---

## 🏗️ Architecture

```
bot.py                  ← entry point, handler registration
config.py               ← env vars, timezone, version
helpers.py              ← shared utilities (display, escape, admin check, RNG)
constants.py            ← all static text (tiers, messages, quotes)
db.py                   ← Upstash Redis client
keep_alive.py           ← Flask server + self-ping thread

handlers/
  ai.py                 ← Gemini client, Serper search, /ask, /choose
  countdown.py          ← /addcountdown wizard, daily reminders
  fun.py                ← ship, roast, game, and other fun commands
  luck.py               ← luck engine, /luck, /luckboard, /streak
  misc.py               ← /start, /help, /recap, /stats, /profile, /status

stores/
  countdown_store.py    ← Redis: countdowns:<chat_id>
  luck_store.py         ← Redis: luckboard:<chat_id>:<date>, fate_streak:<uid>
  ship_store.py         ← Redis: ship_pairs:<chat_id>:<bucket> (48 h TTL)
  user_store.py         ← Redis: seen_users:<chat_id> (90-day TTL)
```

### Key design decisions

- **All persistence in Redis** — no SQL, no file system. Upstash free tier is enough for hundreds of groups.
- **Async throughout** — blocking store calls are wrapped in `asyncio.to_thread`. The bot stays responsive under load.
- **Deterministic daily randomness** — luck and ship scores use `random.Random(seed)` seeded by `user_id + date`, so results are consistent within a day but change the next.
- **Job persistence** — countdown reminders survive bot restarts via Redis + `restore_jobs` on startup.

---

## 📝 License

MIT — feel free to fork, extend, and deploy your own version.

---

*Built with ❤️ using python-telegram-bot, Gemini, and Upstash Redis.*
