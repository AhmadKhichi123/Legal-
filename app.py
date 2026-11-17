"""
Legal Client Management System
------------------------------
A small web application for lawyers: it stores clients, their cases, hearing
dates and case documents in one place, and makes them searchable. Any number
of lawyers can register; each one sees only their own records.

Run with:   python app.py
Then open:  https://127.0.0.1:5000

Options:    python app.py --network    (let other computers on the same Wi-Fi connect)
            python app.py --no-https   (plain HTTP, for testing only)
"""

import argparse
import ipaddress
import os
import re
import secrets
import socket
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------- settings --

if getattr(sys, "frozen", False):
    # Running as the packaged .exe: keep the data folder next to the .exe
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# LEGAL_CMS_DATA lets the tests use a throw-away folder instead of the real data
DATA_DIR = os.environ.get("LEGAL_CMS_DATA") or os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "documents")
DB_PATH = os.path.join(DATA_DIR, "legal.db")
KEY_PATH = os.path.join(DATA_DIR, "secret.key")

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt", "jpg", "jpeg", "png", "xlsx"}
CASE_STATUSES = ["Ongoing", "Pending", "Completed", "Closed"]
ACTIVE_STATUSES = ("Ongoing", "Pending")        # cases still being worked on
FINISHED_STATUSES = ("Completed", "Closed")     # case history
REMINDER_DAYS = 7                               # hearings this close are flagged
CNIC_PATTERN = re.compile(r"^\d{5}-\d{7}-\d$")   # Pakistani CNIC: 12345-1234567-1
CASE_TYPES = ["Civil", "Criminal", "Family", "Property", "Corporate", "Other"]

os.makedirs(UPLOAD_DIR, exist_ok=True)


def load_secret_key():
    """Create a random session key on first run and reuse it afterwards."""
    if not os.path.exists(KEY_PATH):
        with open(KEY_PATH, "w") as f:
            f.write(secrets.token_hex(32))
    with open(KEY_PATH) as f:
        return f.read().strip()


app = Flask(__name__)
app.config["SECRET_KEY"] = load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB per upload

# ---------------------------------------------------------------- database --

# Indexes on the columns used to join and filter, so lookups stay fast as the
# number of records grows (created after any upgrade, see init_db)
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_clients_user   ON clients(user_id);
CREATE INDEX IF NOT EXISTS idx_cases_client   ON cases(client_id);
CREATE INDEX IF NOT EXISTS idx_hearings_case  ON hearings(case_id, hearing_date);
CREATE INDEX IF NOT EXISTS idx_hearings_date  ON hearings(hearing_date);
CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- the lawyer who owns this record
    name       TEXT NOT NULL,
    cnic       TEXT,
    phone      TEXT,
    email      TEXT,
    address    TEXT,
    notes      TEXT,
    created_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    case_number TEXT,
    court       TEXT,
    case_type   TEXT,
    status      TEXT NOT NULL DEFAULT 'Pending',
    description TEXT,
    opened_on   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hearings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    hearing_date TEXT NOT NULL,
    purpose      TEXT,
    outcome      TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    filename    TEXT NOT NULL,      -- original name shown to the user
    stored_name TEXT NOT NULL,      -- unique name used on disk
    uploaded_on TEXT NOT NULL
);
"""


def get_db():
    """One database connection per request, stored on Flask's `g` object."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row          # rows behave like dictionaries
        g.db.execute("PRAGMA foreign_keys = ON")  # enforce ON DELETE CASCADE
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)
        # Upgrade a database created by the earlier single-user version
        columns = [row[1] for row in db.execute("PRAGMA table_info(clients)")]
        if "user_id" not in columns:
            db.execute("ALTER TABLE clients ADD COLUMN user_id INTEGER REFERENCES users(id)")
            db.execute("UPDATE clients SET user_id = (SELECT MIN(id) FROM users)")
        if "cnic" not in columns:
            db.execute("ALTER TABLE clients ADD COLUMN cnic TEXT")
        # Statuses used by earlier versions -> the four statuses used now
        for old, new in [("Open", "Pending"), ("In Progress", "Ongoing"),
                         ("Adjourned", "Pending")]:
            db.execute("UPDATE cases SET status = ? WHERE status = ?", (new, old))
        db.executescript(INDEXES)


# Each lawyer may only see their own records. Every client row stores its
# owner's user_id; cases, hearings and documents are reached through the
# client, so these functions check ownership before returning anything.
# A record owned by someone else looks exactly like one that does not exist.

def uid():
    return session["user_id"]


def fetch_owned(sql, row_id):
    row = get_db().execute(sql, (row_id, uid())).fetchone()
    if row is None:
        abort(404)
    return row


def get_client(client_id):
    return fetch_owned("SELECT * FROM clients WHERE id = ? AND user_id = ?", client_id)


def get_case(case_id):
    return fetch_owned(
        """SELECT c.* FROM cases c JOIN clients cl ON cl.id = c.client_id
           WHERE c.id = ? AND cl.user_id = ?""", case_id)


def get_hearing(hearing_id):
    return fetch_owned(
        """SELECT h.* FROM hearings h
           JOIN cases c ON c.id = h.case_id JOIN clients cl ON cl.id = c.client_id
           WHERE h.id = ? AND cl.user_id = ?""", hearing_id)


def get_document(doc_id):
    return fetch_owned(
        """SELECT d.* FROM documents d
           JOIN cases c ON c.id = d.case_id JOIN clients cl ON cl.id = c.client_id
           WHERE d.id = ? AND cl.user_id = ?""", doc_id)


# ---------------------------------------------------------------- security --

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def csrf_protect():
    """Every form carries a hidden token so other sites cannot submit forms."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not secrets.compare_digest(sent, session["csrf_token"]):
            abort(400)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def remove_files(stored_names):
    for name in stored_names:
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.exists(path):
            os.remove(path)


# ------------------------------------------------------------- install app --

@app.route("/sw.js")
def service_worker():
    """Served from the top level so it can cover the whole site."""
    return send_from_directory(app.static_folder, "sw.js",
                               mimetype="application/javascript")


# ------------------------------------------------------------ login/logout --

@app.route("/register", methods=["GET", "POST"])
def register():
    """Any lawyer can create an account; each account has its own records."""
    if request.method == "POST":
        db = get_db()
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or len(password) < 8:
            flash("Enter a username and a password of at least 8 characters.", "error")
        elif password != request.form["confirm"]:
            flash("The two passwords do not match.", "error")
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            flash("That username is already taken.", "error")
        else:
            db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                       (username, generate_password_hash(password)))
            db.commit()
            flash("Account created. Please sign in.", "success")
            return redirect(url_for("login"))
    return render_template("login.html", registering=True)


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (uid(),)).fetchone()
        new = request.form["new"]
        if not check_password_hash(user["password_hash"], request.form["current"]):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("The new password must be at least 8 characters.", "error")
        elif new != request.form["confirm"]:
            flash("The two new passwords do not match.", "error")
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(new), uid()))
            db.commit()
            flash("Password changed.", "success")
            return redirect(url_for("dashboard"))
    return render_template("password.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if request.method == "POST":
        user = db.execute("SELECT * FROM users WHERE username = ?",
                          (request.form["username"].strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html", registering=False)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------- dashboard --

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    today = date.today().isoformat()
    my_cases = "FROM cases c JOIN clients cl ON cl.id = c.client_id WHERE cl.user_id = ?"

    def count(sql, *extra):
        return db.execute("SELECT COUNT(*) " + sql, (uid(), *extra)).fetchone()[0]

    stats = {
        "clients": count("FROM clients WHERE user_id = ?"),
        "active": count(my_cases + " AND c.status IN ('Ongoing', 'Pending')"),
        "completed": count(my_cases + " AND c.status IN ('Completed', 'Closed')"),
        "upcoming": count("FROM hearings h JOIN cases c ON c.id = h.case_id "
                          "JOIN clients cl ON cl.id = c.client_id "
                          "WHERE cl.user_id = ? AND h.hearing_date >= ?", today),
    }
    upcoming = upcoming_hearings(today, limit=10)
    # Reminders: every hearing in the next REMINDER_DAYS days (fetched on its
    # own, so a busy week is never cut off by the 10-row limit above)
    last_day = (date.today() + timedelta(days=REMINDER_DAYS)).isoformat()
    reminders = []
    for h in upcoming_hearings(today, last_day):
        days_left = (date.fromisoformat(h["hearing_date"]) - date.today()).days
        reminders.append((h, days_left))
    recent = db.execute(
        """SELECT c.*, cl.name AS client_name
           FROM cases c JOIN clients cl ON cl.id = c.client_id
           WHERE cl.user_id = ?
           ORDER BY c.id DESC LIMIT 5""", (uid(),)).fetchall()
    return render_template("dashboard.html", stats=stats, upcoming=upcoming,
                           reminders=reminders, recent=recent, today=today,
                           reminder_days=REMINDER_DAYS)


def upcoming_hearings(from_date, to_date="9999-12-31", limit=1000):
    """This lawyer's hearings between two dates (inclusive), soonest first."""
    return get_db().execute(
        """SELECT h.*, c.title AS case_title, c.court, c.case_number,
                  cl.name AS client_name
           FROM hearings h
           JOIN cases c    ON c.id = h.case_id
           JOIN clients cl ON cl.id = c.client_id
           WHERE cl.user_id = ? AND h.hearing_date BETWEEN ? AND ?
           ORDER BY h.hearing_date LIMIT ?""",
        (uid(), from_date, to_date, limit)).fetchall()


# ----------------------------------------------------------------- clients --

@app.route("/clients")
@login_required
def clients():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    rows = get_db().execute(
        """SELECT cl.*, COUNT(c.id) AS case_count
           FROM clients cl LEFT JOIN cases c ON c.client_id = cl.id
           WHERE cl.user_id = ?
             AND (cl.name LIKE ? OR cl.phone LIKE ? OR cl.email LIKE ? OR cl.cnic LIKE ?)
           GROUP BY cl.id ORDER BY cl.name""", (uid(), like, like, like, like)).fetchall()
    return render_template("clients.html", clients=rows, q=q)


def client_from_form():
    f = request.form
    return (f["name"].strip(), f.get("cnic", "").strip(), f["phone"].strip(),
            f["email"].strip(), f["address"].strip(), f["notes"].strip())


def client_form_error(values):
    """Return an error message, or None if the client form is valid."""
    name, cnic = values[0], values[1]
    if not name:
        return "Client name is required."
    if cnic and not CNIC_PATTERN.match(cnic):
        return "CNIC must be in the form 12345-1234567-1."
    return None


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def client_new():
    if request.method == "POST":
        values = client_from_form()
        error = client_form_error(values)
        if error:
            flash(error, "error")
        else:
            db = get_db()
            cur = db.execute(
                """INSERT INTO clients (name, cnic, phone, email, address, notes,
                                        created_on, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                values + (date.today().isoformat(), uid()))
            db.commit()
            flash("Client added.", "success")
            return redirect(url_for("client_detail", client_id=cur.lastrowid))
    return render_template("client_form.html", client=request.form)


@app.route("/clients/<int:client_id>")
@login_required
def client_detail(client_id):
    client = get_client(client_id)
    cases_ = get_db().execute(
        "SELECT * FROM cases WHERE client_id = ? ORDER BY opened_on DESC",
        (client_id,)).fetchall()
    return render_template("client_detail.html", client=client, cases=cases_)


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def client_edit(client_id):
    client = get_client(client_id)
    if request.method == "POST":
        values = client_from_form()
        error = client_form_error(values)
        if error:
            flash(error, "error")
            client = request.form | {"id": client_id}   # keep what was typed
        else:
            db = get_db()
            db.execute("""UPDATE clients SET name=?, cnic=?, phone=?, email=?, address=?,
                          notes=? WHERE id=?""", values + (client_id,))
            db.commit()
            flash("Client updated.", "success")
            return redirect(url_for("client_detail", client_id=client_id))
    return render_template("client_form.html", client=client, editing=True)


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def client_delete(client_id):
    get_client(client_id)
    db = get_db()
    files = [r["stored_name"] for r in db.execute(
        """SELECT d.stored_name FROM documents d
           JOIN cases c ON c.id = d.case_id WHERE c.client_id = ?""", (client_id,))]
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))  # cascades to cases etc.
    db.commit()
    remove_files(files)
    flash("Client and all related records deleted.", "success")
    return redirect(url_for("clients"))


# ------------------------------------------------------------------- cases --

@app.route("/cases")
@login_required
def cases():
    """List cases, narrowed by any combination of the filters on the page."""
    args = request.args
    q = args.get("q", "").strip()
    status = args.get("status", "")
    case_type = args.get("type", "")
    hearing_from = valid_date(args.get("from", ""))
    hearing_to = valid_date(args.get("to", ""))

    like = f"%{q}%"
    sql = """SELECT c.*, cl.name AS client_name,
                    (SELECT MIN(hearing_date) FROM hearings
                     WHERE case_id = c.id AND hearing_date >= ?) AS next_hearing
             FROM cases c JOIN clients cl ON cl.id = c.client_id
             WHERE cl.user_id = ?
               AND (c.title LIKE ? OR c.case_number LIKE ? OR cl.name LIKE ?)"""
    params = [date.today().isoformat(), uid(), like, like, like]

    # Each filter that is set adds one condition (all values are parameters)
    if status in CASE_STATUSES:
        sql += " AND c.status = ?"
        params.append(status)
    elif status == "active":
        sql += " AND c.status IN ('Ongoing', 'Pending')"
    elif status == "history":
        sql += " AND c.status IN ('Completed', 'Closed')"
    if case_type in CASE_TYPES:
        sql += " AND c.case_type = ?"
        params.append(case_type)
    if hearing_from or hearing_to:
        sql += """ AND EXISTS (SELECT 1 FROM hearings h WHERE h.case_id = c.id
                               AND h.hearing_date BETWEEN ? AND ?)"""
        params += [hearing_from or "0000-01-01", hearing_to or "9999-12-31"]

    rows = get_db().execute(sql + " ORDER BY c.opened_on DESC", params).fetchall()
    return render_template("cases.html", cases=rows, q=q, status=status,
                           case_type=case_type, hearing_from=hearing_from,
                           hearing_to=hearing_to, statuses=CASE_STATUSES,
                           types=CASE_TYPES)


def valid_date(text):
    """Return the text if it is a real YYYY-MM-DD date, otherwise ''."""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def case_from_form():
    f = request.form
    status = f["status"] if f["status"] in CASE_STATUSES else "Pending"
    return (f["client_id"], f["title"].strip(), f["case_number"].strip(),
            f["court"].strip(), f["case_type"], status, f["description"].strip(),
            f["opened_on"] or date.today().isoformat())


def render_case_form(case, editing=False):
    all_clients = get_db().execute(
        "SELECT id, name FROM clients WHERE user_id = ? ORDER BY name", (uid(),)).fetchall()
    return render_template("case_form.html", case=case, editing=editing,
                           clients=all_clients, statuses=CASE_STATUSES,
                           types=CASE_TYPES, today=date.today().isoformat())


@app.route("/cases/new", methods=["GET", "POST"])
@login_required
def case_new():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (uid(),)).fetchone()[0] == 0:
        flash("Add a client first, then open a case for them.", "error")
        return redirect(url_for("client_new"))

    if request.method == "POST":
        values = case_from_form()
        if not values[1]:
            flash("Case title is required.", "error")
        else:
            get_client(values[0])
            cur = db.execute(
                """INSERT INTO cases (client_id, title, case_number, court, case_type,
                                      status, description, opened_on)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", values)
            db.commit()
            flash("Case opened.", "success")
            return redirect(url_for("case_detail", case_id=cur.lastrowid))
        return render_case_form(request.form)
    # Pre-select the client when arriving from a client's page
    return render_case_form({"client_id": request.args.get("client_id", type=int)})


@app.route("/cases/<int:case_id>")
@login_required
def case_detail(case_id):
    db = get_db()
    case = get_case(case_id)
    client = get_client(case["client_id"])
    hearings = db.execute(
        "SELECT * FROM hearings WHERE case_id = ? ORDER BY hearing_date DESC",
        (case_id,)).fetchall()
    documents = db.execute(
        "SELECT * FROM documents WHERE case_id = ? ORDER BY uploaded_on DESC, id DESC",
        (case_id,)).fetchall()
    return render_template("case_detail.html", case=case, client=client,
                           hearings=hearings, documents=documents,
                           today=date.today().isoformat(),
                           extensions=", ".join(sorted(ALLOWED_EXTENSIONS)))


@app.route("/cases/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
def case_edit(case_id):
    case = get_case(case_id)
    if request.method == "POST":
        values = case_from_form()
        if not values[1]:
            flash("Case title is required.", "error")
        else:
            get_client(values[0])
            db = get_db()
            db.execute("""UPDATE cases SET client_id=?, title=?, case_number=?, court=?,
                          case_type=?, status=?, description=?, opened_on=?
                          WHERE id=?""", values + (case_id,))
            db.commit()
            flash("Case updated.", "success")
            return redirect(url_for("case_detail", case_id=case_id))
    return render_case_form(case, editing=True)


@app.route("/cases/<int:case_id>/delete", methods=["POST"])
@login_required
def case_delete(case_id):
    case = get_case(case_id)
    db = get_db()
    files = [r["stored_name"] for r in db.execute(
        "SELECT stored_name FROM documents WHERE case_id = ?", (case_id,))]
    db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    db.commit()
    remove_files(files)
    flash("Case deleted.", "success")
    return redirect(url_for("client_detail", client_id=case["client_id"]))


# ---------------------------------------------------------------- hearings --

@app.route("/cases/<int:case_id>/hearings", methods=["POST"])
@login_required
def hearing_add(case_id):
    get_case(case_id)
    hearing_date = request.form["hearing_date"]
    try:
        date.fromisoformat(hearing_date)   # validate the date
    except ValueError:
        flash("Please choose a valid hearing date.", "error")
    else:
        db = get_db()
        db.execute("""INSERT INTO hearings (case_id, hearing_date, purpose, outcome)
                      VALUES (?, ?, ?, ?)""",
                   (case_id, hearing_date, request.form["purpose"].strip(),
                    request.form["outcome"].strip()))
        db.commit()
        flash("Hearing added.", "success")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/hearings/<int:hearing_id>/delete", methods=["POST"])
@login_required
def hearing_delete(hearing_id):
    hearing = get_hearing(hearing_id)
    db = get_db()
    db.execute("DELETE FROM hearings WHERE id = ?", (hearing_id,))
    db.commit()
    flash("Hearing removed.", "success")
    return redirect(url_for("case_detail", case_id=hearing["case_id"]))


# --------------------------------------------------------------- documents --

@app.route("/cases/<int:case_id>/documents", methods=["POST"])
@login_required
def document_upload(case_id):
    get_case(case_id)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a file to upload.", "error")
    elif not allowed_file(file.filename):
        flash("That file type is not allowed.", "error")
    else:
        extension = file.filename.rsplit(".", 1)[1].lower()
        # secure_filename removes path tricks and non-English letters; if nothing
        # usable is left (e.g. an Urdu file name), fall back to a plain name
        original = secure_filename(file.filename)
        if "." not in original:
            original = f"document.{extension}"
        # A random name on disk means two files called "affidavit.pdf" never clash
        stored = uuid.uuid4().hex + "." + extension
        file.save(os.path.join(UPLOAD_DIR, stored))
        db = get_db()
        db.execute(
            """INSERT INTO documents (case_id, title, filename, stored_name, uploaded_on)
               VALUES (?, ?, ?, ?, ?)""",
            (case_id, request.form["title"].strip() or original, original, stored,
             date.today().isoformat()))
        db.commit()
        flash("Document uploaded.", "success")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/documents/<int:doc_id>/download")
@login_required
def document_download(doc_id):
    doc = get_document(doc_id)
    return send_from_directory(UPLOAD_DIR, doc["stored_name"], as_attachment=True,
                               download_name=doc["filename"])


@app.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def document_delete(doc_id):
    doc = get_document(doc_id)
    db = get_db()
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    db.commit()
    remove_files([doc["stored_name"]])
    flash("Document deleted.", "success")
    return redirect(url_for("case_detail", case_id=doc["case_id"]))


# ------------------------------------------------------------------ search --

@app.route("/search")
@login_required
def search():
    """Search clients, cases and documents at once."""
    q = request.args.get("q", "").strip()
    results = {"clients": [], "cases": [], "documents": []}
    if q:
        db = get_db()
        like = f"%{q}%"
        results["clients"] = db.execute(
            """SELECT * FROM clients
               WHERE user_id = ?
                 AND (name LIKE ? OR cnic LIKE ? OR phone LIKE ? OR email LIKE ?
                      OR notes LIKE ?)
               ORDER BY name""", (uid(),) + (like,) * 5).fetchall()
        results["cases"] = db.execute(
            """SELECT c.*, cl.name AS client_name
               FROM cases c JOIN clients cl ON cl.id = c.client_id
               WHERE cl.user_id = ?
                 AND (c.title LIKE ? OR c.case_number LIKE ? OR c.court LIKE ?
                      OR c.description LIKE ?)
               ORDER BY c.opened_on DESC""", (uid(),) + (like,) * 4).fetchall()
        results["documents"] = db.execute(
            """SELECT d.*, c.title AS case_title
               FROM documents d JOIN cases c ON c.id = d.case_id
               JOIN clients cl ON cl.id = c.client_id
               WHERE cl.user_id = ? AND (d.title LIKE ? OR d.filename LIKE ?)
               ORDER BY d.uploaded_on DESC""", (uid(),) + (like,) * 2).fetchall()
    return render_template("search.html", q=q, results=results)


# -------------------------------------------------------------------- main --

init_db()


# ----------------------------------------------------------------- reports --

@app.route("/reports")
@login_required
def reports():
    """A printable summary of case activity, clients and the coming schedule."""
    db = get_db()
    today = date.today()
    start = valid_date(request.args.get("from", "")) or (today - timedelta(days=30)).isoformat()
    end = valid_date(request.args.get("to", "")) or today.isoformat()
    if start > end:
        start, end = end, start

    by_status = {s: 0 for s in CASE_STATUSES}
    for row in db.execute(
            """SELECT c.status, COUNT(*) AS n FROM cases c
               JOIN clients cl ON cl.id = c.client_id
               WHERE cl.user_id = ? GROUP BY c.status""", (uid(),)):
        by_status[row["status"]] = row["n"]
    by_type = db.execute(
        """SELECT c.case_type, COUNT(*) AS n FROM cases c
           JOIN clients cl ON cl.id = c.client_id
           WHERE cl.user_id = ? GROUP BY c.case_type ORDER BY n DESC""", (uid(),)).fetchall()
    opened = db.execute(
        """SELECT c.*, cl.name AS client_name FROM cases c
           JOIN clients cl ON cl.id = c.client_id
           WHERE cl.user_id = ? AND c.opened_on BETWEEN ? AND ?
           ORDER BY c.opened_on""", (uid(), start, end)).fetchall()
    held = db.execute(
        """SELECT h.*, c.title AS case_title, cl.name AS client_name FROM hearings h
           JOIN cases c ON c.id = h.case_id JOIN clients cl ON cl.id = c.client_id
           WHERE cl.user_id = ? AND h.hearing_date BETWEEN ? AND ?
           ORDER BY h.hearing_date""",
        # "held" = already happened, so it stops at yesterday even if the period runs on
        (uid(), start, min(end, (today - timedelta(days=1)).isoformat()))).fetchall()
    clients_ = db.execute(
        """SELECT cl.name, cl.cnic, cl.phone,
                  COUNT(c.id) AS total,
                  SUM(c.status IN ('Ongoing', 'Pending')) AS active
           FROM clients cl LEFT JOIN cases c ON c.client_id = cl.id
           WHERE cl.user_id = ? GROUP BY cl.id ORDER BY cl.name""", (uid(),)).fetchall()
    schedule = upcoming_hearings(today.isoformat(),
                                 (today + timedelta(days=30)).isoformat())
    return render_template("reports.html", start=start, end=end, by_status=by_status,
                           by_type=by_type, opened=opened, held=held,
                           clients=clients_, schedule=schedule,
                           generated=today.isoformat())


# --------------------------------------------------------------- demo data --

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo12345"


def seed_demo():
    """Create a demo account filled with made-up records (run with --demo).

    Everything here is fictional. It lets a reviewer see a populated system
    without entering data or seeing any real client's information.
    """
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    if db.execute("SELECT 1 FROM users WHERE username = ?", (DEMO_USERNAME,)).fetchone():
        db.close()
        return

    today = date.today()

    def day(offset):
        return (today + timedelta(days=offset)).isoformat()

    user_id = db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         (DEMO_USERNAME, generate_password_hash(DEMO_PASSWORD))).lastrowid

    # (name, cnic, phone, email, address, notes, [cases])
    # case = (title, number, court, type, status, description, opened, [hearings])
    # hearing = (days from today, purpose, outcome)
    sample = [
        ("Sample Client A", "00000-0000001-1", "0300-0000001", "client.a@example.com", "12 Example Road",
         "Prefers contact by phone in the evening.", [
            ("Client A v. Example Traders", "CS-101/2026", "Civil Court", "Civil",
             "Ongoing", "Recovery of unpaid invoice.", day(-90), [
                (-60, "First hearing", "Notice issued to defendant."),
                (-20, "Written statement", "Defendant filed reply."),
                (7, "Evidence", ""),
             ]),
         ]),
        ("Sample Client B", "00000-0000002-2", "0300-0000002", "client.b@example.com", "45 Sample Street",
         "", [
            ("Client B – Property Transfer", "PT-55/2026", "Revenue Office", "Property",
             "Pending", "Transfer of title for residential plot.", day(-30), [
                (14, "Document verification", ""),
             ]),
            ("Client B v. Sample Builders", "CS-210/2025", "High Court", "Civil",
             "Completed", "Dispute over construction delay. Settled.", day(-400), [
                (-200, "Final arguments", "Decided in favour of the client."),
             ]),
         ]),
        ("Sample Client C", "00000-0000003-3", "0300-0000003", "client.c@example.com", "8 Demo Avenue",
         "Company client; send copies of all orders by email.", [
            ("Example Pvt Ltd – Contract Review", "CR-12/2026", "", "Corporate",
             "Pending", "Review of supplier agreement.", day(-45), [
                (-10, "Meeting with opposing counsel", "Adjourned at their request."),
                (0, "Rescheduled meeting", ""),
             ]),
         ]),
    ]

    for name, cnic, phone, email, address, notes, cases_ in sample:
        client_id = db.execute(
            """INSERT INTO clients (user_id, name, cnic, phone, email, address, notes,
                                    created_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, cnic, phone, email, address, notes, day(-120))).lastrowid
        for title, number, court, case_type, status, description, opened, hearings in cases_:
            case_id = db.execute(
                """INSERT INTO cases (client_id, title, case_number, court, case_type,
                                      status, description, opened_on)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_id, title, number, court, case_type, status, description,
                 opened)).lastrowid
            for offset, purpose, outcome in hearings:
                db.execute("""INSERT INTO hearings (case_id, hearing_date, purpose, outcome)
                              VALUES (?, ?, ?, ?)""", (case_id, day(offset), purpose, outcome))
            # One small text document per case, so upload/download can be tried
            stored = uuid.uuid4().hex + ".txt"
            with open(os.path.join(UPLOAD_DIR, stored), "w") as f:
                f.write(f"SAMPLE DOCUMENT (fictional)\n\nCase: {title}\nCase number: {number}\n")
            db.execute(
                """INSERT INTO documents (case_id, title, filename, stored_name, uploaded_on)
                   VALUES (?, ?, ?, ?, ?)""",
                (case_id, "Case summary (sample)", "case_summary.txt", stored, opened))
    db.commit()
    db.close()


# ------------------------------------------------------------------- https --

CERT_PATH = os.path.join(DATA_DIR, "cert.pem")
CERT_KEY_PATH = os.path.join(DATA_DIR, "cert-key.pem")


def local_ip():
    """This computer's address on the local network (e.g. 192.168.1.20)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no data is actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_certificate():
    """Create a self-signed HTTPS certificate on first run and reuse it.

    The certificate encrypts everything sent between browser and server
    (passwords, client records, documents). Because it is signed by this
    computer rather than a certificate authority, browsers show a warning
    the first time; choose "Advanced" -> "Proceed" to continue.
    """
    if os.path.exists(CERT_PATH) and os.path.exists(CERT_KEY_PATH):
        return CERT_PATH, CERT_KEY_PATH

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Legal Client Management")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address(local_ip())),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(CERT_KEY_PATH, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return CERT_PATH, CERT_KEY_PATH


@app.after_request
def security_headers(response):
    if request.is_secure:
        # Tell the browser to always use HTTPS for this site from now on
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legal Client Management System")
    parser.add_argument("--network", action="store_true",
                        help="allow other computers on the same network to connect")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-https", action="store_true",
                        help="run without encryption (not recommended)")
    parser.add_argument("--demo", action="store_true",
                        help="add a demo account with made-up sample records")
    args = parser.parse_args()

    if args.demo:
        seed_demo()
        print(f"\n  Demo account ->  username: {DEMO_USERNAME}   password: {DEMO_PASSWORD}")

    host = "0.0.0.0" if args.network else "127.0.0.1"
    shown = local_ip() if args.network else "127.0.0.1"

    if args.no_https:
        print(f"\n  Open http://{shown}:{args.port}\n")
        app.run(host=host, port=args.port)
    else:
        # Session cookie is only ever sent over the encrypted connection
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE="Lax")
        print(f"\n  Open https://{shown}:{args.port}\n")
        app.run(host=host, port=args.port, ssl_context=ensure_certificate())
