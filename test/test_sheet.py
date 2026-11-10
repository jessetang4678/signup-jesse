"""The sign-up sheet: the month calendar, booking, cancelling, and the admin."""
from datetime import date

import pytest

from conftest import ADMIN, register, text, weekdays_in


def sheet(client, first):
    return client.get(f"/sheet?month={first:%Y-%m}")


def free_slots(response):
    return text(response).count('class="chip free"')


def table(response):
    return text(response).split("<table")[1]


def book(client, day, start="14:30"):
    return client.post("/book", data={"day": day.isoformat(), "start": start}, follow_redirects=True)


def cancel(client, day, start="14:30"):
    return client.post("/cancel", data={"day": day.isoformat(), "start": start}, follow_redirects=True)


def label(day):
    return f"{day:%A}, {day:%B} {day.day}"  # e.g. "Wednesday, December 9"


@pytest.fixture
def day(future_month):
    """A weekday in the future month."""
    return weekdays_in(future_month)[2]


# --- Viewing the calendar ----------------------------------------------------

def test_sheet_requires_login(client):
    response = client.get("/sheet", follow_redirects=True)
    assert "Please log in first" in text(response)


def test_month_shows_weekdays_and_all_slots(as_user, future_month):
    response = sheet(as_user("bob"), future_month)
    assert response.status_code == 200
    assert f"<h2>{future_month:%B %Y}</h2>" in text(response)
    for name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        assert f"<th>{name}</th>" in text(response)
    assert "<th>Saturday</th>" not in text(response)
    assert free_slots(response) == 4 * len(weekdays_in(future_month))
    for time in ("2:00 pm", "2:30 pm", "3:00 pm", "3:30 pm"):
        assert time in text(response)


@pytest.mark.parametrize("query", ["", "?month=nonsense", "?month=2026-13", "?month=9999-12"])
def test_missing_or_bad_month_shows_current_month(as_user, query):
    response = as_user("bob").get(f"/sheet{query}")
    assert response.status_code == 200
    assert f"<h2>{date.today():%B %Y}</h2>" in text(response)


def test_month_links_go_to_previous_and_next_month(as_user):
    response = as_user("bob").get("/sheet?month=2027-01")
    assert "/sheet?month=2026-12" in text(response)
    assert "/sheet?month=2027-02" in text(response)


def test_past_slots_cannot_be_booked(as_user):
    response = as_user("bob").get("/sheet?month=2020-01")
    assert free_slots(response) == 0
    assert 'class="chip free past"' in text(response)


# --- Booking and cancelling --------------------------------------------------

def test_book_a_slot(as_user, future_month, day):
    client = as_user("bob")
    response = book(client, day)
    assert f"Booked {label(day)} at 2:30 pm" in text(response)
    assert f"<h2>{future_month:%B %Y}</h2>" in text(response)  # back on that month
    assert "2:30 pm &middot; Yours" in text(response)
    assert free_slots(response) == 4 * len(weekdays_in(future_month)) - 1


def test_booking_is_stored(as_user, db, day):
    book(as_user("bob"), day)
    row = db.execute(
        "SELECT b.day, b.start, u.username FROM bookings b JOIN users u ON u.id = b.user_id"
    ).fetchone()
    assert row == {"day": day, "start": "14:30", "username": "bob"}


def test_slot_cannot_be_booked_twice(as_user, day):
    client = as_user("bob")
    book(client, day)
    response = book(client, day)
    assert "already been taken" in text(response)


def test_slot_booked_by_someone_else_cannot_be_booked(client, as_user, day):
    book(as_user("bob"), day)
    response = book(as_user("carol"), day)
    assert "already been taken" in text(response)


@pytest.mark.parametrize(
    "form",
    [
        {"day": "2027-03-06", "start": "14:30"},  # a Saturday
        {"day": "2027-03-07", "start": "14:30"},  # a Sunday
        {"day": "2027-03-08", "start": "16:00"},  # not a slot time
        {"day": "2027-03-08", "start": "14:15"},  # not a slot time
        {"day": "2027-02-30", "start": "14:30"},  # not a date
        {"day": "", "start": "14:30"},
        {"day": "2027-03-08"},
    ],
)
def test_invalid_slots_are_rejected(as_user, form):
    client = as_user("bob")
    for url in ("/book", "/cancel"):
        response = client.post(url, data=form, follow_redirects=True)
        assert "not a valid slot" in text(response), (url, form)


def test_past_slot_is_rejected(as_user):
    response = book(as_user("bob"), date(2020, 1, 6))  # a Monday in the past
    assert "in the past" in text(response)


def test_booking_requires_login(client, day):
    response = book(client, day)
    assert "Please log in first" in text(response)


def test_cancel_own_booking(as_user, future_month, day):
    client = as_user("bob")
    book(client, day)
    response = cancel(client, day)
    assert f"Cancelled {label(day)} at 2:30 pm" in text(response)
    assert free_slots(response) == 4 * len(weekdays_in(future_month))


def test_cannot_cancel_someone_elses_booking(as_user, db, day):
    book(as_user("bob"), day)
    response = cancel(as_user("carol"), day)
    assert "no booking in that slot" in text(response)
    assert db.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"] == 1


# --- Privacy and the administrator ------------------------------------------

def test_other_users_see_taken_without_the_name(as_user, future_month, day):
    book(as_user("bob"), day)
    response = sheet(as_user("carol"), future_month)
    assert "2:30 pm &middot; Taken" in table(response)
    assert "bob" not in table(response)


def test_admin_is_the_user_named_in_admin_username(client, as_user, future_month):
    register(client, "zed")  # registers first, but is not the admin
    response = sheet(as_user(ADMIN), future_month)
    assert "You are the administrator" in text(response)


def test_first_user_is_not_admin(as_user, future_month):
    response = sheet(as_user("zed"), future_month)
    assert "You are the administrator" not in text(response)


def test_admin_sees_who_booked(as_user, future_month, day):
    book(as_user("bob"), day)
    response = sheet(as_user(ADMIN), future_month)
    assert "2:30 pm &middot; <strong>bob</strong>" in table(response)
