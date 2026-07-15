from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from .calendar import HolidayCalendar, classify_day
from .models import (
    AttendanceReport,
    AttendanceSource,
    CalculationStatus,
    DailyAttendanceResult,
    PunchRecord,
    PunchType,
)

STANDARD_MINUTES = 8 * 60
LOCATION_KEYWORD = "云岭东路586号"
LOCATION_BENEFIT_MINUTES = 30
LUNCH_START = time(12, 0)
LUNCH_END = time(13, 30)


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _lunch_overlap_minutes(work_date: date, start: datetime, end: datetime) -> int:
    lunch_start = datetime.combine(work_date, LUNCH_START)
    lunch_end = datetime.combine(work_date, LUNCH_END)
    overlap_start = max(start, lunch_start)
    overlap_end = min(end, lunch_end)
    return _minutes_between(overlap_start, overlap_end) if overlap_end > overlap_start else 0


def calculate_attendance(
    source: AttendanceSource,
    holiday_calendar: HolidayCalendar | None = None,
) -> AttendanceReport:
    grouped: dict[date, list[PunchRecord]] = defaultdict(list)
    for record in source.records:
        if source.start_date <= record.work_date <= source.end_date:
            grouped[record.work_date].append(record)
    compensatory_leave_by_date: dict[date, int] = defaultdict(int)
    for record in source.compensatory_leave_records:
        if source.start_date <= record.work_date <= source.end_date:
            compensatory_leave_by_date[record.work_date] += record.minutes

    results: list[DailyAttendanceResult] = []
    for work_date in _date_range(source.start_date, source.end_date):
        records = sorted(grouped.get(work_date, []), key=lambda item: item.timestamp)
        compensatory_leave_minutes = compensatory_leave_by_date[work_date]
        leave_message = (
            [f"假勤申请包含调休 {compensatory_leave_minutes / 60:g} 小时，已从净剩加班时长扣除"]
            if compensatory_leave_minutes
            else []
        )
        day_type = (
            holiday_calendar.classify_day(work_date)
            if holiday_calendar is not None
            else classify_day(work_date)
        )
        required_standard = STANDARD_MINUTES if day_type.is_workday else 0
        check_ins = [record for record in records if record.punch_type is PunchType.CHECK_IN]
        check_outs = [record for record in records if record.punch_type is PunchType.CHECK_OUT]
        first_check_in = min((record.timestamp for record in check_ins), default=None)
        last_check_out = max((record.timestamp for record in check_outs), default=None)

        if not check_ins and not check_outs:
            results.append(
                DailyAttendanceResult(
                    work_date=work_date,
                    day_type=day_type,
                    status=CalculationStatus.MISSING_BOTH,
                    required_standard_minutes=required_standard,
                    actual_regular_minutes=None,
                    actual_effective_minutes=None,
                    extra_minutes=None,
                    compensatory_leave_minutes=compensatory_leave_minutes,
                    net_extra_minutes=-compensatory_leave_minutes,
                    records=records,
                    messages=["当天没有上班或下班打卡记录", *leave_message],
                )
            )
            continue
        if not check_ins:
            results.append(
                DailyAttendanceResult(
                    work_date=work_date,
                    day_type=day_type,
                    status=CalculationStatus.MISSING_CHECK_IN,
                    required_standard_minutes=required_standard,
                    actual_regular_minutes=None,
                    actual_effective_minutes=None,
                    extra_minutes=None,
                    compensatory_leave_minutes=compensatory_leave_minutes,
                    net_extra_minutes=-compensatory_leave_minutes,
                    last_check_out=last_check_out,
                    records=records,
                    messages=["缺少上班打卡，实际工时未计算", *leave_message],
                )
            )
            continue
        if not check_outs:
            results.append(
                DailyAttendanceResult(
                    work_date=work_date,
                    day_type=day_type,
                    status=CalculationStatus.MISSING_CHECK_OUT,
                    required_standard_minutes=required_standard,
                    actual_regular_minutes=None,
                    actual_effective_minutes=None,
                    extra_minutes=None,
                    compensatory_leave_minutes=compensatory_leave_minutes,
                    net_extra_minutes=-compensatory_leave_minutes,
                    first_check_in=first_check_in,
                    records=records,
                    messages=["缺少下班打卡，实际工时未计算", *leave_message],
                )
            )
            continue
        if last_check_out <= first_check_in:
            results.append(
                DailyAttendanceResult(
                    work_date=work_date,
                    day_type=day_type,
                    status=CalculationStatus.INVALID_TIME_RANGE,
                    required_standard_minutes=required_standard,
                    actual_regular_minutes=None,
                    actual_effective_minutes=None,
                    extra_minutes=None,
                    compensatory_leave_minutes=compensatory_leave_minutes,
                    net_extra_minutes=-compensatory_leave_minutes,
                    first_check_in=first_check_in,
                    last_check_out=last_check_out,
                    records=records,
                    messages=["最晚下班时间不晚于最早上班时间", *leave_message],
                )
            )
            continue

        raw_minutes = _minutes_between(first_check_in, last_check_out)
        if day_type.is_workday:
            lunch_minutes = _lunch_overlap_minutes(work_date, first_check_in, last_check_out)
            has_benefit = any(LOCATION_KEYWORD in record.location for record in records)
            benefit_minutes = LOCATION_BENEFIT_MINUTES if has_benefit else 0
            effective_minutes = max(0, raw_minutes - lunch_minutes)
            regular_minutes = min(effective_minutes, STANDARD_MINUTES)
            credited_minutes = effective_minutes + benefit_minutes
            extra_minutes = max(0, credited_minutes - STANDARD_MINUTES)
        else:
            lunch_minutes = 0
            benefit_minutes = 0
            effective_minutes = raw_minutes
            regular_minutes = 0
            extra_minutes = effective_minutes

        messages = []
        if lunch_minutes:
            messages.append(f"扣除午休 {lunch_minutes} 分钟")
        if benefit_minutes:
            messages.append("地点包含“云岭东路586号”，地点福利 30 分钟计入加班计算")
        if not messages:
            messages.append("未发生午休扣除或地点福利加计")
        messages.extend(leave_message)

        results.append(
            DailyAttendanceResult(
                work_date=work_date,
                day_type=day_type,
                status=CalculationStatus.SUCCESS,
                required_standard_minutes=required_standard,
                actual_regular_minutes=regular_minutes,
                actual_effective_minutes=effective_minutes,
                extra_minutes=extra_minutes,
                compensatory_leave_minutes=compensatory_leave_minutes,
                net_extra_minutes=extra_minutes - compensatory_leave_minutes,
                raw_duration_minutes=raw_minutes,
                lunch_deduction_minutes=lunch_minutes,
                location_benefit_minutes=benefit_minutes,
                first_check_in=first_check_in,
                last_check_out=last_check_out,
                records=records,
                messages=messages,
            )
        )

    return AttendanceReport(
        source=source,
        days=results,
        calendar_info=holiday_calendar.to_dict() if holiday_calendar is not None else {},
    )
