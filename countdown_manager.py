from datetime import datetime
from config import TIMEZONE

active_countdowns = {}

def add_countdown(chat_id, target_date):
    active_countdowns[chat_id] = target_date


def get_countdown(chat_id):
    return active_countdowns.get(chat_id, None)


def list_all():
    return active_countdowns