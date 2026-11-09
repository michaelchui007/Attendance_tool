from openpyxl import Workbook
from datetime import date, datetime

from attendance_engine.calculator import calculate_attendance
from attendance_engine.models import AttendanceSource

from attendance_engine.models import PunchType
from attendance_engine.parser import parse_attendance_workbook
from attendance_engine.parser import _collect_compensatory_leave, _parse_leave_interval, _resolve_work_date


def test_parse_source_workbook(tmp_path):
    path = tmp_path / "attendance.xlsx"
    wb = Workbook()
    wb.active.title = "概况统计与打卡明细"
    detail = wb.create_sheet("打卡详情")
    detail["A2"] = "统计时间:07-01 ～ 07-02     制表时间:2026-07-02 10:00(UTC+8)"
    detail.append([])
    detail.append(["日期", "姓名", "账号", "部门", "职务", "所属规则", "打卡类型", "应打卡时间", "实际打卡时间", "打卡状态", "打卡地点"])
    detail.append([])
    detail.append(["2026/07/01 星期三", "张三", "zhangsan", "部门", "职务", "规则", "上班", "09:00", "08:50", "正常", "云岭东路586号"])
    detail.append(["2026/07/01 星期三", "张三", "zhangsan", "部门", "职务", "规则", "下班", "18:30", "19:00", "正常", "其他地点"])
    wb.save(path)

    parsed = parse_attendance_workbook(path)
    assert parsed.employee_name == "张三"
    assert len(parsed.records) == 2
    assert parsed.records[0].punch_type is PunchType.CHECK_IN


def test_parse_compensatory_leave_and_deduplicate_detail_rows(tmp_path):
    path = tmp_path / "中文考勤报表.xlsx"
    wb = Workbook()
    summary = wb.active
    summary.title = "概况统计与打卡明细"
    summary.append([])
    summary.append([])
    summary.append([])
    summary.append([])
    summary.append([
        "2026/07/01 星期三", "张三", "zhangsan", None, None, None, None,
        None, None, None, None, None, None, "调休假8.0小时（7/1 09:00 - 7/1 18:30）",
    ])
    detail = wb.create_sheet("打卡详情")
    detail["A2"] = "统计时间:07-01 ～ 07-01     制表时间:2026-07-01 18:30(UTC+8)"
    detail.append([])
    detail.append([])
    leave = "调休假8.0小时（7/1 09:00 - 7/1 18:30）"
    detail.append(["2026/07/01 星期三", "张三", "zhangsan", None, None, None, "上班", "09:00", "--", "无需打卡（请假）", None, None, None, None, leave])
    detail.append(["2026/07/01 星期三", "张三", "zhangsan", None, None, None, "下班", "18:30", "--", "无需打卡（请假）", None, None, None, None, leave])
    wb.save(path)

    parsed = parse_attendance_workbook(path)

    assert parsed.input_path.name == "中文考勤报表.xlsx"
    assert len(parsed.compensatory_leave_records) == 1
    assert parsed.compensatory_leave_records[0].work_date.isoformat() == "2026-07-01"
    assert parsed.compensatory_leave_records[0].minutes == 480
    assert parsed.compensatory_leave_records[0].start_at == datetime(2026, 7, 1, 9, 0)
    assert parsed.compensatory_leave_records[0].end_at == datetime(2026, 7, 1, 18, 30)


def test_multiple_leave_items_without_intervals_are_not_collapsed(tmp_path):
    """同一单元格两条无区间申请保留单项身份，不误合并为第一项。"""
    records = {}
    description = "调休假2小时；调休假3小时"
    _collect_compensatory_leave(records, date(2026, 7, 1), description, "测试", 5)
    _collect_compensatory_leave(records, date(2026, 7, 1), description, "测试", 6)
    source = AttendanceSource(tmp_path / "input.xlsx", "测试员工", "test", date(2026, 7, 1), date(2026, 7, 1), [], list(records.values()))
    day = calculate_attendance(source).days[0]
    assert len(records) == 2
    assert day.leave_requested_minutes == 300
    assert day.compensatory_leave_minutes == 300
    assert day.requires_review


def test_leave_interval_infers_year_across_december_and_january():
    """不带年份的跨年申请按源行日期找到相邻年份，而非颠倒区间。"""
    start, end, warning = _parse_leave_interval("调休假16小时（12/31 09:00 - 1/2 18:30）", date(2027, 1, 2))
    assert start == datetime(2026, 12, 31, 9, 0)
    assert end == datetime(2027, 1, 2, 18, 30)
    assert not warning


def test_invalid_leave_interval_is_marked_for_review():
    """起止时间倒置时保留解析提示，不能退回普通单日时长。"""
    start, end, warning = _parse_leave_interval("调休假2小时（7/1 18:30 - 7/1 09:00）", date(2026, 7, 1))
    assert start is None and end is None
    assert warning


def test_workday_boundary_is_four_am():
    """03:59属于前一日，04:00精确归入新日。"""
    assert _resolve_work_date(datetime(2026, 7, 2, 3, 59)) == date(2026, 7, 1)
    assert _resolve_work_date(datetime(2026, 7, 2, 4, 0)) == date(2026, 7, 2)
