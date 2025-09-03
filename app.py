# app.py — Legal Time Tracker (multi-user, Flask)
# Run:
#   python -m venv .venv
#   .\.venv\Scripts\activate
#   pip install -r requirements.txt
#   python app.py
#
# Master login (ensured at startup):
#   Username (or email): Law  (or law@local)
#   Password: ilovemyjob

from __future__ import annotations

from datetime import datetime, timedelta, date
from pathlib import Path
import csv
import io
import os
import smtplib
import ssl
from email.message import EmailMessage
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_file, abort
)
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from sqlalchemy import (
    create_engine, Integer, String, Float, Date, Text,
    ForeignKey, select, func, UniqueConstraint, or_
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv


# ---------- Email/Export helpers ----------

def _build_csv_bytes(rows, default_timekeeper: str) -> tuple[bytes, str]:
    """Return (csv_bytes, filename) for time entries."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Client", "Matter", "Date of Work",
        "Timekeeper's Name", "Billable Hours", "Description of Work Performed",
    ])
    for r in rows:
        date_str = getattr(r.date_of_work, "isoformat", lambda: str(r.date_of_work))()
        tk = getattr(r, "timekeeper", "") or default_timekeeper
        hours = f"{(getattr(r, 'hours', 0) or 0):.2f}"
        w.writerow([r.client, r.matter, date_str, tk, hours, r.desc or ""])
    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"time_entries_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return csv_bytes, filename


def _smtp_send(
    server: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> None:
    """Send an email with optional attachments (SSL on 465, STARTTLS if requested)."""
    attachments = attachments or []
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    for fname, mimetype, data in attachments:
        main, _, sub = (mimetype or "application/octet-stream").partition("/")
        msg.add_attachment(
            data, maintype=main or "application", subtype=sub or "octet-stream", filename=fname
        )

    timeout = 30
    if use_tls and port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(server, port, context=context, timeout=timeout) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(server, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(msg)

# ------------------------------------------------------


# --- Paths / .env ---
BASE_DIR = Path(__file__).resolve().parent
APP_DIR = Path.home() / ".legal_time_tracker_web"
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "time_tracker.db"
load_dotenv(BASE_DIR / ".env")

# --- Constants / Abbrev map ---
STOPWATCH_MIN_SECONDS = 300  # 5 minutes
AUTO_REPLACE = {
    "OP": "Opposing Counsel",
    "OSC": "Order to Show Cause",
    "NYSCEF": "New York State Courts Electronic Filing",
    "RJI": "Request for Judicial Intervention",
    "AFF": "Affirmation",
    "MOL": "Memorandum of Law",
}
DEFAULT_CLIENTS = ["Potential Client", "Sales", "Test1", "Private Client", "Pro Bono"]
DEFAULT_MATTERS = ["Divorce", "Custody", "Motion Practice", "Appeal", "Consultation", "Court Appearance", "Sales Call"]
DEFAULT_RATES   = ["300", "500", "350", "Non Billable", "40"]


def create_app():
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", os.getenv("SECRET_KEY", "dev-secret-change-me"))
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_ECHO"] = False

    # --- DB (SQLAlchemy 2.x) ---
    class Base(DeclarativeBase):
        pass

    class User(UserMixin, Base):
        __tablename__ = "users"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
        name: Mapped[str] = mapped_column(String(120), nullable=False)  # used for username login too
        password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
        is_admin: Mapped[int] = mapped_column(Integer, default=0)

        entries: Mapped[list["Entry"]] = relationship(back_populates="user", cascade="all,delete-orphan")
        settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False, cascade="all,delete-orphan")
        clients: Mapped[list["ClientName"]] = relationship(back_populates="user", cascade="all,delete-orphan")
        matters: Mapped[list["MatterName"]] = relationship(back_populates="user", cascade="all,delete-orphan")
        rates: Mapped[list["RateName"]] = relationship(back_populates="user", cascade="all,delete-orphan")

        def set_password(self, pw: str) -> None:
            self.password_hash = generate_password_hash(pw)

        def check_password(self, pw: str) -> bool:
            return check_password_hash(self.password_hash, pw)

    class UserSettings(Base):
        __tablename__ = "user_settings"
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
        auto_expand: Mapped[int] = mapped_column(Integer, default=1)
        smtp_server: Mapped[str] = mapped_column(String(255), default="smtp.office365.com")
        smtp_port: Mapped[str] = mapped_column(String(10), default="587")
        smtp_username: Mapped[str] = mapped_column(String(255), default="")
        smtp_from: Mapped[str] = mapped_column(String(255), default="")
        smtp_use_tls: Mapped[int] = mapped_column(Integer, default=1)
        admin_email: Mapped[str] = mapped_column(String(255), default="")
        manager_email: Mapped[str] = mapped_column(String(255), default="")

        user: Mapped["User"] = relationship(back_populates="settings")

        # populated at runtime for templates
        clients: list[str] = []
        matters: list[str] = []
        rates: list[str] = []

    class ClientName(Base):
        __tablename__ = "client_names"
        __table_args__ = (UniqueConstraint("user_id", "name", name="uq_client_per_user"),)
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
        name: Mapped[str] = mapped_column(String(255), nullable=False)
        user: Mapped["User"] = relationship(back_populates="clients")

    class MatterName(Base):
        __tablename__ = "matter_names"
        __table_args__ = (UniqueConstraint("user_id", "name", name="uq_matter_per_user"),)
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
        name: Mapped[str] = mapped_column(String(255), nullable=False)
        user: Mapped["User"] = relationship(back_populates="matters")

    class RateName(Base):
        __tablename__ = "rate_names"
        __table_args__ = (UniqueConstraint("user_id", "name", name="uq_rate_per_user"),)
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
        name: Mapped[str] = mapped_column(String(64), nullable=False)
        user: Mapped["User"] = relationship(back_populates="rates")

    class Entry(Base):
        __tablename__ = "entries"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
        client: Mapped[str] = mapped_column(String(255))
        matter: Mapped[str] = mapped_column(String(255))
        date_of_work: Mapped[date] = mapped_column(Date)
        hours: Mapped[float] = mapped_column(Float, default=0.0)
        timekeeper: Mapped[str] = mapped_column(String(120), default="")
        desc: Mapped[str] = mapped_column(Text, default="")
        created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

        user: Mapped["User"] = relationship(back_populates="entries")

    # --- Migration helpers ---
    def _drop_index_no_if_present(engine):
        with engine.begin() as conn:
            rows = conn.exec_driver_sql("PRAGMA table_info('entries')").fetchall()
            col_names = [r[1] for r in rows] if rows else []
            if "index_no" not in col_names:
                return
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS entries__new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    client TEXT,
                    matter TEXT,
                    date_of_work DATE,
                    hours FLOAT DEFAULT 0.0,
                    "desc" TEXT DEFAULT '',
                    created_at DATETIME
                )
                """
            )
            conn.exec_driver_sql(
                """
                INSERT INTO entries__new (id, user_id, client, matter, date_of_work, hours, "desc", created_at)
                SELECT id, user_id, client, matter, date_of_work, hours, "desc", created_at
                FROM entries
                """
            )
            conn.exec_driver_sql("DROP TABLE entries")
            conn.exec_driver_sql("ALTER TABLE entries__new RENAME TO entries")

    def _add_timekeeper_column_if_missing(engine):
        with engine.begin() as conn:
            rows = conn.exec_driver_sql("PRAGMA table_info('entries')").fetchall()
            col_names = [r[1] for r in rows] if rows else []
            if "timekeeper" not in col_names:
                conn.exec_driver_sql("ALTER TABLE entries ADD COLUMN timekeeper TEXT DEFAULT ''")

    def _ensure_unique_username_index(engine):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_name ON users(name)")
        except Exception as ex:
            print("[WARN] Could not create unique index on users(name). "
                  "You may have duplicate usernames. Error:", ex)

    # --- Create DB / apply migrations ---
    engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"], future=True)
    Base.metadata.create_all(engine)
    _drop_index_no_if_present(engine)
    _add_timekeeper_column_if_missing(engine)
    _ensure_unique_username_index(engine)

    # --- Seed or update master account ---
    def seed_or_update_master() -> None:
        with Session(engine) as s:
            u = s.scalar(
                select(User).where(
                    (func.lower(User.email) == "law@local") | (func.lower(User.name) == "law")
                )
            )
            created = False
            if not u:
                u = User(email="law@local", name="Law", is_admin=1)
                u.set_password("ilovemyjob")
                s.add(u)
                s.flush()
                created = True
            else:
                u.name = "Law"
                u.is_admin = 1
                u.set_password("ilovemyjob")

            if not s.scalar(select(func.count(ClientName.id)).where(ClientName.user_id == u.id)):
                for n in DEFAULT_CLIENTS:
                    s.add(ClientName(user_id=u.id, name=n))
            if not s.scalar(select(func.count(MatterName.id)).where(MatterName.user_id == u.id)):
                for n in DEFAULT_MATTERS:
                    s.add(MatterName(user_id=u.id, name=n))
            if not s.scalar(select(func.count(RateName.id)).where(RateName.user_id == u.id)):
                for n in DEFAULT_RATES:
                    s.add(RateName(user_id=u.id, name=n))
            if not s.get(UserSettings, u.id):
                s.add(UserSettings(user_id=u.id))

            s.commit()
            print("✔ Master account ready: username/email='Law'/'law@local' password='ilovemyjob'",
                  "(created)" if created else "(reset)")
            print(f"DB: {DB_PATH}")

    seed_or_update_master()

    # --- Auth wiring ---
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        with Session(engine) as s:
            return s.get(User, int(user_id))

    # --- Helpers ---
    def replace_abbreviations(text: str) -> str:
        return " ".join([AUTO_REPLACE.get(w.upper(), w) for w in (text or "").split()])

    def ensure_default_lists(user: User, s: Session) -> None:
        if not s.scalar(select(func.count(ClientName.id)).where(ClientName.user_id == user.id)):
            for n in DEFAULT_CLIENTS:
                s.add(ClientName(user_id=user.id, name=n))
        if not s.scalar(select(func.count(MatterName.id)).where(MatterName.user_id == user.id)):
            for n in DEFAULT_MATTERS:
                s.add(MatterName(user_id=user.id, name=n))
        if not s.scalar(select(func.count(RateName.id)).where(RateName.user_id == user.id)):
            for n in DEFAULT_RATES:
                s.add(RateName(user_id=user.id, name=n))

    def get_settings(user_id: int, s: Session) -> "UserSettings":
        us = s.get(UserSettings, user_id)
        if not us:
            us = UserSettings(user_id=user_id)
            s.add(us)
            s.commit()
        return us

    def filtered_entries_query(
        s: Session,
        user_id: int,
        mode: str,
        dfrom: str | None,
        dto: str | None,
        q: str | None,
    ):
        """Return a query for the user's entries filtered by date range and search.

        ``mode`` selects a predefined date span (7d/30d/90d/range/all). ``q`` is
        a free-text search where each space-separated term is matched
        case-insensitively against the client, matter, timekeeper, or description
        fields. An entry must satisfy *all* terms to be returned.
        """
        q_text = (q or "").strip()
        stmt = select(Entry).where(Entry.user_id == user_id)
               
        today = date.today()
        if mode == "7d":
            stmt = stmt.where(Entry.date_of_work >= today - timedelta(days=7))
        elif mode == "30d":
            stmt = stmt.where(Entry.date_of_work >= today - timedelta(days=30))
        elif mode == "90d":
            stmt = stmt.where(Entry.date_of_work >= today - timedelta(days=90))
        elif mode == "range":
            try:
                if dfrom:
                    stmt = stmt.where(Entry.date_of_work >= datetime.fromisoformat(dfrom).date())
                if dto:
                    stmt = stmt.where(Entry.date_of_work <= datetime.fromisoformat(dto).date())
            except Exception:
                pass
        elif mode == "all":
            pass
        else:
            stmt = stmt.where(Entry.date_of_work >= today - timedelta(days=30))
        
        if q_text:
            for term in q_text.split():
                like = f"%{term}%"
                stmt = stmt.where(
                    or_(
                        Entry.client.ilike(like),
                        Entry.matter.ilike(like),
                        Entry.timekeeper.ilike(like),
                        Entry.desc.ilike(like),
                    )
                )
        return stmt.order_by(Entry.date_of_work.desc(), Entry.id.desc()
                             
   # --- Admin guard ---
    def admin_required(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if not getattr(current_user, "is_admin", 0):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
                             
     # Expose defaults for templates
    @app.context_processor
    def _inject_defaults():
        return {"form": {}}                           
   
    # --- Health / index / 404 ---
    @app.get("/healthz")
    def healthz():
        return "ok", 200

    @app.get("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("entries"))
        return redirect(url_for("login"))

    @app.errorhandler(404)
    def not_found(e):
        home = url_for("index")
        return (f"<h1>404 – Not Found</h1><p><a href='{home}'>Go home</a></p>", 404)

    # --- Register / Login ---
    @app.get("/register")
    def register():
        # Open registration page for everyone (even after first user exists)
        if current_user.is_authenticated:
            return redirect(url_for("entries"))
        return render_template("register.html")

    @app.post("/register")
    def register_post():
        if current_user.is_authenticated:
            return redirect(url_for("entries"))

        name  = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        pw1   = request.form.get("password") or ""
        pw2   = request.form.get("password2") or ""

        # Basic validation
        errors = []
        if not name:
            errors.append("name")
        if not email or "@" not in email or "." not in email:
            errors.append("valid email")
        if not pw1 or len(pw1) < 6:
            errors.append("password (min 6 chars)")
        if pw1 != pw2:
            errors.append("matching passwords")
        if errors:
            flash("Please provide: " + ", ".join(errors) + ".", "danger")
            return redirect(url_for("register"))

        with Session(engine) as s:
            if s.scalar(select(User).where(func.lower(User.email) == email)):
                flash("Email already registered.", "danger")
                return redirect(url_for("register"))
            if s.scalar(select(User).where(func.lower(User.name) == name.lower())):
                flash("Username is already taken.", "danger")
                return redirect(url_for("register"))

            is_first_user = not s.scalar(select(func.count(User.id)))
            u = User(email=email, name=name, is_admin=1 if is_first_user else 0)
            u.set_password(pw1)
            s.add(u)
            s.commit()

            ensure_default_lists(u, s)
            get_settings(u.id, s)
            s.commit()

        # Redirect to login with flags to trigger in-page modal
        return redirect(url_for("login", created=1, username=name, email=email))

    @app.get("/login")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("entry"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        raw = (request.form.get("username") or request.form.get("email") or "").strip()
        pw = (request.form.get("password") or "").strip()
        if not raw or not pw:
            flash("Please enter your email/username and password.", "danger")
            return redirect(url_for("login"))

        with Session(engine) as s:
            u = s.scalar(
                select(User).where((func.lower(User.email) == raw.lower()) | (func.lower(User.name) == raw.lower()))
            )
            if not u or not u.check_password(pw):
                flash("Invalid email/username or password.", "danger")
                return redirect(url_for("login"))

            remember = (str(request.form.get("remember", "")).lower() in ("1", "true", "on", "yes"))
            login_user(u, remember=remember)

        return redirect(url_for("entry"))

    @app.get("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # --- New Time Entry (form) ---
    @app.get("/entry")
    @login_required
    def entry():
        with Session(engine) as s:
            ensure_default_lists(current_user, s)
            us = get_settings(current_user.id, s)
            us.clients = [c.name for c in s.scalars(select(ClientName).where(ClientName.user_id == current_user.id).order_by(ClientName.name))]
            us.matters = [m.name for m in s.scalars(select(MatterName).where(MatterName.user_id == current_user.id).order_by(MatterName.name))]
            us.rates   = [r.name for r in s.scalars(select(RateName).where(RateName.user_id == current_user.id).order_by(RateName.name))]

        return render_template(
            "entry.html",
            settings=us,
            date_today=date.today(),
            stop_min=STOPWATCH_MIN_SECONDS,
        )

    @app.post("/entry")
    @login_required
    def entry_post():
        return save_entry()

    @app.post("/save")
    @login_required
    def save_entry():
        client = (request.form.get("client") or "").strip() or "(Unspecified)"
        matter = (request.form.get("matter") or "").strip() or "(Unspecified)"
        date_str = (request.form.get("date_of_work") or request.form.get("date") or "").strip()
        hours_str = (request.form.get("hours") or "0").strip()
        timekeeper = (request.form.get("timekeeper") or current_user.name or "").strip()
        desc = (request.form.get("desc") or "").strip()
        elapsed = int(request.form.get("elapsed_seconds") or "0")

        with Session(engine) as s:
            us = get_settings(current_user.id, s)
            if us.auto_expand == 1:
                desc = replace_abbreviations(desc)
            try:
                d = datetime.fromisoformat(date_str).date()
            except Exception:
                d = date.today()
            try:
                hours = float(hours_str)
            except Exception:
                hours = 0.0

            s.add(Entry(
                user_id=current_user.id,
                client=client,
                matter=matter,
                date_of_work=d,
                hours=hours,
                timekeeper=timekeeper,
                desc=desc
            ))
            s.commit()

        flash(("Timed session " + str(elapsed // 60) + "m saved.") if elapsed >= STOPWATCH_MIN_SECONDS else "Entry saved.", "success")
        if request.form.get("close") == "1":
            return redirect(url_for("entries"))
        return redirect(url_for("entry"))

    # --- Entries list / filters / inline edit & delete ---
    @app.get("/entries")
    @login_required
    def entries():
        mode = request.args.get("mode", "30d")
        dfrom = request.args.get("from")
        dto   = request.args.get("to")
        q     = request.args.get("q", "")

        with Session(engine) as s:
            rows = s.scalars(
                filtered_entries_query(s, current_user.id, mode, dfrom, dto, q)
            ).all()

        total_hours = round(sum((r.hours or 0.0) for r in rows), 2)

        by = {}
        for r in rows:
            key = (r.client or "(Unspecified)").strip()
            by[key] = by.get(key, 0.0) + (r.hours or 0.0)
        by_client = sorted(by.items(), key=lambda kv: kv[1], reverse=True)

        return render_template(
            "entries.html",
            rows=rows, mode=mode, dfrom=dfrom, dto=dto, q=q,
            total_hours=total_hours, by_client=by_client,
        )

    @app.post("/entries/edit")
    @login_required
    def entries_edit():
        try:
            eid = int(request.form.get("id"))
        except Exception:
            abort(400)

        with Session(engine) as s:
            e = s.get(Entry, eid)
            if not e or e.user_id != current_user.id:
                abort(404)
            e.client = (request.form.get("client") or e.client).strip()
            e.matter = (request.form.get("matter") or e.matter).strip()
            try:
                ds = request.form.get("date_of_work")
                if ds:
                    e.date_of_work = datetime.fromisoformat(ds).date()
            except Exception:
                pass
            try:
                e.hours = float(request.form.get("hours") or e.hours)
            except Exception:
                pass
            e.timekeeper = (request.form.get("timekeeper") or e.timekeeper).strip()
            e.desc = (request.form.get("desc") or e.desc).strip()
            s.commit()

        flash("Entry updated.", "success")
        return redirect(url_for("entries"))

    @app.post("/entries/delete")
    @login_required
    def entries_delete():
        ids = request.form.getlist("id")
        try:
            id_list = [int(x) for x in ids if str(x).strip()]
        except Exception:
            flash("Invalid selection.", "warning")
            return redirect(url_for("entries"))

        if not id_list:
            flash("Nothing selected.", "warning")
            return redirect(url_for("entries"))

        with Session(engine) as s:
            to_delete = s.scalars(select(Entry).where(
                Entry.user_id == current_user.id,
                Entry.id.in_(id_list)
            )).all()
            for e in to_delete:
                s.delete(e)
            s.commit()

        flash(f"Deleted {len(id_list)} entr{'y' if len(id_list)==1 else 'ies' }.", "success")
        return redirect(url_for("entries"))

    # --- Export (CSV) ---
    @app.route("/export", methods=["GET", "POST"], endpoint="export_entries")
    @login_required
    def export_csv():
        """
        Export entries as CSV.
        - If POSTed from the bulk form with checkboxes, export only the selected IDs.
        - Otherwise, export rows matching the current filters.
        """
        # Gather selected IDs (when coming from the bulk form)
        raw_ids = request.values.getlist("id")
        id_list: list[int] = []
        for x in raw_ids:
            try:
                id_list.append(int(x))
            except (TypeError, ValueError):
                pass

        export_selected = (request.values.get("export_selected") == "1")

        with Session(engine) as s:
            if id_list:
                rows = s.scalars(
                    select(Entry)
                    .where(Entry.user_id == current_user.id, Entry.id.in_(id_list))
                    .order_by(Entry.date_of_work.desc(), Entry.id.desc())
                ).all()
            else:
                mode  = request.values.get("mode", "30d")
                dfrom = request.values.get("from")
                dto   = request.values.get("to")
                q     = request.values.get("q", "")
                rows = s.scalars(
                    filtered_entries_query(s, current_user.id, mode, dfrom, dto, q)
                ).all()

        if export_selected and not id_list:
            flash("Please select at least one row to export.", "warning")
            return redirect(url_for("entries"))

        csv_bytes, filename = _build_csv_bytes(rows, current_user.name)
        return send_file(
            io.BytesIO(csv_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv",
        )

    # --- Export (XLSX) ---
    @app.route("/export_xlsx", methods=["GET", "POST"])
    @login_required
    def export_xlsx():
        try:
            from openpyxl import Workbook
        except ModuleNotFoundError:
            flash("XLSX export requires the 'openpyxl' package. Activate your venv and run: pip install openpyxl", "danger")
            return redirect(url_for("entries"))

        # Gather selected IDs (when coming from the bulk form)
        raw_ids = request.values.getlist("id")
        id_list: list[int] = []
        for x in raw_ids:
            try:
                id_list.append(int(x))
            except (TypeError, ValueError):
                pass

        export_selected = (request.values.get("export_selected") == "1")

        with Session(engine) as s:
            if id_list:
                rows = s.scalars(
                    select(Entry)
                    .where(Entry.user_id == current_user.id, Entry.id.in_(id_list))
                    .order_by(Entry.date_of_work.desc(), Entry.id.desc())
                ).all()
            else:
                mode  = request.values.get("mode", "30d")
                dfrom = request.values.get("from")
                dto   = request.values.get("to")
                q     = request.values.get("q", "")
                rows = s.scalars(
                    filtered_entries_query(s, current_user.id, mode, dfrom, dto, q)
                ).all()

        if export_selected and not id_list:
            flash("Please select at least one row to export.", "warning")
            return redirect(url_for("entries"))

        wb = Workbook()
        ws = wb.active
        ws.title = "Time Entries"
        ws.append(["Client","Matter","Date of Work","Timekeeper's Name","Billable Hours","Description of Work Performed"])
        for r in rows:
            tk = getattr(r, "timekeeper", "") or current_user.name
            ws.append([r.client, r.matter, r.date_of_work.isoformat(), tk, f"{(r.hours or 0):.2f}", r.desc])

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        filename = f"time_entries_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            out,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # --- Options ---
    @app.get("/options")
    @login_required
    def options():
        with Session(engine) as s:
            us = get_settings(current_user.id, s)
            us.clients = [c.name for c in s.scalars(select(ClientName).where(ClientName.user_id == current_user.id).order_by(ClientName.name))]
            us.matters = [m.name for m in s.scalars(select(MatterName).where(MatterName.user_id == current_user.id).order_by(MatterName.name))]
            us.rates   = [r.name for r in s.scalars(select(RateName).where(RateName.user_id == current_user.id).order_by(RateName.name))]
        return render_template("options.html", settings=us)

    @app.post("/options")
    @login_required
    def options_save():
        # accept "1" OR "on" for checkboxes
        def as_bool(v):
            return 1 if (str(v or "").lower() in ("1", "true", "on", "yes")) else 0

        auto_expand  = as_bool(request.form.get("auto_expand"))
        smtp_use_tls = as_bool(request.form.get("smtp_use_tls"))

        smtp_server   = (request.form.get("smtp_server") or "").strip()
        smtp_port     = (request.form.get("smtp_port") or "").strip()
        smtp_username = (request.form.get("smtp_username") or "").strip()
        smtp_from     = (request.form.get("smtp_from") or "").strip()
        admin_email   = (request.form.get("admin_email") or "").strip()
        manager_email = (request.form.get("manager_email") or "").strip()

        clients_text = request.form.get("clients") or ""
        matters_text = request.form.get("matters") or ""
        rates_text   = request.form.get("rates") or ""

        with Session(engine) as s:
            us = get_settings(current_user.id, s)
            us.auto_expand   = auto_expand
            us.smtp_server   = smtp_server or us.smtp_server
            us.smtp_port     = smtp_port or us.smtp_port
            us.smtp_username = smtp_username or us.smtp_username
            us.smtp_from     = smtp_from or us.smtp_from
            us.smtp_use_tls  = smtp_use_tls
            us.admin_email   = admin_email
            us.manager_email = manager_email

            def _replace(model, lines: list[str]):
                # Clear and replace simple list tables
                s.query(model).filter(model.user_id == current_user.id).delete()
                for name in [x.strip() for x in lines if x.strip()]:
                    s.add(model(user_id=current_user.id, name=name))

            _replace(ClientName, clients_text.splitlines())
            _replace(MatterName, matters_text.splitlines())
            _replace(RateName,   rates_text.splitlines())
            s.commit()

        flash("Settings saved.", "success")
        return redirect(url_for("options"))

    # --- Admin: Users (list/add/delete/reset) ---
    @app.get("/admin/users", endpoint="admin_users")
    @admin_required
    def admin_users():
        with Session(engine) as s:
            users = s.scalars(select(User).order_by(User.id)).all()
        return render_template("admin_users.html", users=users)

    @app.post("/admin/users/add", endpoint="admin_users_add")
    @admin_required
    def admin_users_add():
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        pw = (request.form.get("password") or "").strip()
        is_admin = 1 if (request.form.get("is_admin") in ("1","on","true","yes")) else 0

        if not name or not email or not pw:
            flash("Username, email, and password are required.", "danger")
            return redirect(url_for("admin_users"))

        with Session(engine) as s:
            if s.scalar(select(User).where(func.lower(User.email) == email)):
                flash("Email already registered.", "danger")
                return redirect(url_for("admin_users"))
            if s.scalar(select(User).where(func.lower(User.name) == name.lower())):
                flash("Username is already taken.", "danger")
                return redirect(url_for("admin_users"))

            u = User(email=email, name=name, is_admin=is_admin)
            u.set_password(pw)
            s.add(u)
            s.commit()
            ensure_default_lists(u, s)
            get_settings(u.id, s)
            s.commit()
        flash("User created.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/delete", endpoint="admin_users_delete")
    @admin_required
    def admin_users_delete():
        try:
            uid = int(request.form.get("user_id") or "0")
        except Exception:
            flash("Invalid user.", "danger")
            return redirect(url_for("admin_users"))

        with Session(engine) as s:
            u = s.get(User, uid)
            if not u:
                flash("User not found.", "warning")
                return redirect(url_for("admin_users"))
            if u.id == current_user.id:
                flash("You cannot delete your own account.", "warning")
                return redirect(url_for("admin_users"))
            if u.email.lower() == "law@local":
                flash("Master account cannot be deleted.", "warning")
                return redirect(url_for("admin_users"))

            s.delete(u)  # cascades to entries/settings via relationships
            s.commit()

        flash("User deleted.", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/reset_password", endpoint="admin_users_reset_password")
    @admin_required
    def admin_users_reset_password():
        try:
            uid = int(request.form.get("user_id") or "0")
        except Exception:
            flash("Invalid user.", "danger")
            return redirect(url_for("admin_users"))

        new_pw  = (request.form.get("new_password") or "").strip()
        new_pw2 = (request.form.get("new_password2") or "").strip()

        if not new_pw:
            flash("New password is required.", "danger")
            return redirect(url_for("admin_users"))
        if new_pw != new_pw2:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("admin_users"))

        with Session(engine) as s:
            u = s.get(User, uid)
            if not u:
                flash("User not found.", "warning")
                return redirect(url_for("admin_users"))
            u.set_password(new_pw)
            s.commit()

        flash("Password updated.", "success")
        return redirect(url_for("admin_users"))

    # --- Self test ---
    @app.get("/selftest")
    def selftest():
        assert replace_abbreviations("Met with OP re OSC") == "Met with Opposing Counsel re Order to Show Cause"
        return "ok"

    # Print routes so you can see exact endpoint names
    print("\nRegistered routes:")
    for r in app.url_map.iter_rules():
        print(f"  {r.endpoint:20} -> {r.rule}")
    print()
  # --- PWA Routes (add these to your existing app.py) ---
    @app.route('/manifest.json')
    def manifest():
        """Serve PWA manifest."""
        return app.send_static_file('manifest.json')

    # --- Mobile API Routes ---
    @app.route('/api/quick-entry', methods=['POST'])
    @login_required
    def api_quick_entry():
        """Quick entry API for offline sync."""
        data = request.get_json() or {}
        
        client = data.get('client', '').strip() or "(Unspecified)"
        matter = data.get('matter', '').strip() or "(Unspecified)" 
        hours = float(data.get('hours', 0))
        desc = data.get('desc', '').strip()
        date_str = data.get('date_of_work', '')
        timekeeper = data.get('timekeeper', '').strip() or current_user.name
        # Parse date
        try:
            work_date = datetime.fromisoformat(date_str).date() if date_str else date.today()
        except:
            work_date = date.today()
        
        with Session(engine) as s:
            us = get_settings(current_user.id, s)
            if us.auto_expand == 1:
                desc = replace_abbreviations(desc)
                
            s.add(Entry(
                user_id=current_user.id,
                client=client,
                matter=matter,
                date_of_work=work_date,
                hours=hours,
                timekeeper=timekeeper,
                desc=desc
            ))
            s.commit()
            
        return jsonify({'success': True, 'message': 'Entry saved'})

    @app.route('/api/entries-cache', methods=['GET'])
    @login_required
    def api_entries_cache():
        """Get recent entries for offline caching."""
        with Session(engine) as s:
            # Get last 50 entries for offline viewing
            recent_entries = s.scalars(
                select(Entry)
                .where(Entry.user_id == current_user.id)
                .order_by(Entry.date_of_work.desc(), Entry.id.desc())
                .limit(50)
            ).all()
            
            entries_data = []
            for r in recent_entries:
                entries_data.append({
                    'id': r.id,
                    'client': r.client,
                    'matter': r.matter,
                    'date_of_work': r.date_of_work.isoformat(),
                    'hours': r.hours,
                    'timekeeper': r.timekeeper,
                    'desc': r.desc
                })
                
        return jsonify({
            'entries': entries_data,
            'cached_at': datetime.now().isoformat()
        })

    @app.route('/api/user-data', methods=['GET'])
    @login_required
    def api_user_data():
        """Get user's clients/matters for offline autocomplete."""
        with Session(engine) as s:
            clients = [c.name for c in s.scalars(select(ClientName).where(ClientName.user_id == current_user.id).order_by(ClientName.name))]
            matters = [m.name for m in s.scalars(select(MatterName).where(MatterName.user_id == current_user.id).order_by(MatterName.name))]
            
        return jsonify({
            'clients': clients,
            'matters': matters,
            'timekeeper': current_user.name
        })
    
    @app.route('/sw.js')
    def service_worker():
        """Serve service worker."""
        return app.send_static_file('sw.js')

    # --- Mobile API Routes ---
    @app.route('/api/quick-entry', methods=['POST'])
    @login_required
    def api_quick_entry():
        """Quick entry API for mobile - minimal data required."""
        data = request.get_json() or {}
        
        client = data.get('client', '').strip() or "(Unspecified)"
        matter = data.get('matter', '').strip() or "(Unspecified)" 
        hours = float(data.get('hours', 0))
        desc = data.get('desc', '').strip()
        
        with Session(engine) as s:
            us = get_settings(current_user.id, s)
            if us.auto_expand == 1:
                desc = replace_abbreviations(desc)
                
            s.add(Entry(
                user_id=current_user.id,
                client=client,
                matter=matter,
                date_of_work=date.today(),
                hours=hours,
                timekeeper=current_user.name,
                desc=desc
            ))
            s.commit()
            
        return jsonify({'success': True, 'message': 'Entry saved'})

    @app.route('/api/today-summary', methods=['GET'])
    @login_required
    def api_today_summary():
        """Get today's time summary for mobile dashboard."""
        with Session(engine) as s:
            today_entries = s.scalars(
                select(Entry).where(
                    Entry.user_id == current_user.id,
                    Entry.date_of_work == date.today()
                )
            ).all()
            
            total_today = sum(e.hours or 0 for e in today_entries)
            
            # Week summary
            week_start = date.today() - timedelta(days=date.today().weekday())
            week_entries = s.scalars(
                select(Entry).where(
                    Entry.user_id == current_user.id,
                    Entry.date_of_work >= week_start
                )
            ).all()
            
            total_week = sum(e.hours or 0 for e in week_entries)
            
        return jsonify({
            'today': {
                'hours': round(total_today, 2),
                'entries': len(today_entries)
            },
            'week': {
                'hours': round(total_week, 2),
                'entries': len(week_entries)
            }
        })

    @app.route('/api/recent-clients', methods=['GET'])
    @login_required  
    def api_recent_clients():
        """Get recently used clients for mobile autocomplete."""
        with Session(engine) as s:
            recent = s.execute(
                select(Entry.client, func.max(Entry.date_of_work))
                .where(Entry.user_id == current_user.id)
                .group_by(Entry.client)
                .order_by(func.max(Entry.date_of_work).desc())
                .limit(10)
            ).all()
            
        return jsonify([row[0] for row in recent if row[0]])

    # --- Timer Routes ---
    @app.route('/timer')
    @login_required
    def timer_page():
        """Dedicated timer page for mobile users."""
        with Session(engine) as s:
            ensure_default_lists(current_user, s)
            us = get_settings(current_user.id, s)
            us.clients = [c.name for c in s.scalars(select(ClientName).where(ClientName.user_id == current_user.id).order_by(ClientName.name))]
            us.matters = [m.name for m in s.scalars(select(MatterName).where(MatterName.user_id == current_user.id).order_by(MatterName.name))]
            
        return render_template('timer.html', settings=us, date_today=date.today())

    # --- Dashboard Route ---
    @app.route('/dashboard')
    @login_required
    def dashboard():
        """Mobile-friendly dashboard with key metrics."""
        with Session(engine) as s:
            # Today's entries
            today_entries = s.scalars(
                select(Entry).where(
                    Entry.user_id == current_user.id,
                    Entry.date_of_work == date.today()
                )
            ).all()
            
            # This week's entries
            week_start = date.today() - timedelta(days=date.today().weekday())
            week_entries = s.scalars(
                select(Entry).where(
                    Entry.user_id == current_user.id,
                    Entry.date_of_work >= week_start
                )
            ).all()
            
            # Top clients this month
            month_start = date.today().replace(day=1)
            client_hours = s.execute(
                select(Entry.client, func.sum(Entry.hours))
                .where(
                    Entry.user_id == current_user.id,
                    Entry.date_of_work >= month_start
                )
                .group_by(Entry.client)
                .order_by(func.sum(Entry.hours).desc())
                .limit(5)
            ).all()
            
        return render_template('dashboard.html', 
            today_entries=today_entries,
            week_entries=week_entries,
            client_hours=client_hours,
            today_total=sum(e.hours or 0 for e in today_entries),
            week_total=sum(e.hours or 0 for e in week_entries)
        )
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=5050)




