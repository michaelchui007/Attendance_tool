"""验证0.3.1手动扣减、独立配置、批量来源和常驻协议。"""
from __future__ import annotations

import argparse
import io
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from attendance_engine import cli
from attendance_engine.calculator import calculate_attendance
from attendance_engine.calendar import HolidayCalendar
from attendance_engine.exporter import export_report
from attendance_engine.importer import SourceCache, merge_workbooks
from attendance_engine.models import AttendanceSource, CompensatoryLeaveRecord, CalculationStatus, PunchRecord, PunchType
from attendance_engine.schedules import DEFAULT_SCHEDULE


def make_book(tmp_path, name, start, end, punches=(), leaves=(), account='tester'):
    """生成无个人资料的最小真实列结构报表。

    Args:
        tmp_path: 临时文件夹。
        name: 文件名。
        start: 统计开始日期。
        end: 统计结束日期。
        punches: 日期、类型、时间、状态组成的打卡。
        leaves: 源日期和申请文本组成的概况记录。
        account: 用于验证不同员工隔离的匿名账号。

    Returns:
        保存的测试文件路径。
    """
    path = tmp_path / name
    wb = Workbook()
    summary = wb.active
    summary.title = '概况统计与打卡明细'
    detail = wb.create_sheet('打卡详情')
    detail['A2'] = f'统计时间:{start:%m-%d} ～ {end:%m-%d} 制表时间:{end:%Y-%m-%d}'
    for number, (day, kind, stamp, status) in enumerate(punches, 5):
        values = [f'{day:%Y/%m/%d}', '测试员工', account, None, None, None, kind, None, stamp, status, '测试办公地点']
        for col, value in enumerate(values, 1):
            detail.cell(number, col, value)
    for number, (day, text) in enumerate(leaves or [(start, '--')], 5):
        for col, value in [(1, f'{day:%Y/%m/%d}'), (2, '测试员工'), (3, account), (14, text)]:
            summary.cell(number, col, value)
    wb.save(path)
    wb.close()
    return path


def calculate(source, **kwargs):
    """注入固定星期日历，不访问网络与年度缓存。"""
    return calculate_attendance(source, HolidayCalendar(online_years={2026, 2027}), **kwargs)


def empty_source(start=date(2026, 11, 2), end=date(2026, 11, 3)):
    """创建公司安排休假但无任何打卡或假勤申请的来源。"""
    return AttendanceSource(Path('source.xlsx'), '测试员工', 'tester', start, end, [])


def test_manual_company_leave_without_punches_or_application():
    """11月2/3日可独立扣减各8h，保留未知实际工时和原始申请量。"""
    source = empty_source()
    report = calculate(source, overrides=[{'workDate': f'2026-11-0{day}', 'compensatoryLeaveMinutes': 480,
                                         'leaveReason': '公司统一调休'} for day in (2, 3)])
    assert report.net_extra_minutes == -960
    for day in report.days:
        assert day.actual_effective_minutes is None
        assert day.leave_requested_minutes == day.auto_compensatory_leave_minutes == 0
        assert day.compensatory_leave_minutes == 480 and day.is_leave_edited and day.is_edited
    assert not source.records and not source.compensatory_leave_records
    assert calculate(source, overrides=[{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': None}]).net_extra_minutes == 0


@pytest.mark.parametrize('value', [-1, 1441, True, 2.5, '480'])
def test_manual_deduction_rejects_invalid_minutes(value):
    """内部扣减只接受非负整分钟，避免符号反转和隐式取整。"""
    with pytest.raises(ValueError):
        calculate(empty_source(), overrides=[{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': value, 'leaveReason': '测试'}])


def test_manual_deduction_requires_reason_and_zero_overrides_unknown():
    """手动零与自动不同，未知来源提示仍保留。"""
    source = empty_source()
    source.compensatory_leave_records = [CompensatoryLeaveRecord(date(2026, 11, 2), 960, '调休假16小时', '测试')]
    with pytest.raises(ValueError, match='原因'):
        calculate(source, overrides=[{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 0}])
    day = calculate(source, overrides=[{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 0, 'leaveReason': '已核实不扣'}]).days[0]
    assert day.auto_compensatory_leave_minutes is None
    assert day.net_extra_minutes == 0 and day.requires_review


def test_standard_is_independent_and_no_interval_leave_not_rescaled():
    """S改为7h不改变源日量8h，实际4h时自动扣减仅3h。"""
    day = date(2026, 11, 2)
    source = empty_source(day, day)
    source.records = [PunchRecord('测试员工', 'tester', datetime(2026, 11, 2, hour), day, kind, '')
                      for hour, kind in [(8, PunchType.CHECK_IN), (12, PunchType.CHECK_OUT)]]
    source.compensatory_leave_records = [CompensatoryLeaveRecord(day, 480, '调休假8小时', '测试')]
    report = calculate(source, settings={'standardMinutes': 420, **DEFAULT_SCHEDULE})
    result = report.days[0]
    assert result.actual_effective_minutes == 240 and result.leave_requested_minutes == 480
    assert result.compensatory_leave_minutes == 180 and result.required_standard_minutes == 420
    manual = calculate(source, settings={'standardMinutes': 420}, overrides=[{
        'workDate': day.isoformat(), 'compensatoryLeaveMinutes': 480, 'leaveReason': '公司指定'}]).days[0]
    assert manual.auto_compensatory_leave_minutes == 180 and manual.compensatory_leave_minutes == 480


def test_schedule_priority_and_no_lunch():
    """单日优先日期段，删除覆盖后回退；无午休有明确空值。"""
    source = empty_source(date(2026, 11, 2), date(2026, 11, 4))
    settings = {**DEFAULT_SCHEDULE, 'dateRules': [{**DEFAULT_SCHEDULE, 'id': 'short', 'name': '新作息',
                'startDate': '2026-11-03', 'endDate': '2026-11-04', 'lunchEnd': '13:00'}]}
    day_rule = {**DEFAULT_SCHEDULE, 'lunchStart': None, 'lunchEnd': None}
    report = calculate(source, settings=settings, overrides=[{'workDate': '2026-11-04', 'schedule': day_rule}])
    assert [day.schedule_source for day in report.days] == ['默认规则', '新作息', '单日覆盖']
    assert report.days[2].schedule['lunchStart'] is None
    assert all(day.required_standard_minutes == 480 for day in report.days)
    restored = calculate(source, settings=settings, overrides=[{'workDate': '2026-11-04', 'schedule': None}])
    assert restored.days[-1].schedule_source == '新作息'


@pytest.mark.parametrize('settings', [
    {'standardMinutes': 0}, {'standardMinutes': True}, {'workdayEnd': '08:00'},
    {'lunchStart': None}, {'lunchEnd': '20:00'},
    {'dateRules': [{**DEFAULT_SCHEDULE, 'startDate': '2026-11-02', 'endDate': '2026-11-04'},
                   {**DEFAULT_SCHEDULE, 'startDate': '2026-11-04', 'endDate': '2026-11-06'}]},
])
def test_invalid_system_settings_rejected(settings):
    """错误标准、跨日作息和日期段重叠不能静默应用。"""
    with pytest.raises(ValueError):
        calculate(empty_source(), settings=settings)


def test_batch_duplicate_gap_monthly_and_employee_validation(tmp_path):
    """重复文件不累加，缺失月份不算缺卡或标准工时，员工不同禁止合并。"""
    first = make_book(tmp_path, '一月.xlsx', date(2026, 1, 1), date(2026, 1, 31))
    third = make_book(tmp_path, '三月.xlsx', date(2026, 3, 1), date(2026, 3, 31))
    clone = tmp_path / '一月副本.xlsx'
    clone.write_bytes(first.read_bytes())
    source, _ = merge_workbooks([str(third), str(first), str(clone)])
    report = calculate(source)
    assert source.input_path == third
    assert source.files[-1]['duplicateOf'] == source.files[1]['id']
    assert report.days[31].status is CalculationStatus.NOT_IMPORTED
    assert report.days[31].required_standard_minutes == 0
    assert report.summary()['isPartial'] and report.net_extra_minutes == 0
    assert report.monthly_summaries()[1]['isPartial']
    assert len(report.to_dict()['coverage']['missingDates']) == 28
    other = make_book(tmp_path, '其他员工.xlsx', date(2026, 1, 1), date(2026, 1, 31), account='other')
    with pytest.raises(ValueError, match='不同员工'):
        merge_workbooks([str(first), str(other)])


def test_batch_conflict_choice_and_complementary_punches(tmp_path):
    """矛盾版本须选择；上班与次日凌晨下班的互补记录可跨文件合并。"""
    day = date(2026, 11, 2)
    first = make_book(tmp_path, '甲.xlsx', day, day, [(day, '上班', '09:00', '正常'), (day, '下班', '18:30', '正常')])
    second = make_book(tmp_path, '乙.xlsx', day, day, [(day, '上班', '10:00', '正常'), (day, '下班', '18:30', '正常')])
    source, _ = merge_workbooks([str(first), str(second)])
    assert len(source.conflicts) == 1
    assert calculate(source).to_dict()['exportBlocked']
    assert calculate(source).net_extra_minutes is None
    selected, _ = merge_workbooks([str(first), str(second)], source_choices={day.isoformat(): source.files[1]['id']})
    assert not calculate(selected).to_dict()['exportBlocked']
    assert calculate(selected).days[0].actual_effective_minutes == 420
    upper = make_book(tmp_path, '月末.xlsx', date(2026, 10, 31), date(2026, 10, 31), [(date(2026, 10, 31), '上班', '09:00', '正常')])
    lower = make_book(tmp_path, '月初.xlsx', date(2026, 11, 1), date(2026, 11, 1), [(date(2026, 11, 1), '下班', '03:59', '正常')])
    joined, _ = merge_workbooks([str(lower), str(upper)])
    assert not joined.conflicts
    assert calculate(joined).days[0].last_check_out == datetime(2026, 11, 1, 3, 59)


def test_cache_reuses_parsing_and_invalidates_changed_file(tmp_path, monkeypatch):
    """缓存命中不重新解析，文件变化后重新读取而非复用旧结果。"""
    from attendance_engine import importer
    day = date(2026, 11, 2)
    path = make_book(tmp_path, '缓存.xlsx', day, day)
    original = importer.parse_attendance_workbook
    calls = []
    monkeypatch.setattr(importer, 'parse_attendance_workbook', lambda p: calls.append(p) or original(p))
    cache = SourceCache()
    _, hits = merge_workbooks([str(path)], cache=cache)
    _, second_hits = merge_workbooks([str(path)], cache=cache)
    assert (hits, second_hits, len(calls)) == (0, 1, 1)
    make_book(tmp_path, path.name, day, day, [(day, '上班', '09:00', '正常')])
    changed, hits = merge_workbooks([str(path)], cache=cache)
    assert hits == 0 and len(calls) == 2 and len(changed.records) == 1


def test_export_audits_standard_manual_and_source_data(tmp_path):
    """Excel与页面保留相同标准、自动扣减、人工理由和来源。"""
    day = date(2026, 11, 2)
    path = make_book(tmp_path, '中文 报表.xlsx', day, day)
    source, _ = merge_workbooks([str(path)])
    report = calculate(source, settings={'standardMinutes': 420}, overrides=[{
        'workDate': day.isoformat(), 'compensatoryLeaveMinutes': 480, 'leaveReason': '公司统一调休'}])
    output = export_report(report, tmp_path / '中文输出')
    wb = load_workbook(output, data_only=True)
    assert wb['计算规则']['B8'].value == 420
    assert wb['每日明细']['N2'].value == 8
    assert wb['每日明细']['V2'].value == 0
    assert wb['每日明细']['X2'].value == '公司统一调休'
    assert wb['来源清单']['B2'].value == path.name
    assert wb['分月汇总']['F2'].value == '-8小时00分钟'
    assert report.days[0].source_ids == [source.files[0]['id']]
    wb.close()
    assert not list(output.parent.glob('.attendance-export-*'))


def test_utf8_stdio_input_and_batch_protocol(tmp_path, monkeypatch):
    """模拟中文Windows代码页，显式UTF8标准输入后路径无损进入协议。"""
    captured = []
    request = {'requestId': 8, 'command': 'calculate', 'inputPaths': ['C:/中文/考勤.xlsx'],
               'settings': {'standardMinutes': 420}, 'sourceChoices': {'2026-11-02': 'source'}}
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request, ensure_ascii=False).encode('utf-8')), encoding='cp936')
    monkeypatch.setattr(cli.sys, 'stdin', stdin)
    monkeypatch.setattr(cli, 'run_calculate', lambda args: captured.append(args) or {'success': True})
    cli._configure_utf8_stdio()
    output = io.StringIO()
    cli.run_server(stdin, output, io.StringIO(), prefetch=False)
    assert captured[0].input_paths == ['C:/中文/考勤.xlsx']
    assert captured[0].settings['standardMinutes'] == 420
    assert json.loads(output.getvalue().splitlines()[-1])['requestId'] == 8


def test_service_progress_conflict_block_and_cache(tmp_path, monkeypatch):
    """真实常驻协议在进度后返回唯一结果，重算命中缓存。"""
    day = date(2026, 11, 2)
    path = make_book(tmp_path, '服务.xlsx', day, day)
    monkeypatch.setattr(cli, '_SOURCE_CACHE', SourceCache())
    monkeypatch.setattr(cli, '_CALENDAR_CACHE', {})
    monkeypatch.setattr(cli.HolidayCalendar, 'for_period', lambda *_: HolidayCalendar(online_years={2026}))
    req = {'requestId': 1, 'command': 'calculate', 'inputPaths': [str(path)], 'outputRoot': str(tmp_path / '导出')}
    output = io.StringIO()
    cli.run_server(io.StringIO(json.dumps(req) + chr(10) + json.dumps({**req, 'requestId': 2})), output, io.StringIO(), prefetch=False)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    results = [item for item in messages if 'success' in item]
    assert len(results) == 2 and all(item['success'] for item in results)
    assert results[1]['timings']['cacheHits'] == 1
    assert {item['stage'] for item in messages if item.get('event') == 'progress'} == {'parse', 'calendar', 'calculate', 'export'}


def test_cross_month_leave_deduplicates_with_all_provenance(tmp_path):
    """跨月同一申请保留双方来源，选择来源不会丢失有效申请区间。"""
    leave = '调休假16小时（11/30 09:00 - 12/1 18:30）'
    first = make_book(tmp_path, '十一月.xlsx', date(2026, 11, 30), date(2026, 11, 30),
                      leaves=[(date(2026, 11, 30), leave)])
    second = make_book(tmp_path, '十二月.xlsx', date(2026, 12, 1), date(2026, 12, 1),
                       leaves=[(date(2026, 12, 1), leave)])
    source, _ = merge_workbooks([str(second), str(first)])
    assert len(source.compensatory_leave_records) == 1
    assert len(source.compensatory_leave_records[0].sources) == 2
    report = calculate(source)
    assert report.compensatory_leave_minutes == 960
    assert [day.leave_requested_minutes for day in report.days] == [480, 480]
    assert len(report.to_dict()['compensatoryLeaves'][0]['sources']) == 2


def test_conflict_selection_filters_leave_and_blocks_export(tmp_path):
    """有申请与无申请的完整版本冲突时，采用无申请来源不继续扣另一版本。"""
    day = date(2026, 11, 2)
    first = make_book(tmp_path, '申请版.xlsx', day, day, leaves=[(day, '调休假8小时（11/2 09:00 - 11/2 18:30）')])
    second = make_book(tmp_path, '无申请版.xlsx', day, day)
    source, _ = merge_workbooks([str(first), str(second)])
    blocked = calculate(source)
    with pytest.raises(ValueError, match='冲突'):
        export_report(blocked, tmp_path / 'blocked')
    assert not (tmp_path / 'blocked').exists()
    chosen, _ = merge_workbooks([str(first), str(second)], source_choices={day.isoformat(): source.files[1]['id']})
    report = calculate(chosen)
    assert report.net_extra_minutes == 0
    assert report.days[0].source_ids == [source.files[1]['id']]
    assert not report.days[0].leave_records
    with pytest.raises(ValueError, match='失效'):
        merge_workbooks([str(first), str(second)], source_choices={day.isoformat(): 'old-file'})


def test_atomic_export_failure_keeps_no_final_or_temporary_file(tmp_path, monkeypatch):
    """模拟写盘异常，不能留下半个正式报表或当前调用的临时文件。"""
    def fail_save(self, name):
        """先写入不完整数据再模拟磁盘错误。"""
        Path(name).write_bytes(b'partial workbook')
        raise OSError('disk full')
    monkeypatch.setattr(Workbook, 'save', fail_save)
    with pytest.raises(OSError, match='disk full'):
        export_report(calculate(empty_source()), tmp_path)
    assert not list(tmp_path.rglob('*.xlsx'))
    assert not list(tmp_path.rglob('.attendance-export-*'))


def test_changed_employee_cannot_receive_previous_manual_edits(tmp_path):
    """即使路径不变，文件替换为另一员工时也不套用旧人工扣减。"""
    day = date(2026, 11, 2)
    path = make_book(tmp_path, '同路径.xlsx', day, day, account='new-employee')
    args = argparse.Namespace(input_paths=[str(path)], input=None, expected_employee={'name': '测试员工', 'account': 'tester'},
                              overrides=[{'workDate': day.isoformat(), 'compensatoryLeaveMinutes': 480, 'leaveReason': '旧修订'}],
                              export_root=str(tmp_path / '输出'))
    with pytest.raises(ValueError, match='员工已改变'):
        cli.run_calculate(args)
    assert not (tmp_path / '输出').exists()


def test_source_preserves_selected_symlink_path(tmp_path):
    """返回原始选择路径及规范路径，界面可正确关联别名文件行。"""
    day = date(2026, 11, 2)
    actual = make_book(tmp_path, '实际报表.xlsx', day, day)
    selected = tmp_path / '选择别名.xlsx'
    try:
        selected.symlink_to(actual)
    except (OSError, NotImplementedError):
        pytest.skip('当前系统未启用测试账号创建符号链接的权限')
    source, _ = merge_workbooks([str(selected)])
    assert source.files[0]['inputPath'] == str(selected)
    assert source.files[0]['path'] == str(actual.resolve())
    assert source.files[0]['id']


def test_no_lunch_export_uses_whole_work_interval(tmp_path):
    """无午休导出明确完整工作区间，不输出空时间拼接的字符串。"""
    report = calculate(empty_source(), settings={'lunchStart': None, 'lunchEnd': None})
    output = export_report(report, tmp_path)
    workbook = load_workbook(output, data_only=True)
    assert workbook['计算规则']['B6'].value == '09:00～18:30'
    assert workbook['计算规则']['B7'].value == '无午休，按上述完整工作区间分摊'
    workbook.close()


def test_reset_times_restores_invalid_source_and_keeps_manual_deduction():
    """恢复原始倒置打卡时不按新覆盖校验，独立人工扣减仍可保存。"""
    work_date = date(2026, 11, 2)
    source = empty_source(work_date, work_date)
    source.records = [PunchRecord('测试员工', 'tester', datetime(2026, 11, 2, hour), work_date, kind, '')
                      for hour, kind in [(10, PunchType.CHECK_IN), (9, PunchType.CHECK_OUT)]]
    day = calculate(source, overrides=[{
        'workDate': work_date.isoformat(), 'resetTimes': True,
        'firstCheckIn': '2026-11-02T09:00', 'lastCheckOut': '2026-11-02T18:30',
        'compensatoryLeaveMinutes': 480, 'leaveReason': '公司统一调休',
    }]).days[0]
    assert day.status is CalculationStatus.INVALID_TIME_RANGE
    assert day.first_check_in == datetime(2026, 11, 2, 10)
    assert day.last_check_out == datetime(2026, 11, 2, 9)
    assert day.actual_effective_minutes is None
    assert day.compensatory_leave_minutes == 480 and day.net_extra_minutes == -480


def test_multiple_sources_need_account_but_single_and_duplicates_remain_compatible(tmp_path):
    """多份独立来源不能仅按姓名合并；单来源缺账号仍可单独处理。"""
    first = make_book(tmp_path, '无账号十一月.xlsx', date(2026, 11, 1), date(2026, 11, 30), account='')
    second = make_book(tmp_path, '同姓名十二月.xlsx', date(2026, 12, 1), date(2026, 12, 31), account='')
    with pytest.raises(ValueError, match='不能仅凭姓名'):
        merge_workbooks([str(first), str(second)])
    single, _ = merge_workbooks([str(first)])
    assert single.employee_account == ''
    duplicate = tmp_path / '无账号副本.xlsx'
    duplicate.write_bytes(first.read_bytes())
    deduplicated, _ = merge_workbooks([str(first), str(duplicate)])
    assert deduplicated.employee_account == ''
    assert deduplicated.files[1]['duplicateOf'] == deduplicated.files[0]['id']
