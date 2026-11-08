from __future__ import annotations

import re
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import AttendanceReport, CalculationStatus
from . import __version__

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


def _resolution_label(status: str) -> str:
    """将处理状态转换为用户可读标签，原始缺卡状态单独保留。"""
    return {'pending': '待处理', 'resolved': '已处理', 'normal': '无需处理',
            'notImported': '未导入'}.get(status, status)


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
    """导出原始资料、分摊扣减及手动修订，保持历史文件不被覆盖。"""
    source = report.source
    if any(not item['resolved'] for item in source.conflicts):
        raise ValueError('请先解决来源冲突，再导出结果')
    root = Path(export_root).expanduser().resolve() if export_root else source.input_path.parent / "export_report"
    output_dir = root / f"{_safe_name(source.employee_name)}-{source.end_date:%Y%m%d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{_safe_name(source.employee_name)}-{source.start_date:%Y%m%d}-{source.end_date:%Y%m%d}-考勤统计"
    output_path = output_dir / f"{base_name}.xlsx"
    if output_path.exists():
        output_path = output_dir / f"{base_name}-{datetime.now():%H%M%S%f}.xlsx"
        suffix = 1
        timestamp_name = output_path.stem
        while output_path.exists():
            output_path = output_dir / f"{timestamp_name}-{suffix}.xlsx"
            suffix += 1

    wb = Workbook()
    summary = wb.active
    summary.title = "月度汇总"
    summary.sheet_view.showGridLines = False
    summary.merge_cells("A1:L1")
    summary["A1"] = "考勤统计汇总" + ("（仅已导入期间，数据不完整）" if report.summary()['isPartial'] else "")
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
        "实际调休扣减", _format_minutes(report.compensatory_leave_minutes),
        "净剩加班时长", _format_minutes(report.net_extra_minutes),
        "待处理天数", report.attention_day_count,
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
        "实际调休扣减", "净剩加班时长", "最早上班", "最晚下班", "处理状态", "待处理原因",
    ])
    _style_header(summary, 6, 1, 12)
    for day in report.days:
        summary.append([
            day.work_date,
            day.day_type.value,
            day.status.value + ("（原始资料有核对提示）" if day.requires_review else ""),
            _format_minutes(day.required_standard_minutes),
            _format_minutes(day.actual_effective_minutes),
            _format_minutes(day.extra_minutes),
            _format_minutes(day.compensatory_leave_minutes),
            _format_minutes(day.net_extra_minutes),
            day.first_check_in,
            day.last_check_out,
            _resolution_label(day.resolution_status),
            "；".join(item['message'] for item in day.attention_reasons),
        ])
    for row in range(7, summary.max_row + 1):
        summary.cell(row, 1).number_format = "yyyy-mm-dd"
        summary.cell(row, 9).number_format = "yyyy-mm-dd hh:mm"
        summary.cell(row, 10).number_format = "yyyy-mm-dd hh:mm"
    summary.freeze_panes = "A7"
    summary.auto_filter.ref = f"A6:L{summary.max_row}"
    summary.conditional_formatting.add(
        f"C7:C{summary.max_row}",
        FormulaRule(formula=["K7=\"待处理\""], fill=_DANGER_FILL),
    )
    _autofit(summary)

    details = wb.create_sheet("每日明细")
    details.sheet_view.showGridLines = False
    headers = [
        "日期", "星期", "日期类型", "状态", "应计标准工时", "最早上班", "最晚下班", "原始跨度",
        "午休扣除", "地点福利（已取消）", "实际正常工时", "实际有效工时", "加班时长",
        "实际调休扣减", "净剩加班时长", "计算说明", "当日申请量",
        "原始最早上班", "原始最晚下班", "手动修订", "需要核对",
        "自动调休扣减", "手动扣减", "修订原因", "生效上班", "生效下班", "午休开始", "午休结束", "作息来源", "已导入", "来源文件标识",
        "处理状态", "待处理原因", "建议操作",
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
            None if day.compensatory_leave_minutes is None else day.compensatory_leave_minutes / 60,
            None if day.net_extra_minutes is None else day.net_extra_minutes / 60,
            "；".join(day.messages),
            None if day.leave_requested_minutes is None else day.leave_requested_minutes / 60,
            day.original_first_check_in, day.original_last_check_out,
            "是" if day.is_edited else "否", "是" if day.requires_review else "否",
            None if day.auto_compensatory_leave_minutes is None else day.auto_compensatory_leave_minutes / 60,
            "是" if day.is_leave_edited else "否", day.leave_reason,
            day.schedule.get('workdayStart'), day.schedule.get('workdayEnd'),
            day.schedule.get('lunchStart'), day.schedule.get('lunchEnd'), day.schedule_source,
            "是" if day.covered else "否", "；".join(day.source_ids),
            _resolution_label(day.resolution_status),
            "；".join(item['message'] for item in day.attention_reasons),
            "；".join(item['action'] for item in day.attention_reasons),
        ])
    for row in range(2, details.max_row + 1):
        details.cell(row, 1).number_format = "yyyy-mm-dd"
        details.cell(row, 6).number_format = "yyyy-mm-dd hh:mm"
        details.cell(row, 7).number_format = "yyyy-mm-dd hh:mm"
        details.cell(row, 17).number_format = "0.00"
        details.cell(row, 18).number_format = "yyyy-mm-dd hh:mm"
        details.cell(row, 19).number_format = "yyyy-mm-dd hh:mm"
        for col in range(5, 16):
            if col not in {6, 7}:
                details.cell(row, col).number_format = "0.00"
    details.freeze_panes = "A2"
    details.auto_filter.ref = f"A1:AH{details.max_row}"
    details.conditional_formatting.add(
        f"D2:D{details.max_row}",
        FormulaRule(formula=["AF2=\"待处理\""], fill=_WARNING_FILL),
    )
    _autofit(details)
    details.column_dimensions["P"].width = 48

    raw = wb.create_sheet("原始打卡记录")
    raw.sheet_view.showGridLines = False
    raw_headers = ["工作日期", "实际打卡时间", "类型", "地点", "打卡状态", "源表行号", "全部来源（文件/工作表/行）"]
    raw.append(raw_headers)
    _style_header(raw, 1, 1, len(raw_headers))
    for record in source.records:
        raw.append([record.work_date, record.timestamp, record.punch_type.value, record.location, record.status, record.source_row,
                    "；".join(f"{item['sourceId']} / {item['name']} / {item['sourceSheet']} / {item['sourceRow']}" for item in record.sources)])
    for row in range(2, raw.max_row + 1):
        raw.cell(row, 1).number_format = "yyyy-mm-dd"
        raw.cell(row, 2).number_format = "yyyy-mm-dd hh:mm"
    raw.freeze_panes = "A2"
    raw.auto_filter.ref = f"A1:G{raw.max_row}"
    _autofit(raw)
    raw.column_dimensions["D"].width = 52

    leave = wb.create_sheet("原始调休记录")
    leave.sheet_view.showGridLines = False
    leave_headers = ["源行日期", "原始填报时长（小时，不跨行累计）", "假勤申请", "来源工作表", "源表行号", "申请开始", "申请结束", "解析提示", "全部来源（文件/工作表/行）"]
    leave.append(leave_headers)
    _style_header(leave, 1, 1, len(leave_headers))
    for record in source.compensatory_leave_records:
        leave.append([
            record.work_date,
            record.minutes / 60,
            record.description,
            record.source_sheet,
            record.source_row,
            record.start_at, record.end_at, record.parse_warning,
            "；".join(f"{item['sourceId']} / {item['name']} / {item['sourceSheet']} / {item['sourceRow']}" for item in record.sources),
        ])
    for row in range(2, leave.max_row + 1):
        leave.cell(row, 1).number_format = "yyyy-mm-dd"
        leave.cell(row, 2).number_format = "0.00"
        leave.cell(row, 6).number_format = "yyyy-mm-dd hh:mm"
        leave.cell(row, 7).number_format = "yyyy-mm-dd hh:mm"
    leave.freeze_panes = "A2"
    leave.auto_filter.ref = f"A1:I{leave.max_row}"
    _autofit(leave)
    leave.column_dimensions["C"].width = 48

    rules = wb.create_sheet("计算规则")
    rules.append(["规则", "本次采用值"])
    rules.append(["软件版本", __version__])
    rules.append(["考勤日边界", report.rules.get("dayBoundary", "04:00")])
    rules.append(["午休开始", report.rules.get("lunchStart", "12:00")])
    rules.append(["午休结束", report.rules.get("lunchEnd", "13:30")])
    rules.append(["请假分摊上午", f"{report.rules.get('workdayStart', '09:00')}～{report.rules.get('lunchStart') or report.rules.get('workdayEnd')}"])
    rules.append(["请假分摊下午", "无午休，按上述完整工作区间分摊" if report.rules.get('lunchStart') is None else
                  f"{report.rules.get('lunchEnd', '13:30')}～{report.rules.get('workdayEnd', '18:30')}"])
    rules.append(["标准工时（分钟）", report.rules.get('standardMinutes', 480)])
    rules.append(["地点福利", "已取消"])
    rules.append(["扣减规则", "完整打卡：自动扣减=min(当日申请量,max(标准工时-实际有效工时,0))；手动值优先"])
    rules.append(["待核对说明", "未知调休扣减及净额显示为未计算或空白，不以0代替；请查看每日说明"])
    _style_header(rules, 1, 1, 2)
    _autofit(rules)
    rules.column_dimensions["B"].width = 80

    rules.append(["完整配置快照", json.dumps(report.rules, ensure_ascii=False)])
    sources_sheet = wb.create_sheet("来源清单")
    sources_sheet.append(["标识", "文件", "路径", "开始日期", "结束日期", "重复来源", "解析缓存"])
    for item in source.files:
        sources_sheet.append([item['id'], item['name'], item['path'], item['startDate'], item['endDate'], item['duplicateOf'], "是" if item['cacheHit'] else "否"])
    _style_header(sources_sheet, 1, 1, 7)
    _autofit(sources_sheet)
    month_sheet = wb.create_sheet("分月汇总")
    month_sheet.append(["月份", "应计标准工时", "实际有效工时", "加班", "实际扣减", "净剩加班", "数据不完整", "待处理天数", "已处理天数"])
    for month in report.monthly_summaries():
        month_sheet.append([month['month'], *[_format_minutes(month[key]) for key in
                           ('requiredStandardMinutes', 'actualEffectiveMinutes', 'extraMinutes', 'compensatoryLeaveMinutes', 'netExtraMinutes')],
                           "是" if month['isPartial'] else "否", month['attentionDayCount'], month['resolvedDayCount']])
    _style_header(month_sheet, 1, 1, 9)
    _autofit(month_sheet)

    # 先写隐藏临时文件，取消或写入失败不会产生貌似完整的结果工作簿。
    handle, temporary = tempfile.mkstemp(prefix='.attendance-export-', suffix='.tmp', dir=output_dir)
    os.close(handle)
    try:
        wb.save(temporary)
        os.replace(temporary, output_path)
    finally:
        wb.close()
        Path(temporary).unlink(missing_ok=True)
    report.output_path = output_path
    return output_path
