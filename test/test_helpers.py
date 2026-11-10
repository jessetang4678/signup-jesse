"""The small helper functions in app.py, and the test setup itself."""
from datetime import date

import pytest

import app as appmod
from conftest import TEST_DATABASE_URL


def test_app_uses_the_test_database():
    assert appmod.DATABASE_URL == TEST_DATABASE_URL


@pytest.mark.parametrize(
    "hhmm, expected",
    [("14:00", "2:00 pm"), ("15:30", "3:30 pm"), ("12:00", "12:00 pm"), ("09:05", "9:05 am"), ("00:00", "12:00 am")],
)
def test_format_time(hhmm, expected):
    assert appmod.format_time(hhmm) == expected


def test_format_day():
    assert appmod.format_day(date(2026, 12, 9)) == "Wednesday, December 9"


def test_parse_slot_accepts_a_real_slot():
    assert appmod.parse_slot({"day": "2026-12-07", "start": "15:00"}) == (date(2026, 12, 7), "15:00")


@pytest.mark.parametrize(
    "form",
    [
        {"day": "2026-12-05", "start": "15:00"},  # Saturday
        {"day": "2026-12-07", "start": "16:00"},
        {"day": "07/12/2026", "start": "15:00"},
        {"day": "", "start": ""},
        {},
    ],
)
def test_parse_slot_rejects_bad_input(form):
    assert appmod.parse_slot(form) is None


def test_parse_month():
    assert appmod.parse_month("2027-02") == date(2027, 2, 1)


@pytest.mark.parametrize("value", [None, "", "2027", "2027-13", "abc", "1999-12", "2101-01"])
def test_parse_month_falls_back_to_this_month(value):
    assert appmod.parse_month(value) == appmod.now_local().date().replace(day=1)


def test_slot_is_past():
    assert appmod.slot_is_past(date(2020, 1, 6), "14:00")
    assert not appmod.slot_is_past(date(2099, 1, 5), "14:00")
