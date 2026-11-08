"""验证人工扣减后的待处理状态，与原始异常事实独立保留。"""
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from attendance_engine.calculator import calculate_attendance
from attendance_engine.calendar import HolidayCalendar
from attendance_engine.exporter import export_report
from attendance_engine.models import (
    AttendanceSource, CalculationStatus, CompensatoryLeaveRecord, PunchRecord, PunchType,
)
from attendance_engine.schedules import DEFAULT_SCHEDULE


def empty_source():
    """构造无打卡、无申请的公司统一调休日期。"""
    return AttendanceSource(Path('source.xlsx'), '测试员工', 'tester',
                            date(2026, 11, 2), date(2026, 11, 3), [])


def calculate(source, **kwargs):
    """注入固定日历，待处理测试不访问外部数据。"""
    return calculate_attendance(source, HolidayCalendar(online_years={2026}), **kwargs)


@pytest.mark.parametrize('reason', [None, '', '   ', '\t'])
def test_empty_manual_reason_uses_default(reason):
    """空白原因均可保存，并通过默认说明保留审计线索。"""
    day = calculate(empty_source(), overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480, 'leaveReason': reason,
    }]).days[0]
    assert day.leave_reason == '手动调整'
    assert day.net_extra_minutes == -480


@pytest.mark.parametrize('reason', [0, False, [], {}])
def test_non_text_manual_reason_is_rejected(reason):
    """无效协议类型不能被转换成看似用户填写的文字。"""
    with pytest.raises(ValueError, match='原因须为文本'):
        calculate(empty_source(), overrides=[{
            'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480, 'leaveReason': reason,
        }])


def test_manual_deduction_resolves_missing_punches_and_restore_reopens():
    """手动扣减清除对应待办；恢复自动后原始缺卡再次成为待办。"""
    source = empty_source()
    before = calculate(source)
    assert before.attention_day_count == 2
    assert before.days[0].attention_reasons[0]['code'] == 'missingBoth'
    assert '补齐打卡' in before.days[0].attention_reasons[0]['action']

    changed = calculate(source, overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480,
    }])
    day = changed.days[0]
    assert day.status is CalculationStatus.MISSING_BOTH
    assert day.actual_effective_minutes is None and day.extra_minutes is None
    assert day.net_extra_minutes == -480
    assert day.resolution_status == 'resolved' and not day.needs_attention
    assert day.attention_reasons == []
    assert changed.summary()['attentionDayCount'] == 1
    assert changed.summary()['resolvedDayCount'] == 1
    assert changed.abnormal_day_count == 2  # 原始异常事实不因人工处理而伪装正常。
    assert changed.to_dict()['days'][0]['needsAttention'] is False

    restored = calculate(source, overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': None,
    }])
    assert restored.attention_day_count == 2
    assert restored.resolved_day_count == 0
    assert restored.days[0].resolution_status == 'pending'


def test_single_day_schedule_does_not_resolve_missing_time():
    """仅修改午休不能把无关缺卡误标为已经处理。"""
    day = calculate(empty_source(), overrides=[{
        'workDate': '2026-11-02', 'schedule': {**DEFAULT_SCHEDULE, 'lunchEnd': '13:00'},
    }]).days[0]
    assert day.is_edited
    assert day.needs_attention and day.resolution_status == 'pending'
    assert [item['code'] for item in day.attention_reasons] == ['missingBoth']


def test_manual_deduction_cannot_resolve_source_conflict():
    """存在来源冲突时仍须选择版本，人工扣减不能绕过冲突。"""
    source = empty_source()
    source.conflicts = [{
        'workDate': '2026-11-02', 'sourceIds': ['a', 'b'], 'reason': '源记录不一致',
        'resolved': False, 'selectedSourceId': None,
    }]
    day = calculate(source, overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480,
    }]).days[0]
    assert day.status is CalculationStatus.SOURCE_CONFLICT
    assert day.needs_attention and day.resolution_status == 'pending'
    assert [item['code'] for item in day.attention_reasons] == ['sourceConflict']
    assert day.net_extra_minutes is None


def test_invalid_time_stays_pending_when_manual_leave_is_saved():
    """人工扣减不消除倒置的独立时间错误，待办须给出具体操作。"""
    source = empty_source()
    day_date = source.start_date
    source.records = [
        PunchRecord('测试员工', 'tester', datetime(2026, 11, 2, hour), day_date, kind, '')
        for hour, kind in [(10, PunchType.CHECK_IN), (9, PunchType.CHECK_OUT)]
    ]
    day = calculate(source, overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480,
    }]).days[0]
    assert day.status is CalculationStatus.INVALID_TIME_RANGE
    assert day.net_extra_minutes == -480
    assert day.resolution_status == 'pending' and day.needs_attention
    assert [item['code'] for item in day.attention_reasons] == ['invalidTimeRange']
    assert '修正上下班' in day.attention_reasons[0]['action']


def test_manual_zero_resolves_unknown_leave_without_removing_source_warning():
    """手动零也属于明确处理，原始未知申请与核对提示继续保留。"""
    source = empty_source()
    source.compensatory_leave_records = [
        CompensatoryLeaveRecord(source.start_date, 960, '调休假16小时', '原始概况'),
    ]
    before = calculate(source).days[0]
    assert [item['code'] for item in before.attention_reasons] == ['missingBoth', 'unknownLeave']
    day = calculate(source, overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 0,
    }]).days[0]
    assert day.requires_review and day.leave_requested_minutes is None
    assert day.net_extra_minutes == 0 and day.resolution_status == 'resolved'
    assert not day.needs_attention and day.attention_reasons == []
    assert day.leave_records[0].description == '调休假16小时'


def test_rest_day_without_records_and_missing_coverage_have_no_pending_task():
    """普通休息与未导入日期不是待处理缺卡任务。"""
    source = empty_source()
    source.start_date = date(2026, 11, 1)
    source.covered_dates = {source.start_date, source.end_date}
    report = calculate(source)
    assert report.days[0].resolution_status == 'normal'
    assert not report.days[0].needs_attention
    assert report.days[1].resolution_status == 'notImported'
    assert not report.days[1].needs_attention and not report.days[1].attention_reasons
    assert report.attention_day_count == 1


def test_export_preserves_original_status_and_current_handling_state(tmp_path):
    """导出追加处理状态，不覆盖原始缺卡和既有列位置。"""
    report = calculate(empty_source(), overrides=[{
        'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480,
    }])
    output = export_report(report, tmp_path)
    workbook = load_workbook(output, data_only=True)
    detail = workbook['每日明细']
    assert detail['D2'].value == '全天缺卡'
    assert detail['N2'].value == 8 and detail['X2'].value == '手动调整'
    assert detail['AF2'].value == '已处理'
    assert detail['AG2'].value is None and detail['AH2'].value is None
    assert detail['AF3'].value == '待处理'
    assert '缺少上下班' in detail['AG3'].value
    assert '补齐打卡' in detail['AH3'].value
    assert workbook['月度汇总']['K4'].value == '待处理天数'
    assert workbook['月度汇总']['L4'].value == 1
    assert workbook['月度汇总']['K7'].value == '已处理'
    assert workbook['分月汇总']['H2'].value == 1
    assert workbook['分月汇总']['I2'].value == 1
    workbook.close()
