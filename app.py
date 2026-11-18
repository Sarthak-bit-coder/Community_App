from flask import Flask, render_template, request, jsonify, session, redirect, current_app, url_for
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
import pyotp
import re
import os

app = Flask(__name__)
app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME='your_email@gmail.com',
    MAIL_PASSWORD='your_email_password',
)
mail = Mail(app)

app.secret_key = 'supersecretkey'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///reports.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

    def __repr__(self):
        return f'<User {self.email}>'

@app.context_processor
def inject_user():
    email = session.get('user_email')
    user = User.query.filter_by(email=email).first() if email else None
    return {"current_user": user, "is_admin": bool(session.get('admin'))}

@app.route('/')
def home():
    total = Report.query.count()
    resolved = Report.query.filter_by(resolved=True).count()
    recent = Report.query.order_by(Report.id.desc()).limit(3).all()
    return render_template('home.html', total=total, resolved=resolved,
                           open_count=total - resolved, recent=recent)

@app.route('/reports')
def reports_page():
    return render_template('reports.html')

@app.route('/report/new')
def new_report_page():
    return render_template('new_report.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html', admin=session.get('admin'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user:
            session['user_email'] = user.email
            session['email'] = user.email
            return redirect(url_for('reports_page'))
        error = "We couldn't find an account with that email."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('email', None)
    return redirect(url_for('home'))

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
    if request.content_type.startswith('multipart/form-data'):
        data = request.form.to_dict()
        file = request.files.get('image')
    else:
        return jsonify({"error": "Content-Type must be multipart/form-data"}), 400

    required_fields = ['type', 'description', 'zip', 'email', 'phone']
    if not all(data.get(k) for k in required_fields):
        return jsonify({"error": "Missing required fields"}), 400
    if not EMAIL_REGEX.match(data['email']):
        return jsonify({"error": "Invalid email"}), 400

    session['email'] = data['email']
    image_url = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        image_url = f"/static/uploads/{filename}"

    report = Report(
        type=data['type'],
        description=data['description'],
        zip=data['zip'],
        email=data['email'],
        phone=data['phone'],
        lat=float(data['lat']) if data.get('lat') else None,
        lng=float(data['lng']) if data.get('lng') else None,
        image_url=image_url
    )
    db.session.add(report)
    db.session.commit()

    matching_users = User.query.filter_by(zip=data['zip']).all()
    for user in matching_users:
        try:
            msg = Message(
                subject=f"Alert: {data['type']} in your area!",
                sender="your_email@gmail.com",
                recipients=[user.email],
                body=f"""Hey {user.name},

There is a {data['type']} reported in your area ({data['zip']}).

Details:
{data['description']}

Stay safe,
Your Community Safety Team
"""
            )
            mail.send(msg)
        except Exception as e:
            print(f"[ERROR] Failed to send email to {user.email}: {e}")
    return jsonify({"message": "Report submitted", "id": report.id}), 201

@app.route('/api/resolve/<int:id>', methods=['POST'])
def resolve_report(id):
    report = Report.query.get(id)
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
    report = Report.query.get(id)
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

@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    error = None
    form = {}
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        email = (form.get('email') or '').strip().lower()
        zip_code = (form.get('zip') or '').strip()

        if not name or not email or not zip_code:
            error = "Please fill in every field."
        elif not EMAIL_REGEX.match(email):
            error = "That email doesn't look right."
        elif User.query.filter(db.func.lower(User.email) == email).first():
            error = "An account with that email already exists. Try logging in."
        else:
            new_user = User(name=name, email=email, zip=zip_code)
            db.session.add(new_user)
            db.session.commit()
            session['user_email'] = email
            session['email'] = email
            return redirect(url_for('reports_page'))
    return render_template('signup.html', error=error, form=form)

@app.before_request
def create_tables():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)