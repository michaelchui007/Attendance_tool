from __future__ import annotations

from datetime import date

from chinese_calendar import get_holiday_detail, is_holiday, is_workday

from .models import DayType


def classify_day(value: date) -> DayType:
    if is_workday(value):
        return DayType.MAKEUP_WORKDAY if value.weekday() >= 5 else DayType.NORMAL_WORKDAY
    if value.weekday() >= 5:
        holiday, name = get_holiday_detail(value)
        if holiday and name:
            return DayType.STATUTORY_HOLIDAY
        return DayType.ORDINARY_WEEKEND
    if is_holiday(value):
        return DayType.STATUTORY_HOLIDAY
    return DayType.NORMAL_WORKDAY
