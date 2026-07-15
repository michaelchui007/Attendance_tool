from openpyxl import Workbook

from attendance_engine.models import PunchType
from attendance_engine.parser import parse_attendance_workbook


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
