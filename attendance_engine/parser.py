from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook

from .models import AttendanceSource, CompensatoryLeaveRecord, PunchRecord, PunchType

_METADATA_RANGE_RE = re.compile(r"统计时间\s*:\s*(\d{2})-(\d{2})\s*[～~\-]\s*(\d{2})-(\d{2})")
_METADATA_YEAR_RE = re.compile(r"制表时间\s*:\s*(\d{4})-")
_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_COMPENSATORY_LEAVE_RE = re.compile(r"调休(?:假)?\s*(\d+(?:\.\d+)?)\s*小时")
_LEAVE_DATETIME_RE = re.compile(
    r"(?:(?P<year>\d{4})[/年-])?(?P<month>\d{1,2})[/月-](?P<day>\d{1,2})日?"
    r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)
_LEAVE_ITEM_BOUNDARY_RE = re.compile(r"[；;\n]|(?:年假|事假|病假|婚假|产假|陪产假|丧假|育儿假|补卡申请)")


class AttendanceParseError(ValueError):
    pass


def _parse_period(metadata: str) -> tuple[date, date]:
    range_match = _METADATA_RANGE_RE.search(metadata)
    year_match = _METADATA_YEAR_RE.search(metadata)
    if not range_match or not year_match:
        raise AttendanceParseError("无法从报表第二行解析统计日期范围")

    year = int(year_match.group(1))
    start_month, start_day, end_month, end_day = map(int, range_match.groups())
    start_year = year - 1 if start_month > end_month else year
    return date(start_year, start_month, start_day), date(year, end_month, end_day)


def _parse_natural_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    match = _DATE_RE.search(str(value))
    if not match:
        return None
    return date(*map(int, match.groups()))


def _parse_time(value: object) -> time | None:
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if value is None:
        return None
    text = str(value).strip()
    match = _TIME_RE.match(text)
    if not match:
        return None
    hour, minute, second = match.groups()
    return time(int(hour), int(minute), int(second or 0))


def _resolve_work_date(timestamp: datetime) -> date:
    """按当地报表时间，以凌晨 04:00 划分考勤日。"""
    return timestamp.date() - timedelta(days=1) if timestamp.time() < time(4, 0) else timestamp.date()


def _parse_leave_interval(description: str, row_date: date) -> tuple[datetime | None, datetime | None, str]:
    """解析假勤文字中的区间，并用所在行日期补全年份。

    Args:
        description: 单项调休申请的原始文字。
        row_date: 申请出现在报表中的自然日期，用于消除跨年歧义。

    Returns:
        开始时间、结束时间以及无法确定区间时的提示。
    """
    matches = list(_LEAVE_DATETIME_RE.finditer(description))
    if not matches:
        return None, None, ""
    if len(matches) != 2:
        return None, None, "申请起止时间不完整，无法确定每日分摊"
    first, last = matches
    candidates = []
    for start_year in ([int(first['year'])] if first['year'] else range(row_date.year - 1, row_date.year + 2)):
        try:
            start = datetime(start_year, int(first['month']), int(first['day']), int(first['hour']), int(first['minute']))
            end_year = int(last['year']) if last['year'] else start_year + int(
                (int(last['month']), int(last['day'])) < (start.month, start.day)
            )
            end = datetime(end_year, int(last['month']), int(last['day']), int(last['hour']), int(last['minute']))
        except ValueError:
            continue
        if end > start and (end - start).days <= 366:
            distance = min(abs((row_date - start.date()).days), abs((row_date - end.date()).days))
            candidates.append((not (start.date() <= row_date <= end.date()), distance, start, end))
    if not candidates:
        return None, None, "申请起止时间无效，无法确定每日分摊"
    _, _, start, end = min(candidates)
    if not start.date() <= row_date <= end.date():
        return start, end, "申请起止日期与源行日期不一致，请核对申请年份及来源"
    return start, end, ""


def _parse_compensatory_leave_minutes(value: object) -> tuple[int, str]:
    description = str(value or "").strip()
    if not description or description == "--":
        return 0, description
    hours = sum((Decimal(match) for match in _COMPENSATORY_LEAVE_RE.findall(description)), Decimal())
    minutes = int((hours * 60).to_integral_value(rounding=ROUND_HALF_UP))
    return minutes, description


def _collect_compensatory_leave(
    records: dict[tuple[date, str, int], CompensatoryLeaveRecord],
    work_date: date | None,
    value: object,
    source_sheet: str,
    source_row: int,
) -> None:
    """收集每项申请，按同一天和申请原文去重，保留源行追溯信息。"""
    if work_date is None:
        return
    description = str(value or "").strip()
    matches = list(_COMPENSATORY_LEAVE_RE.finditer(description))
    for index, match in enumerate(matches):
        segment = description[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(description)]
        boundary = _LEAVE_ITEM_BOUNDARY_RE.search(segment, match.end() - match.start())
        if boundary is not None:
            segment = segment[:boundary.start()]
        minutes = int((Decimal(match.group(1)) * 60).to_integral_value(rounding=ROUND_HALF_UP))
        if minutes <= 0:
            continue
        start, end, warning = _parse_leave_interval(segment, work_date)
        records.setdefault(
            (work_date, description, index),
            CompensatoryLeaveRecord(
                work_date=work_date, minutes=minutes, description=description,
                source_sheet=source_sheet, source_row=source_row,
                start_at=start, end_at=end, parse_warning=warning, item_index=index,
            ),
        )


def parse_attendance_workbook(input_path: str | Path) -> AttendanceSource:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise AttendanceParseError(f"源文件不存在：{path}")
    if path.suffix.lower() != ".xlsx":
        raise AttendanceParseError("当前仅支持 .xlsx 文件")

    workbook = load_workbook(path, data_only=True, read_only=True)
    if "打卡详情" not in workbook.sheetnames:
        raise AttendanceParseError("Excel 中缺少“打卡详情”工作表")

    detail_sheet = workbook["打卡详情"]
    metadata = str(detail_sheet.cell(row=2, column=1).value or "")
    start_date, end_date = _parse_period(metadata)

    records: list[PunchRecord] = []
    employees: set[tuple[str, str]] = set()
    compensatory_leave_records: dict[tuple[date, str, int], CompensatoryLeaveRecord] = {}

    for row_index, row in enumerate(detail_sheet.iter_rows(min_row=5, values_only=True), start=5):
        natural_date = _parse_natural_date(row[0] if len(row) > 0 else None)
        employee_name = str(row[1] or "").strip() if len(row) > 1 else ""
        employee_account = str(row[2] or "").strip() if len(row) > 2 else ""
        punch_type_text = str(row[6] or "").strip() if len(row) > 6 else ""
        actual_time = _parse_time(row[8] if len(row) > 8 else None)
        punch_status = str(row[9] or "").strip() if len(row) > 9 else ""
        location = str(row[10] or "").strip() if len(row) > 10 else ""
        _collect_compensatory_leave(
            compensatory_leave_records,
            natural_date,
            row[14] if len(row) > 14 else None,
            detail_sheet.title,
            row_index,
        )

        if not natural_date or not employee_name:
            continue
        employees.add((employee_name, employee_account))
        if actual_time is None:
            continue
        if "上班" in punch_type_text:
            punch_type = PunchType.CHECK_IN
        elif "下班" in punch_type_text:
            punch_type = PunchType.CHECK_OUT
        else:
            continue

        timestamp = datetime.combine(natural_date, actual_time)
        records.append(
            PunchRecord(
                employee_name=employee_name,
                employee_account=employee_account,
                timestamp=timestamp,
                work_date=_resolve_work_date(timestamp),
                punch_type=punch_type,
                location=location,
                status=punch_status,
                source_row=row_index,
            )
        )

    if "概况统计与打卡明细" in workbook.sheetnames:
        summary_sheet = workbook["概况统计与打卡明细"]
        for row_index, row in enumerate(summary_sheet.iter_rows(min_row=5, values_only=True), start=5):
            natural_date = _parse_natural_date(row[0] if len(row) > 0 else None)
            if len(row) > 1 and row[1]:
                employees.add((str(row[1]).strip(), str(row[2] or "").strip()))
            _collect_compensatory_leave(
                compensatory_leave_records,
                natural_date,
                row[13] if len(row) > 13 else None,
                summary_sheet.title,
                row_index,
            )

    if not employees:
        raise AttendanceParseError("没有找到员工信息")
    if len(employees) != 1:
        names = "、".join(sorted(name for name, _ in employees))
        raise AttendanceParseError(f"源文件包含多个员工：{names}")

    employee_name, employee_account = next(iter(employees))
    records.sort(key=lambda item: item.timestamp)
    return AttendanceSource(
        input_path=path,
        employee_name=employee_name,
        employee_account=employee_account,
        start_date=start_date,
        end_date=end_date,
        records=records,
        compensatory_leave_records=sorted(
            compensatory_leave_records.values(),
            key=lambda item: (item.work_date, item.description),
        ),
    )
