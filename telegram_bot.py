"""Free Telegram bot: sends alerts and turns messages into reports.

Create a bot with @BotFather in Telegram (free), then put its token in .env as
TELEGRAM_BOT_TOKEN. The app reads messages with long polling, so it works on
localhost without a public URL or webhook.

To let the bot read every message in a neighborhood group (not just commands),
send /setprivacy to @BotFather, pick the bot and choose Disable.
"""
import os
import threading
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def log(msg):
    print(msg, flush=True)


def token():
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def configured():
    return bool(token())


def call(method, wait=20, **params):
    resp = requests.post(API.format(token=token(), method=method), json=params, timeout=wait)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


_username = None


def bot_username():
    """The bot's @username (without the @), from .env or asked from Telegram once."""
    global _username
    if _username is None:
        env = (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
        if env:
            _username = env
        else:
            try:
                _username = call("getMe")["username"]
            except Exception as e:
                log(f"[telegram] couldn't read bot name: {e}")
                return None
    return _username


def send_message(chat_id, text):
    call("sendMessage", chat_id=chat_id, text=text[:4000], disable_web_page_preview=True)


def start_polling(handle_update):
    """Read new messages forever on a background thread and pass each update to handle_update."""
    if not configured():
        return

    def loop():
        try:
            call("deleteWebhook")   # getUpdates doesn't work while a webhook is set
        except Exception as e:
            log(f"[telegram] {e}")
        log(f"[telegram] listening as @{bot_username()}")
        offset = None
        while True:
            try:
                updates = call("getUpdates", wait=60, timeout=50, offset=offset, allowed_updates=["message"])
            except Exception as e:
                log(f"[telegram] polling error: {e}")
                time.sleep(10)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as e:
                    log(f"[telegram] couldn't handle message: {e}")

    threading.Thread(target=loop, daemon=True).start()
