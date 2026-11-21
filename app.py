from datetime import datetime, timedelta
from functools import wraps
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid

from markupsafe import escape

from flask import (Flask, render_template, request, jsonify, session, redirect,
                   current_app, url_for, flash, abort, has_request_context)
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import pyotp
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests

try:  # optional: load settings from a .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import ai
import telegram_bot
import geo
import news
import notifications

app = Flask(__name__)
app.config.update(
    MAIL_SERVER=os.environ.get('MAIL_SERVER', 'smtp.gmail.com'),
    MAIL_PORT=int(os.environ.get('MAIL_PORT', 587)),
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.environ.get('MAIL_USERNAME', ''),
    MAIL_PASSWORD=os.environ.get('MAIL_PASSWORD', ''),
    MAIL_DEFAULT_SENDER=os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('MAIL_USERNAME'),
)
mail = Mail(app)

app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///reports.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Behind ngrok (or another proxy) trust its X-Forwarded-Proto/Host so links use the public https address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

Session(app)
CORS(app)
db = SQLAlchemy(app)

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")

# Admin TOTP secrets live in .env as ADMIN_TOTP_<Name>=<base32 secret> (see authenticator.py).
ADMINS = {k[len('ADMIN_TOTP_'):].lower(): v for k, v in os.environ.items() if k.upper().startswith('ADMIN_TOTP_') and v}

ITEM_CATEGORIES = ai.ITEM_CATEGORIES

# Category key -> label shown on reports (keys match ai.CATEGORIES and static/map.js).
CATEGORY_LABELS = {
    "car_crash": "Car crash", "fire": "Fire", "crime": "Theft or break-in",
    "suspicious": "Suspicious activity", "road_closure": "Road closure",
    "hazard": "Road or sidewalk hazard", "outage": "Broken streetlight / outage",
    "missing_person": "Missing person", "noise": "Noise", "other": "Other",
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    """Save an uploaded image under a unique name and return its URL, or None."""
    if not file or not file.filename or not allowed_file(file.filename):
        return None
    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(file.filename)}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return f"/static/uploads/{filename}"


# ---------------------------------------------------------------- models

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    zip = db.Column(db.String(10), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    resolved = db.Column(db.Boolean, default=False)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    image_url = db.Column(db.String(300), nullable=True)
    category = db.Column(db.String(30), nullable=True)        # key from CATEGORY_LABELS
    source = db.Column(db.String(20), default='community')    # community | news | whatsapp
    source_url = db.Column(db.String(500), nullable=True)
    source_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_note = db.Column(db.String(300), nullable=True)


class NewsArticle(db.Model):
    """Every article we've looked at, so each one is only processed once."""
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(800), unique=True, nullable=False)
    title = db.Column(db.String(300), nullable=False)
    source = db.Column(db.String(120), nullable=True)
    zip = db.Column(db.String(10), nullable=True)
    outcome = db.Column(db.String(20), nullable=True)          # incident | resolution | ignored
    report_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    zip = db.Column(db.String(10), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    notify_email = db.Column(db.Boolean, default=True)
    notify_sms = db.Column(db.Boolean, default=False)
    notify_whatsapp = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(256), nullable=True)
    google_sub = db.Column(db.String(64), nullable=True)
    telegram_chat_id = db.Column(db.String(32), nullable=True)
    telegram_link_code = db.Column(db.String(32), nullable=True)
    notify_telegram = db.Column(db.Boolean, default=False)

    @property
    def first_name(self):
        return (self.name or '').split(' ')[0]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password or '')

    def __repr__(self):
        return f'<User {self.email}>'


class LostItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)          # 'lost' or 'found'
    title = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    description = db.Column(db.Text, nullable=False)
    zip = db.Column(db.String(10), nullable=False)
    place = db.Column(db.String(160), nullable=True)          # "near Dolores Park"
    seen_on = db.Column(db.String(40), nullable=True)         # date it was lost/found
    image_url = db.Column(db.String(300), nullable=True)
    lat = db.Column(db.Float, nullable=True)                  # last-seen / found spot
    lng = db.Column(db.Float, nullable=True)
    radius_m = db.Column(db.Integer, default=300)             # how sure the poster is about the spot
    source = db.Column(db.String(20), default='community')    # community | whatsapp
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(10), default='open')         # 'open' or 'closed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User')


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    kind = db.Column(db.String(20), default='info')           # report | lost | found | match | update | contact
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(300), nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(10), default='normal')     # 'normal' or 'urgent'
    zip = db.Column(db.String(10), nullable=True)             # None = everyone
    author = db.Column(db.String(60), nullable=True)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TelegramGroup(db.Model):
    """A Telegram group the bot was added to, and the ZIP code its messages are about."""
    chat_id = db.Column(db.String(32), primary_key=True)
    title = db.Column(db.String(200))
    zip = db.Column(db.String(10))


def ensure_schema():
    """Create tables and add columns introduced after the database was first made.

    The project has no migrations, so new columns are added with ALTER TABLE.
    """
    db.create_all()
    added = {
        "user": [("phone", "VARCHAR(20)"), ("notify_email", "BOOLEAN DEFAULT 1"),
                 ("notify_sms", "BOOLEAN DEFAULT 0"), ("notify_whatsapp", "BOOLEAN DEFAULT 0"),
                 ("password_hash", "VARCHAR(256)"), ("google_sub", "VARCHAR(64)"),
                 ("telegram_chat_id", "VARCHAR(32)"), ("telegram_link_code", "VARCHAR(32)"),
                 ("notify_telegram", "BOOLEAN DEFAULT 0")],
        "report": [("category", "VARCHAR(30)"), ("source", "VARCHAR(20) DEFAULT 'community'"),
                   ("source_url", "VARCHAR(500)"), ("source_name", "VARCHAR(120)"),
                   ("created_at", "DATETIME"), ("resolved_at", "DATETIME"),
                   ("resolved_note", "VARCHAR(300)")],
        "lost_item": [("lat", "FLOAT"), ("lng", "FLOAT"), ("radius_m", "INTEGER DEFAULT 300"),
                      ("source", "VARCHAR(20) DEFAULT 'community'")],
    }
    for table, columns in added.items():
        existing = {row[1] for row in db.session.execute(db.text(f"PRAGMA table_info({table})"))}
        for name, ddl in columns:
            if name not in existing:
                db.session.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    db.session.commit()


with app.app_context():
    ensure_schema()


# ---------------------------------------------------------------- notifications

def notify(users, title, body, link=None, kind='info'):
    """Save an in-app notification for each user and send it on their chosen channels."""
    jobs = []
    base = request.host_url if has_request_context() else os.environ.get('PUBLIC_BASE_URL', '')
    full_link = (base.rstrip("/") + link) if (link and base) else None
    for user in {u.id: u for u in users if u}.values():
        db.session.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link))
        text = body + (f"\n\n{full_link}" if full_link else "")
        if user.notify_email and user.email:
            jobs.append(("email", user.email, title, f"Hi {user.first_name},\n\n{text}\n\n— Neighborhood Watch"))
        phone = notifications.normalize_phone(user.phone)
        if phone and user.notify_sms:
            jobs.append(("sms", phone, title, text))
        if phone and user.notify_whatsapp:
            jobs.append(("whatsapp", phone, title, text))
        if user.telegram_chat_id and user.notify_telegram:
            jobs.append(("telegram", user.telegram_chat_id, title, text))
    db.session.commit()
    notifications.dispatch(app, mail, jobs)


def current_user():
    email = session.get('user_email')
    return User.query.filter_by(email=email).first() if email else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "info")
            return redirect(url_for('login_page', next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    user = current_user()
    unread = Notification.query.filter_by(user_id=user.id, read=False).count() if user else 0
    week_ago = datetime.utcnow() - timedelta(days=7)
    banner_q = Announcement.query.filter(Announcement.active.is_(True),
                                         Announcement.priority == 'urgent',
                                         Announcement.created_at >= week_ago)
    if user and user.zip:
        banner_q = banner_q.filter(db.or_(Announcement.zip.is_(None), Announcement.zip == user.zip))
    else:
        banner_q = banner_q.filter(Announcement.zip.is_(None))
    return {"current_user": user, "is_admin": bool(session.get('admin')),
            "unread_count": unread,
            "banner": banner_q.order_by(Announcement.created_at.desc()).first()}


@app.template_filter('ago')
def ago(dt):
    if not dt:
        return ''
    s = int((datetime.utcnow() - dt).total_seconds())
    if s < 60:
        return 'just now'
    for unit, size in (('day', 86400), ('hour', 3600), ('minute', 60)):
        if s >= size:
            n = s // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"


# ---------------------------------------------------------------- pages

@app.route('/')
def home():
    total = Report.query.count()
    resolved = Report.query.filter_by(resolved=True).count()
    recent = Report.query.order_by(Report.id.desc()).limit(3).all()
    items = LostItem.query.filter_by(status='open').order_by(LostItem.created_at.desc()).limit(3).all()
    reunited = LostItem.query.filter_by(status='closed').count()
    return render_template('home.html', total=total, resolved=resolved,
                           open_count=total - resolved, recent=recent, items=items,
                           reunited=reunited, members=member_count())


def member_count():
    return User.query.filter(User.email != BOT_EMAIL).count()


@app.route('/reports')
def reports_page():
    return render_template('reports.html')


@app.route('/report/new')
def new_report_page():
    return render_template('new_report.html')


@app.route('/admin')
def admin_page():
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(10).all()
    recent_news = NewsArticle.query.filter(NewsArticle.outcome.in_(['incident', 'resolution'])) \
        .order_by(NewsArticle.created_at.desc()).limit(6).all()
    return render_template('admin.html', admin=session.get('admin'), announcements=announcements,
                           channels=notifications.channel_status(app), members=member_count(),
                           ai_engine=ai.engine(), ai_label=ai.ENGINE_LABELS[ai.engine()],
                           news_sources=news.sources_status(),
                           recent_news=recent_news,
                           news_checked=NewsArticle.query.count(),
                           last_poll=LAST_POLL, poll_minutes=float(os.environ.get('NEWS_POLL_MINUTES', 30)),
                           webhook_url=(os.environ.get('PUBLIC_BASE_URL') or request.host_url).rstrip('/') + '/webhooks/whatsapp')


# ---------------------------------------------------------------- auth & account

def safe_next(default):
    nxt = request.args.get('next') or request.form.get('next')
    return nxt if nxt and nxt.startswith('/') and not nxt.startswith('//') else default


def log_in(user):
    admin = session.get('admin')
    session.clear()   # start a fresh session at login (prevents session fixation)
    if admin:
        session['admin'] = admin
    session['user_email'] = user.email
    session['email'] = user.email


def google_configured():
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))


@app.context_processor
def inject_auth():
    return {"google_enabled": google_configured()}


# ---------------------------------------------------------------- login attempt limits
# Failed attempts are counted per email and per IP address (in memory, so a restart clears them).

class AttemptLimiter:
    def __init__(self, limit, window_minutes):
        self.limit, self.window = limit, window_minutes * 60
        self.hits = {}
        self.lock = threading.Lock()

    def _recent(self, key, now):
        hits = [t for t in self.hits.get(key, []) if now - t < self.window]
        self.hits[key] = hits
        return hits

    def blocked(self, *keys):
        """Minutes until the first blocked key frees up, or 0 if none is blocked."""
        now = time.time()
        with self.lock:
            for key in keys:
                hits = self._recent(key, now)
                if len(hits) >= self.limit:
                    return max(1, int((hits[0] + self.window - now) // 60) + 1)
        return 0

    def hit(self, *keys):
        now = time.time()
        with self.lock:
            for key in keys:
                self._recent(key, now).append(now)

    def clear(self, *keys):
        with self.lock:
            for key in keys:
                self.hits.pop(key, None)


login_limiter = AttemptLimiter(limit=5, window_minutes=15)       # wrong passwords per email
ip_limiter = AttemptLimiter(limit=20, window_minutes=15)         # wrong passwords per IP (any email)
admin_limiter = AttemptLimiter(limit=5, window_minutes=15)       # wrong admin codes per username / IP
reset_limiter = AttemptLimiter(limit=3, window_minutes=60)       # reset emails per address


def client_ip():
    return request.remote_addr or 'unknown'


def too_many(minutes):
    return f"Too many attempts. Please wait {minutes} minute{'s' if minutes != 1 else ''} and try again."


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        keys = (f"email:{email}",)
        wait = login_limiter.blocked(*keys) or ip_limiter.blocked(client_ip())
        if wait:
            return render_template('login.html', error=too_many(wait)), 429
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user and user.check_password(password):
            login_limiter.clear(*keys)
            nxt = safe_next(url_for('reports_page'))
            log_in(user)
            return redirect(nxt)
        login_limiter.hit(*keys)
        ip_limiter.hit(client_ip())
        if user and not user.password_hash:
            error = ("This account doesn't have a password yet. Use \"Forgot password\" below to set one, "
                     "or sign in with Google using the same email.")
        else:
            error = "That email and password don't match."
    return render_template('login.html', error=error)


# ---------------------------------------------------------------- forgot password
# The reset link is signed with SECRET_KEY, expires after an hour, and stops working once the
# password changes (it includes a fingerprint of the current password hash).

RESET_MAX_AGE = 3600


def reset_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='password-reset')


def password_fingerprint(user):
    return hashlib.sha256((user.password_hash or 'no-password').encode()).hexdigest()[:16]


def reset_token(user):
    return reset_serializer().dumps({"id": user.id, "pw": password_fingerprint(user)})


def user_from_reset_token(token):
    try:
        data = reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("id")) if isinstance(data, dict) else None
    if not user or not hmac.compare_digest(data.get("pw", ""), password_fingerprint(user)):
        return None
    return user


@app.route('/forgot', methods=['GET', 'POST'])
def forgot_page():
    sent = False
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        key = f"reset:{email}"
        user = User.query.filter(db.func.lower(User.email) == email).first()
        # Same answer whether or not the account exists, so this page can't be used to find members.
        if user and user.email != BOT_EMAIL and not reset_limiter.blocked(key, f"reset-ip:{client_ip()}"):
            reset_limiter.hit(key, f"reset-ip:{client_ip()}")
            link = url_for('reset_page', token=reset_token(user), _external=True)
            body = (f"Hi {user.first_name},\n\nSomeone (hopefully you) asked to reset your Neighborhood Watch "
                    f"password. Choose a new one here:\n\n{link}\n\nThe link works for one hour. If you didn't "
                    f"ask for this, you can ignore this email.\n\n— Neighborhood Watch")
            if not notifications.email_configured(app):
                # No email server yet: print the link so the site owner can pass it on.
                notifications.log(f"[reset] email isn't set up; reset link for {user.email}: {link}")
            notifications.dispatch(app, mail, [("email", user.email, "Reset your password", body)])
        sent = True
    return render_template('forgot.html', sent=sent)


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_page(token):
    user = user_from_reset_token(token)
    if not user:
        flash("That reset link has expired or was already used. Ask for a new one below.", "error")
        return redirect(url_for('forgot_page'))
    error = None
    if request.method == 'POST':
        error = password_error(request.form.get('password') or '', request.form.get('confirm') or '')
        if not error:
            user.set_password(request.form['password'])
            db.session.commit()
            login_limiter.clear(f"email:{user.email.lower()}")
            log_in(user)
            flash("Your password was changed and you're logged in.", "success")
            return redirect(url_for('reports_page'))
    return render_template('reset.html', error=error, email=user.email)


@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('email', None)
    return redirect(url_for('home'))


PASSWORD_MIN = 8


def password_error(password, confirm):
    if len(password) < PASSWORD_MIN:
        return f"Use a password with at least {PASSWORD_MIN} characters."
    if password != confirm:
        return "The two passwords don't match."
    return None


def read_prefs(form):
    """Validate the phone + channel fields shared by signup and account pages."""
    phone_raw = (form.get('phone') or '').strip()
    phone = notifications.normalize_phone(phone_raw) if phone_raw else None
    prefs = {"notify_email": bool(form.get('notify_email')),
             "notify_sms": bool(form.get('notify_sms')),
             "notify_whatsapp": bool(form.get('notify_whatsapp'))}
    if phone_raw and not phone:
        return None, prefs, "That phone number doesn't look right. Include the country code, e.g. +1 415 555 0123."
    if (prefs["notify_sms"] or prefs["notify_whatsapp"]) and not phone:
        return None, prefs, "Add a phone number to get SMS or WhatsApp alerts."
    return phone, prefs, None


@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    error = None
    form = {"notify_email": "on"}
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        email = (form.get('email') or '').strip().lower()
        zip_code = (form.get('zip') or '').strip()
        password = form.get('password') or ''
        phone, prefs, error = read_prefs(form)

        if not error and (not name or not email or not zip_code):
            error = "Please fill in your name, email and ZIP code."
        elif not error and not EMAIL_REGEX.match(email):
            error = "That email doesn't look right."
        elif not error and User.query.filter(db.func.lower(User.email) == email).first():
            error = "An account with that email already exists. Try logging in."
        if not error:
            error = password_error(password, form.get('confirm') or '')
        if not error:
            user = User(name=name, email=email, zip=zip_code, phone=phone, **prefs)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            nxt = safe_next(url_for('home'))
            log_in(user)
            flash("Welcome! You'll get alerts for ZIP " + zip_code + ".", "success")
            return redirect(nxt)
    return render_template('signup.html', error=error, form=form)


@app.route('/account', methods=['GET', 'POST'])
@login_required
def account_page():
    user = current_user()
    error = None
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        zip_code = (request.form.get('zip') or '').strip()
        phone, prefs, error = read_prefs(request.form)
        if not error and (not name or not zip_code):
            error = "Name and ZIP code are required."
        if not error:
            user.name, user.zip, user.phone = name, zip_code, phone
            for k, v in prefs.items():
                setattr(user, k, v)
            if user.telegram_chat_id:
                user.notify_telegram = bool(request.form.get('notify_telegram'))
            db.session.commit()
            flash("Your settings were saved.", "success")
            return redirect(url_for('account_page'))
    return render_template('account.html', user=user, error=error,
                           channels=notifications.channel_status(app),
                           telegram_bot_name=telegram_bot.bot_username() if telegram_bot.configured() else None)


@app.route('/account/password', methods=['POST'])
@login_required
def account_password():
    user = current_user()
    if user.password_hash and not user.check_password(request.form.get('current') or ''):
        flash("Your current password isn't right.", "error")
        return redirect(url_for('account_page'))
    error = password_error(request.form.get('password') or '', request.form.get('confirm') or '')
    if error:
        flash(error, "error")
        return redirect(url_for('account_page'))
    had = bool(user.password_hash)
    user.set_password(request.form['password'])
    db.session.commit()
    flash("Password changed." if had else "Password added. You can now log in with your email too.", "success")
    return redirect(url_for('account_page'))


@app.route('/account/telegram', methods=['POST'])
@login_required
def account_telegram():
    """Connect: send the user to the bot with a one-time code. Disconnect: forget their chat."""
    user = current_user()
    if request.form.get('action') == 'disconnect':
        user.telegram_chat_id, user.notify_telegram, user.telegram_link_code = None, False, None
        db.session.commit()
        flash("Telegram disconnected.", "success")
        return redirect(url_for('account_page'))
    name = telegram_bot.bot_username() if telegram_bot.configured() else None
    if not name:
        flash("The Telegram bot isn't set up on this server yet.", "error")
        return redirect(url_for('account_page'))
    user.telegram_link_code = secrets.token_urlsafe(16)
    db.session.commit()
    return redirect(f"https://t.me/{name}?start={user.telegram_link_code}")


# ---------------------------------------------------------------- Sign in with Google
# Google's standard OAuth 2.0 web flow. Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env.

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def google_redirect_uri():
    return url_for('google_callback', _external=True)


@app.route('/auth/google')
def google_login():
    if not google_configured():
        flash("Sign in with Google isn't set up on this server yet.", "error")
        return redirect(url_for('login_page'))
    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    session['oauth_next'] = safe_next(url_for('reports_page'))
    params = {"client_id": os.environ['GOOGLE_CLIENT_ID'], "redirect_uri": google_redirect_uri(),
              "response_type": "code", "scope": "openid email profile", "state": state,
              "prompt": "select_account"}
    return redirect(requests.Request('GET', GOOGLE_AUTH, params=params).prepare().url)


@app.route('/auth/google/callback')
def google_callback():
    state = session.pop('oauth_state', None)
    nxt = session.pop('oauth_next', None) or url_for('reports_page')
    if not state or not hmac.compare_digest(state, request.args.get('state', '')) or 'code' not in request.args:
        flash("Google sign-in was cancelled or expired. Please try again.", "error")
        return redirect(url_for('login_page'))
    try:
        tok = requests.post(GOOGLE_TOKEN, timeout=15, data={
            "code": request.args['code'], "client_id": os.environ['GOOGLE_CLIENT_ID'],
            "client_secret": os.environ['GOOGLE_CLIENT_SECRET'], "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code"})
        tok.raise_for_status()
        info = requests.get(GOOGLE_USERINFO, timeout=15,
                            headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
        info.raise_for_status()
        info = info.json()
    except Exception as e:
        print(f"[google] sign-in failed: {e}", flush=True)
        flash("Couldn't sign you in with Google. Please try again.", "error")
        return redirect(url_for('login_page'))
    email = (info.get('email') or '').lower()
    if not email or not info.get('email_verified'):
        flash("Your Google account's email isn't verified, so we can't use it to sign in.", "error")
        return redirect(url_for('login_page'))
    user = (User.query.filter_by(google_sub=info['sub']).first()
            or User.query.filter(db.func.lower(User.email) == email).first())
    if user:
        if not user.google_sub:
            user.google_sub = info['sub']
            db.session.commit()
        log_in(user)
        return redirect(nxt)
    # New neighbor: we still need their ZIP code before creating the account.
    session['google_profile'] = {"sub": info['sub'], "email": email,
                                 "name": info.get('name') or email.split('@')[0]}
    session['oauth_next'] = nxt
    return redirect(url_for('google_finish'))


@app.route('/signup/google', methods=['GET', 'POST'])
def google_finish():
    profile = session.get('google_profile')
    if not profile:
        return redirect(url_for('signup_page'))
    error = None
    form = {"notify_email": "on", "name": profile['name']}
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        zip_code = (form.get('zip') or '').strip()
        phone, prefs, error = read_prefs(form)
        if not error and (not name or not zip_code):
            error = "Please fill in your name and ZIP code."
        if not error and User.query.filter(db.func.lower(User.email) == profile['email']).first():
            error = "An account with that email already exists. Try logging in."
        if not error:
            user = User(name=name, email=profile['email'], zip=zip_code, phone=phone,
                        google_sub=profile['sub'], **prefs)
            db.session.add(user)
            db.session.commit()
            nxt = session.get('oauth_next') or url_for('home')
            log_in(user)
            flash("Welcome! You'll get alerts for ZIP " + zip_code + ".", "success")
            return redirect(nxt)
    return render_template('signup_google.html', error=error, form=form, email=profile['email'])


@app.route('/account/test', methods=['POST'])
@login_required
def account_test():
    notify([current_user()], "Test notification",
           "If you can read this, your Neighborhood Watch alerts are working.",
           link=url_for('notifications_page'), kind='info')
    flash("Test notification sent to your enabled channels.", "success")
    return redirect(url_for('account_page'))


# ---------------------------------------------------------------- notifications inbox

@app.route('/notifications')
@login_required
def notifications_page():
    user = current_user()
    items = (Notification.query.filter_by(user_id=user.id)
             .order_by(Notification.created_at.desc()).limit(100).all())
    unread_ids = {n.id for n in items if not n.read}
    Notification.query.filter_by(user_id=user.id, read=False).update({"read": True})
    db.session.commit()
    return render_template('notifications.html', items=items, unread_ids=unread_ids)


# ---------------------------------------------------------------- lost & found

@app.route('/lost-found')
def lost_found_page():
    kind = request.args.get('kind', 'all')
    zip_code = (request.args.get('zip') or '').strip()
    show_closed = request.args.get('closed') == '1'
    q = LostItem.query
    if kind in ('lost', 'found'):
        q = q.filter_by(kind=kind)
    if zip_code:
        q = q.filter_by(zip=zip_code)
    if not show_closed:
        q = q.filter_by(status='open')
    items = q.order_by(LostItem.created_at.desc()).all()
    return render_template('lost_found.html', items=items, kind=kind, zip=zip_code,
                           show_closed=show_closed)


def to_float(v):
    try:
        return float(v) if v not in (None, '') else None
    except ValueError:
        return None


def create_lost_item(user, kind, title, category, description, zip_code, place=None, seen_on=None,
                     image_url=None, lat=None, lng=None, radius_m=300, source='community'):
    """Save a lost/found post and send the matching alerts. Shared by the form, WhatsApp and admin tester."""
    if lat is None or lng is None:
        lat, lng = geo.locate(place or '', zip_code)
    item = LostItem(kind=kind, title=title, category=category, description=description, zip=zip_code,
                    place=place, seen_on=seen_on, image_url=image_url, lat=lat, lng=lng,
                    radius_m=radius_m or 300, source=source, user_id=user.id)
    db.session.add(item)
    db.session.commit()
    link = f"/lost-found/{item.id}"
    where = f" near {place}" if place else ""
    who = "A neighbor on WhatsApp" if user.email == BOT_EMAIL else user.first_name

    if kind == 'lost':
        # Ask neighbors to keep an eye out.
        neighbors = User.query.filter(User.zip == zip_code, User.id != user.id).all()
        notify(neighbors, f"Lost in your area: {title}",
               f"{who} lost {title.lower()}{where} (ZIP {zip_code}). "
               f"If you see it, tap 'I found this' on the post.", link=link, kind='lost')
        # Tell the owner about found items that might already be theirs.
        candidates = LostItem.query.filter_by(kind='found', status='open', zip=zip_code,
                                              category=category).count()
        if candidates:
            notify([user], "Possible matches for your lost item",
                   f"There {'is' if candidates == 1 else 'are'} {candidates} found "
                   f"{category.lower()} item{'s' if candidates != 1 else ''} posted in ZIP {zip_code}. Take a look!",
                   link=f"/lost-found?kind=found&zip={zip_code}", kind='match')
    else:
        # A found item: alert owners of matching lost items.
        owners = [li.user for li in LostItem.query.filter(
            LostItem.kind == 'lost', LostItem.status == 'open', LostItem.zip == zip_code,
            LostItem.category == category, LostItem.user_id != user.id).all()]
        notify(owners, f"Someone found a {category.lower()} near you",
               f"{who} found {title.lower()}{where} (ZIP {zip_code}). Could it be yours?",
               link=link, kind='found')
    return item


@app.route('/lost-found/new', methods=['GET', 'POST'])
@login_required
def lost_found_new():
    user = current_user()
    error = None
    form = {"kind": request.args.get('kind', 'lost'), "zip": user.zip or '', "radius_m": "300"}
    if request.method == 'POST':
        form = request.form
        kind = form.get('kind')
        title = (form.get('title') or '').strip()
        category = form.get('category')
        description = (form.get('description') or '').strip()
        zip_code = (form.get('zip') or '').strip()
        if kind not in ('lost', 'found') or not title or not description or not zip_code \
                or category not in ITEM_CATEGORIES:
            error = "Please fill in every required field."
        else:
            radius = int(form.get('radius_m') or 300)
            item = create_lost_item(
                user, kind, title, category, description, zip_code,
                place=(form.get('place') or '').strip() or None, seen_on=form.get("seen_on") or None,
                image_url=save_upload(request.files.get('image')),
                lat=to_float(form.get('lat')), lng=to_float(form.get('lng')),
                radius_m=radius if radius in (100, 300, 1000) else 300)
            flash("Your post is live. Neighbors have been notified.", "success")
            return redirect(url_for('lost_found_detail', id=item.id))
    return render_template('lost_found_new.html', form=form, error=error, categories=ITEM_CATEGORIES)


@app.route('/lost-found/<int:id>')
def lost_found_detail(id):
    item = db.session.get(LostItem, id) or abort(404)
    return render_template('lost_found_detail.html', item=item)


@app.route('/lost-found/<int:id>/contact', methods=['POST'])
@login_required
def lost_found_contact(id):
    item = db.session.get(LostItem, id) or abort(404)
    user = current_user()
    if item.user_id == user.id or item.status != 'open':
        abort(400)
    message = (request.form.get('message') or '').strip()
    contact = f"{user.name} — {user.email}" + (f", {user.phone}" if user.phone else "")
    if item.kind == 'lost':
        title = f"Good news! Someone may have found your {item.title.lower()}"
    else:
        title = f"Someone says the {item.title.lower()} you found is theirs"
    notify([item.user], title,
           (f'"{message}"\n\n' if message else '') + f"Contact them: {contact}",
           link=url_for('lost_found_detail', id=item.id), kind='contact')
    flash(f"We've sent your message and contact details to {item.user.first_name}.", "success")
    return redirect(url_for('lost_found_detail', id=item.id))


@app.route('/lost-found/<int:id>/close', methods=['POST'])
@login_required
def lost_found_close(id):
    item = db.session.get(LostItem, id) or abort(404)
    if item.user_id != current_user().id and not session.get('admin'):
        abort(403)
    item.status = 'closed'
    db.session.commit()
    flash("Marked as reunited. Thanks for closing the loop!", "success")
    return redirect(url_for('lost_found_detail', id=item.id))


# ---------------------------------------------------------------- announcements

@app.route('/updates')
def updates_page():
    items = Announcement.query.filter_by(active=True).order_by(Announcement.created_at.desc()).all()
    return render_template('updates.html', items=items)


@app.route('/admin/announcements', methods=['POST'])
def admin_announce():
    if not session.get('admin'):
        abort(401)
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    priority = 'urgent' if request.form.get('priority') == 'urgent' else 'normal'
    zip_code = (request.form.get('zip') or '').strip() or None
    if not title or not body:
        flash("An update needs a title and a message.", "error")
        return redirect(url_for('admin_page'))
    a = Announcement(title=title, body=body, priority=priority, zip=zip_code, author=session['admin'])
    db.session.add(a)
    db.session.commit()
    recipients = User.query.filter_by(zip=zip_code).all() if zip_code else User.query.all()
    notify(recipients, ("URGENT: " if priority == 'urgent' else "Update: ") + title, body,
           link=url_for('updates_page'), kind='update')
    flash(f"Update posted and sent to {len(recipients)} member{'s' if len(recipients) != 1 else ''}.", "success")
    return redirect(url_for('admin_page'))


@app.route('/admin/announcements/<int:id>/archive', methods=['POST'])
def admin_archive(id):
    if not session.get('admin'):
        abort(401)
    a = db.session.get(Announcement, id) or abort(404)
    a.active = False
    db.session.commit()
    flash("Update archived.", "success")
    return redirect(url_for('admin_page'))


# ---------------------------------------------------------------- reports API

@app.route('/api/reports', methods=['GET'])
def get_reports():
    zip_code = request.args.get('zip')
    query = Report.query
    if zip_code:
        query = query.filter_by(zip=zip_code)
    reports = query.order_by(Report.resolved.asc()).all()
    is_admin = bool(session.get('admin'))

    return jsonify([{
        "id": r.id,
        "type": r.type,
        "category": category_of(r),
        "description": r.description,
        "zip": r.zip,
        # Reporter contact details are for admins only.
        "email": r.email if is_admin else None,
        "phone": r.phone if is_admin else None,
        "resolved": r.resolved,
        "lat": r.lat,
        "lng": r.lng,
        "imageUrl": r.image_url,
        "source": r.source or 'community',
        "sourceUrl": r.source_url,
    } for r in reports])


def category_of(report):
    """Category key for a report; older reports only have a free-text type."""
    if report.category:
        return report.category
    by_label = {v.lower(): k for k, v in CATEGORY_LABELS.items()}
    return by_label.get((report.type or '').lower()) or ai._keyword_category(
        f"{report.type} {report.description}") or 'other'


def iso(dt):
    return dt.isoformat() + 'Z' if dt else None


def create_report(category, description, zip_code, lat=None, lng=None, title=None, source='community',
                  source_url=None, source_name=None, email='', phone='', image_url=None,
                  exclude_email=None, notify_zips=None):
    """Save a report and alert members in its ZIP (or the given ZIPs)."""
    category = category if category in CATEGORY_LABELS else 'other'
    report = Report(type=(title or CATEGORY_LABELS[category])[:100], category=category,
                    description=description, zip=zip_code, email=email or '', phone=phone or '',
                    lat=lat, lng=lng, image_url=image_url, source=source, source_url=source_url,
                    source_name=source_name, created_at=datetime.utcnow())
    db.session.add(report)
    db.session.commit()
    q = User.query.filter(User.zip.in_(notify_zips or [zip_code]))
    if exclude_email:
        q = q.filter(db.func.lower(User.email) != exclude_email.lower())
    label = {"news": "News alert", "whatsapp": "Neighbor alert"}.get(source, "Alert")
    notify(q.all(), f"{label}: {report.type}",
           f"{report.type} reported in ZIP {zip_code}:\n{description}"
           + (f"\nSource: {source_name}" if source_name else ""),
           link=f"/reports?focus=report-{report.id}", kind='news' if source == 'news' else 'report')
    return report


def resolve(report, note=None, announce=True):
    """Mark a report resolved and, optionally, tell the neighbors it's over."""
    report.resolved = True
    report.resolved_at = datetime.utcnow()
    report.resolved_note = (note or '')[:300] or None
    db.session.commit()
    if announce:
        notify(User.query.filter_by(zip=report.zip).all(), f"Resolved: {report.type}",
               note or f"{report.type} in ZIP {report.zip} has been resolved.",
               link=f"/reports?focus=report-{report.id}", kind='resolved')


@app.route('/api/reports', methods=['POST'])
def post_report():
    if (request.content_type or '').startswith('multipart/form-data'):
        data = request.form.to_dict()
    else:
        return jsonify({"error": "Content-Type must be multipart/form-data"}), 400

    required_fields = ['type', 'description', 'zip', 'email', 'phone']
    if not all(data.get(k) for k in required_fields):
        return jsonify({"error": "Missing required fields"}), 400
    if not EMAIL_REGEX.match(data['email']):
        return jsonify({"error": "Invalid email"}), 400

    session['email'] = data['email']
    category = data['type'] if data['type'] in CATEGORY_LABELS else 'other'
    report = create_report(category, data['description'], data['zip'],
                           lat=to_float(data.get('lat')), lng=to_float(data.get('lng')),
                           email=data['email'], phone=data['phone'],
                           image_url=save_upload(request.files.get('image')),
                           exclude_email=data['email'])
    return jsonify({"message": "Report submitted", "id": report.id}), 201


@app.route('/api/resolve/<int:id>', methods=['POST'])
def resolve_report(id):
    report = db.session.get(Report, id)
    if not report:
        return jsonify({"error": "Not found"}), 404
    if session.get('admin') or (report.email and session.get('email') == report.email):
        resolve(report, f"{report.type} in ZIP {report.zip} was marked resolved.")
        return jsonify({"message": "Report resolved"}), 200
    return jsonify({"error": "Forbidden"}), 403


# ---------------------------------------------------------------- map overlay data

@app.route('/api/zip/<zip_code>')
def zip_lookup(zip_code):
    """Center and bounds of a US ZIP so maps can zoom to it (cached, rate-limited lookups)."""
    if not re.fullmatch(r"\d{5}", zip_code):
        return jsonify({"error": "Enter a 5-digit ZIP code"}), 400
    info = geo.zip_info(zip_code)
    if not info:
        return jsonify({"error": "ZIP code not found"}), 404
    return jsonify(info)


@app.route('/api/map')
def map_data():
    """Everything with a location: reports (community, news, WhatsApp) and lost/found items."""
    show_resolved = request.args.get('resolved') == '1'
    feats = []
    rq = Report.query.filter(Report.lat.isnot(None))
    if not show_resolved:
        rq = rq.filter(db.or_(Report.resolved.is_(False), Report.resolved.is_(None)))
    for r in rq.all():
        feats.append({"id": f"report-{r.id}", "reportId": r.id, "kind": "report",
                      "category": category_of(r), "title": r.type, "description": r.description,
                      "zip": r.zip, "lat": r.lat, "lng": r.lng, "image": r.image_url,
                      "resolved": bool(r.resolved), "resolvedNote": r.resolved_note,
                      "source": r.source or 'community', "sourceUrl": r.source_url,
                      "sourceName": r.source_name, "created": iso(r.created_at)})
    iq = LostItem.query.filter(LostItem.lat.isnot(None))
    if not show_resolved:
        iq = iq.filter_by(status='open')
    for i in iq.all():
        feats.append({"id": f"item-{i.id}", "kind": i.kind,
                      "category": "lost_item" if i.kind == 'lost' else "found_item",
                      "itemCategory": i.category, "title": i.title, "description": i.description,
                      "zip": i.zip, "lat": i.lat, "lng": i.lng, "radius": i.radius_m or 300,
                      "image": i.image_url, "place": i.place, "resolved": i.status == 'closed',
                      "source": i.source or 'community', "url": f"/lost-found/{i.id}",
                      "created": iso(i.created_at)})
    return jsonify(feats)


# ---------------------------------------------------------------- AI: WhatsApp messages -> posts

BOT_EMAIL = "whatsapp-bot@neighborhood.local"


def bot_user():
    """Placeholder owner for lost/found posts sent in by people without an account."""
    u = User.query.filter_by(email=BOT_EMAIL).first()
    if not u:
        # zip is NOT NULL in older databases; '' never matches a real ZIP, so the bot gets no area alerts.
        u = User(name="WhatsApp Community", email=BOT_EMAIL, zip='', notify_email=False)
        db.session.add(u)
        db.session.commit()
    return u


def user_by_phone(raw):
    phone = notifications.normalize_phone(raw)
    if not phone:
        return None
    return next((u for u in User.query.filter(User.phone.isnot(None)).all()
                 if notifications.normalize_phone(u.phone) == phone), None)


WHATSAPP_HINT = " Sign up on the website with this phone number to get notified when someone responds."


def process_message(text, user=None, default_zip=None, media_url=None, source='whatsapp',
                    signup_hint=WHATSAPP_HINT):
    """Let the AI read one community message and create a report / lost / found post.

    Returns (reply_text, link or None, extraction).
    """
    ex = ai.extract_message(text)
    if ex.action == 'none':
        return "Thanks! I didn't spot an incident or a lost/found item in that message, so nothing was posted.", None, ex
    zip_code = ex.zip or (user.zip if user else None) or default_zip
    if not zip_code:
        return "Thanks! Please resend with your 5-digit ZIP code so I know where this is.", None, ex
    lat, lng = geo.locate(ex.location, zip_code, seed=text)
    if ex.action == 'report':
        r = create_report(ex.category, ex.description, zip_code, lat, lng, title=ex.title,
                          source=source, email=user.email if user else '',
                          phone=user.phone if user else '', image_url=media_url,
                          exclude_email=user.email if user else None)
        return f"Thanks! I posted a report: \"{r.type}\" in ZIP {zip_code} and alerted neighbors.", \
            f"/reports?focus=report-{r.id}", ex
    owner = user or bot_user()
    item = create_lost_item(owner, ex.action, ex.title, ex.item_category, ex.description, zip_code,
                            place=ex.location or None, image_url=media_url, lat=lat, lng=lng,
                            source=source)
    reply = f"Thanks! I posted a {ex.action} item: \"{item.title}\" in ZIP {zip_code}."
    if not user:
        reply += signup_hint
    return reply, f"/lost-found/{item.id}", ex


def twilio_signature_ok():
    """Verify the X-Twilio-Signature header (skipped when no auth token is configured)."""
    token = os.environ.get('TWILIO_AUTH_TOKEN')
    if not token:
        return True
    base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    url = (base + request.full_path.rstrip('?')) if base else request.url
    payload = url + ''.join(k + v for k, v in sorted(request.form.items()))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, request.headers.get('X-Twilio-Signature', ''))


@app.route('/webhooks/whatsapp', methods=['POST'])
def whatsapp_webhook():
    """Twilio calls this for every WhatsApp message sent to the bot number."""
    if not twilio_signature_ok():
        abort(403)
    body = (request.form.get('Body') or '').strip()
    sender = (request.form.get('From') or '').replace('whatsapp:', '')
    media = None
    if int(request.form.get('NumMedia') or 0) and (request.form.get('MediaContentType0') or '').startswith('image/'):
        media = request.form.get('MediaUrl0')
    base = (os.environ.get('PUBLIC_BASE_URL') or request.host_url).rstrip('/')
    if not body:
        reply = "Hi! Forward me messages from your neighborhood group and I'll turn incidents and lost/found items into posts."
    elif ai.engine() == 'ollama':
        # A local model can take longer than Twilio's 15-second limit: answer now, follow up when done.
        user = user_by_phone(sender)

        def work():
            with app.app_context():
                try:
                    text, link, _ = process_message(body, db.session.get(User, user.id) if user else None, media_url=media)
                except Exception as e:
                    print(f"[whatsapp] processing failed: {e}", flush=True)
                    text, link = "Sorry, something went wrong reading that message. Please try again.", None
                notifications.dispatch(app, mail, [("whatsapp", notifications.normalize_phone(sender) or sender,
                                                    "Neighborhood Watch", text + (f"\n{base}{link}" if link else ""))])
        threading.Thread(target=work, daemon=True).start()
        reply = "Got it, reading your message now. I'll reply in a moment."
    else:
        reply, link, _ = process_message(body, user_by_phone(sender), media_url=media)
        if link:
            reply += f"\n{base}{link}"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(reply)}</Message></Response>'
    return xml, 200, {"Content-Type": "application/xml"}


# ---------------------------------------------------------------- Telegram bot

TELEGRAM_HELP = ("Hi! I'm the Neighborhood Watch bot.\n\n"
                 "- Send me a message about an incident or a lost/found item and I'll post it for your neighbors.\n"
                 "- Add me to your neighborhood group and I'll do the same for messages there. "
                 "Set the group's area with /zip 94103.\n"
                 "- To get alerts here, open your Account page on the website and click Connect Telegram.\n\n"
                 "/stop pauses alerts, /start resumes them.")
TELEGRAM_HINT = " Connect Telegram on your Account page on the website to get notified when someone responds."


def handle_telegram(update):
    msg = update.get('message') or {}
    chat, sender = msg.get('chat') or {}, msg.get('from') or {}
    text = (msg.get('text') or msg.get('caption') or '').strip()
    if not chat or not text or sender.get('is_bot'):
        return
    chat_id = str(chat['id'])
    is_group = chat.get('type') in ('group', 'supergroup')

    def say(t):
        telegram_bot.send_message(chat_id, t)

    cmd, arg = '', ''
    if text.startswith('/'):
        first, _, arg = text.partition(' ')
        cmd, arg = first.split('@')[0].lower(), arg.strip()
    base = (os.environ.get('PUBLIC_BASE_URL') or 'http://localhost:5000').rstrip('/')

    with app.app_context():
        # In a private chat the chat id is the person's Telegram id, so this finds them in groups too.
        user = User.query.filter_by(telegram_chat_id=str(sender.get('id'))).first()
        if cmd == '/start' and not is_group:
            if arg:
                linked = User.query.filter_by(telegram_link_code=arg).first()
                if not linked:
                    return say("That link has expired. Open your Account page and click Connect Telegram again.")
                User.query.filter(User.telegram_chat_id == chat_id, User.id != linked.id) \
                    .update({"telegram_chat_id": None, "notify_telegram": False})
                linked.telegram_chat_id, linked.notify_telegram, linked.telegram_link_code = chat_id, True, None
                db.session.commit()
                return say(f"Connected, {linked.first_name}! You'll get Neighborhood Watch alerts for "
                           f"ZIP {linked.zip} here. Send /stop to pause them.")
            if user and not user.notify_telegram:
                user.notify_telegram = True
                db.session.commit()
                return say("Alerts are back on.")
            return say(TELEGRAM_HELP)
        if cmd == '/stop' and not is_group:
            if user:
                user.notify_telegram = False
                db.session.commit()
            return say("Alerts paused. Send /start to turn them back on.")
        if cmd == '/zip':
            if not is_group:
                return say("Your alert ZIP is set on your Account page on the website.")
            if not re.fullmatch(r"\d{5}", arg):
                return say("Send the group's 5-digit ZIP code, like /zip 94103.")
            group = db.session.get(TelegramGroup, chat_id) or TelegramGroup(chat_id=chat_id)
            group.title, group.zip = chat.get('title'), arg
            db.session.add(group)
            db.session.commit()
            return say(f"Got it. I'll treat messages in this group as being about ZIP {arg}.")
        if cmd:
            return None if is_group else say(TELEGRAM_HELP)

        group = db.session.get(TelegramGroup, chat_id) if is_group else None
        if not is_group and ai.engine() == 'ollama':
            say("Got it, reading your message now.")
        reply, link, ex = process_message(text, user, default_zip=group.zip if group else None,
                                          source='telegram', signup_hint=TELEGRAM_HINT)
        if is_group and not link:
            # Stay quiet about ordinary chatter; only speak up when something was posted or a ZIP is missing.
            if ex.action != 'none':
                say("I spotted something to post but don't know the area. Set this group's ZIP with /zip 94103.")
            return
        say(reply + (f"\n{base}{link}" if link else ''))


@app.route('/admin/ai-test', methods=['POST'])
def admin_ai_test():
    if not session.get('admin'):
        abort(401)
    text = (request.form.get('message') or '').strip()
    if not text:
        flash("Paste a message to test.", "error")
        return redirect(url_for('admin_page'))
    reply, link, ex = process_message(text, default_zip=(request.form.get('zip') or '').strip() or None)
    engine = ai.ENGINE_LABELS[ai.engine()]
    reply = reply.split(" Sign up on the website")[0]   # that hint is for WhatsApp senders
    flash(f"[{engine}] Detected: {ex.action} / {ex.category}. {reply}", "success" if link else "info")
    return redirect(link or url_for('admin_page'))


# ---------------------------------------------------------------- local news

def poll_news():
    """Fetch news for every member ZIP, create reports for incidents, resolve finished ones."""
    zips = sorted({z for (z,) in db.session.query(User.zip).distinct() if z})
    areas = {}
    for z in zips:
        info = geo.zip_info(z)
        if info and info["city"]:
            areas.setdefault((info["city"], info["state"]), []).append(z)
    stats = {"areas": len(areas), "articles": 0, "incidents": 0, "resolved": 0}
    for (city, state), area_zips in areas.items():
        # Every open report from the last week (neighbor, chat or news), so a news story can resolve any of them.
        week_ago = datetime.utcnow() - timedelta(days=7)
        open_news = Report.query.filter(Report.zip.in_(area_zips), Report.created_at >= week_ago,
                                        db.or_(Report.resolved.is_(False), Report.resolved.is_(None)))             .order_by(Report.created_at.desc()).limit(40).all()
        for a in news.fetch_for_area(city, state)[:25]:
            if NewsArticle.query.filter_by(url=a["url"][:800]).first():
                continue
            stats["articles"] += 1
            c = ai.classify_news(a["title"], a["summary"], f"{city}, {state} (ZIPs {', '.join(area_zips)})",
                                 [{"id": r.id, "title": r.type} for r in open_news], city=city)
            art = NewsArticle(url=a["url"][:800], title=a["title"][:300], source=(a["source"] or "")[:120],
                              zip=area_zips[0], outcome="ignored")
            if c.relevant and c.kind == "incident" and c.duplicate_of_report_id:
                art.outcome, art.report_id = "duplicate", c.duplicate_of_report_id   # same event, another outlet
            elif c.relevant and c.kind == "incident":
                lat, lng = geo.locate(c.location, area_zips[0], seed=a["url"])
                r = create_report(c.category, c.summary, area_zips[0], lat, lng, title=c.title,
                                  source='news', source_url=a["url"][:500], source_name=a["source"],
                                  notify_zips=area_zips)
                open_news.append(r)
                art.outcome, art.report_id = "incident", r.id
                stats["incidents"] += 1
            elif c.kind == "resolution" and c.resolves_report_id:
                r = db.session.get(Report, c.resolves_report_id)
                if r and not r.resolved:
                    resolve(r, f"Resolved, per {a['source']}: {a['title']}")
                    open_news = [o for o in open_news if o.id != r.id]
                    art.outcome, art.report_id = "resolution", r.id
                    stats["resolved"] += 1
            db.session.add(art)
            db.session.commit()
    # News incidents nobody followed up on are cleared automatically.
    cutoff = datetime.utcnow() - timedelta(hours=float(os.environ.get('NEWS_EXPIRE_HOURS', 48)))
    for r in Report.query.filter(Report.source == 'news', Report.created_at < cutoff,
                                 db.or_(Report.resolved.is_(False), Report.resolved.is_(None))).all():
        resolve(r, "Cleared automatically: no news updates for 48 hours.", announce=False)
    return stats


LAST_POLL = {"at": None, "stats": None, "error": None}


def run_news_poll():
    with app.app_context():
        try:
            stats = poll_news()
            LAST_POLL.update(at=datetime.utcnow(), stats=stats, error=None)
            print(f"[news] poll finished: {stats}", flush=True)
        except Exception as e:
            LAST_POLL.update(at=datetime.utcnow(), error=str(e)[:200])
            print(f"[news] poll failed: {e}", flush=True)


def start_news_poller():
    minutes = float(os.environ.get('NEWS_POLL_MINUTES', 30))
    if minutes <= 0:
        return

    def loop():
        time.sleep(30)  # let the server finish starting
        while True:
            run_news_poll()
            time.sleep(minutes * 60)
    threading.Thread(target=loop, daemon=True).start()


@app.route('/admin/news/poll', methods=['POST'])
def admin_news_poll():
    if not session.get('admin'):
        abort(401)
    threading.Thread(target=run_news_poll, daemon=True).start()
    flash("Checking local news in the background. New incidents will appear on the map in a minute or two.", "success")
    return redirect(url_for('admin_page'))


@app.route('/api/reports/<int:id>', methods=['DELETE'])
def delete_report(id):
    if not session.get('admin'):
        return jsonify({"error": "Unauthorized"}), 401
    report = db.session.get(Report, id)
    if not report:
        return jsonify({"error": "Not found"}), 404
    if not report.resolved:
        return jsonify({"error": "Cannot delete unresolved report"}), 403

    if report.image_url:
        filename = report.image_url.replace("/static/uploads/", "")
        image_full_path = os.path.join(current_app.root_path, 'static', 'uploads', filename)
        if os.path.exists(image_full_path):
            os.remove(image_full_path)

    db.session.delete(report)
    db.session.commit()
    return jsonify({"message": "Deleted"}), 200


@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    token = str(data.get("token") or "").strip()
    keys = (f"admin:{username.lower()}", f"admin-ip:{client_ip()}")
    wait = admin_limiter.blocked(*keys)
    if wait:
        return jsonify({"error": too_many(wait)}), 429
    secret = ADMINS.get(username.lower())
    if secret and pyotp.TOTP(secret).verify(token):
        admin_limiter.clear(*keys)
        session['admin'] = username
        return jsonify({"message": f"Logged in as {username}"}), 200
    admin_limiter.hit(*keys)
    return jsonify({"error": "Unauthorized"}), 401


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({"message": "Logged out"}), 200


if __name__ == '__main__':
    # Set APP_DEBUG=0 when the app is reachable from the internet (e.g. through ngrok):
    # debug mode shows code and an interactive console on error pages.
    debug = os.environ.get('APP_DEBUG', '1') != '0'
    # With the debug reloader, only the child process (WERKZEUG_RUN_MAIN=true) serves requests.
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_news_poller()
        telegram_bot.start_polling(handle_telegram)
    app.run(debug=debug)
