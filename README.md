# Neighborhood Watch

A community safety app for your neighborhood. Neighbors report incidents on a live map, post lost and found items, and get alerts by **email, SMS or WhatsApp** whenever something happens in their ZIP code.

Built with Flask, SQLite and Leaflet maps.

## Features

| | |
|---|---|
| **Incident reports** | Report hazards, break-ins, broken streetlights and more. Each report has a photo and an exact pin on the map. Reports can be filtered by status and ZIP code. |
| **Lost & Found** | Post lost or found items. A *lost* post alerts neighbors in the same ZIP. A *found* post alerts everyone with a matching open lost post (same ZIP and category). Tapping **"I found this"** or **"This is mine"** sends your message and contact details straight to the poster. |
| **Multi-channel alerts** | Each member picks email, SMS, WhatsApp, or any mix of them. Every alert is also saved to an in-app inbox, shown under the 🔔 bell. |
| **Important updates** | Admins post announcements to everyone or to a single ZIP. *Urgent* updates also show as a banner across the site. |
| **Admin dashboard** | Admins sign in with an authenticator app (TOTP). From the dashboard they can resolve and delete reports, post updates, and see which delivery channels are configured. |

## Quick start

```bash
git clone https://github.com/Sarthak-bit-coder/Community_App.git
cd Community_App
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: fill in email / Twilio settings
python app.py
```

Then open <http://localhost:5000>.

The SQLite database (`instance/reports.db`) is created automatically on first run, and new columns are added automatically when you upgrade.

## Setting up notifications

Every channel is **optional**. If a channel's credentials are missing, its messages are printed to the console, for example `[notify:sms:not-configured] to=+1415...`. This lets you develop without any accounts. The admin dashboard and the account page show which channels are live.

Put these settings in a `.env` file (see `.env.example`) or set them as environment variables.

### Email (Gmail)

1. Turn on 2-step verification on the Google account.
2. Create an **App Password**: Google Account → Security → App passwords.
3. Set `MAIL_USERNAME` (your Gmail address) and `MAIL_PASSWORD` (the app password).

### SMS and WhatsApp (Twilio)

Both channels go through [Twilio](https://www.twilio.com/).

1. Create a Twilio account and copy the **Account SID** and **Auth Token** into `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`.
2. **SMS:** buy or claim a Twilio phone number and set it as `TWILIO_SMS_FROM`, for example `+15551234567`. Trial accounts can only text numbers you have verified.
3. **WhatsApp:** in the Twilio console, open *Messaging → Try it out → Send a WhatsApp message* to use the sandbox. Set `TWILIO_WHATSAPP_FROM=+14155238886`, the sandbox number. Each recipient must first send the sandbox join code (for example `join <two-words>`) to that number on WhatsApp. For production, register your own WhatsApp sender with Twilio.

Members turn on SMS or WhatsApp in **Account** settings and add their mobile number with a country code. To check everything works, use **Send test notification** on the account page.

## Who gets notified, and when

| Event | Recipients |
|---|---|
| New incident report | Members in that ZIP (not the reporter) |
| New **lost** item | Members in that ZIP. The owner is also told if matching *found* items already exist. |
| New **found** item | Owners of open *lost* posts with the same ZIP and category |
| "I found this" / "This is mine" | The person who made the post, along with the responder's contact details |
| Admin update | All members, or only the chosen ZIP |

Sending runs on a background thread, so a slow SMS or email provider never slows down the page.

## Admin access

Admins are listed in `ADMINS` in `app.py`, each with a TOTP secret. Run `python authenticator.py` to generate a QR code for each admin. Scan it with Google Authenticator, Authy or a similar app, then sign in at `/admin` with your username and the 6-digit code.

> ⚠️ The admin secrets and the default `SECRET_KEY` are committed to the repo. Before deploying, generate new secrets and load them from environment variables.

## Project structure

```
app.py               Flask app: models, routes, notification fan-out
notifications.py     Email / SMS / WhatsApp senders (Flask-Mail + Twilio REST API)
authenticator.py     Generates admin TOTP QR codes
templates/           Jinja templates (base.html is the shared layout)
static/style.css     All styles
static/uploads/      Uploaded photos
```

## Main routes

| Route | Page |
|---|---|
| `/` | Landing page |
| `/reports`, `/report/new` | Incident map and list, and the new-report form |
| `/lost-found`, `/lost-found/new`, `/lost-found/<id>` | Lost & Found |
| `/updates` | Community announcements |
| `/notifications` | In-app notification inbox |
| `/account` | Profile and alert channel settings |
| `/signup`, `/login`, `/logout` | Member accounts (email-only login) |
| `/admin` | Admin dashboard |
