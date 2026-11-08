from datetime import date, datetime
from pathlib import Path

import pytest

from attendance_engine.calculator import calculate_attendance
from attendance_engine.models import (
    AttendanceSource,
    CalculationStatus,
    CompensatoryLeaveRecord,
    PunchRecord,
    PunchType,
)


def record(day: int, hour: int, minute: int, punch_type: PunchType, location: str = "") -> PunchRecord:
    timestamp = datetime(2026, 7, day, hour, minute)
    return PunchRecord("测试员工", "tester", timestamp, timestamp.date(), punch_type, location)


def source(records: list[PunchRecord], start: date, end: date) -> AttendanceSource:
    return AttendanceSource(Path("input.xlsx"), "测试员工", "tester", start, end, records)


def test_location_benefit_is_removed_without_changing_effective_work():
    """指定地点满勤不再额外增加半小时加班。"""
    records = [
        record(1, 9, 0, PunchType.CHECK_IN, "弘快科技 | 上海市普陀区云岭东路586号"),
        record(1, 18, 30, PunchType.CHECK_OUT),
    ]
    report = calculate_attendance(source(records, date(2026, 7, 1), date(2026, 7, 1)))
    day = report.days[0]
    assert day.required_standard_minutes == 480
    assert day.lunch_deduction_minutes == 90
    assert day.location_benefit_minutes == 0
    assert day.actual_effective_minutes == 480
    assert day.extra_minutes == 0


def test_overtime_uses_actual_work_without_location_benefit():
    """超过八小时的实际工时计加班，地点不再增加额度。"""
    records = [
        record(1, 9, 0, PunchType.CHECK_IN, "云岭东路586号"),
        record(1, 19, 0, PunchType.CHECK_OUT),
    ]
    day = calculate_attendance(source(records, date(2026, 7, 1), date(2026, 7, 1))).days[0]
    assert day.actual_effective_minutes == 510
    assert day.extra_minutes == 30


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


def test_compensatory_leave_is_deducted_from_net_overtime_even_without_punches():
    attendance = source([], date(2026, 7, 1), date(2026, 7, 1))
    attendance.compensatory_leave_records.append(
        CompensatoryLeaveRecord(
            work_date=date(2026, 7, 1),
            minutes=480,
            description="调休假8.0小时（7/1 09:00 - 7/1 18:30）",
            source_sheet="概况统计与打卡明细",
            source_row=5,
        )
    )

    report = calculate_attendance(attendance)
    day = report.days[0]

    assert day.extra_minutes is None
    assert day.compensatory_leave_minutes == 480
    assert day.net_extra_minutes == -480
    assert report.extra_minutes == 0
    assert report.compensatory_leave_minutes == 480
    assert report.net_extra_minutes == -480
    assert report.to_dict()["compensatoryLeaves"] == [
        {
            "workDate": "2026-07-01",
            "minutes": 480,
            "description": "调休假8.0小时（7/1 09:00 - 7/1 18:30）",
            "sourceSheet": "概况统计与打卡明细",
            "sourceRow": 5,
            "startAt": None,
            "endAt": None,
            "parseWarning": "",
            "itemIndex": 0,
        }
    ]


def test_manual_override_recalculates_without_changing_source():
    """补卡重算后来源记录保持原样，并保留修订前的缺失值。"""
    attendance = source([record(1, 9, 0, PunchType.CHECK_IN)], date(2026, 7, 1), date(2026, 7, 1))
    report = calculate_attendance(attendance, overrides=[{
        "workDate": "2026-07-01", "firstCheckIn": "2026-07-01T09:00",
        "lastCheckOut": "2026-07-01T18:30",
    }])
    day = report.days[0]
    assert day.status is CalculationStatus.SUCCESS
    assert day.actual_effective_minutes == 480
    assert day.is_edited
    assert day.original_last_check_out is None
    assert len(attendance.records) == 1
    restored = calculate_attendance(attendance).days[0]
    assert restored.status is CalculationStatus.MISSING_CHECK_OUT


@pytest.mark.parametrize("last_check_out", ["2026-07-02T04:00", "2026-07-01T08:00", "2026-07-01T18:30+08:00"])
def test_manual_override_rejects_other_workday_or_invalid_range(last_check_out):
    """禁止通过手动覆盖跨考勤日、倒置时间或转换报表时区。"""
    attendance = source([], date(2026, 7, 1), date(2026, 7, 1))
    with pytest.raises(ValueError):
        calculate_attendance(attendance, overrides=[{
            "workDate": "2026-07-01", "firstCheckIn": "2026-07-01T09:00",
            "lastCheckOut": last_check_out,
        }])


def test_manual_override_allows_next_day_0359():
    """次日03:59仍属于前一天，可作为该日的下班修订。"""
    attendance = source([], date(2026, 7, 1), date(2026, 7, 1))
    day = calculate_attendance(attendance, overrides=[{
        "workDate": "2026-07-01", "firstCheckIn": "2026-07-01T09:00",
        "lastCheckOut": "2026-07-02T03:59",
    }]).days[0]
    assert day.raw_duration_minutes == 1139
    assert day.actual_effective_minutes == 1049


def test_lunch_configuration_changes_work_and_leave_schedule_together():
    """一小时午休同步调整实际工时扣除及八小时请假分摊时段。"""
    attendance = source([record(1, 9, 0, PunchType.CHECK_IN), record(1, 18, 0, PunchType.CHECK_OUT)], date(2026, 7, 1), date(2026, 7, 1))
    attendance.compensatory_leave_records.append(CompensatoryLeaveRecord(
        date(2026, 7, 1), 60, "调休假1小时", "测试",
        start_at=datetime(2026, 7, 1, 13, 0), end_at=datetime(2026, 7, 1, 14, 0),
    ))
    old = calculate_attendance(attendance)
    new = calculate_attendance(attendance, lunch_start="12:00", lunch_end="13:00")
    assert old.days[0].actual_effective_minutes == 450
    assert old.days[0].leave_requested_minutes == 30
    assert new.days[0].actual_effective_minutes == 480
    assert new.days[0].leave_requested_minutes == 60
    assert new.days[0].compensatory_leave_minutes == 0
    assert new.days[0].requires_review
    assert new.rules["workdayEnd"] == "18:00"


def test_unknown_cross_day_leave_does_not_become_zero_balance():
    """跨日重复总量缺少区间时，日扣减及汇总净额保持未知。"""
    attendance = source([], date(2026, 7, 1), date(2026, 7, 2))
    attendance.compensatory_leave_records = [
        CompensatoryLeaveRecord(date(2026, 7, day), 480, "调休假8小时", "测试")
        for day in (1, 2)
    ]
    report = calculate_attendance(attendance)
    assert all(day.compensatory_leave_minutes is None for day in report.days)
    assert report.net_extra_minutes is None
    assert report.to_dict()["summary"]["requiresReview"]


def test_export_keeps_unknowns_and_manual_revision_audit(tmp_path, monkeypatch):
    """导出区分原始值与修订值，并保留未知扣减而不是写入零。"""
    from openpyxl import load_workbook
    from attendance_engine import exporter
    from attendance_engine.exporter import export_report

    class FixedDatetime(datetime):
        """固定导出时刻，验证快速连续导出也不会覆盖历史文件。"""

        @classmethod
        def now(cls, tz=None):
            """返回固定本地时刻以制造同名候选文件。"""
            return cls(2026, 7, 1, 18, 30, 0)

    monkeypatch.setattr(exporter, "datetime", FixedDatetime)

    attendance = source([], date(2026, 7, 1), date(2026, 7, 1))
    attendance.compensatory_leave_records = [CompensatoryLeaveRecord(
        date(2026, 7, 1), 600, "调休假10小时", "测试",
    )]
    report = calculate_attendance(attendance, overrides=[{
        "workDate": "2026-07-01", "firstCheckIn": "2026-07-01T09:00",
        "lastCheckOut": "2026-07-01T18:30",
    }])
    first = export_report(report, tmp_path)
    first_bytes = first.read_bytes()
    second = export_report(report, tmp_path)
    third = export_report(report, tmp_path)
    assert len({first, second, third}) == 3
    assert first.read_bytes() == first_bytes
    workbook = load_workbook(first, data_only=True)
    assert workbook["每日明细"]["N2"].value is None
    assert workbook["每日明细"]["O2"].value is None
    assert workbook["每日明细"]["R2"].value is None
    assert workbook["每日明细"]["T2"].value == "是"
    assert workbook["计算规则"]["B3"].value == "04:00"
    assert workbook["月度汇总"]["H4"].value == "未计算"
    workbook.close()
