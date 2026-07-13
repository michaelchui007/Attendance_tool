from datetime import date, datetime
from pathlib import Path

from attendance_engine.calculator import calculate_attendance
from attendance_engine.models import AttendanceSource, CalculationStatus, PunchRecord, PunchType


def record(day: int, hour: int, minute: int, punch_type: PunchType, location: str = "") -> PunchRecord:
    timestamp = datetime(2026, 7, day, hour, minute)
    return PunchRecord("测试员工", "tester", timestamp, timestamp.date(), punch_type, location)


def source(records: list[PunchRecord], start: date, end: date) -> AttendanceSource:
    return AttendanceSource(Path("input.xlsx"), "测试员工", "tester", start, end, records)


def test_workday_lunch_and_location_benefit():
    records = [
        record(1, 9, 0, PunchType.CHECK_IN, "弘快科技 | 上海市普陀区云岭东路586号"),
        record(1, 19, 0, PunchType.CHECK_OUT),
    ]
    report = calculate_attendance(source(records, date(2026, 7, 1), date(2026, 7, 1)))
    day = report.days[0]
    assert day.required_standard_minutes == 480
    assert day.lunch_deduction_minutes == 90
    assert day.location_benefit_minutes == 30
    assert day.actual_effective_minutes == 480
    assert day.extra_minutes == 0


def test_weekend_has_no_lunch_or_location_benefit():
    records = [
        record(5, 10, 0, PunchType.CHECK_IN, "云岭东路586号"),
        record(5, 16, 0, PunchType.CHECK_OUT),
    ]
    day = calculate_attendance(source(records, date(2026, 7, 5), date(2026, 7, 5))).days[0]
    assert day.required_standard_minutes == 0
    assert day.actual_effective_minutes == 360
    assert day.extra_minutes == 360
    assert day.lunch_deduction_minutes == 0
    assert day.location_benefit_minutes == 0


def test_missing_workday_keeps_required_standard_hours():
    report = calculate_attendance(source([], date(2026, 7, 1), date(2026, 7, 1)))
    day = report.days[0]
    assert day.status is CalculationStatus.MISSING_BOTH
    assert day.required_standard_minutes == 480
    assert day.actual_effective_minutes is None
    assert day.extra_minutes is None


def test_missing_weekend_does_not_add_required_standard_hours():
    report = calculate_attendance(source([], date(2026, 7, 4), date(2026, 7, 4)))
    day = report.days[0]
    assert day.required_standard_minutes == 0
    assert day.actual_effective_minutes is None


def test_earliest_check_in_and_latest_check_out_are_used():
    records = [
        record(2, 9, 5, PunchType.CHECK_IN),
        record(2, 8, 50, PunchType.CHECK_IN),
        record(2, 18, 0, PunchType.CHECK_OUT),
        record(2, 19, 20, PunchType.CHECK_OUT),
    ]
    day = calculate_attendance(source(records, date(2026, 7, 2), date(2026, 7, 2))).days[0]
    assert day.first_check_in == datetime(2026, 7, 2, 8, 50)
    assert day.last_check_out == datetime(2026, 7, 2, 19, 20)
