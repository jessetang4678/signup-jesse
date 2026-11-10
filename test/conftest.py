"""Shared setup for the tests.

The tests run against TEST_DATABASE_URL only. Before the app is imported, every
database variable the app could read is pointed at the test database, so the
production database (DATABASE_URL) can never be used, even by accident.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.exit("TEST_DATABASE_URL is not set in .env.", returncode=1)
for other in ("DATABASE_URL", "DEV_DATABASE_URL"):
    if TEST_DATABASE_URL == os.environ.get(other):
        pytest.exit(
            f"TEST_DATABASE_URL is the same as {other}. The tests empty their "
            "database, so they need a database of their own.",
            returncode=1,
        )

# Locally the app reads DEV_DATABASE_URL; on Cloud Run (K_SERVICE set) it reads
# DATABASE_URL. Point both at the test database.
os.environ.pop("K_SERVICE", None)
os.environ["DEV_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Fixed values so the tests don't depend on what .env says.
os.environ["SITE_NAME"] = "Test Sign-up Site"
os.environ["ADMIN_USERNAME"] = "alice"
SITE_NAME = os.environ["SITE_NAME"]
ADMIN = os.environ["ADMIN_USERNAME"]

# Start from an empty database: drop the tables so importing the app creates
# them fresh, with the current schema.
with psycopg.connect(TEST_DATABASE_URL) as _db:
    _db.execute("DROP TABLE IF EXISTS bookings, bookings_weekly_old, users")

import app as appmod  # noqa: E402  (must come after the environment is set)

PASSWORD = "correct-horse"  # long enough for any sensible minimum


@pytest.fixture(autouse=True)
def empty_database():
    """Every test starts with no users and no bookings."""
    with psycopg.connect(TEST_DATABASE_URL) as db:
        db.execute("TRUNCATE bookings, users RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def app():
    appmod.app.config["TESTING"] = True
    return appmod.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db():
    """A connection to the test database, for checking what the app stored."""
    with psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
        yield conn


def text(response):
    return response.get_data(as_text=True)


def register(client, username, password=PASSWORD):
    return client.post(
        "/register",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def login(client, username, password=PASSWORD):
    client.post("/logout")
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture
def as_user(client):
    """Register a user and log them in: as_user("bob")."""
    def _as_user(username):
        register(client, username)
        response = login(client, username)
        assert f"Welcome, {username}" in text(response)
        return client
    return _as_user


@pytest.fixture
def future_month():
    """First day of a month two months ahead, so none of its slots are past."""
    return (date.today().replace(day=1) + timedelta(days=62)).replace(day=1)


def weekdays_in(first):
    days = [first + timedelta(days=i) for i in range(31)]
    return [d for d in days if d.month == first.month and d.weekday() < 5]
