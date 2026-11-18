from datetime import datetime, timedelta
from functools import wraps
import os
import re
import uuid

from flask import (Flask, render_template, request, jsonify, session, redirect,
                   current_app, url_for, flash, abort, has_request_context)
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from werkzeug.utils import secure_filename
import pyotp

try:  # optional: load settings from a .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

Session(app)
CORS(app)
db = SQLAlchemy(app)

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")

ADMINS = {
    "Sarthak": "JBSWY3DPEHPK3PXP",
    "Ayesha": "KZXW6YTBMFWWKZLU",
    "Shubham": "MFRGGZDFMZTWQ2LK",
    "Anav": "ONSWG4TFOQYTEMY="
}

ITEM_CATEGORIES = ["Pet", "Keys", "Wallet / purse", "Phone", "Bag", "Bike / scooter",
                   "Jewelry", "Documents / ID", "Clothing", "Other"]


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


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    zip = db.Column(db.String(10), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    notify_email = db.Column(db.Boolean, default=True)
    notify_sms = db.Column(db.Boolean, default=False)
    notify_whatsapp = db.Column(db.Boolean, default=False)

    @property
    def first_name(self):
        return (self.name or '').split(' ')[0]

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


def ensure_schema():
    """Create tables and add columns introduced after the database was first made.

    The project has no migrations, so new User columns are added with ALTER TABLE.
    """
    db.create_all()
    cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(user)"))}
    for name, ddl in [("phone", "VARCHAR(20)"),
                      ("notify_email", "BOOLEAN DEFAULT 1"),
                      ("notify_sms", "BOOLEAN DEFAULT 0"),
                      ("notify_whatsapp", "BOOLEAN DEFAULT 0")]:
        if name not in cols:
            db.session.execute(db.text(f"ALTER TABLE user ADD COLUMN {name} {ddl}"))
    db.session.commit()


with app.app_context():
    ensure_schema()


# ---------------------------------------------------------------- notifications

def notify(users, title, body, link=None, kind='info'):
    """Save an in-app notification for each user and send it on their chosen channels."""
    jobs = []
    full_link = (request.host_url.rstrip("/") + link) if (link and has_request_context()) else None
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
                           reunited=reunited, members=User.query.count())


@app.route('/reports')
def reports_page():
    return render_template('reports.html')


@app.route('/report/new')
def new_report_page():
    return render_template('new_report.html')


@app.route('/admin')
def admin_page():
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(10).all()
    return render_template('admin.html', admin=session.get('admin'), announcements=announcements,
                           channels=notifications.channel_status(app), members=User.query.count())


# ---------------------------------------------------------------- auth & account

def safe_next(default):
    nxt = request.args.get('next') or request.form.get('next')
    return nxt if nxt and nxt.startswith('/') and not nxt.startswith('//') else default


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user:
            session['user_email'] = user.email
            session['email'] = user.email
            return redirect(safe_next(url_for('reports_page')))
        error = "We couldn't find an account with that email."
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('email', None)
    return redirect(url_for('home'))


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
        phone, prefs, error = read_prefs(form)

        if not error and (not name or not email or not zip_code):
            error = "Please fill in your name, email and ZIP code."
        elif not error and not EMAIL_REGEX.match(email):
            error = "That email doesn't look right."
        elif not error and User.query.filter(db.func.lower(User.email) == email).first():
            error = "An account with that email already exists. Try logging in."
        if not error:
            db.session.add(User(name=name, email=email, zip=zip_code, phone=phone, **prefs))
            db.session.commit()
            session['user_email'] = email
            session['email'] = email
            flash("Welcome! You'll get alerts for ZIP " + zip_code + ".", "success")
            return redirect(safe_next(url_for('home')))
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
            db.session.commit()
            flash("Your settings were saved.", "success")
            return redirect(url_for('account_page'))
    return render_template('account.html', user=user, error=error,
                           channels=notifications.channel_status(app))


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


@app.route('/lost-found/new', methods=['GET', 'POST'])
@login_required
def lost_found_new():
    user = current_user()
    error = None
    form = {"kind": request.args.get('kind', 'lost'), "zip": user.zip or ''}
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
            item = LostItem(kind=kind, title=title, category=category, description=description,
                            zip=zip_code, place=(form.get('place') or '').strip() or None,
                            seen_on=form.get("seen_on") or None, image_url=save_upload(request.files.get('image')),
                            user_id=user.id)
            db.session.add(item)
            db.session.commit()
            link = url_for('lost_found_detail', id=item.id)
            where = f" near {item.place}" if item.place else ""

            if kind == 'lost':
                # Ask neighbors to keep an eye out.
                neighbors = User.query.filter(User.zip == zip_code, User.id != user.id).all()
                notify(neighbors, f"Lost in your area: {title}",
                       f"{user.first_name} lost {title.lower()}{where} (ZIP {zip_code}). "
                       f"If you see it, tap 'I found this' on the post.", link=link, kind='lost')
                # Tell the owner about found items that might already be theirs.
                candidates = LostItem.query.filter_by(kind='found', status='open', zip=zip_code,
                                                      category=category).count()
                if candidates:
                    notify([user], "Possible matches for your lost item",
                           f"There {'is' if candidates == 1 else 'are'} {candidates} found "
                           f"{category.lower()} item{'s' if candidates != 1 else ''} posted in ZIP {zip_code}. Take a look!",
                           link=url_for('lost_found_page', kind='found', zip=zip_code), kind='match')
            else:
                # A found item: alert owners of matching lost items.
                owners = [li.user for li in LostItem.query.filter(
                    LostItem.kind == 'lost', LostItem.status == 'open', LostItem.zip == zip_code,
                    LostItem.category == category, LostItem.user_id != user.id).all()]
                notify(owners, f"Someone found a {category.lower()} near you",
                       f"{user.first_name} found {title.lower()}{where} (ZIP {zip_code}). Could it be yours?",
                       link=link, kind='found')
            flash("Your post is live. Neighbors have been notified.", "success")
            return redirect(link)
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

    return jsonify([{
        "id": r.id,
        "type": r.type,
        "description": r.description,
        "zip": r.zip,
        "email": r.email,
        "phone": r.phone,
        "resolved": r.resolved,
        "lat": r.lat,
        "lng": r.lng,
        "imageUrl": r.image_url
    } for r in reports])


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
    report = Report(
        type=data['type'],
        description=data['description'],
        zip=data['zip'],
        email=data['email'],
        phone=data['phone'],
        lat=float(data['lat']) if data.get('lat') else None,
        lng=float(data['lng']) if data.get('lng') else None,
        image_url=save_upload(request.files.get('image'))
    )
    db.session.add(report)
    db.session.commit()

    neighbors = User.query.filter(User.zip == data['zip'],
                                  db.func.lower(User.email) != data['email'].lower()).all()
    notify(neighbors, f"Alert: {data['type']} in your area",
           f"{data['type']} reported in ZIP {data['zip']}:\n{data['description']}",
           link=url_for('reports_page'), kind='report')
    return jsonify({"message": "Report submitted", "id": report.id}), 201


@app.route('/api/resolve/<int:id>', methods=['POST'])
def resolve_report(id):
    report = db.session.get(Report, id)
    if not report:
        return jsonify({"error": "Not found"}), 404
    if session.get('admin') or session.get('email') == report.email:
        report.resolved = True
        db.session.commit()
        return jsonify({"message": "Report resolved"}), 200
    return jsonify({"error": "Forbidden"}), 403


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
    data = request.json
    username = data.get("username")
    token = data.get("token")
    secret = ADMINS.get(username)
    if secret and pyotp.TOTP(secret).verify(token):
        session['admin'] = username
        return jsonify({"message": f"Logged in as {username}"}), 200
    return jsonify({"error": "Unauthorized"}), 401


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({"message": "Logged out"}), 200


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get("email")
    user = User.query.filter_by(email=email).first()
    if user:
        session["user_email"] = email
        return jsonify({"message": "Logged in"}), 200
    return jsonify({"error": "User not found"}), 404


if __name__ == '__main__':
    app.run(debug=True)
