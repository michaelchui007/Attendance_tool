"""验证真实报表脱敏场景及由其扩展的关键日期边界。

样例只保留用于规则验证的日期、时间与假勤结构；不依赖用户原始文件。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile
import re

import pytest
from openpyxl import Workbook

from attendance_engine.calculator import calculate_attendance
from attendance_engine.calendar import HolidayCalendar, HolidayOverride
from attendance_engine.models import CalculationStatus, DayType
from attendance_engine.parser import parse_attendance_workbook


def _workbook(
    tmp_path: Path,
    start: date,
    end: date,
    punches: list[tuple[date, str, str, str, str]],
    summary_leaves: list[tuple[date, str]] | None = None,
    *,
    omit_dimensions: bool = False,
) -> Path:
    """按实际列位置生成最小脱敏工作簿。

    Args:
        tmp_path: pytest 提供的临时目录。
        start: 报表统计起始日期。
        end: 报表统计结束日期。
        punches: 日期、打卡类型、时间、状态、假勤描述组成的记录。
        summary_leaves: 概况表中的日期和假勤描述。
        omit_dimensions: 是否模拟真实文件缺少工作表维度的情况。

    Returns:
        仅供测试读取的临时工作簿路径。
    """
    path = tmp_path / "脱敏 考勤样例.xlsx"
    workbook = Workbook()
    summary = workbook.active
    summary.title = "概况统计与打卡明细"
    detail = workbook.create_sheet("打卡详情")
    detail["A2"] = (
        f"统计时间:{start:%m-%d} ～ {end:%m-%d}     "
        f"制表时间:{end:%Y-%m-%d} 18:30(UTC+8)"
    )
    for row_number, (work_date, kind, stamp, status, leave) in enumerate(punches, start=5):
        values = [
            work_date.strftime("%Y/%m/%d"), "测试员工", "tester",
            None, None, None, kind, None, stamp, status, "测试办公地点",
            None, None, None, leave,
        ]
        for column, value in enumerate(values, start=1):
            detail.cell(row_number, column, value)
    for row_number, (work_date, leave) in enumerate(summary_leaves or [], start=5):
        summary.cell(row_number, 1, work_date.strftime("%Y/%m/%d"))
        summary.cell(row_number, 2, "测试员工")
        summary.cell(row_number, 3, "tester")
        summary.cell(row_number, 14, leave)
    workbook.save(path)
    workbook.close()
    if omit_dimensions:
        # 部分真实导出文件没有 dimension，解析器仍需读取所有数据行。
        with ZipFile(path) as archive:
            entries = {item.filename: archive.read(item) for item in archive.infolist()}
        with ZipFile(path, "w") as archive:
            for name, content in entries.items():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    content = re.sub(rb"<dimension[^>]*/>", b"", content)
                archive.writestr(name, content)
    return path


def _calendar() -> HolidayCalendar:
    """提供固定周历，避免测试读取网络或机器中的节假日缓存。"""
    return HolidayCalendar(online_years={2026, 2027})


def _pair(work_date: date, start: str, end: str, leave: str):
    """生成同一申请在上下班行重复展示的脱敏记录。"""
    return [
        (work_date, "上班", start, "正常", leave),
        (work_date, "下班", end, "正常", leave),
    ]


@pytest.mark.parametrize(
    ("work_date", "start", "end", "leave", "effective", "requested", "deducted"),
    [
        (date(2026, 9, 2), "07:52", "12:01", "调休假5.0小时（9/2 12:00 - 9/2 18:30）", 248, 300, 232),
        (date(2026, 10, 9), "14:02", "18:35", "调休假4.0小时（10/9 09:00 - 10/9 14:30）", 273, 240, 207),
        (date(2026, 4, 10), "09:33", "15:03", "调休假3.5小时（4/10 15:00 - 4/10 18:30）", 240, 210, 210),
    ],
    ids=["september-afternoon-leave", "october-morning-leave", "april-short-leave"],
)
def test_real_partial_leave_uses_minutes_and_caps_deduction(
    tmp_path, work_date, start, end, leave, effective, requested, deducted
):
    """真实半天申请按分钟扣午休、跨表去重，并受剩余标准工时限制。"""
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, start, end, leave), [(work_date, leave)])
    source = parse_attendance_workbook(path)
    report = calculate_attendance(source, _calendar())
    day = report.days[0]

    assert len(source.compensatory_leave_records) == 1
    assert day.actual_effective_minutes == effective
    assert day.leave_requested_minutes == requested
    assert day.compensatory_leave_minutes == deducted
    assert day.net_extra_minutes == -deducted
    assert report.net_extra_minutes == -deducted
    assert day.leave_records[0].description == leave


def test_real_cross_weekend_total_is_not_repeated_per_day(tmp_path):
    """四月跨周末整次八小时申请应分为五小时和三小时。"""
    start, end = date(2026, 4, 24), date(2026, 4, 27)
    leave = "调休假8.0小时（4/24 12:00 - 4/27 13:30）"
    punches = [
        *_pair(end, "15:18", "17:24", leave),
        *_pair(date(2026, 4, 26), "--", "--", leave),
        *_pair(start, "08:36", "11:38", leave),
    ]
    summary = [(date(2026, 4, day), leave) for day in (27, 26, 25, 24)]
    path = _workbook(tmp_path, start, end, punches, summary, omit_dimensions=True)
    source = parse_attendance_workbook(path)
    report = calculate_attendance(source, _calendar())

    assert len(source.compensatory_leave_records) == 4
    assert {record.minutes for record in source.compensatory_leave_records} == {480}
    assert [day.leave_requested_minutes for day in report.days] == [300, 0, 0, 180]
    assert [day.compensatory_leave_minutes for day in report.days] == [298, 0, 0, 180]
    assert report.compensatory_leave_minutes == 478
    assert report.net_extra_minutes == -478
    assert sum(day.net_extra_minutes for day in report.days) == report.net_extra_minutes
    assert all(day.leave_records for day in report.days)
    assert source.compensatory_leave_records[0].source_sheet in {"打卡详情", "概况统计与打卡明细"}
    assert source.compensatory_leave_records[0].source_row >= 5


@pytest.mark.parametrize("day_number", [2, 30])
def test_real_full_day_leave_keeps_missing_punch_semantics(tmp_path, day_number):
    """四月全天请假保留缺卡状态，同时仅扣一次明确的八小时申请。"""
    work_date = date(2026, 4, day_number)
    leave = f"调休假8.0小时（4/{day_number} 09:00 - 4/{day_number} 18:30）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "--", "--", leave), [(work_date, leave)])
    report = calculate_attendance(parse_attendance_workbook(path), _calendar())
    day = report.days[0]

    assert day.status is CalculationStatus.MISSING_BOTH
    assert day.actual_effective_minutes is None
    assert day.extra_minutes is None
    assert day.required_standard_minutes == 480
    assert day.leave_requested_minutes == 480
    assert day.compensatory_leave_minutes == 480
    assert day.net_extra_minutes == -480


@pytest.mark.parametrize(
    ("work_date", "starts", "end", "effective", "extra"),
    [
        (date(2026, 9, 15), [("09:00", "正常(补卡)"), ("09:34", "失效 迟到4分钟")], "21:30", 660, 180),
        (date(2026, 10, 23), [("08:43", "正常(补卡)"), ("08:43", "失效"), ("11:30", "失效 迟到120分钟")], "17:44", 451, 0),
    ],
    ids=["september-corrected-punch", "october-duplicate-punch"],
)
def test_real_corrected_punches_do_not_become_leave(tmp_path, work_date, starts, end, effective, extra):
    """保留多次打卡来源，补卡申请不得误作调休或重复累计工时。"""
    punches = [(work_date, "上班", stamp, status, "补卡申请") for stamp, status in starts]
    punches.append((work_date, "下班", end, "正常", "补卡申请"))
    path = _workbook(tmp_path, work_date, work_date, punches, [(work_date, "补卡申请")])
    source = parse_attendance_workbook(path)
    day = calculate_attendance(source, _calendar()).days[0]

    assert len(source.records) == len(punches)
    assert source.compensatory_leave_records == []
    assert day.actual_effective_minutes == effective
    assert day.extra_minutes == extra
    assert day.compensatory_leave_minutes == 0
    assert day.location_benefit_minutes == 0


@pytest.mark.parametrize(("clock", "expected_day"), [("03:00", 2), ("03:59", 2), ("04:00", 3)])
def test_synthetic_punch_boundary_uses_four_am(tmp_path, clock, expected_day):
    """合成边界卡验证 03:00/03:59 归前日而 04:00 归新日。"""
    work_date = date(2026, 9, 3)
    path = _workbook(tmp_path, date(2026, 9, 2), work_date, [(work_date, "下班", clock, "正常", "--")])
    source = parse_attendance_workbook(path)
    report = calculate_attendance(source, _calendar())

    assert source.records[0].work_date == date(2026, 9, expected_day)
    matched_day = next(day for day in report.days if day.work_date.day == expected_day)
    assert matched_day.last_check_out == datetime.fromisoformat(f"2026-09-03T{clock}")
    assert matched_day.status is CalculationStatus.MISSING_CHECK_IN


def test_synthetic_midday_leave_excludes_lunch(tmp_path):
    """合成十一点至十四点申请只包含九十分钟正常工作时段。"""
    work_date = date(2026, 9, 2)
    leave = "调休假1.5小时（9/2 11:00 - 9/2 14:00）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "--", "--", leave))
    day = calculate_attendance(parse_attendance_workbook(path), _calendar()).days[0]

    assert day.leave_requested_minutes == 90
    assert day.compensatory_leave_minutes == 90


def test_cross_weekend_application_is_clipped_to_report_period(tmp_path):
    """同一真实跨日申请在只含周一的报表中不能再扣周五的量。"""
    work_date = date(2026, 4, 27)
    leave = "调休假8.0小时（4/24 12:00 - 4/27 13:30）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "15:18", "17:24", leave))
    source = parse_attendance_workbook(path)
    report = calculate_attendance(source, _calendar())

    assert source.compensatory_leave_records[0].minutes == 480
    assert report.leave_requested_minutes == 180
    assert report.compensatory_leave_minutes == 180
    assert report.net_extra_minutes == -180


def test_synthetic_working_sunday_does_not_exclude_leave(tmp_path):
    """注入明确上班的周日，验证日期分类优先于星期值。"""
    work_date = date(2026, 10, 11)
    leave = "调休假8.0小时（10/11 09:00 - 10/11 18:30）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "--", "--", leave))
    calendar = _calendar()
    calendar.overrides[work_date] = HolidayOverride(name="测试指定上班日", is_off_day=False)
    day = calculate_attendance(parse_attendance_workbook(path), calendar).days[0]

    assert day.day_type is DayType.MAKEUP_WORKDAY
    assert day.leave_requested_minutes == 480
    assert day.compensatory_leave_minutes == 480


def test_synthetic_cross_year_range_resolves_month_day_years(tmp_path):
    """申请文本省略年份时，跨年两端仍归属正确年份。"""
    start, end = date(2026, 12, 31), date(2027, 1, 1)
    leave = "调休假16.0小时（12/31 09:00 - 1/1 18:30）"
    path = _workbook(tmp_path, start, end, [*_pair(end, "--", "--", leave), *_pair(start, "--", "--", leave)])
    source = parse_attendance_workbook(path)
    # 测试日历只验证年份推断，不声称这里使用了真实元旦安排。
    report = calculate_attendance(source, _calendar())

    assert source.start_date == start
    assert source.end_date == end
    assert len(source.compensatory_leave_records) == 2
    assert {record.start_at for record in source.compensatory_leave_records} == {datetime(2026, 12, 31, 9)}
    assert {record.end_at for record in source.compensatory_leave_records} == {datetime(2027, 1, 1, 18, 30)}
    assert [day.leave_requested_minutes for day in report.days] == [480, 480]


def test_synthetic_other_leave_type_does_not_pollute_compensatory_interval(tmp_path):
    """同一单元格内其他假种的区间不能破坏明确的调休申请。"""
    work_date = date(2026, 9, 2)
    leave = "调休假2.0小时（9/2 09:00 - 9/2 11:00）；年假1.0天（9/3 09:00 - 9/3 18:30）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "--", "--", leave))
    source = parse_attendance_workbook(path)
    day = calculate_attendance(source, _calendar()).days[0]

    assert len(source.compensatory_leave_records) == 1
    assert source.compensatory_leave_records[0].description == leave
    assert day.leave_requested_minutes == 120
    assert day.compensatory_leave_minutes == 120
    assert day.net_extra_minutes == -120
    assert not day.requires_review


def test_synthetic_explicit_year_conflict_remains_unknown(tmp_path):
    """源行与申请明确年份冲突时，不得静默漏掉申请并显示零扣减。"""
    work_date = date(2026, 9, 2)
    leave = "调休假5.0小时（2025/9/2 12:00 - 2025/9/2 18:30）"
    path = _workbook(tmp_path, work_date, work_date, _pair(work_date, "--", "--", leave))
    source = parse_attendance_workbook(path)
    report = calculate_attendance(source, _calendar())
    day = report.days[0]

    assert source.compensatory_leave_records[0].description == leave
    assert day.leave_records
    assert day.requires_review
    assert day.leave_requested_minutes is None
    assert day.compensatory_leave_minutes is None
    assert day.net_extra_minutes is None
    assert report.net_extra_minutes is None
