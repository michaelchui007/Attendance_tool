from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class PunchType(StrEnum):
    CHECK_IN = "上班"
    CHECK_OUT = "下班"


class DayType(StrEnum):
    NORMAL_WORKDAY = "普通工作日"
    MAKEUP_WORKDAY = "国家调休工作日"
    STATUTORY_HOLIDAY = "国家节假日"
    ORDINARY_WEEKEND = "普通周末"

    @property
    def is_workday(self) -> bool:
        return self in {self.NORMAL_WORKDAY, self.MAKEUP_WORKDAY}


class CalculationStatus(StrEnum):
    SUCCESS = "正常"
    MISSING_CHECK_IN = "缺少上班卡"
    MISSING_CHECK_OUT = "缺少下班卡"
    MISSING_BOTH = "全天缺卡"
    INVALID_TIME_RANGE = "时间范围异常"


@dataclass(slots=True)
class PunchRecord:
    employee_name: str
    employee_account: str
    timestamp: datetime
    work_date: date
    punch_type: PunchType
    location: str
    status: str = ""
    source_row: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat(timespec="minutes")
        data["work_date"] = self.work_date.isoformat()
        data["punch_type"] = self.punch_type.value
        return data


@dataclass(slots=True)
class CompensatoryLeaveRecord:
    work_date: date
    minutes: int
    description: str
    source_sheet: str
    source_row: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workDate": self.work_date.isoformat(),
            "minutes": self.minutes,
            "description": self.description,
            "sourceSheet": self.source_sheet,
            "sourceRow": self.source_row,
        }


@dataclass(slots=True)
class AttendanceSource:
    input_path: Path
    employee_name: str
    employee_account: str
    start_date: date
    end_date: date
    records: list[PunchRecord]
    compensatory_leave_records: list[CompensatoryLeaveRecord] = field(default_factory=list)


@dataclass(slots=True)
class DailyAttendanceResult:
    work_date: date
    day_type: DayType
    status: CalculationStatus
    required_standard_minutes: int
    actual_regular_minutes: int | None
    actual_effective_minutes: int | None
    extra_minutes: int | None
    compensatory_leave_minutes: int = 0
    net_extra_minutes: int = 0
    raw_duration_minutes: int | None = None
    lunch_deduction_minutes: int = 0
    location_benefit_minutes: int = 0
    first_check_in: datetime | None = None
    last_check_out: datetime | None = None
    records: list[PunchRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workDate": self.work_date.isoformat(),
            "weekday": "一二三四五六日"[self.work_date.weekday()],
            "dayType": self.day_type.value,
            "status": self.status.value,
            "requiredStandardMinutes": self.required_standard_minutes,
            "actualRegularMinutes": self.actual_regular_minutes,
            "actualEffectiveMinutes": self.actual_effective_minutes,
            "extraMinutes": self.extra_minutes,
            "compensatoryLeaveMinutes": self.compensatory_leave_minutes,
            "netExtraMinutes": self.net_extra_minutes,
            "rawDurationMinutes": self.raw_duration_minutes,
            "lunchDeductionMinutes": self.lunch_deduction_minutes,
            "locationBenefitMinutes": self.location_benefit_minutes,
            "firstCheckIn": self.first_check_in.isoformat(timespec="minutes") if self.first_check_in else None,
            "lastCheckOut": self.last_check_out.isoformat(timespec="minutes") if self.last_check_out else None,
            "messages": self.messages,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(slots=True)
class AttendanceReport:
    source: AttendanceSource
    days: list[DailyAttendanceResult]
    output_path: Path | None = None
    calendar_info: dict[str, Any] = field(default_factory=dict)

    @property
    def required_standard_minutes(self) -> int:
        return sum(day.required_standard_minutes for day in self.days)

    @property
    def actual_effective_minutes(self) -> int:
        return sum(day.actual_effective_minutes or 0 for day in self.days)

    @property
    def extra_minutes(self) -> int:
        return sum(day.extra_minutes or 0 for day in self.days)

    @property
    def compensatory_leave_minutes(self) -> int:
        return sum(day.compensatory_leave_minutes for day in self.days)

    @property
    def net_extra_minutes(self) -> int:
        return self.extra_minutes - self.compensatory_leave_minutes

    @property
    def abnormal_day_count(self) -> int:
        return sum(day.status is not CalculationStatus.SUCCESS for day in self.days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": True,
            "employee": {
                "name": self.source.employee_name,
                "account": self.source.employee_account,
            },
            "period": {
                "startDate": self.source.start_date.isoformat(),
                "endDate": self.source.end_date.isoformat(),
            },
            "summary": {
                "requiredStandardMinutes": self.required_standard_minutes,
                "actualEffectiveMinutes": self.actual_effective_minutes,
                "extraMinutes": self.extra_minutes,
                "compensatoryLeaveMinutes": self.compensatory_leave_minutes,
                "netExtraMinutes": self.net_extra_minutes,
                "normalDayCount": len(self.days) - self.abnormal_day_count,
                "abnormalDayCount": self.abnormal_day_count,
            },
            "days": [day.to_dict() for day in self.days],
            "compensatoryLeaves": [
                record.to_dict() for record in self.source.compensatory_leave_records
            ],
            "calendar": self.calendar_info,
            "outputPath": str(self.output_path) if self.output_path else None,
        }
