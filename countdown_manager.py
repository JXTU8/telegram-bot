from datetime import datetime

data = {}

# =====================
# COUNTDOWN FUNCTIONS
# =====================
def set_countdown(chat_id, name, date):
    if chat_id not in data:
        data[chat_id] = {"countdowns": {}, "reminders": {}}
    data[chat_id]["countdowns"][name] = date


def list_countdowns(chat_id):
    return data.get(chat_id, {}).get("countdowns", {})


# =====================
# REMINDER FUNCTIONS
# =====================
def set_reminder(chat_id, name, time_str):
    if chat_id not in data:
        data[chat_id] = {"countdowns": {}, "reminders": {}}
    data[chat_id]["reminders"][name] = time_str


def list_reminders():
    return data