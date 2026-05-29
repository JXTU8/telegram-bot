# 🤖 Countdown Bot

A feature-rich Telegram group bot built for keeping groups engaged, organised, and entertained. Originally focused on countdowns, it has grown into a full-featured group companion with AI-powered commands, a daily luck system, reminders, birthday tracking, ships, quotes, and more.

---

## ✨ Features

| Category | Highlights |
|---|---|
| ⏱ **Countdowns** | Multi-step creation wizard, daily reminders, short codes, auto-cleanup of expired entries |
| 🤖 **AI (Groq + Serper)** | `/ask` with web search + 6-hour conversation memory, natural language reminders, AI roasts/compliments/8ball |
| 🍀 **Daily Luck** | Deterministic daily scores, tier system, streaks, birthday overrides, April Fools reveal, New Year max-luck |
| ⏰ **Reminders** | Personal and group-wide, regex fast path or AI natural language (`/remind tmr 3pm call mum`), persisted across restarts |
| 🎂 **Birthdays** | Per-chat archive, daily auto-greeting job at 00:01 MYT, birthday luck override |
| 🎮 **Games** | Number guessing game with 60 s inactivity reveal, higher/lower hints |
| 💞 **Ships** | Deterministic daily compatibility scores, 48-hour rolling leaderboard |
| 🏆 **MVP** | Daily random winner, all-time leaderboard |
| 💬 **Quotes** | Save, browse (paginated), and delete group quotes |
| 📊 **Summary** | `/recap` (MVP, luck extremes, top ship, next countdown, birthdays this week), `/leaderboard`, `/stats`, `/profile` |
| 🎲 **Decisions** | `/choose` wizard, `/decide`, `/rank`, `/toss`, `/poll`, `/coinflip` |
| 🎉 **Fun** | `/truth`, `/dare`, `/wouldyourather`, `/vibecheck`, `/curse`, `/bless`, `/hot` |

---

## 🛠️ Tech Stack

- **Runtime** — Python 3.11+
- **Bot framework** — [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v20 (async, job queue)
- **AI** — [Groq](https://groq.com/) (`llama-3.3-70b-versatile`) for fast inference
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
| `GROQ_API_KEY` | ⚠️ | Enables `/ask`, `/roast`, `/compliment`, `/8ball`, `/hot`, AI reminders |
| `SERPER_API_KEY` | ⚠️ | Enables web search in `/ask` |
| `BOT_OWNER_ID` | ⚠️ | Telegram user ID of the bot owner (unlocks `/status`, `/lucktest`) |
| `BOT_OWNER_USERNAME` | ⚠️ | Username(s) of the bot owner, comma-separated |
| `RENDER_URL` | ⚠️ | Your Render service URL — enables the self-ping keep-alive |
| `DEFAULT_REMINDER_HOUR` | ➖ | Default countdown reminder hour in MYT (default: `12`) |
| `DEFAULT_REMINDER_MINUTE` | ➖ | Default countdown reminder minute (default: `0`) |
| `FATE_LUCKY_ID` | ➖ | Force a user to always roll max luck |
| `FATE_UNLUCKY_ID` | ➖ | Force a user to always roll min luck |
| `SEARCH_CACHE_TTL_SECONDS` | ➖ | How long to cache Serper results (default: `900`) |
| `GROQ_MODEL` | ➖ | Groq model to use (default: `llama-3.3-70b-versatile`) |

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

### ⏰ Reminders
| Command | Description |
|---|---|
| `/remind 10m take a break` | Set a personal reminder (fast regex) |
| `/remind tmr 3pm call mum` | Natural language reminder (AI-parsed) |
| `/cancelremind` | View and cancel pending reminders |
| `/remindall 1h meeting` | Set a group-wide reminder (admins only) |

### 🎂 Birthdays
| Command | Description |
|---|---|
| `/addbirthday DD/MM` | Save your birthday |
| `/birthday` | See upcoming birthdays in this chat |
| `/deletebirthday` | Remove your birthday |
| `/deletebirthday @user` | Remove someone's birthday (admins only) |

### 🎮 Games & Fun
| Command | Description |
|---|---|
| `/game` | Start a number guessing game (1–100) |
| `/ship @user1 @user2` | Compatibility percentage |
| `/shipboard` | Top ship pairs (resets every 48 h) |
| `/roast @user` | AI-generated personalised roast |
| `/compliment @user` | AI-generated compliment |
| `/vibecheck` | Group mood score |
| `/mvp` | Crown today's MVP |
| `/mvpboard` | All-time MVP leaderboard |
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
| `/rank topic: item1, item2` | Randomly rank a list |
| `/toss` | Pick a random group member |
| `/poll question: opt1, opt2` | Send a native Telegram poll |

### 💬 Quotes
| Command | Description |
|---|---|
| `/quote` | Show a random saved quote |
| `/quote` (reply to a message) | Save that message to the archive |
| `/quotes` | Browse all quotes with prev/next buttons |
| `/deletequote <number>` | Delete a quote (admins only) |

### 📊 Summary
| Command | Description |
|---|---|
| `/recap` | Today's full group summary (MVP, luck, ships, countdown, birthdays) |
| `/leaderboard` | Combined luck + ships + MVP leaderboard |
| `/stats` | Group activity overview |
| `/profile` | Your bot profile in this chat |

### ⚙️ Other
| Command | Description |
|---|---|
| `/start` | Introduction and quick-start |
| `/help` | Paginated help menu by category |
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
  ai.py                 ← Groq client, Serper search, /ask, /choose
  birthdays.py          ← /birthday, /addbirthday, daily job
  countdown.py          ← /addcountdown wizard, daily reminders
  fun.py                ← ship, roast, game, poll, mvp, …
  luck.py               ← luck engine, /luck, /luckboard, /streak
  misc.py               ← /start, /help, /recap, /stats, /profile, /status
  quotes.py             ← /quote, /quotes, /deletequote
  reminders.py          ← /remind (regex + AI), /remindall, restore jobs

stores/
  birthday_store.py     ← Redis: birthdays:<chat_id>
  countdown_store.py    ← Redis: countdowns:<chat_id>
  luck_store.py         ← Redis: luckboard:<chat_id>:<date>, fate_streak:<uid>
  mvp_store.py          ← Redis: mvp_daily:<chat_id>:<date>, mvp_wins:<chat_id>
  quote_store.py        ← Redis: quotes:<chat_id>
  reminder_store.py     ← Redis: remind_jobs:<chat_id>, remind_count:<uid>
  ship_store.py         ← Redis: ship_pairs:<chat_id>:<bucket> (48 h TTL)
  user_store.py         ← Redis: seen_users:<chat_id> (90-day TTL)
```

### Key design decisions

- **All persistence in Redis** — no SQL, no file system. Upstash free tier is enough for hundreds of groups.
- **Async throughout** — blocking store calls are wrapped in `asyncio.to_thread`. The bot stays responsive under load.
- **Deterministic daily randomness** — luck scores, ship scores, and MVP picks use `random.Random(seed)` seeded by `user_id + date`, so results are consistent within a day but change the next.
- **Job persistence** — countdown reminders and personal reminders survive bot restarts via Redis + `restore_jobs` on startup. Overdue reminders are delivered with an apology rather than dropped.
- **Two-tier reminder parsing** — fast regex for standard formats (`10m`, `2h`), Groq AI fallback for natural language (`tmr 3pm`, `next monday`).

---

## 📝 License

MIT — feel free to fork, extend, and deploy your own version.

---

*Built with ❤️ using python-telegram-bot, Groq, and Upstash Redis.*
