# Neighborhood Watch

A community safety app for your neighborhood. Neighbors report incidents on a live map, post lost and found items, and get alerts by **email, SMS or WhatsApp** whenever something happens in their ZIP code.

Built with Flask, SQLite and Leaflet maps.

## Features

| | |
|---|---|
| **Live map overlay** | One map shows everything: neighbor reports, local news incidents, WhatsApp-sourced posts and lost/found items. Each category has its own marker (🚗 crash, 🔥 fire, 🚨 crime, 🚧 closure…). Lost and found items get a circle around the last-seen spot. Click any marker for full details, filter layers from the overlay panel, or tap **📍 Near me** to use your location. |
| **Incident reports** | Report car crashes, fires, hazards, break-ins and more, with a photo and an exact pin. Tap **Use my location** to drop the pin where you're standing. |
| **Local news** | Checks Google News, your chosen local outlets' RSS feeds and NewsAPI for every member ZIP. The AI turns real incidents into map reports and alerts neighbors. When a follow-up article says it's over ("road reopened"), the report is resolved and taken off the map automatically. Stale news incidents clear themselves after 48 h. |
| **WhatsApp AI bot** | Neighbors forward messages from their WhatsApp group to the app's number. The AI reads each one and creates a report or a lost/found post when it spots one (for example, "lost brown dog near 24th & Mission" becomes a lost-pet post with a pin and a search circle). It replies with a link. |
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

## APIs: what's free and where to sign up

| Service | Used for | Cost | Get a key |
|---|---|---|---|
| OpenStreetMap + Nominatim | Map tiles, turning addresses and ZIPs into coordinates | Free, no key (max 1 lookup/second) | none needed |
| Google News RSS | Local news search | Free, no key | none needed |
| Local outlet RSS feeds | News from your local paper or TV station | Free | Find the outlet's RSS link (often `/feed` or `/rss`) and add it to `NEWS_RSS_FEEDS` |
| NewsAPI.org | Extra news coverage | Free developer plan: 100 requests/day, articles delayed 24 h, non-commercial use only. Paid plans for production. | <https://newsapi.org/register> |
| Anthropic Claude (Haiku 5.5) | AI that reads WhatsApp messages and news | Pay-as-you-go, about $0.10 per million input tokens (roughly 1,000 messages per cent). Without a key, the app uses free keyword rules. | <https://console.anthropic.com/> |
| Twilio | SMS + WhatsApp alerts, WhatsApp bot | Free trial credit; WhatsApp sandbox is free for testing | <https://www.twilio.com/try-twilio> |
| ngrok | Gives your local app a public URL so Twilio can reach it | Free plan | <https://ngrok.com/download> |

## Setting up the WhatsApp AI bot

WhatsApp doesn't let bots read group chats, so neighbors **forward** (or send) messages to the bot's number instead.

1. Set up Twilio WhatsApp (see above) and add `ANTHROPIC_API_KEY` to `.env` for the AI. It works without the key, but less accurately.
2. Expose your app: `ngrok http 5000`, then set `PUBLIC_BASE_URL` to the https URL ngrok prints.
3. In the Twilio console, open the WhatsApp sandbox settings. Set **When a message comes in** to `<PUBLIC_BASE_URL>/webhooks/whatsapp` (method POST). The admin dashboard shows this URL ready to copy.
4. Save the bot's number as a contact and share it in your group: *"Forward anything important here."*

What happens to each message:

* **An incident** ("crash at 3rd & King, 2 cars"): becomes a report with a pin, and neighbors are alerted.
* **Lost or found** ("found a set of Honda keys outside Safeway"): becomes a lost/found post with a search circle, and matching owners are alerted.
* **Anything else** (chit-chat, questions): nothing is posted.

If the sender's phone number matches a member, the post is theirs and uses their ZIP. Otherwise it's posted as "WhatsApp Community" and they're invited to sign up. Incoming requests are checked against Twilio's signature whenever `TWILIO_AUTH_TOKEN` is set.

You can try the AI without Twilio: paste any message into **Admin → WhatsApp AI → Try it**.

## Who gets notified, and when

| Event | Recipients |
|---|---|
| New incident report | Members in that ZIP (not the reporter) |
| New **lost** item | Members in that ZIP. The owner is also told if matching *found* items already exist. |
| New **found** item | Owners of open *lost* posts with the same ZIP and category |
| "I found this" / "This is mine" | The person who made the post, along with the responder's contact details |
| Admin update | All members, or only the chosen ZIP |
| Local news incident | Members in every ZIP of that city |
| News says an incident is over / report marked resolved | Members in that ZIP ("Resolved: …") |
| WhatsApp message turned into a post | Same as a report or lost/found post made on the website |

Sending runs on a background thread, so a slow SMS or email provider never slows down the page.

## Admin access

Admins are listed in `ADMINS` in `app.py`, each with a TOTP secret. Run `python authenticator.py` to generate a QR code for each admin. Scan it with Google Authenticator, Authy or a similar app, then sign in at `/admin` with your username and the 6-digit code.

> ⚠️ The admin secrets and the default `SECRET_KEY` are committed to the repo. Before deploying, generate new secrets and load them from environment variables.

## Project structure

```
app.py               Flask app: models, routes, notification fan-out, news + WhatsApp pipelines
ai.py                Claude Haiku classification of messages/news (keyword fallback)
news.py              Google News RSS, outlet RSS and NewsAPI fetchers
geo.py               Nominatim geocoding (ZIP -> city, place -> coordinates)
notifications.py     Email / SMS / WhatsApp senders (Flask-Mail + Twilio REST API)
static/map.js        Map overlay: category markers, circles, popups, layer toggles
authenticator.py     Generates admin TOTP QR codes
templates/           Jinja templates (base.html is the shared layout)
static/style.css     All styles
static/uploads/      Uploaded photos
```

## Main routes

| Route | Page |
|---|---|
| `/` | Landing page |
| `/reports`, `/report/new` | Live map overlay, and the new-report form |
| `/api/map` | JSON of everything on the map |
| `/webhooks/whatsapp` | Twilio WhatsApp webhook (AI bot) |
| `/lost-found`, `/lost-found/new`, `/lost-found/<id>` | Lost & Found |
| `/updates` | Community announcements |
| `/notifications` | In-app notification inbox |
| `/account` | Profile and alert channel settings |
| `/signup`, `/login`, `/logout` | Member accounts (email-only login) |
| `/admin` | Admin dashboard |
