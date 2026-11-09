"""验证 v0.3.2 工时不足扣减及日、月、导出结果一致性。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from attendance_engine.calculator import calculate_attendance
from attendance_engine.calendar import HolidayCalendar, HolidayOverride
from attendance_engine.exporter import export_report
from attendance_engine.models import (
    AttendanceSource, CompensatoryLeaveRecord, DayType, PunchRecord, PunchType,
)
from attendance_engine.parser import parse_attendance_workbook


DAY = date(2026, 10, 26)


def _pair(work_date: date, first: str, last: str) -> list[PunchRecord]:
    """创建指定日期的一对匿名原始打卡，不读取真实员工报表。"""
    return [PunchRecord('测试员工', 'tester', datetime.fromisoformat(f'{work_date}T{clock}'),
                        work_date, kind, '')
            for clock, kind in [(first, PunchType.CHECK_IN), (last, PunchType.CHECK_OUT)]]


def _source(work_date: date = DAY, first: str = '09:30', last: str = '18:00') -> AttendanceSource:
    """构造只有一天的匿名报表，默认保留用户反馈中的有效时间。"""
    return AttendanceSource(Path('匿名短工时.xlsx'), '测试员工', 'tester', work_date, work_date,
                            _pair(work_date, first, last))


def _calculate(source: AttendanceSource, **kwargs):
    """注入固定周历并默认扣除一小时午休，避免网络和系统缓存干扰。"""
    kwargs.setdefault('settings', {'lunchStart': '12:00', 'lunchEnd': '13:00'})
    return calculate_attendance(source, HolidayCalendar(online_years={2026}), **kwargs)


@pytest.mark.parametrize(('lunch_end', 'effective', 'shortfall'), [('13:00', 450, 30), ('13:30', 420, 60)])
def test_october_26_workbook_shortfall(tmp_path, lunch_end, effective, shortfall):
    """匿名真实列结构报表的工时不足进入日、月及总净额。"""
    path = tmp_path / '匿名十月报表.xlsx'
    workbook = Workbook()
    summary = workbook.active
    summary.title = '概况统计与打卡明细'
    detail = workbook.create_sheet('打卡详情')
    detail['A2'] = '统计时间:10-26 ～ 10-26 制表时间:2026-10-26'
    for index, (kind, stamp) in enumerate([('上班', '09:30'), ('下班', '18:00')], 5):
        for column, value in enumerate(['2026/10/26', '测试员工', 'tester', None, None, None,
                                         kind, None, stamp, '正常', '测试地点'], 1):
            detail.cell(index, column, value)
    workbook.save(path)
    workbook.close()
    original = path.read_bytes()
    report = _calculate(parse_attendance_workbook(path),
                        settings={'lunchStart': '12:00', 'lunchEnd': lunch_end})
    day = report.days[0]
    assert day.actual_effective_minutes == effective
    assert day.extra_minutes == day.compensatory_leave_minutes == 0
    assert day.shortfall_minutes == shortfall and day.net_extra_minutes == -shortfall
    assert report.net_extra_minutes == -shortfall
    assert report.to_dict()['days'][0]['shortfallMinutes'] == shortfall
    assert report.summary()['shortfallMinutes'] == shortfall
    assert report.monthly_summaries()[0]['netExtraMinutes'] == -shortfall
    assert path.read_bytes() == original


def test_time_edit_and_restore_recalculate_without_changing_original():
    """编辑上下班后从正加班变为不足，恢复原始时间时恢复原结果。"""
    source = _source(first='09:00', last='18:30')
    original = [record.to_dict() for record in source.records]
    day = _calculate(source, overrides=[{'workDate': str(DAY), 'firstCheckIn': f'{DAY}T09:30',
                                        'lastCheckOut': f'{DAY}T18:00'}]).days[0]
    assert day.actual_effective_minutes == 450 and day.net_extra_minutes == -30
    assert day.shortfall_minutes == 30 and day.is_edited
    assert day.original_first_check_in == datetime(2026, 10, 26, 9)
    assert day.original_last_check_out == datetime(2026, 10, 26, 18, 30)
    assert [record.to_dict() for record in source.records] == original
    restored = _calculate(source, overrides=[{'workDate': str(DAY), 'resetTimes': True}]).days[0]
    assert restored.actual_effective_minutes == 510 and restored.net_extra_minutes == 30
    assert restored.shortfall_minutes == 0 and not restored.is_edited


@pytest.mark.parametrize(('standard', 'shortfall', 'net'), [(420, 0, 30), (450, 0, 0), (480, 30, -30), (510, 60, -60)])
def test_shortfall_uses_configured_standard(standard, shortfall, net):
    """独立标准工时同时决定正加班和不足，不写死八小时。"""
    day = _calculate(_source(), settings={'standardMinutes': standard, 'lunchEnd': '13:00'}).days[0]
    assert day.actual_effective_minutes == 450
    assert day.shortfall_minutes == shortfall and day.net_extra_minutes == net


def test_date_rule_and_daily_lunch_override_recalculate_shortfall():
    """日期段午休和单日无午休按优先级重算不足，恢复后回到日期段。"""
    from attendance_engine.schedules import DEFAULT_SCHEDULE

    settings = {**DEFAULT_SCHEDULE, 'dateRules': [
        {**DEFAULT_SCHEDULE, 'id': 'autumn', 'startDate': str(DAY), 'endDate': str(DAY),
         'lunchEnd': '13:00'}]}
    source = _source()
    date_rule_day = _calculate(source, settings=settings).days[0]
    assert date_rule_day.actual_effective_minutes == 450 and date_rule_day.shortfall_minutes == 30
    changed = _calculate(source, settings=settings, overrides=[{
        'workDate': str(DAY), 'schedule': {**DEFAULT_SCHEDULE, 'lunchStart': None, 'lunchEnd': None}}]).days[0]
    assert changed.actual_effective_minutes == 510 and changed.shortfall_minutes == 0
    assert changed.net_extra_minutes == 30
    restored = _calculate(source, settings=settings, overrides=[{'workDate': str(DAY), 'schedule': None}]).days[0]
    assert restored.shortfall_minutes == 30 and restored.net_extra_minutes == -30


def test_rest_day_and_makeup_workday_have_distinct_shortfall_rules():
    """周末完整跨度计加班，国家调休上班日仍按工作日标准扣不足。"""
    saturday = date(2026, 10, 24)
    source = _source(saturday)
    rest = _calculate(source).days[0]
    assert rest.day_type is DayType.ORDINARY_WEEKEND
    assert rest.required_standard_minutes == rest.shortfall_minutes == 0
    assert rest.actual_effective_minutes == rest.net_extra_minutes == 510
    calendar = HolidayCalendar(overrides={saturday: HolidayOverride('调休上班', False)}, online_years={2026})
    makeup = calculate_attendance(source, calendar, settings={'lunchEnd': '13:00'}).days[0]
    assert makeup.day_type is DayType.MAKEUP_WORKDAY
    assert makeup.shortfall_minutes == 30 and makeup.net_extra_minutes == -30


@pytest.mark.parametrize(('requested', 'deducted'), [(120, 120), (300, 240)])
def test_c03_leave_does_not_duplicate_shortfall(requested, deducted):
    """已工作四小时申请两小时只扣两小时，申请五小时最多扣四小时。"""
    source = _source(first='08:00', last='12:00')
    source.compensatory_leave_records = [CompensatoryLeaveRecord(DAY, requested, '调休申请', '测试')]
    day = _calculate(source).days[0]
    assert day.actual_effective_minutes == 240
    assert day.compensatory_leave_minutes == deducted and day.net_extra_minutes == -deducted
    assert day.shortfall_minutes == 0


def test_leave_outside_schedule_does_not_become_unrequested_shortfall():
    """有原始申请但与正常时段无交集时，保留申请规则且不自动补扣。"""
    source = _source()
    source.compensatory_leave_records = [CompensatoryLeaveRecord(
        DAY, 30, '调休假0.5小时', '测试',
        start_at=datetime(2026, 10, 26, 18, 30), end_at=datetime(2026, 10, 26, 19))]
    day = _calculate(source).days[0]
    assert day.leave_requested_minutes == day.shortfall_minutes == day.net_extra_minutes == 0
    assert day.leave_records


@pytest.mark.parametrize('manual', [0, 15, 480])
def test_manual_deduction_has_priority_and_restoring_auto_restores_shortfall(manual):
    """人工最终扣减包括零均优先，恢复自动后才重新扣工时不足。"""
    source = _source()
    day = _calculate(source, overrides=[{'workDate': str(DAY), 'compensatoryLeaveMinutes': manual}]).days[0]
    assert day.shortfall_minutes == 0 and day.net_extra_minutes == -manual
    restored = _calculate(source, overrides=[{'workDate': str(DAY), 'compensatoryLeaveMinutes': None}]).days[0]
    assert restored.shortfall_minutes == 30 and restored.net_extra_minutes == -30


@pytest.mark.parametrize('kind', ['missing_both', 'missing_out', 'invalid', 'unknown_leave', 'not_imported', 'conflict'])
def test_uncertain_days_do_not_assume_zero_work_and_deduct_standard(kind):
    """缺卡、时间异常、未知申请、来源冲突和未导入均不推定不足。"""
    source = _source()
    if kind == 'missing_both':
        source.records = []
    elif kind == 'missing_out':
        source.records = source.records[:1]
    elif kind == 'invalid':
        source.records = _pair(DAY, '18:00', '09:30')
    elif kind == 'unknown_leave':
        source.compensatory_leave_records = [CompensatoryLeaveRecord(DAY, 960, '调休假16小时', '测试')]
    elif kind == 'not_imported':
        source.covered_dates = set()
    else:
        source.conflicts = [{'workDate': str(DAY), 'resolved': False}]
    report = _calculate(source)
    day = report.days[0]
    assert day.shortfall_minutes == 0
    if kind in {'unknown_leave', 'not_imported', 'conflict'}:
        assert day.net_extra_minutes is None
    else:
        assert day.actual_effective_minutes is None and day.net_extra_minutes == 0
    if kind in {'unknown_leave', 'conflict'}:
        assert report.net_extra_minutes is None


def test_monthly_and_excel_totals_include_shortfall_once(tmp_path):
    """跨月正负工时逐日相加，并核对协议、首页汇总和导出末列。"""
    november = date(2026, 11, 2)
    source = _source()
    source.end_date = november
    source.covered_dates = {DAY, november}
    source.records.extend(_pair(november, '09:00', '19:00'))
    report = _calculate(source)
    assert report.extra_minutes == 60 and report.compensatory_leave_minutes == 0
    assert report.summary()['shortfallMinutes'] == 30 and report.net_extra_minutes == 30
    assert sum(day.net_extra_minutes or 0 for day in report.days) == 30
    months = report.monthly_summaries()
    assert [month['netExtraMinutes'] for month in months] == [-30, 60]
    assert [month['shortfallMinutes'] for month in months] == [30, 0]
    workbook = load_workbook(export_report(report, tmp_path), data_only=True)
    try:
        assert workbook['月度汇总']['J4'].value == '0小时30分钟'
        detail = workbook['每日明细']
        headers = [cell.value for cell in detail[1]]
        assert headers[-1] == '工时不足扣减'
        assert detail.cell(2, len(headers)).value == 0.5
        assert detail['O2'].value == -0.5
        month_sheet = workbook['分月汇总']
        assert month_sheet.cell(1, month_sheet.max_column).value == '工时不足扣减'
        assert month_sheet.cell(2, month_sheet.max_column).value == '0小时30分钟'
        assert month_sheet.cell(3, month_sheet.max_column).value == '0小时00分钟'
        assert month_sheet['F2'].value == '-0小时30分钟'
        assert month_sheet['F3'].value == '1小时00分钟'
    finally:
        workbook.close()


def test_saved_workbook_state_restores_shortfall_on_new_path(tmp_path):
    """源表携带时间修订和午休配置后，换路径导入仍计算负三十分钟。"""
    from attendance_engine import portable
    from attendance_engine.importer import merge_workbooks
    from test_v031 import make_book

    path = make_book(tmp_path, '原始匿名报表.xlsx', DAY, DAY, punches=[
        (DAY, '上班', '09:00', '正常'), (DAY, '下班', '18:30', '正常')])
    source, _ = merge_workbooks([str(path)])
    overrides = [{'workDate': str(DAY), 'firstCheckIn': f'{DAY}T09:30', 'lastCheckOut': f'{DAY}T18:00'}]
    saved = portable.save_workbook_state(
        [str(path)], {'standardMinutes': 480, 'lunchEnd': '13:00'}, overrides,
        {'name': source.employee_name, 'account': source.employee_account}, source.files)
    assert saved['allSaved']
    moved = tmp_path / '换电脑后的匿名报表.xlsx'
    moved.write_bytes(path.read_bytes())
    state = portable.inspect_workbook_state([str(moved)])['files'][0]
    assert state['status'] == 'valid'
    restored_source, _ = merge_workbooks([str(moved)])
    report = _calculate(restored_source, settings=state['state']['settings'], overrides=state['state']['overrides'])
    assert report.days[0].actual_effective_minutes == 450
    assert report.days[0].shortfall_minutes == 30 and report.net_extra_minutes == -30
    assert _calculate(restored_source).days[0].actual_effective_minutes == 510
