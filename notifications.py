"""Outbound notification channels: email (Flask-Mail), SMS and WhatsApp (Twilio).

Every channel is optional. If its credentials are not set in the environment,
the message is printed to the console instead of being sent, so the app keeps
working in development.

Environment variables:
  MAIL_USERNAME, MAIL_PASSWORD        Gmail address + app password for email
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
  TWILIO_SMS_FROM                     e.g. +15551234567 (a Twilio phone number)
  TWILIO_WHATSAPP_FROM                e.g. +14155238886 (Twilio WhatsApp sender / sandbox)
  TELEGRAM_BOT_TOKEN                  free bot token from @BotFather
"""
import os
import re
import threading

import requests
from flask_mail import Message

import telegram_bot

def log(msg):
    print(msg, flush=True)


TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def normalize_phone(raw):
    """Return an E.164 phone number (+15551234567) or None if it can't be parsed.

    Bare 10-digit numbers are assumed to be US numbers.
    """
    if not raw:
        return None
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def email_configured(app):
    user = app.config.get("MAIL_USERNAME") or ""
    return bool(user and app.config.get("MAIL_PASSWORD") and user != "your_email@gmail.com")


def twilio_configured(channel):
    base = os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_SMS_FROM" if channel == "sms" else "TWILIO_WHATSAPP_FROM")
    return bool(base and sender)


def channel_status(app):
    return {
        "email": email_configured(app),
        "sms": twilio_configured("sms"),
        "whatsapp": twilio_configured("whatsapp"),
        "telegram": telegram_bot.configured(),
    }


def _send_twilio(channel, to, body):
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    if channel == "whatsapp":
        sender = "whatsapp:" + os.environ["TWILIO_WHATSAPP_FROM"]
        to = "whatsapp:" + to
    else:
        sender = os.environ["TWILIO_SMS_FROM"]
    resp = requests.post(
        TWILIO_URL.format(sid=sid),
        data={"From": sender, "To": to, "Body": body[:1500]},
        auth=(sid, token),
        timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Twilio {resp.status_code}: {resp.text[:200]}")


def _send_one(app, mail, job):
    channel, to, subject, body = job
    try:
        if channel == "email":
            if not email_configured(app):
                log(f"[notify:email:not-configured] to={to} subject={subject!r}")
                return
            with app.app_context():
                mail.send(Message(subject=subject, recipients=[to], body=body,
                                  sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config["MAIL_USERNAME"]))
        elif channel == "telegram":
            if not telegram_bot.configured():
                log(f"[notify:telegram:not-configured] to={to} body={body[:80]!r}")
                return
            telegram_bot.send_message(to, f"{subject}\n\n{body}")
        else:
            if not twilio_configured(channel):
                log(f"[notify:{channel}:not-configured] to={to} body={body[:80]!r}")
                return
            _send_twilio(channel, to, f"{subject}\n\n{body}")
        log(f"[notify:{channel}] sent to {to}")
    except Exception as e:  # never let a failed send break the request
        log(f"[notify:{channel}:error] to={to}: {e}")


def dispatch(app, mail, jobs):
    """Send (channel, to, subject, body) jobs on a background thread."""
    if not jobs:
        return
    def run():
        for job in jobs:
            _send_one(app, mail, job)
    threading.Thread(target=run, daemon=True).start()
