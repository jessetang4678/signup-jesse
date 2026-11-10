import calendar
import os
from datetime import date, datetime, time, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
# Cloud Run sets K_SERVICE on every service. There, use the production database,
# whose URL Cloud Run reads from Secret Manager. Anywhere else (your laptop), use
# the development database from .env.
ON_CLOUD_RUN = "K_SERVICE" in os.environ
DATABASE_URL = os.environ["DATABASE_URL" if ON_CLOUD_RUN else "TEST_DATABASE_URL"]
SITE_NAME = os.environ["SITE_NAME"]
ADMIN_USERNAME = os.environ["ADMIN_USERNAME"]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
app.jinja_env.globals["site_name"] = SITE_NAME


# --- Database helpers -------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with psycopg.connect(DATABASE_URL) as db:
        # Bookings used to be weekly ("every Monday at 2:00"); now they are for a
        # date. Weekly ones can't be turned into dates, so move the old table aside
        # (keeping its rows) and let a new one be created below.
        old = db.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'bookings' AND column_name = 'weekday'
            """
        ).fetchone()
        if old:
            db.execute("ALTER TABLE bookings RENAME TO bookings_weekly_old")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                day DATE NOT NULL,
                start TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                UNIQUE (day, start)
            )
            """
        )


# --- Sign-up sheet ----------------------------------------------------------

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
# Half-hour slots from 2:00 pm to 4:00 pm, stored as 24-hour "HH:MM" strings.
SLOT_TIMES = ["14:00", "14:30", "15:00", "15:30"]
# The time zone office hours are held in. It decides which slots are in the past.
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "America/Los_Angeles"))


def format_time(hhmm):
    hour, minute = (int(p) for p in hhmm.split(":"))
    suffix = "pm" if hour >= 12 else "am"
    return f"{(hour - 1) % 12 + 1}:{minute:02d} {suffix}"


app.jinja_env.filters["ampm"] = format_time


def format_day(day):
    """E.g. "Wednesday, December 9"."""
    return f"{DAYS[day.weekday()]}, {day:%B} {day.day}"


def now_local():
    return datetime.now(TIMEZONE)


def slot_is_past(day, start):
    """True once a slot has started, so it can no longer be booked or cancelled."""
    hour, minute = (int(p) for p in start.split(":"))
    return datetime.combine(day, time(hour, minute), TIMEZONE) <= now_local()


def parse_slot(form):
    """Return (day, start) from a form, or None if it isn't a real slot."""
    try:
        day = date.fromisoformat(form.get("day", ""))
    except ValueError:
        return None
    start = form.get("start", "")
    if day.weekday() >= len(DAYS) or start not in SLOT_TIMES:
        return None
    return day, start


def parse_month(value):
    """Return the first day of a "YYYY-MM" month, or of this month if it isn't one."""
    try:
        first = datetime.strptime(value or "", "%Y-%m").date()
    except ValueError:
        first = None
    if first is None or not 2000 <= first.year <= 2100:
        first = now_local().date().replace(day=1)
    return first


def is_admin(user):
    """The administrator is the user named by ADMIN_USERNAME in .env."""
    return user is not None and user["username"] == ADMIN_USERNAME


# --- Auth helpers -----------------------------------------------------------

@app.before_request
def load_current_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            "SELECT id, username FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if g.user is None:
            session.clear()
    g.is_admin = is_admin(g.user)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


# --- Routes -----------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username:
            flash("Username is required.")
        elif not password:
            flash("Password is required.")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except psycopg.errors.UniqueViolation:
                db.rollback()
                flash(f"Username '{username}' is already taken.")
            else:
                flash("Account created. You can log in now.")
                return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = get_db().execute(
            "SELECT * FROM users WHERE username = %s", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome, {user['username']}!")
            return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("index"))


@app.route("/sheet")
@login_required
def sheet():
    first = parse_month(request.args.get("month"))
    # Whole weeks, Monday to Sunday, covering the month.
    weeks = calendar.Calendar().monthdatescalendar(first.year, first.month)

    rows = get_db().execute(
        """
        SELECT b.day, b.start, b.user_id, u.username
        FROM bookings b JOIN users u ON u.id = b.user_id
        WHERE b.day BETWEEN %s AND %s
        """,
        (weeks[0][0], weeks[-1][-1]),
    ).fetchall()
    bookings = {(r["day"], r["start"]): r for r in rows}
    today = now_local().date()

    # One row per week, one cell per weekday (None for days outside the month),
    # and in each cell the day's slots: free, yours, or taken.
    grid = []
    for week in weeks:
        cells = []
        for day in week[: len(DAYS)]:
            if day.month != first.month:
                cells.append(None)
                continue
            slots = []
            for start in SLOT_TIMES:
                booking = bookings.get((day, start))
                if booking is None:
                    status = "free"
                elif booking["user_id"] == g.user["id"]:
                    status = "mine"
                else:
                    status = "taken"
                slots.append(
                    {
                        "start": start,
                        "status": status,
                        "past": slot_is_past(day, start),
                        "username": booking["username"] if booking else None,
                    }
                )
            cells.append({"date": day, "today": day == today, "slots": slots})
        if any(cells):
            grid.append(cells)

    return render_template(
        "sheet.html",
        days=DAYS,
        grid=grid,
        month=first,
        prev_month=(first - timedelta(days=1)).replace(day=1),
        next_month=(first + timedelta(days=31)).replace(day=1),
    )


def back_to_month(day):
    return redirect(url_for("sheet", month=day.strftime("%Y-%m")))


@app.route("/book", methods=["POST"])
@login_required
def book():
    slot = parse_slot(request.form)
    if slot is None:
        flash("That is not a valid slot.")
        return redirect(url_for("sheet"))
    day, start = slot
    if slot_is_past(day, start):
        flash("That slot is in the past.")
        return back_to_month(day)

    db = get_db()
    try:
        db.execute(
            "INSERT INTO bookings (day, start, user_id) VALUES (%s, %s, %s)",
            (day, start, g.user["id"]),
        )
        db.commit()
    except psycopg.errors.UniqueViolation:
        db.rollback()
        flash("Sorry, that slot has already been taken.")
    else:
        flash(f"Booked {format_day(day)} at {format_time(start)}.")
    return back_to_month(day)


@app.route("/cancel", methods=["POST"])
@login_required
def cancel():
    slot = parse_slot(request.form)
    if slot is None:
        flash("That is not a valid slot.")
        return redirect(url_for("sheet"))
    day, start = slot
    if slot_is_past(day, start):
        flash("That slot is in the past.")
        return back_to_month(day)

    db = get_db()
    # Only the person who booked a slot may cancel it.
    deleted = db.execute(
        "DELETE FROM bookings WHERE day = %s AND start = %s AND user_id = %s",
        (day, start, g.user["id"]),
    ).rowcount
    db.commit()
    if deleted:
        flash(f"Cancelled {format_day(day)} at {format_time(start)}.")
    else:
        flash("You have no booking in that slot.")
    return back_to_month(day)


init_db()

if __name__ == "__main__":
    app.run(debug=True)
