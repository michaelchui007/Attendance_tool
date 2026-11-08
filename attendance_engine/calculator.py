from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .calendar import HolidayCalendar, classify_day
from .models import (
    AttendanceReport, AttendanceSource, CalculationStatus, CompensatoryLeaveRecord,
    DailyAttendanceResult, PunchRecord, PunchType,
)
from .parser import _resolve_work_date

STANDARD_MINUTES = 8 * 60
LUNCH_START = time(12, 0)
LUNCH_END = time(13, 30)


def _date_range(start: date, end: date):
    """按自然日遍历包含两端的统计区间。"""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _minutes_between(start: datetime, end: datetime) -> int:
    """计算非负整数分钟，保持与已有打卡分钟口径一致。"""
    return max(0, int((end - start).total_seconds() // 60))


def _parse_schedule(lunch_start: str | None, lunch_end: str | None) -> dict:
    """校验单段午休并推导保持八小时标准的下午结束时间。

    Args:
        lunch_start: HH:mm 格式的午休开始时间，省略时使用 12:00。
        lunch_end: HH:mm 格式的午休结束时间，省略时使用 13:30。

    Returns:
        可供界面、计算和导出共同使用的规则快照。

    Raises:
        ValueError: 午休格式非法或不能构成同日八小时工作安排。
    """
    values = [lunch_start if lunch_start is not None else '12:00', lunch_end if lunch_end is not None else '13:30']
    parsed = []
    for value in values:
        if not isinstance(value, str) or len(value) != 5:
            raise ValueError('午休时间必须为 HH:mm 格式')
        try:
            parsed.append(datetime.strptime(value, '%H:%M').time())
        except ValueError as exc:
            raise ValueError('午休时间必须为 HH:mm 格式') from exc
    start, end = parsed
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    work_end = 9 * 60 + STANDARD_MINUTES + end_minutes - start_minutes
    if not (9 * 60 < start_minutes < 17 * 60 and start_minutes < end_minutes and work_end < 24 * 60):
        raise ValueError('午休须在 09:00 之后、上午工作不足 8 小时，且结束晚于开始、当日可完成 8 小时工作')
    return {
        'lunchStart': start.strftime('%H:%M'), 'lunchEnd': end.strftime('%H:%M'),
        'workdayStart': '09:00', 'workdayEnd': f'{work_end // 60:02d}:{work_end % 60:02d}',
        'dayBoundary': '04:00', 'standardMinutes': STANDARD_MINUTES, 'locationBenefitEnabled': False,
    }


def _schedule_intervals(work_date: date, rules: dict) -> list[tuple[datetime, datetime]]:
    """返回请假可计入的上午、下午工作区间。"""
    values = [datetime.combine(work_date, time.fromisoformat(rules[key]))
              for key in ('workdayStart', 'lunchStart', 'lunchEnd', 'workdayEnd')]
    return [(values[0], values[1]), (values[2], values[3])]


def _merge_minutes(intervals: list[tuple[datetime, datetime]]) -> int:
    """合并重复和重叠申请区间，防止同一时段被多次扣减。"""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(_minutes_between(start, end) for start, end in merged)


def _leave_for_day(
    source: AttendanceSource, work_date: date, is_workday: bool, rules: dict,
) -> tuple[int | None, list[CompensatoryLeaveRecord], list[str]]:
    """从原申请区间分配当日量，并保留无法确定的申请为未知。

    Args:
        source: 原始报表及调休申请。
        work_date: 当前计算的自然工作日期。
        is_workday: 工作日历判定结果。
        rules: 当前午休及正常工作区间。

    Returns:
        当日申请分钟数、去重的原始依据，以及调整或异常说明。
    """
    intervals = []
    originals = []
    seen = set()
    simple_minutes = 0
    unknown = False
    notes = []
    for record in source.compensatory_leave_records:
        has_interval = record.start_at is not None and record.end_at is not None
        applies = record.start_at.date() <= work_date <= record.end_at.date() if has_interval else record.work_date == work_date
        if record.parse_warning and record.work_date == work_date:
            applies = True
        if not applies:
            continue
        key = (record.start_at, record.end_at) if has_interval else (record.work_date, record.description, record.item_index)
        if key in seen:
            continue
        seen.add(key)
        originals.append(record)
        if not is_workday:
            continue
        if record.parse_warning:
            unknown = True
            notes.append(record.parse_warning)
            continue
        if has_interval:
            for window_start, window_end in _schedule_intervals(work_date, rules):
                start, end = max(record.start_at, window_start), min(record.end_at, window_end)
                if start < end:
                    intervals.append((start, end))
        else:
            repeated_dates = {item.work_date for item in source.compensatory_leave_records if item.description == record.description and item.item_index == record.item_index}
            if record.parse_warning or record.minutes > STANDARD_MINUTES or len(repeated_dates) > 1:
                unknown = True
                notes.append(record.parse_warning or '申请缺少可核验的起止区间，不能确定每日调休量')
            else:
                simple_minutes += record.minutes
                notes.append('申请未提供起止区间，暂按源表明确单日时长处理，请核对原记录')
    if not is_workday:
        return 0, originals, ['休息日不计调休扣减'] if originals else []
    if unknown or (simple_minutes and intervals) or simple_minutes > STANDARD_MINUTES:
        return None, originals, notes + ['每日调休分摊待核对，净剩加班暂不计算']
    return _merge_minutes(intervals) + simple_minutes, originals, notes


def _parse_overrides(source: AttendanceSource, overrides: list[dict] | None) -> dict:
    """校验会话内首尾修订，拒绝跨考勤日移动及带时区的输入。"""
    if overrides is None:
        return {}
    if not isinstance(overrides, list):
        raise ValueError('时间修订必须为列表')
    result = {}
    for item in overrides:
        if not isinstance(item, dict):
            raise ValueError('时间修订必须包含日期及上下班时间')
        try:
            work_date = date.fromisoformat(item['workDate'])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('时间修订缺少有效工作日期') from exc
        if not source.start_date <= work_date <= source.end_date or work_date in result:
            raise ValueError('修订日期超出统计期间或重复')
        times = []
        for key in ('firstCheckIn', 'lastCheckOut'):
            if key not in item:
                raise ValueError('时间修订须同时提供上下班字段，缺卡时使用空值')
            value = item[key]
            try:
                stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
            except ValueError as exc:
                raise ValueError('修订时间格式无效') from exc
            if value is not None and stamp is None:
                raise ValueError('修订时间必须为日期时间字符串或空值')
            if stamp is not None and (stamp.tzinfo is not None or stamp.second or stamp.microsecond or _resolve_work_date(stamp) != work_date):
                raise ValueError('修订时间须精确到分钟，且属于当天 04:00 至次日 04:00 前')
            times.append(stamp)
        if all(times) and times[1] <= times[0]:
            raise ValueError('下班时间必须晚于上班时间')
        result[work_date] = tuple(times)
    return result


def calculate_attendance(
    source: AttendanceSource, holiday_calendar: HolidayCalendar | None = None,
    *, overrides: list[dict] | None = None, lunch_start: str | None = None, lunch_end: str | None = None,
) -> AttendanceReport:
    """根据日历、请假区间和已确认规则生成可追溯的考勤统计。

    Args:
        source: 源报表，函数不会修改其中的原始记录。
        holiday_calendar: 已加载的节假日日历，省略时使用内置分类。
        overrides: 当前会话按日期提供的上下班首尾覆盖值。
        lunch_start: 午休开始 HH:mm。
        lunch_end: 午休结束 HH:mm。

    Returns:
        同时包含原始资料、日结果、规则及汇总的统计报告。
    """
    rules = _parse_schedule(lunch_start, lunch_end)
    changes = _parse_overrides(source, overrides)
    grouped: dict[date, list[PunchRecord]] = defaultdict(list)
    for record in source.records:
        if source.start_date <= record.work_date <= source.end_date:
            grouped[record.work_date].append(record)
    results = []
    for work_date in _date_range(source.start_date, source.end_date):
        records = sorted(grouped.get(work_date, []), key=lambda item: item.timestamp)
        day_type = holiday_calendar.classify_day(work_date) if holiday_calendar is not None else classify_day(work_date)
        requested, leave_records, messages = _leave_for_day(source, work_date, day_type.is_workday, rules)
        original_start = min((item.timestamp for item in records if item.punch_type is PunchType.CHECK_IN), default=None)
        original_end = max((item.timestamp for item in records if item.punch_type is PunchType.CHECK_OUT), default=None)
        first, last = changes.get(work_date, (original_start, original_end))
        status = CalculationStatus.SUCCESS
        if first is None and last is None:
            status = CalculationStatus.MISSING_BOTH
        elif first is None:
            status = CalculationStatus.MISSING_CHECK_IN
        elif last is None:
            status = CalculationStatus.MISSING_CHECK_OUT
        elif last <= first:
            status = CalculationStatus.INVALID_TIME_RANGE
        day = DailyAttendanceResult(
            work_date=work_date, day_type=day_type, status=status,
            required_standard_minutes=STANDARD_MINUTES if day_type.is_workday else 0,
            actual_regular_minutes=None, actual_effective_minutes=None, extra_minutes=None,
            compensatory_leave_minutes=requested, net_extra_minutes=-requested if requested is not None else None,
            first_check_in=first, last_check_out=last, records=records, messages=messages,
            leave_requested_minutes=requested, leave_records=leave_records,
            original_first_check_in=original_start, original_last_check_out=original_end,
            is_edited=work_date in changes and (first, last) != (original_start, original_end),
            requires_review=requested is None or any(record.parse_warning or record.start_at is None for record in leave_records),
        )
        if day.is_edited:
            messages.append('已使用手动修订的上下班时间，原始打卡记录保留')
        if status is not CalculationStatus.SUCCESS:
            messages.append(f'{status.value}，实际工时及加班未计算')
            if requested:
                messages.append(f'按已确定的当日调休扣减 {requested} 分钟；缺卡无法核验剩余标准工时')
            results.append(day)
            continue
        raw = _minutes_between(first, last)
        if day_type.is_workday:
            lunch_start_at = datetime.combine(work_date, time.fromisoformat(rules['lunchStart']))
            lunch_end_at = datetime.combine(work_date, time.fromisoformat(rules['lunchEnd']))
            day.lunch_deduction_minutes = _minutes_between(max(first, lunch_start_at), min(last, lunch_end_at))
        effective = max(0, raw - day.lunch_deduction_minutes)
        day.raw_duration_minutes = raw
        day.actual_effective_minutes = effective
        day.actual_regular_minutes = min(effective, STANDARD_MINUTES) if day_type.is_workday else 0
        day.extra_minutes = max(0, effective - STANDARD_MINUTES) if day_type.is_workday else effective
        if requested is not None:
            day.compensatory_leave_minutes = min(requested, max(STANDARD_MINUTES - effective, 0)) if day_type.is_workday else 0
            day.net_extra_minutes = day.extra_minutes - day.compensatory_leave_minutes
            if requested:
                messages.append(f'当日申请 {requested} 分钟，实际扣减 {day.compensatory_leave_minutes} 分钟（不超过剩余标准工时）')
            if requested and effective >= STANDARD_MINUTES:
                day.requires_review = True
                messages.append('已满标准工时但仍有调休申请，请核对打卡与申请是否冲突')
        if day.lunch_deduction_minutes:
            messages.append(f'扣除午休 {day.lunch_deduction_minutes} 分钟')
        if not messages:
            messages.append('按实际打卡计算，不加计地点福利')
        results.append(day)
    return AttendanceReport(source=source, days=results, rules=rules, calendar_info=holiday_calendar.to_dict() if holiday_calendar is not None else {})
