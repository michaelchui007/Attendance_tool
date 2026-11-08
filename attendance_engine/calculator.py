from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .calendar import HolidayCalendar, classify_day
from .models import (
    AttendanceReport, AttendanceSource, CalculationStatus, CompensatoryLeaveRecord,
    DailyAttendanceResult, PunchRecord, PunchType,
)
from .parser import _resolve_work_date
from .schedules import normalize_settings, schedule_for_day, minute_value, validate_schedule

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


def _schedule_intervals(work_date: date, rules: dict) -> list[tuple[datetime, datetime]]:
    """返回请假可计入的正常工作区间，显式无午休时保留完整区间。"""
    stamp = lambda key: datetime.combine(work_date, time.fromisoformat(rules[key]))
    if rules['lunchStart'] is None:
        return [(stamp('workdayStart'), stamp('workdayEnd'))]
    return [(stamp('workdayStart'), stamp('lunchStart')), (stamp('lunchEnd'), stamp('workdayEnd'))]


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
        chosen = source.source_choices.get(work_date.isoformat())
        if chosen and record.sources and not any(item["sourceId"] == chosen for item in record.sources):
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
    """独立校验打卡、扣减和单日作息修订，允许只指定其中一项。"""
    if overrides is None:
        return {}
    if not isinstance(overrides, list):
        raise ValueError('考勤修订必须为列表')
    result = {}
    for item in overrides:
        if not isinstance(item, dict):
            raise ValueError('考勤修订必须为对象')
        try:
            work_date = date.fromisoformat(item['workDate'])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('考勤修订缺少有效日期') from exc
        if not source.start_date <= work_date <= source.end_date or work_date in result:
            raise ValueError('修订日期超出统计期间或重复')
        if source.covered_dates is not None and work_date not in source.covered_dates:
            raise ValueError(f'{work_date} 尚未导入报表，不能应用修订')
        change = dict(item)
        if 'resetTimes' in item and type(item['resetTimes']) is not bool:
            raise ValueError('恢复原始时间标记须为布尔值')
        if not item.get('resetTimes', False) and ('firstCheckIn' in item or 'lastCheckOut' in item):
            if not all(key in item for key in ('firstCheckIn', 'lastCheckOut')):
                raise ValueError('修订打卡时须同时提供上下班字段，缺卡使用空值')
            times = []
            for key in ('firstCheckIn', 'lastCheckOut'):
                value = item[key]
                try:
                    stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
                except ValueError as exc:
                    raise ValueError('修订时间格式无效') from exc
                if value is not None and stamp is None:
                    raise ValueError('修订时间必须为日期时间字符串或空值')
                if stamp is not None and (stamp.tzinfo is not None or stamp.second or stamp.microsecond or _resolve_work_date(stamp) != work_date):
                    raise ValueError('修订时间须精确到分钟，且属于当天04:00至次日04:00前')
                times.append(stamp)
            if all(times) and times[1] <= times[0]:
                raise ValueError('下班时间必须晚于上班时间')
            change['times'] = tuple(times)
        manual = item.get('compensatoryLeaveMinutes')
        if manual is not None:
            minute_value(manual, '手动调休扣减', allow_zero=True)
            reason = item.get('leaveReason')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('手动调休扣减须填写修订原因')
            change['leaveReason'] = reason.strip()
        if item.get('schedule') is not None:
            change['schedule'] = validate_schedule(item['schedule'])
        result[work_date] = change
    return result


def calculate_attendance(
    source: AttendanceSource, holiday_calendar: HolidayCalendar | None = None,
    *, overrides: list[dict] | None = None, lunch_start: str | None = None, lunch_end: str | None = None,
    settings: dict | None = None,
) -> AttendanceReport:
    """按有效来源、独立标准及当日规则计算，不修改原始资料。

    Args:
        source: 已解析或合并的源记录。
        holiday_calendar: 注入的工作日历。
        overrides: 每日打卡、手动扣减或作息覆盖。
        lunch_start: 旧协议午休开始。
        lunch_end: 旧协议午休结束。
        settings: 系统标准、默认作息及日期段规则。

    Returns:
        含日结果、自动值、修订审计和来源的完整报告。
    """
    rules = normalize_settings(settings, lunch_start, lunch_end)
    changes = _parse_overrides(source, overrides)
    grouped: dict[date, list[PunchRecord]] = defaultdict(list)
    for record in source.records:
        if source.start_date <= record.work_date <= source.end_date:
            chosen = source.source_choices.get(record.work_date.isoformat())
            if not chosen or not record.sources or any(item['sourceId'] == chosen for item in record.sources):
                grouped[record.work_date].append(record)
    unresolved = {item['workDate'] for item in source.conflicts if not item['resolved']}
    results = []
    for work_date in _date_range(source.start_date, source.end_date):
        change = changes.get(work_date, {})
        schedule, schedule_source = schedule_for_day(rules, work_date, change.get('schedule'))
        standard = rules['standardMinutes']
        records = sorted(grouped.get(work_date, []), key=lambda item: item.timestamp)
        day_type = holiday_calendar.classify_day(work_date) if holiday_calendar is not None else classify_day(work_date)
        covered = source.covered_dates is None or work_date in source.covered_dates
        requested, leave_records, messages = _leave_for_day(source, work_date, day_type.is_workday, schedule)
        original_start = min((item.timestamp for item in records if item.punch_type is PunchType.CHECK_IN), default=None)
        original_end = max((item.timestamp for item in records if item.punch_type is PunchType.CHECK_OUT), default=None)
        first, last = change.get('times', (original_start, original_end))
        status = CalculationStatus.SUCCESS
        if first is None and last is None:
            status = CalculationStatus.MISSING_BOTH
        elif first is None:
            status = CalculationStatus.MISSING_CHECK_IN
        elif last is None:
            status = CalculationStatus.MISSING_CHECK_OUT
        elif last <= first:
            status = CalculationStatus.INVALID_TIME_RANGE
        if not covered:
            status = CalculationStatus.NOT_IMPORTED
        elif work_date.isoformat() in unresolved:
            status = CalculationStatus.SOURCE_CONFLICT
        selected_source = source.source_choices.get(work_date.isoformat())
        source_ids = sorted({entry['sourceId'] for record in [*records, *leave_records] for entry in record.sources
                             if not selected_source or entry['sourceId'] == selected_source}
                            | {item['id'] for item in source.files if not item['duplicateOf']
                               and item['startDate'] <= work_date.isoformat() <= item['endDate']
                               and (not selected_source or item['id'] == selected_source)})
        day = DailyAttendanceResult(
            work_date=work_date, day_type=day_type, status=status, covered=covered, source_ids=source_ids,
            required_standard_minutes=standard if covered and day_type.is_workday else 0,
            actual_regular_minutes=None, actual_effective_minutes=None, extra_minutes=None,
            compensatory_leave_minutes=requested, net_extra_minutes=-requested if requested is not None else None,
            first_check_in=first, last_check_out=last, records=records, messages=messages,
            leave_requested_minutes=requested, leave_records=leave_records,
            original_first_check_in=original_start, original_last_check_out=original_end,
            is_edited=(first, last) != (original_start, original_end),
            requires_review=requested is None or any(record.parse_warning or record.start_at is None for record in leave_records),
            schedule=schedule, schedule_source=schedule_source,
        )
        if not covered or status is CalculationStatus.SOURCE_CONFLICT:
            day.leave_requested_minutes = day.compensatory_leave_minutes = day.net_extra_minutes = None
            day.auto_compensatory_leave_minutes = None
            day.requires_review = covered
            day.messages = ['日期尚未导入，不计入已覆盖期间汇总'] if not covered else ['来源记录冲突，请先选择当天采用的报表版本']
            results.append(day)
            continue
        if day.is_edited:
            messages.append('已使用手动修订的上下班时间，原始打卡记录保留')
        if status is CalculationStatus.SUCCESS:
            raw = _minutes_between(first, last)
            if day_type.is_workday and schedule['lunchStart'] is not None:
                left = datetime.combine(work_date, time.fromisoformat(schedule['lunchStart']))
                right = datetime.combine(work_date, time.fromisoformat(schedule['lunchEnd']))
                day.lunch_deduction_minutes = _minutes_between(max(first, left), min(last, right))
            effective = max(0, raw - day.lunch_deduction_minutes)
            day.raw_duration_minutes = raw
            day.actual_effective_minutes = effective
            day.actual_regular_minutes = min(effective, standard) if day_type.is_workday else 0
            day.extra_minutes = max(0, effective - standard) if day_type.is_workday else effective
            if requested is not None:
                day.compensatory_leave_minutes = min(requested, max(standard - effective, 0)) if day_type.is_workday else 0
                day.net_extra_minutes = day.extra_minutes - day.compensatory_leave_minutes
                if requested:
                    messages.append(f'当日申请 {requested} 分钟，自动扣减 {day.compensatory_leave_minutes} 分钟（不超过剩余标准工时）')
                if requested and effective >= standard:
                    day.requires_review = True
                    messages.append('已满标准工时但仍有调休申请，请核对打卡与申请是否冲突')
        else:
            messages.append(f'{status.value}，实际工时及加班未计算')
            if requested:
                messages.append(f'按已确定的当日调休扣减 {requested} 分钟；缺卡无法核验剩余标准工时')
        day.auto_compensatory_leave_minutes = day.compensatory_leave_minutes
        manual = change.get('compensatoryLeaveMinutes')
        if manual is not None:
            day.compensatory_leave_minutes = manual
            day.net_extra_minutes = (day.extra_minutes or 0) - manual
            day.is_leave_edited = day.is_edited = True
            day.leave_reason = change['leaveReason']
            messages.append(f'手动指定扣减 {manual} 分钟，替代自动值；原因：{day.leave_reason}')
        if change.get('schedule') is not None:
            day.is_edited = True
        if schedule['netScheduleMinutes'] != standard:
            messages.append(f'作息净时长 {schedule["netScheduleMinutes"]} 分钟，独立标准工时 {standard} 分钟')
        if day.lunch_deduction_minutes:
            messages.append(f'扣除午休 {day.lunch_deduction_minutes} 分钟')
        if not messages:
            messages.append('按实际打卡计算，不加计地点福利')
        results.append(day)
    return AttendanceReport(source=source, days=results, rules=rules,
                            calendar_info=holiday_calendar.to_dict() if holiday_calendar is not None else {'years': [], 'warnings': []})
