import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, os.getcwd())

# Run locally, the app uses TEST_DATABASE_URL, so the smoke test never touches
# the production database (DATABASE_URL).
load_dotenv()
TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]
if TEST_DATABASE_URL == os.environ.get("DATABASE_URL"):
    sys.exit("TEST_DATABASE_URL must differ from DATABASE_URL: the smoke test wipes its tables.")
os.environ.pop("K_SERVICE", None)  # make sure the app picks the local database
# Fixed values so the test doesn't depend on what .env says.
os.environ["SITE_NAME"] = "Test Sign-up Site"
os.environ["ADMIN_USERNAME"] = "alice"

# Start from empty tables so the usernames below are free to register.
with psycopg.connect(TEST_DATABASE_URL) as db:
    db.execute("DROP TABLE IF EXISTS bookings, users")

import app as appmod
appmod.app.config["TESTING"] = True
c = appmod.app.test_client()

def body(r):
    return r.get_data(as_text=True)

r = c.get("/")
assert r.status_code == 200 and "not logged in" in body(r), body(r)
assert "<title>Home - Test Sign-up Site</title>" in body(r), body(r)
assert "<h1>Test Sign-up Site</h1>" in body(r), body(r)

# Someone registers before alice, to show the first user is no longer the admin.
r = c.post("/register", data={"username": "zed", "password": "pw"}, follow_redirects=True)
assert "Account created" in body(r), body(r)

r = c.post("/register", data={"username": "alice", "password": "pw"}, follow_redirects=True)
assert "Account created" in body(r), body(r)

r = c.post("/register", data={"username": "alice", "password": "pw"})
assert "already taken" in body(r), body(r)

r = c.post("/register", data={"username": "", "password": "pw"})
assert "Username is required" in body(r), body(r)

r = c.post("/login", data={"username": "alice", "password": "wrong"})
assert "Incorrect" in body(r), body(r)

r = c.post("/login", data={"username": "alice", "password": "pw"}, follow_redirects=True)
assert "logged in as <strong>alice</strong>" in body(r), body(r)

r = c.post("/logout", follow_redirects=True)
assert "logged out" in body(r) and "not logged in" in body(r), body(r)

r = c.post("/logout", follow_redirects=True)
assert "Please log in first" in body(r), body(r)

# Password must be hashed, not stored in plain text.
with psycopg.connect(TEST_DATABASE_URL) as db:
    row = db.execute("SELECT password_hash FROM users").fetchone()
assert row[0] != "pw" and row[0].startswith(("scrypt:", "pbkdf2:")), row

# --- Sign-up sheet ---------------------------------------------------------

from datetime import date, timedelta

def login(username):
    c.post("/logout")
    r = c.post("/login", data={"username": username, "password": "pw"}, follow_redirects=True)
    assert f"Welcome, {username}" in body(r), body(r)

def table(r):
    return body(r).split("<table")[1]

def free_count(r):
    return body(r).count('class="chip free"')

# Test in a month two months ahead, so none of its slots are in the past.
first = (date.today().replace(day=1) + timedelta(days=62)).replace(day=1)
MONTH = first.strftime("%Y-%m")
days_in_month = [first + timedelta(days=i) for i in range(31) if (first + timedelta(days=i)).month == first.month]
weekdays = [d for d in days_in_month if d.weekday() < 5]
saturday = next(d for d in days_in_month if d.weekday() == 5)
day = weekdays[2]
DAY = day.isoformat()
DAY_LABEL = f"{day:%A}, {day:%B} {day.day}"     # e.g. "Wednesday, December 9"

# The sheet needs a login.
r = c.get("/sheet", follow_redirects=True)
assert "Please log in first" in body(r), body(r)

c.post("/register", data={"username": "bob", "password": "pw"})

# alice is ADMIN_USERNAME, so she is the administrator (even though zed registered first).
login("alice")
r = c.get(f"/sheet?month={MONTH}")
assert r.status_code == 200 and "You are the administrator" in body(r), body(r)
assert f"<h2>{first:%B %Y}</h2>" in body(r), body(r)
assert body(r).count("<th>") == 5, body(r)                  # Monday to Friday
assert free_count(r) == 4 * len(weekdays), body(r)          # every slot of every weekday free
assert "2:00 pm" in body(r) and "3:30 pm" in body(r), body(r)

# Without a month (or with a bad one) the sheet shows the current month.
this_month = date.today().strftime("%B %Y")
for url in ("/sheet", "/sheet?month=nonsense", "/sheet?month=9999-12"):
    r = c.get(url)
    assert r.status_code == 200 and f"<h2>{this_month}</h2>" in body(r), (url, body(r))

# zed registered first but is not ADMIN_USERNAME, so zed is not the administrator.
login("zed")
r = c.get(f"/sheet?month={MONTH}")
assert "You are the administrator" not in body(r), body(r)

# bob is not the administrator; he books a slot.
login("bob")
r = c.get(f"/sheet?month={MONTH}")
assert "You are the administrator" not in body(r), body(r)
r = c.post("/book", data={"day": DAY, "start": "14:30"}, follow_redirects=True)
assert f"Booked {DAY_LABEL} at 2:30 pm" in body(r), body(r)
assert f"<h2>{first:%B %Y}</h2>" in body(r), body(r)       # back on that month
assert "2:30 pm &middot; Yours" in body(r) and 'class="chip mine"' in body(r), body(r)
assert free_count(r) == 4 * len(weekdays) - 1, body(r)

# Booking the same slot twice, or a slot that doesn't exist or is past, fails.
r = c.post("/book", data={"day": DAY, "start": "14:30"}, follow_redirects=True)
assert "already been taken" in body(r), body(r)
for bad in ({"day": saturday.isoformat(), "start": "14:30"},   # weekend
            {"day": DAY, "start": "16:00"},                   # not a slot time
            {"day": "2026-02-30", "start": "14:30"},          # not a date
            {"day": "", "start": "14:30"}):
    r = c.post("/book", data=bad, follow_redirects=True)
    assert "not a valid slot" in body(r), (bad, body(r))
r = c.post("/book", data={"day": "2020-01-06", "start": "14:30"}, follow_redirects=True)
assert "in the past" in body(r), body(r)

# A third user sees the slot as taken, without the name, and cannot book or cancel it.
c.post("/register", data={"username": "carol", "password": "pw"})
login("carol")
r = c.get(f"/sheet?month={MONTH}")
assert "2:30 pm &middot; Taken" in table(r) and "bob" not in table(r), body(r)
r = c.post("/book", data={"day": DAY, "start": "14:30"}, follow_redirects=True)
assert "already been taken" in body(r), body(r)
r = c.post("/cancel", data={"day": DAY, "start": "14:30"}, follow_redirects=True)
assert "no booking in that slot" in body(r) and "&middot; Taken" in table(r), body(r)

# The administrator sees who booked it.
login("alice")
r = c.get(f"/sheet?month={MONTH}")
assert "2:30 pm &middot; <strong>bob</strong>" in table(r), body(r)

# bob cancels his own booking and the slot is free again.
login("bob")
r = c.post("/cancel", data={"day": DAY, "start": "14:30"}, follow_redirects=True)
assert f"Cancelled {DAY_LABEL} at 2:30 pm" in body(r), body(r)
assert free_count(r) == 4 * len(weekdays), body(r)

print("ALL SMOKE TESTS PASSED")
