# Merging guide

This guide is for combining Neighborhood Watch with another community app into one new repository. It covers what this app does, where each piece lives, what it stores, and what to decide when merging.

Snapshot: everything below is on `main` as of the `v1.0` tag.

## 1. What this app does

| Feature | Main code | Pages / endpoints |
|---|---|---|
| Live map of incidents, news and lost/found items, with category markers and search circles | `static/map.js`, `templates/reports.html` | `/reports`, `/api/map` |
| Incident reports with photo and pin | `app.py` (`create_report`, `resolve`) | `/report/new`, `/api/reports`, `/api/resolve/<id>` |
| Lost & Found with "I found this" / "This is mine" contact | `app.py` (`create_lost_item`) | `/lost-found/...` |
| Local news every 30 min: incidents become reports, follow-ups resolve them | `news.py`, `app.py` (`poll_news`) | `/admin/news/poll` |
| AI that reads chat messages and news (Ollama for free, Claude optional, keyword fallback) | `ai.py` | `/admin/ai-test` |
| Alerts: in-app inbox plus email, SMS, WhatsApp and Telegram | `notifications.py`, `app.py` (`notify`) | `/notifications`, `/account` |
| Telegram bot: alerts, private messages, group chats | `telegram_bot.py`, `app.py` (`handle_telegram`) | none (long polling) |
| WhatsApp bot through Twilio | `app.py` (`whatsapp_webhook`) | `/webhooks/whatsapp` |
| Accounts: email + password, Sign in with Google, forgot password, login limits | `app.py` | `/signup`, `/login`, `/auth/google`, `/forgot`, `/reset/<token>` |
| Admin dashboard (TOTP codes): announcements, AI tester, news, report moderation | `app.py`, `templates/admin.html`, `authenticator.py` | `/admin` |
| Announcements with urgent site-wide banner | `app.py` | `/updates`, `/admin/announcements` |
| ZIP lookup and geocoding (free OpenStreetMap / Nominatim) | `geo.py` | `/api/zip/<zip>` |
| Hosting from a PC through ngrok with a site password, auto-start with Windows | `go_online.ps1`, `start_all.ps1`, `stop_all.ps1` | none |

## 2. Code layout

```
app.py              Flask app: models, every route, notification fan-out, news + chat pipelines (about 1,300 lines)
ai.py               Message / news classification (Claude, Ollama, keyword rules)
news.py             Google News RSS, outlet RSS feeds, NewsAPI
geo.py              Nominatim geocoding with caching and 1 request/second throttle
notifications.py    Email (Flask-Mail), SMS + WhatsApp (Twilio REST), Telegram senders
telegram_bot.py     Telegram Bot API: polling loop and sendMessage
authenticator.py    Creates admin TOTP secrets and QR codes, stored in .env
templates/          Jinja templates; base.html is the shared layout, _prefs/_google are macros
static/             style.css, map.js, img/ (Pexels photos as WebP, logo.svg), uploads/
*.ps1               Windows helper scripts for ngrok and auto-start
```

Everything is in `app.py`, with no Flask Blueprints. When merging, splitting it into Blueprints (`auth`, `reports`, `lost_found`, `admin`, `bots`) is the easiest way to sit beside another app's routes.

**Unused leftovers:** `templates/dashboard.html`, `alembic.ini` and `migrations/` are left over from the original project and nothing uses them. Delete or replace them in the new repo.

## 3. Database

SQLite at `instance/reports.db`, created automatically on first run. There are no migration files. `ensure_schema()` in `app.py` runs `create_all()` and adds any missing columns with `ALTER TABLE`.

| Table | Columns |
|---|---|
| `user` | id, name, email, zip, phone, notify_email, notify_sms, notify_whatsapp, notify_telegram, password_hash, google_sub, telegram_chat_id, telegram_link_code |
| `report` | id, type, description, zip, email, phone, resolved, lat, lng, image_url, category, source (`community` / `news` / `whatsapp` / `telegram`), source_url, source_name, created_at, resolved_at, resolved_note |
| `lost_item` | id, kind (`lost` / `found`), title, category, description, zip, place, seen_on, image_url, lat, lng, radius_m, source, user_id, status, created_at |
| `notification` | id, user_id, kind, title, body, link, read, created_at |
| `announcement` | id, title, body, priority, zip, author, active, created_at |
| `news_article` | id, url, title, source, zip, outcome, report_id, created_at |
| `telegram_group` | chat_id, title, zip |

Merge notes:
* `report` stores the reporter's email and phone instead of a `user_id`. If the other app has users, add `report.user_id`.
* Passwords use Werkzeug hashes (`pbkdf2` / `scrypt`). If the other app hashes differently, keep both checkers during the move, or ask members to use "Forgot password".
* Areas are US ZIP codes (`user.zip`). Alerts are matched on ZIP.
* The database file is not in git. Each person starts with an empty one. To carry real members over, export them from `instance/reports.db`.

## 4. Settings (`.env`)

`.env` is git-ignored and must never be committed. `.env.example` lists every setting with instructions.

| Group | Variables | Needed? |
|---|---|---|
| Core | `SECRET_KEY`, `APP_DEBUG`, `PUBLIC_BASE_URL` | `SECRET_KEY` yes (signs sessions and reset links) |
| Admins | `ADMIN_TOTP_<Name>` | yes, to use `/admin` (`python authenticator.py <Name>`) |
| AI | `AI_ENGINE`, `OLLAMA_URL`, `OLLAMA_MODEL`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | no (keyword rules work with nothing) |
| News | `NEWS_RSS_FEEDS`, `NEWSAPI_KEY`, `NEWS_POLL_MINUTES`, `NEWS_EXPIRE_HOURS` | no (Google News is free and keyless) |
| Email | `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` | for email alerts and password resets |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM`, `TWILIO_WHATSAPP_FROM` | no (paid; SMS/WhatsApp only) |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | no (free) |
| Google sign-in | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | no (free) |
| Public site password | `SITE_USER`, `SITE_PASSWORD` | only for `go_online.ps1` |

Keys are personal: each Google OAuth client, Telegram bot and Gmail app password belongs to whoever made it. In the new repo, decide whose accounts the combined app uses and create fresh keys there. Don't send `.env` files to each other in chat.

## 5. External services

| Service | Cost | Used for |
|---|---|---|
| OpenStreetMap tiles + Nominatim | free, no key, 1 request/second | map, ZIP and address lookup |
| Google News RSS | free, no key | news |
| Ollama (`qwen2.5:3b`) | free, runs locally | AI |
| Telegram Bot API | free | alerts + bot |
| Google OAuth | free | sign-in |
| Gmail SMTP | free | email |
| Anthropic Claude Haiku | pay as you go | optional, better AI |
| Twilio | paid after trial | SMS + WhatsApp |
| ngrok | free plan | public link from a PC |

## 6. Run it from scratch

```
git clone https://github.com/Sarthak-bit-coder/Community_App.git
cd Community_App
python -m venv .venv
.venv\Scripts\activate        (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        (macOS/Linux: cp .env.example .env)
python app.py                 then open http://localhost:5000
```

Tested on a fresh clone of `main` with Python 3.13: it installs, creates the database, serves every page and accepts a signup.

## 7. Merging checklist

1. **Pick the base.** Choose which app's structure the new repo starts from. Bring the other app's features over one area at a time.
2. **Keep history (optional).** In the new repo, add both repos as remotes, then run `git merge --allow-unrelated-histories` for each, with each app in its own folder first. Or copy files fresh and link the old repos in the README.
3. **Users.** Use one `user` table. Merge the columns: login fields, ZIP/location, alert preferences, Telegram/Google ids.
4. **Location model.** This app uses US ZIP codes plus lat/lng. If the other app uses cities, neighborhoods or radius, pick one approach and convert.
5. **Routes.** Look for path clashes (`/login`, `/admin`, `/reports`, `/api/...`) and namespace with Blueprints.
6. **Design.** The tokens are at the top of `static/style.css` (minimal black and white, photo-led, no emojis). Agree on one look.
7. **Secrets.** Create new keys for the combined app, a new `SECRET_KEY`, and new admin TOTP codes.
8. **Hosting.** The ngrok + PC setup is only for now. For a shared app, use a host (Render, Fly.io, Railway) and a hosted database (Postgres) instead of SQLite.
9. **Tests.** There's no test suite in the repo yet. Adding pytest smoke tests for the main pages early makes the merge safer.

## 8. Known gaps

* Reset emails and email alerts need Gmail set up. Until then they are written to `logs/app.log`.
* Login-attempt limits are kept in memory and reset when the app restarts.
* Members can't edit or delete their own reports yet. Only admins can.
* Telegram photos aren't saved; only the caption text is used.
* Each member has one ZIP.
