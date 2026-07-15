from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import AttendanceReport, CalculationStatus

_TITLE_FILL = PatternFill("solid", fgColor="16324F")
_HEADER_FILL = PatternFill("solid", fgColor="DCEAF7")
_KPI_FILL = PatternFill("solid", fgColor="E8F4F0")
_WARNING_FILL = PatternFill("solid", fgColor="FFF2CC")
_DANGER_FILL = PatternFill("solid", fgColor="FCE8E6")
_THIN_GRAY = Side(style="thin", color="D9E2EC")


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", value).strip() or "员工"


def _format_minutes(value: int | None) -> str:
    if value is None:
        return "未计算"
    sign = "-" if value < 0 else ""
    hours, minutes = divmod(abs(value), 60)
    return f"{sign}{hours}小时{minutes:02d}分钟"


def _style_header(ws, row: int, start_col: int, end_col: int) -> None:
    for cell in ws.iter_cols(min_col=start_col, max_col=end_col, min_row=row, max_row=row):
        c = cell[0]
        c.fill = _HEADER_FILL
        c.font = Font(bold=True, color="16324F")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = Border(bottom=_THIN_GRAY)


def _autofit(ws, min_width: int = 10, max_width: int = 38) -> None:
    for column_cells in ws.columns:
        width = min_width
        for cell in column_cells:
            if cell.value is not None:
                width = max(width, min(max_width, len(str(cell.value)) + 2))
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = width


def _calendar_source_text(report: AttendanceReport) -> str:
    labels = {"online": "在线更新", "cache": "本地缓存", "stale-cache": "离线缓存", "builtin": "内置数据"}
    years = report.calendar_info.get("years", [])
    return "；".join(
        f"{item['year']}年 {labels.get(item['source'], item['source'])}"
        for item in years
    ) or "未记录"


def export_report(report: AttendanceReport, export_root: str | Path | None = None) -> Path:
    source = report.source
    root = Path(export_root).expanduser().resolve() if export_root else source.input_path.parent / "export_report"
    output_dir = root / f"{_safe_name(source.employee_name)}-{source.end_date:%Y%m%d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{_safe_name(source.employee_name)}-{source.start_date:%Y%m%d}-{source.end_date:%Y%m%d}-考勤统计"
    output_path = output_dir / f"{base_name}.xlsx"
    if output_path.exists():
        output_path = output_dir / f"{base_name}-{datetime.now():%H%M%S}.xlsx"

    wb = Workbook()
    summary = wb.active
    summary.title = "月度汇总"
    summary.sheet_view.showGridLines = False
    summary.merge_cells("A1:L1")
    summary["A1"] = "考勤统计汇总"
    summary["A1"].fill = _TITLE_FILL
    summary["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    summary["A1"].alignment = Alignment(horizontal="center", vertical="center")
    summary.row_dimensions[1].height = 32
    summary.append(["员工", source.employee_name, "账号", source.employee_account, "统计开始", source.start_date, "统计结束", source.end_date])
    for cell in summary[2]:
        cell.alignment = Alignment(vertical="center")
    summary["F2"].number_format = "yyyy-mm-dd"
    summary["H2"].number_format = "yyyy-mm-dd"
    summary.append([])
    summary.append([
        "应计标准工时", _format_minutes(report.required_standard_minutes),
        "实际有效工时", _format_minutes(report.actual_effective_minutes),
        "加班时长", _format_minutes(report.extra_minutes),
        "调休时长", _format_minutes(report.compensatory_leave_minutes),
        "净剩加班时长", _format_minutes(report.net_extra_minutes),
        "异常天数", report.abnormal_day_count,
    ])
    for cell in summary[4]:
        cell.fill = _KPI_FILL
        cell.font = Font(bold=True, color="16324F")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    summary.row_dimensions[4].height = 28
    summary.append([])
    summary["A5"] = "节假日数据"
    summary.merge_cells("B5:L5")
    summary["B5"] = _calendar_source_text(report)
    summary["A5"].font = Font(bold=True, color="16324F")
    summary["B5"].alignment = Alignment(vertical="center")
    summary.append([
        "日期", "日期类型", "状态", "应计标准", "实际有效", "加班时长",
        "调休时长", "净剩加班时长", "最早上班", "最晚下班",
    ])
    _style_header(summary, 6, 1, 10)
    for day in report.days:
        summary.append([
            day.work_date,
            day.day_type.value,
            day.status.value,
            _format_minutes(day.required_standard_minutes),
            _format_minutes(day.actual_effective_minutes),
            _format_minutes(day.extra_minutes),
            _format_minutes(day.compensatory_leave_minutes),
            _format_minutes(day.net_extra_minutes),
            day.first_check_in,
            day.last_check_out,
        ])
    for row in range(7, summary.max_row + 1):
        summary.cell(row, 1).number_format = "yyyy-mm-dd"
        summary.cell(row, 9).number_format = "yyyy-mm-dd hh:mm"
        summary.cell(row, 10).number_format = "yyyy-mm-dd hh:mm"
    summary.freeze_panes = "A7"
    summary.auto_filter.ref = f"A6:J{summary.max_row}"
    summary.conditional_formatting.add(
        f"C7:C{summary.max_row}",
        FormulaRule(formula=["C7<>\"正常\""], fill=_DANGER_FILL),
    )
    _autofit(summary)

    details = wb.create_sheet("每日明细")
    details.sheet_view.showGridLines = False
    headers = [
        "日期", "星期", "日期类型", "状态", "应计标准工时", "最早上班", "最晚下班", "原始跨度",
        "午休扣除", "地点福利加计", "实际正常工时", "实际有效工时", "加班时长",
        "调休时长", "净剩加班时长", "计算说明",
    ]
    details.append(headers)
    _style_header(details, 1, 1, len(headers))
    for day in report.days:
        details.append([
            day.work_date,
            "星期" + "一二三四五六日"[day.work_date.weekday()],
            day.day_type.value,
            day.status.value,
            day.required_standard_minutes / 60,
            day.first_check_in,
            day.last_check_out,
            None if day.raw_duration_minutes is None else day.raw_duration_minutes / 60,
            day.lunch_deduction_minutes / 60,
            day.location_benefit_minutes / 60,
            None if day.actual_regular_minutes is None else day.actual_regular_minutes / 60,
            None if day.actual_effective_minutes is None else day.actual_effective_minutes / 60,
            None if day.extra_minutes is None else day.extra_minutes / 60,
            day.compensatory_leave_minutes / 60,
            day.net_extra_minutes / 60,
            "；".join(day.messages),
        ])
    for row in range(2, details.max_row + 1):
        details.cell(row, 1).number_format = "yyyy-mm-dd"
        details.cell(row, 6).number_format = "yyyy-mm-dd hh:mm"
        details.cell(row, 7).number_format = "yyyy-mm-dd hh:mm"
        for col in range(5, 16):
            if col not in {6, 7}:
                details.cell(row, col).number_format = "0.00"
    details.freeze_panes = "A2"
    details.auto_filter.ref = f"A1:P{details.max_row}"
    details.conditional_formatting.add(
        f"D2:D{details.max_row}",
        FormulaRule(formula=["D2<>\"正常\""], fill=_WARNING_FILL),
    )
    _autofit(details)
    details.column_dimensions["P"].width = 48

    raw = wb.create_sheet("原始打卡记录")
    raw.sheet_view.showGridLines = False
    raw_headers = ["工作日期", "实际打卡时间", "类型", "地点", "打卡状态", "源表行号"]
    raw.append(raw_headers)
    _style_header(raw, 1, 1, len(raw_headers))
    for record in source.records:
        raw.append([record.work_date, record.timestamp, record.punch_type.value, record.location, record.status, record.source_row])
    for row in range(2, raw.max_row + 1):
        raw.cell(row, 1).number_format = "yyyy-mm-dd"
        raw.cell(row, 2).number_format = "yyyy-mm-dd hh:mm"
    raw.freeze_panes = "A2"
    raw.auto_filter.ref = f"A1:F{raw.max_row}"
    _autofit(raw)
    raw.column_dimensions["D"].width = 52

    leave = wb.create_sheet("原始调休记录")
    leave.sheet_view.showGridLines = False
    leave_headers = ["工作日期", "调休时长（小时）", "假勤申请", "来源工作表", "源表行号"]
    leave.append(leave_headers)
    _style_header(leave, 1, 1, len(leave_headers))
    for record in source.compensatory_leave_records:
        leave.append([
            record.work_date,
            record.minutes / 60,
            record.description,
            record.source_sheet,
            record.source_row,
        ])
    for row in range(2, leave.max_row + 1):
        leave.cell(row, 1).number_format = "yyyy-mm-dd"
        leave.cell(row, 2).number_format = "0.00"
    leave.freeze_panes = "A2"
    leave.auto_filter.ref = f"A1:E{leave.max_row}"
    _autofit(leave)
    leave.column_dimensions["C"].width = 48

    wb.save(output_path)
    report.output_path = output_path
    return output_path
