import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_ROOT = BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///zamestnanci_vozidla.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH_MB", "16")) * 1024 * 1024

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Nejdříve se přihlaste."

ABSENCE_TYPES = {
    "vacation": "Dovolená",
    "doctor": "Lékař",
    "sick": "Nemoc",
    "training": "Školení",
    "comp_time": "Náhradní volno",
    "business_trip": "Služební cesta",
    "obstacle": "Překážka v práci",
    "other": "Ostatní",
}

ABSENCE_COLORS = {
    "vacation": "#198754",
    "doctor": "#0d6efd",
    "sick": "#dc3545",
    "training": "#fd7e14",
    "comp_time": "#6f42c1",
    "business_trip": "#0dcaf0",
    "obstacle": "#ffc107",
    "other": "#6c757d",
}

EMPLOYEE_DOC_TYPES = [
    "Řidičský průkaz",
    "Občanský průkaz",
    "Profesní průkaz",
    "Karta řidiče",
    "Lékařská prohlídka",
    "Školení",
    "Pracovní smlouva",
    "Jiný dokument",
]

VEHICLE_DOC_TYPES = [
    "STK",
    "Emise",
    "Povinné ručení",
    "Havarijní pojištění",
    "Dálniční známka",
    "Tachograf",
    "ADR",
    "Technický průkaz",
    "Jiný dokument",
]


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(200))
    vacation_entitlement = db.Column(db.Float, default=20)
    active = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.Text)
    absences = db.relationship("Absence", backref="employee", cascade="all, delete-orphan", lazy=True)
    documents = db.relationship("EmployeeDocument", backref="employee", cascade="all, delete-orphan", lazy=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Absence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    absence_type = db.Column(db.String(40), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class EmployeeDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    doc_type = db.Column(db.String(100), nullable=False)
    number = db.Column(db.String(100))
    issued_on = db.Column(db.Date)
    valid_until = db.Column(db.Date)
    original_name = db.Column(db.String(255))
    stored_name = db.Column(db.String(255))
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    vin = db.Column(db.String(100))
    year = db.Column(db.Integer)
    odometer = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.Text)
    documents = db.relationship("VehicleDocument", backref="vehicle", cascade="all, delete-orphan", lazy=True)
    services = db.relationship("VehicleService", backref="vehicle", cascade="all, delete-orphan", lazy=True)


class VehicleDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=False)
    doc_type = db.Column(db.String(100), nullable=False)
    number = db.Column(db.String(100))
    issued_on = db.Column(db.Date)
    valid_until = db.Column(db.Date)
    original_name = db.Column(db.String(255))
    stored_name = db.Column(db.String(255))
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class VehicleService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    odometer = db.Column(db.Integer)
    description = db.Column(db.Text, nullable=False)
    next_date = db.Column(db.Date)
    next_odometer = db.Column(db.Integer)
    cost = db.Column(db.Float)
    note = db.Column(db.Text)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def parse_time(value):
    return datetime.strptime(value, "%H:%M").time() if value else None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file, category):
    if not file or not file.filename:
        return None, None
    if not allowed_file(file.filename):
        raise ValueError("Povolené formáty jsou PDF, PNG, JPG, JPEG a WEBP.")
    original = secure_filename(file.filename)
    extension = original.rsplit(".", 1)[1].lower()
    stored = f"{category}/{uuid.uuid4().hex}.{extension}"
    target = UPLOAD_ROOT / stored
    target.parent.mkdir(parents=True, exist_ok=True)
    file.save(target)
    return original, stored


def document_status(valid_until):
    if not valid_until:
        return "neutral", "Bez platnosti"
    days = (valid_until - date.today()).days
    if days < 0:
        return "danger", f"Po platnosti {abs(days)} dní"
    if days <= 7:
        return "danger", f"Končí za {days} dní"
    if days <= 30:
        return "warning", f"Končí za {days} dní"
    return "success", "Platný"


@app.context_processor
def inject_helpers():
    return {
        "absence_types": ABSENCE_TYPES,
        "employee_doc_types": EMPLOYEE_DOC_TYPES,
        "vehicle_doc_types": VEHICLE_DOC_TYPES,
        "document_status": document_status,
        "today": date.today(),
    }


@app.before_request
def require_initial_setup():
    if request.endpoint in {"setup", "static"}:
        return None
    if not User.query.first():
        return redirect(url_for("setup"))
    return None


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if User.query.first():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if len(username) < 3:
            flash("Uživatelské jméno musí mít alespoň 3 znaky.", "danger")
        elif len(password) < 8:
            flash("Heslo musí mít alespoň 8 znaků.", "danger")
        elif password != password2:
            flash("Hesla se neshodují.", "danger")
        else:
            user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("První správce byl vytvořen.", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth.html", setup_mode=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Neplatné přihlašovací údaje.", "danger")
    return render_template("auth.html", setup_mode=False)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    deadline = date.today() + timedelta(days=30)
    employee_docs = EmployeeDocument.query.filter(EmployeeDocument.valid_until.isnot(None), EmployeeDocument.valid_until <= deadline).order_by(EmployeeDocument.valid_until).all()
    vehicle_docs = VehicleDocument.query.filter(VehicleDocument.valid_until.isnot(None), VehicleDocument.valid_until <= deadline).order_by(VehicleDocument.valid_until).all()
    service_dates = VehicleService.query.filter(VehicleService.next_date.isnot(None), VehicleService.next_date <= deadline).order_by(VehicleService.next_date).all()
    mileage_services = VehicleService.query.join(Vehicle).filter(VehicleService.next_odometer.isnot(None), Vehicle.odometer >= VehicleService.next_odometer - 1000).order_by(VehicleService.next_odometer).all()
    upcoming_absences = Absence.query.filter(Absence.end_date >= date.today(), Absence.start_date <= date.today() + timedelta(days=14)).order_by(Absence.start_date).all()
    return render_template(
        "dashboard.html",
        employee_docs=employee_docs,
        vehicle_docs=vehicle_docs,
        service_dates=service_dates,
        mileage_services=mileage_services,
        upcoming_absences=upcoming_absences,
        employee_count=Employee.query.filter_by(active=True).count(),
        vehicle_count=Vehicle.query.filter_by(active=True).count(),
    )


@app.route("/employees")
@login_required
def employees():
    items = Employee.query.order_by(Employee.active.desc(), Employee.last_name, Employee.first_name).all()
    return render_template("employees.html", employees=items)


@app.route("/employees/new", methods=["GET", "POST"])
@app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
@login_required
def employee_form(employee_id=None):
    employee = db.session.get(Employee, employee_id) if employee_id else Employee()
    if employee_id and not employee:
        abort(404)
    if request.method == "POST":
        employee.first_name = request.form.get("first_name", "").strip()
        employee.last_name = request.form.get("last_name", "").strip()
        employee.phone = request.form.get("phone", "").strip()
        employee.email = request.form.get("email", "").strip()
        employee.vacation_entitlement = float(request.form.get("vacation_entitlement") or 0)
        employee.active = request.form.get("active") == "on"
        employee.note = request.form.get("note", "").strip()
        if not employee.first_name or not employee.last_name:
            flash("Jméno a příjmení jsou povinné.", "danger")
        else:
            if not employee_id:
                db.session.add(employee)
            db.session.commit()
            flash("Zaměstnanec byl uložen.", "success")
            return redirect(url_for("employee_detail", employee_id=employee.id))
    return render_template("employee_form.html", employee=employee)


@app.route("/employees/<int:employee_id>")
@login_required
def employee_detail(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    absences = Absence.query.filter_by(employee_id=employee.id).order_by(Absence.start_date.desc()).all()
    documents = EmployeeDocument.query.filter_by(employee_id=employee.id).order_by(EmployeeDocument.valid_until.is_(None), EmployeeDocument.valid_until).all()
    return render_template("employee_detail.html", employee=employee, absences=absences, documents=documents)


@app.route("/employees/<int:employee_id>/absence/new", methods=["GET", "POST"])
@login_required
def absence_new(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    if request.method == "POST":
        start_date = parse_date(request.form.get("start_date"))
        end_date = parse_date(request.form.get("end_date"))
        absence_type = request.form.get("absence_type")
        if not start_date or not end_date or end_date < start_date:
            flash("Zadejte platné datum od–do.", "danger")
        elif absence_type not in ABSENCE_TYPES:
            flash("Neplatný typ události.", "danger")
        else:
            item = Absence(
                employee_id=employee.id,
                absence_type=absence_type,
                start_date=start_date,
                end_date=end_date,
                start_time=parse_time(request.form.get("start_time")),
                end_time=parse_time(request.form.get("end_time")),
                note=request.form.get("note", "").strip(),
            )
            db.session.add(item)
            db.session.commit()
            flash("Událost byla přidána do kalendáře.", "success")
            return redirect(url_for("employee_detail", employee_id=employee.id))
    return render_template("absence_form.html", employee=employee)


@app.post("/absences/<int:absence_id>/delete")
@login_required
def absence_delete(absence_id):
    item = db.get_or_404(Absence, absence_id)
    employee_id = item.employee_id
    db.session.delete(item)
    db.session.commit()
    flash("Událost byla odstraněna.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id))


@app.route("/employees/<int:employee_id>/documents/new", methods=["GET", "POST"])
@login_required
def employee_document_new(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    if request.method == "POST":
        try:
            original, stored = save_upload(request.files.get("file"), f"employees/{employee.id}")
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("document_form.html", owner=employee, owner_type="employee", doc_types=EMPLOYEE_DOC_TYPES)
        doc = EmployeeDocument(
            employee_id=employee.id,
            doc_type=request.form.get("doc_type", "Jiný dokument"),
            number=request.form.get("number", "").strip(),
            issued_on=parse_date(request.form.get("issued_on")),
            valid_until=parse_date(request.form.get("valid_until")),
            original_name=original,
            stored_name=stored,
            note=request.form.get("note", "").strip(),
        )
        db.session.add(doc)
        db.session.commit()
        flash("Dokument zaměstnance byl uložen.", "success")
        return redirect(url_for("employee_detail", employee_id=employee.id))
    return render_template("document_form.html", owner=employee, owner_type="employee", doc_types=EMPLOYEE_DOC_TYPES)


@app.route("/vehicles")
@login_required
def vehicles():
    items = Vehicle.query.order_by(Vehicle.active.desc(), Vehicle.label).all()
    return render_template("vehicles.html", vehicles=items)


@app.route("/vehicles/new", methods=["GET", "POST"])
@app.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@login_required
def vehicle_form(vehicle_id=None):
    vehicle = db.session.get(Vehicle, vehicle_id) if vehicle_id else Vehicle()
    if vehicle_id and not vehicle:
        abort(404)
    if request.method == "POST":
        vehicle.label = request.form.get("label", "").strip()
        vehicle.plate = request.form.get("plate", "").strip().upper()
        vehicle.make = request.form.get("make", "").strip()
        vehicle.model = request.form.get("model", "").strip()
        vehicle.vin = request.form.get("vin", "").strip().upper()
        vehicle.year = int(request.form.get("year")) if request.form.get("year") else None
        vehicle.odometer = int(request.form.get("odometer") or 0)
        vehicle.active = request.form.get("active") == "on"
        vehicle.note = request.form.get("note", "").strip()
        if not vehicle.label or not vehicle.plate:
            flash("Označení a SPZ jsou povinné.", "danger")
        else:
            try:
                if not vehicle_id:
                    db.session.add(vehicle)
                db.session.commit()
                flash("Vozidlo bylo uloženo.", "success")
                return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
            except Exception:
                db.session.rollback()
                flash("SPZ už je pravděpodobně v evidenci.", "danger")
    return render_template("vehicle_form.html", vehicle=vehicle)


@app.route("/vehicles/<int:vehicle_id>")
@login_required
def vehicle_detail(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    documents = VehicleDocument.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleDocument.valid_until.is_(None), VehicleDocument.valid_until).all()
    services = VehicleService.query.filter_by(vehicle_id=vehicle.id).order_by(VehicleService.service_date.desc()).all()
    return render_template("vehicle_detail.html", vehicle=vehicle, documents=documents, services=services)


@app.route("/vehicles/<int:vehicle_id>/documents/new", methods=["GET", "POST"])
@login_required
def vehicle_document_new(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if request.method == "POST":
        try:
            original, stored = save_upload(request.files.get("file"), f"vehicles/{vehicle.id}")
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("document_form.html", owner=vehicle, owner_type="vehicle", doc_types=VEHICLE_DOC_TYPES)
        doc = VehicleDocument(
            vehicle_id=vehicle.id,
            doc_type=request.form.get("doc_type", "Jiný dokument"),
            number=request.form.get("number", "").strip(),
            issued_on=parse_date(request.form.get("issued_on")),
            valid_until=parse_date(request.form.get("valid_until")),
            original_name=original,
            stored_name=stored,
            note=request.form.get("note", "").strip(),
        )
        db.session.add(doc)
        db.session.commit()
        flash("Dokument vozidla byl uložen.", "success")
        return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
    return render_template("document_form.html", owner=vehicle, owner_type="vehicle", doc_types=VEHICLE_DOC_TYPES)


@app.route("/vehicles/<int:vehicle_id>/service/new", methods=["GET", "POST"])
@login_required
def vehicle_service_new(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if request.method == "POST":
        service_date = parse_date(request.form.get("service_date"))
        description = request.form.get("description", "").strip()
        if not service_date or not description:
            flash("Datum a popis servisu jsou povinné.", "danger")
        else:
            item = VehicleService(
                vehicle_id=vehicle.id,
                service_date=service_date,
                odometer=int(request.form.get("odometer")) if request.form.get("odometer") else None,
                description=description,
                next_date=parse_date(request.form.get("next_date")),
                next_odometer=int(request.form.get("next_odometer")) if request.form.get("next_odometer") else None,
                cost=float(request.form.get("cost").replace(",", ".")) if request.form.get("cost") else None,
                note=request.form.get("note", "").strip(),
            )
            if item.odometer and item.odometer > (vehicle.odometer or 0):
                vehicle.odometer = item.odometer
            db.session.add(item)
            db.session.commit()
            flash("Servisní záznam byl uložen.", "success")
            return redirect(url_for("vehicle_detail", vehicle_id=vehicle.id))
    return render_template("service_form.html", vehicle=vehicle)


@app.post("/employee-documents/<int:document_id>/delete")
@login_required
def employee_document_delete(document_id):
    doc = db.get_or_404(EmployeeDocument, document_id)
    employee_id = doc.employee_id
    delete_stored_file(doc.stored_name)
    db.session.delete(doc)
    db.session.commit()
    flash("Dokument byl odstraněn.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id))


@app.post("/vehicle-documents/<int:document_id>/delete")
@login_required
def vehicle_document_delete(document_id):
    doc = db.get_or_404(VehicleDocument, document_id)
    vehicle_id = doc.vehicle_id
    delete_stored_file(doc.stored_name)
    db.session.delete(doc)
    db.session.commit()
    flash("Dokument byl odstraněn.", "success")
    return redirect(url_for("vehicle_detail", vehicle_id=vehicle_id))


def delete_stored_file(stored_name):
    if not stored_name:
        return
    path = (UPLOAD_ROOT / stored_name).resolve()
    root = UPLOAD_ROOT.resolve()
    if root in path.parents and path.exists():
        path.unlink()


@app.route("/files/<path:stored_name>")
@login_required
def protected_file(stored_name):
    path = (UPLOAD_ROOT / stored_name).resolve()
    if UPLOAD_ROOT.resolve() not in path.parents or not path.exists():
        abort(404)
    return send_from_directory(UPLOAD_ROOT, stored_name, as_attachment=False)


@app.route("/calendar")
@login_required
def calendar_view():
    employees = Employee.query.filter_by(active=True).order_by(Employee.last_name, Employee.first_name).all()
    return render_template("calendar.html", employees=employees)


@app.route("/api/absences")
@login_required
def api_absences():
    start = parse_date((request.args.get("start") or "")[:10])
    end = parse_date((request.args.get("end") or "")[:10])
    query = Absence.query
    employee_id = request.args.get("employee_id", type=int)
    if employee_id:
        query = query.filter_by(employee_id=employee_id)
    if start and end:
        query = query.filter(Absence.start_date < end, Absence.end_date >= start)
    events = []
    for item in query.all():
        timed = item.start_time is not None
        if timed:
            start_value = datetime.combine(item.start_date, item.start_time).isoformat()
            event_end_time = item.end_time or item.start_time
            end_value = datetime.combine(item.end_date, event_end_time).isoformat()
        else:
            start_value = item.start_date.isoformat()
            end_value = (item.end_date + timedelta(days=1)).isoformat()
        events.append({
            "id": item.id,
            "title": f"{item.employee.full_name} – {ABSENCE_TYPES.get(item.absence_type, item.absence_type)}",
            "start": start_value,
            "end": end_value,
            "allDay": not timed,
            "backgroundColor": ABSENCE_COLORS.get(item.absence_type, "#6c757d"),
            "borderColor": ABSENCE_COLORS.get(item.absence_type, "#6c757d"),
            "extendedProps": {"note": item.note or ""},
        })
    return jsonify(events)


with app.app_context():
    db.create_all()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
