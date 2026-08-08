import re

# Grouping logic
categories = {
    "Normal User": [],
    "Admin/Owner Only": [],
}

bot_py_content = open("bot.py").read()

# We need to map command logic. We can extract help descriptions.
help_pages = open("handlers/misc.py").read()

cmd_pattern = re.compile(r'app\.add_handler\(_cmd\("([^"]+)",\s*([a-zA-Z0-9_]+)')
commands = cmd_pattern.findall(bot_py_content)

owner_cmds = ["cmdstats", "status", "ban", "unban", "banlist", "say"]
admin_cmds = ["remindall", "deletequote", "deletebirthday"]

def get_desc(cmd):
    # Some hardcoded descriptions based on misc HELP_PAGES
    if cmd == "start": return "Start the bot"
    elif cmd == "help": return "Show help menu"
    elif cmd == "id": return "Get current Chat and Thread ID"
    elif cmd == "cancel": return "Cancel active setup flow"
    elif cmd == "ask": return "Ask AI anything"
    elif cmd == "addcountdown": return "Add a new countdown"
    elif cmd == "listcountdown": return "See all active countdowns"
    elif cmd == "removecountdown": return "Remove a countdown"
    elif cmd == "editcountdown": return "Edit a countdown's date or time"
    elif cmd == "choose": return "Let the bot pick for you"
    elif cmd == "decide": return "Instant pick"
    elif cmd == "rank": return "Rank anything"
    elif cmd == "toss": return "Pick a random person"
    elif cmd == "poll": return "Send a poll"
    elif cmd == "8ball": return "Magic 8-ball"
    elif cmd == "hot": return "Rate anything out of 100"
    elif cmd == "predict": return "AI predicts the outcome"
    elif cmd == "luck": return "Check daily luck"
    elif cmd == "luckboard": return "Today's luck leaderboard"
    elif cmd == "streak": return "Check luck streak"
    elif cmd == "game": return "Number guessing game (1-100)"
    elif cmd == "trivia": return "AI trivia question"
    elif cmd == "triviaboard": return "All-time trivia leaderboard"
    elif cmd == "ship": return "Compatibility percentage"
    elif cmd == "shipboard": return "Top ship pairs"
    elif cmd == "roast": return "Personalised roast"
    elif cmd == "compliment": return "Personalised compliment"
    elif cmd == "vibecheck": return "Group mood score"
    elif cmd == "mvp": return "Today's MVP"
    elif cmd == "mvpboard": return "All-time MVP leaderboard"
    elif cmd == "truth": return "Random truth question"
    elif cmd == "dare": return "Random dare question"
    elif cmd == "wouldyourather": return "Random would you rather"
    elif cmd == "coinflip": return "Heads or tails"
    elif cmd == "curse": return "Daily curse"
    elif cmd == "bless": return "Daily blessing"
    elif cmd == "quote": return "Show a random saved quote"
    elif cmd == "quotes": return "Browse saved quotes"
    elif cmd == "remind": return "Set a personal reminder"
    elif cmd == "cancelremind": return "View and cancel pending reminders"
    elif cmd == "birthday": return "See upcoming birthdays"
    elif cmd == "addbirthday": return "Set your birthday"
    elif cmd == "leaderboard": return "Combined leaderboard"
    elif cmd == "recap": return "Today's full group activity summary"
    elif cmd == "stats": return "Group activity overview"
    elif cmd == "profile": return "Your bot profile"
    elif cmd == "fate": return "Daily fate reading"
    elif cmd == "fateboard": return "Daily fate leaderboard"
    elif cmd == "lucktest": return "Test your luck multiplier"
    elif cmd in owner_cmds: return f"Owner only command ({cmd})"
    elif cmd in admin_cmds: return f"Admin command ({cmd})"
    return f"{cmd} command"

out = "=== BotFather Command List ===\n\n"
out += "--- NORMAL USER COMMANDS ---\n"
for cmd, handler in commands:
    if cmd not in owner_cmds and cmd not in admin_cmds and cmd != "roastmax":
        out += f"{cmd} - {get_desc(cmd)}\n"

out += "\n--- ADMIN / OWNER COMMANDS ---\n"
for cmd, handler in commands:
    if cmd in admin_cmds or cmd in owner_cmds:
        out += f"{cmd} - {get_desc(cmd)}\n"

with open("botfather_commands.txt", "w") as f:
    f.write(out)
print(out)
