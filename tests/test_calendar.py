from datetime import date, datetime, timedelta, timezone

import pytest

from attendance_engine.calendar import HolidayCalendar, HolidayDataUnavailableError
from attendance_engine.models import DayType


def payload(year: int) -> dict:
    return {
        "year": year,
        "papers": [f"https://www.gov.cn/{year}"],
        "days": [
            {"name": "元旦", "date": f"{year}-01-02", "isOffDay": True},
            {"name": "元旦调休", "date": f"{year}-01-04", "isOffDay": False},
        ],
    }


def test_online_data_overrides_weekday_and_weekend(tmp_path):
    calendar = HolidayCalendar.for_period(
        date(2027, 1, 1),
        date(2027, 1, 5),
        cache_dir=tmp_path,
        fetcher=lambda year: (payload(year), f"https://example.test/{year}.json"),
        now=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )

    assert calendar.classify_day(date(2027, 1, 2)) is DayType.STATUTORY_HOLIDAY
    assert calendar.classify_day(date(2027, 1, 4)) is DayType.MAKEUP_WORKDAY
    assert calendar.classify_day(date(2027, 1, 5)) is DayType.NORMAL_WORKDAY
    assert calendar.year_sources[0]["source"] == "online"


def test_fresh_cache_avoids_network_request(tmp_path):
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    HolidayCalendar.for_period(
        date(2027, 1, 1),
        date(2027, 1, 5),
        cache_dir=tmp_path,
        fetcher=lambda year: (payload(year), "https://example.test/data.json"),
        now=now,
    )

    def fail_fetch(year: int):
        raise AssertionError(f"fresh cache should avoid fetching {year}")

    cached = HolidayCalendar.for_period(
        date(2027, 1, 1),
        date(2027, 1, 5),
        cache_dir=tmp_path,
        fetcher=fail_fetch,
        now=now + timedelta(hours=1),
    )
    assert cached.year_sources[0]["source"] == "cache"


def test_stale_cache_is_used_when_refresh_fails(tmp_path):
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    HolidayCalendar.for_period(
        date(2027, 1, 1),
        date(2027, 1, 5),
        cache_dir=tmp_path,
        fetcher=lambda year: (payload(year), "https://example.test/data.json"),
        now=now,
    )
    cached = HolidayCalendar.for_period(
        date(2027, 1, 1),
        date(2027, 1, 5),
        cache_dir=tmp_path,
        fetcher=lambda year: (_ for _ in ()).throw(OSError("offline")),
        now=now + timedelta(days=2),
    )
    assert cached.year_sources[0]["source"] == "stale-cache"
    assert cached.classify_day(date(2027, 1, 2)) is DayType.STATUTORY_HOLIDAY


def test_unsupported_future_year_fails_instead_of_guessing(tmp_path):
    with pytest.raises(HolidayDataUnavailableError, match="无法获取 2099 年节假日安排"):
        HolidayCalendar.for_period(
            date(2099, 1, 1),
            date(2099, 1, 2),
            cache_dir=tmp_path,
            allow_online=False,
        )
