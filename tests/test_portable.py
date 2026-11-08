"""验证源Excel配置的只读识别、原始内容保护和跨设备恢复。"""
from datetime import date
from io import BytesIO, StringIO
import json
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from openpyxl import load_workbook
from openpyxl.styles import Font

from attendance_engine import cli, portable
from attendance_engine.calculator import calculate_attendance
from attendance_engine.importer import SourceCache, merge_workbooks
from attendance_engine.schedules import DEFAULT_SCHEDULE
from test_v031 import make_book, calculate


def example(tmp_path, name='源报表.xlsx', day=2):
    """生成独立临时报表，不接触用户原始文件。"""
    work_date = date(2026, 11, day)
    return make_book(tmp_path, name, work_date, work_date)


def save(paths, overrides=None, settings=None, expected_sources=None):
    """从当前计算来源准备显式保存请求。"""
    source, _ = merge_workbooks([str(path) for path in paths])
    return portable.save_workbook_state([str(path) for path in paths], settings or dict(DEFAULT_SCHEDULE),
                                       overrides or [], {'name': source.employee_name, 'account': source.employee_account},
                                       expected_sources if expected_sources is not None else source.files)


def zip_entries(path):
    """取得解压成员，验证原始工作表和相关资源逐字节不变。"""
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def replace_entry(path, name, content):
    """只在测试工作簿中定点制造不可信或兼容性输入。"""
    entries = zip_entries(path)
    entries[name] = content
    with ZipFile(path, 'w') as archive:
        for key, value in entries.items():
            archive.writestr(key, value)


def test_round_trip_keeps_raw_workbook_and_restores_on_new_computer(tmp_path):
    """保存后源表样式、公式及资源不变；复制改名后可恢复手动扣减。"""
    path = example(tmp_path)
    workbook = load_workbook(path)
    notes = workbook.create_sheet('用户自己的表')
    notes['A1'] = '=SUM(1,2)'
    notes['B2'] = '用户说明'
    notes['B2'].font = Font(bold=True, color='00FF00')
    notes.merge_cells('D3:E3')
    workbook.save(path)
    workbook.close()
    before_bytes, before = path.read_bytes(), zip_entries(path)
    original_id = merge_workbooks([str(path)])[0].files[0]['id']
    changes = [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480}]
    result = save([path], changes)
    assert result['allSaved']
    assert Path(result['files'][0]['backupPath']).read_bytes() == before_bytes
    assert result['files'][0]['sourceId'] == original_id
    after = zip_entries(path)
    for name, content in before.items():
        if name not in {portable.WORKBOOK, portable.RELATIONS, portable.CONTENT_TYPES}:
            assert after[name] == content, name
    wb = load_workbook(path)
    assert wb[portable.SHEET_NAME].sheet_state == 'visible'
    assert wb['用户自己的表']['A1'].value == '=SUM(1,2)'
    assert wb['用户自己的表']['B2'].font.bold
    assert 'D3:E3' in wb['用户自己的表'].merged_cells
    wb.close()
    moved = tmp_path / '换电脑复制.xlsx'
    moved.write_bytes(path.read_bytes())
    inspection = portable.inspect_workbook_state([str(moved)])['files'][0]
    assert inspection['status'] == 'valid'
    assert inspection['sourceId'] == original_id
    assert inspection['state']['overrides'][0]['leaveReason'] == '手动调整'
    restored = calculate(merge_workbooks([str(moved)])[0], settings=inspection['state']['settings'],
                         overrides=inspection['state']['overrides'])
    assert restored.net_extra_minutes == -480
    assert restored.days[0].actual_effective_minutes is None


def test_updating_existing_state_only_replaces_that_sheet_and_keeps_source_choice_id(tmp_path):
    """第二次保存不再重写注册XML，缓存重读后来源ID保持稳定。"""
    path = example(tmp_path)
    cache = SourceCache()
    source, _ = merge_workbooks([str(path)], cache=cache)
    first = save([path], [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480}])
    before = zip_entries(path)
    second = save([path], [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 0}])
    after = zip_entries(path)
    target = portable._layout(before)[-1][2]
    assert [name for name in before if before[name] != after[name]] == [target]
    assert first['files'][0]['backupPath'] != second['files'][0]['backupPath']
    new_source, hits = merge_workbooks([str(path)], cache=cache)
    assert hits == 0
    assert source.files[0]['id'] == new_source.files[0]['id']
    assert portable.inspect_workbook_state([str(path)])['files'][0]['state']['overrides'][0]['compensatoryLeaveMinutes'] == 0


def test_read_only_inspection_does_not_write_and_normal_calculate_does_not_apply_state(tmp_path):
    """只有显式采用配置才改变计算，识别和普通统计都不自动写源表。"""
    path = example(tmp_path)
    before = path.read_bytes()
    assert portable.inspect_workbook_state([str(path)])['files'][0]['status'] == 'none'
    assert path.read_bytes() == before
    save([path], [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480}])
    snapshot = path.read_bytes()
    assert calculate(merge_workbooks([str(path)])[0]).net_extra_minutes == 0
    portable.inspect_workbook_state([str(path)])
    assert path.read_bytes() == snapshot


def test_plain_workbook_inspection_skips_business_parser(tmp_path, monkeypatch):
    """没有配置表的文件只检查包结构，不在统计前重复解析所有业务行。"""
    path = example(tmp_path)
    def must_not_parse(_path):
        raise AssertionError('不应提前解析业务表')
    monkeypatch.setattr(portable, 'parse_attendance_workbook', must_not_parse)
    value = portable.inspect_workbook_state([str(path)])['files'][0]
    assert value['status'] == 'none' and value['sourceId']


def test_explicit_null_reason_round_trip_matches_calculation(tmp_path):
    """合法的空原因与计算协议相同，保存后恢复默认说明及处理状态。"""
    path = example(tmp_path)
    assert save([path], [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480, 'leaveReason': None}])['allSaved']
    state = portable.inspect_workbook_state([str(path)])['files'][0]['state']
    assert state['overrides'][0]['leaveReason'] == '手动调整'
    restored = calculate(merge_workbooks([str(path)])[0], overrides=state['overrides'])
    assert restored.days[0].resolution_status == 'resolved'


def test_multi_file_save_splits_overrides_and_reports_partial_failure(tmp_path):
    """修订只写入各文件覆盖日期，某文件只读不掩盖另一文件成功。"""
    first = example(tmp_path, '第一份.xlsx', 2)
    second = example(tmp_path, '第二份.xlsx', 3)
    second.chmod(0o444)
    try:
        result = save([first, second], [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480},
                                     {'workDate': '2026-11-03', 'compensatoryLeaveMinutes': 420}])
        assert not result['allSaved']
        assert result['files'][0]['success']
        assert not result['files'][1]['success'] and '只读' in result['files'][1]['error']
        state = portable.inspect_workbook_state([str(first)])['files'][0]['state']
        assert [item['workDate'] for item in state['overrides']] == ['2026-11-02']
    finally:
        second.chmod(0o644)


def test_existing_foreign_named_sheet_is_never_overwritten(tmp_path):
    """用户同名工作表不被当成工具配置，不创建备份或改写原件。"""
    path = example(tmp_path)
    wb = load_workbook(path)
    wb.create_sheet(portable.SHEET_NAME)['A1'] = '用户自己的内容'
    wb.save(path)
    wb.close()
    before = path.read_bytes()
    result = save([path])
    assert not result['allSaved'] and '不能覆盖' in result['files'][0]['error']
    assert portable.inspect_workbook_state([str(path)])['files'][0]['status'] == 'invalid'
    assert path.read_bytes() == before and not list(tmp_path.glob('*.backup-*'))


def test_source_change_and_identity_change_cannot_receive_old_state(tmp_path):
    """旧计算来源版本或员工不匹配时，不把旧修订保存到新内容。"""
    path = example(tmp_path)
    source, _ = merge_workbooks([str(path)])
    make_book(tmp_path, path.name, date(2026, 11, 2), date(2026, 11, 2), account='other')
    current = path.read_bytes()
    result = portable.save_workbook_state([str(path)], dict(DEFAULT_SCHEDULE), [],
        {'name': '测试员工', 'account': 'tester'}, source.files)
    assert not result['allSaved'] and '已变化' in result['files'][0]['error']
    assert path.read_bytes() == current
    result = portable.save_workbook_state([str(path)], dict(DEFAULT_SCHEDULE), [],
        {'name': '测试员工', 'account': 'tester'}, merge_workbooks([str(path)])[0].files)
    assert not result['allSaved'] and '员工' in result['files'][0]['error']


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(schemaVersion=999),
    lambda value: value.update(settings={**DEFAULT_SCHEDULE, 'outputRoot': 'C:/private'}),
    lambda value: value.update(overrides=[{'workDate': '2026-11-02', 'command': 'delete'}]),
    lambda value: value.update(overrides=[{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': -480}]),
    lambda value: value.update(employee={'name': '其他人', 'account': 'else'}),
])
def test_invalid_embedded_state_is_data_not_instruction(tmp_path, mutate):
    """未知版本、非法字段、负扣减和他人配置只报告无效，不执行或应用。"""
    path = example(tmp_path)
    save([path])
    entries = zip_entries(path)
    target = portable._layout(entries)[-1][2]
    value = portable._read_state(entries, target)
    mutate(value)
    replace_entry(path, target, portable._sheet_xml(value))
    result = portable.inspect_workbook_state([str(path)])['files'][0]
    assert result['status'] == 'invalid' and result['state'] is None


def test_raw_namespace_declarations_and_formula_parts_survive(tmp_path):
    """mc:Ignorable引用的扩展前缀不能因重写注册XML而丢失。"""
    path = example(tmp_path)
    raw = zip_entries(path)[portable.WORKBOOK]
    with_extensions = raw.replace(b'<workbook ', b'<workbook xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:foo="urn:unused-extension" mc:Ignorable="foo" ')
    replace_entry(path, portable.WORKBOOK, with_extensions)
    assert save([path])['allSaved']
    updated = zip_entries(path)[portable.WORKBOOK]
    assert b'xmlns:foo="urn:unused-extension"' in updated
    assert b'mc:Ignorable="foo"' in updated
    assert updated.replace(updated[updated.index(b'<sheet xmlns='):updated.index(b'</sheets>')], b'') == with_extensions


def test_state_supports_shared_string_cells_and_rejects_oversized_chunks():
    """共享字符串读取不把索引当文本，分块超限安全失败。"""
    value = {'example': '中文'}
    sheet = ET.fromstring(portable._sheet_xml(value))
    sst = ET.Element(f'{{{portable.MAIN_NS}}}sst')
    for index, cell in enumerate(sheet.iter(f'{{{portable.MAIN_NS}}}c')):
        text = ''.join(node.text or '' for node in cell.iter(f'{{{portable.MAIN_NS}}}t'))
        item = ET.SubElement(sst, f'{{{portable.MAIN_NS}}}si')
        ET.SubElement(item, f'{{{portable.MAIN_NS}}}t').text = text
        for child in list(cell):
            cell.remove(child)
        cell.set('t', 's')
        ET.SubElement(cell, f'{{{portable.MAIN_NS}}}v').text = str(index)
    entries = {'state.xml': ET.tostring(sheet), 'xl/sharedStrings.xml': ET.tostring(sst)}
    assert portable._read_state(entries, 'state.xml') == value
    with pytest.raises(ValueError, match='大小限制'):
        portable._sheet_xml({'large': '中' * portable.MAX_STATE_BYTES})


def test_replace_failure_retains_original_and_backup(tmp_path, monkeypatch):
    """模拟Excel锁定文件，原始内容和备份仍在，不留下临时半文件。"""
    path = example(tmp_path)
    before = path.read_bytes()
    def locked(*_args):
        raise PermissionError('Excel正在占用文件')
    monkeypatch.setattr(portable.os, 'replace', locked)
    result = save([path])
    assert not result['allSaved'] and '备份' in result['files'][0]['error']
    assert path.read_bytes() == before
    backups = list(tmp_path.glob('*.backup-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert not list(tmp_path.glob('.attendance-state-*'))


def test_future_schema_cannot_be_overwritten(tmp_path):
    """未来版本配置不被当前软件覆盖，避免扩展字段被静默丢弃。"""
    path = example(tmp_path)
    save([path])
    entries = zip_entries(path)
    target = portable._layout(entries)[-1][2]
    state = portable._read_state(entries, target)
    state['schemaVersion'] = 2
    replace_entry(path, target, portable._sheet_xml(state))
    before = path.read_bytes()
    result = save([path])
    assert not result['allSaved'] and '未知版本' in result['files'][0]['error']
    assert path.read_bytes() == before


def test_state_target_shared_with_user_sheet_is_rejected(tmp_path):
    """配置与用户表共用底层部件时拒绝写入，不能连带改变用户表。"""
    path = example(tmp_path)
    save([path])
    entries = zip_entries(path)
    workbook, _, _, sheets, _, configured = portable._layout(entries)
    attrs = dict(configured[0].attrib)
    attrs.update(name='用户附加表', sheetId='99')
    ET.SubElement(sheets, f'{{{portable.MAIN_NS}}}sheet', attrs)
    replace_entry(path, portable.WORKBOOK, ET.tostring(workbook))
    before = path.read_bytes()
    result = portable.inspect_workbook_state([str(path)])['files'][0]
    assert result['status'] == 'invalid' and '共用' in result['message']
    with pytest.raises(ValueError, match='共用'):
        portable.workbook_fingerprint(before)
    assert path.read_bytes() == before


def test_protocol_inspect_and_save_are_explicit_and_continue_after_invalid_request(tmp_path):
    """常驻协议仅在保存命令写文件，失败后仍可读取有效配置。"""
    path = example(tmp_path)
    source, _ = merge_workbooks([str(path)])
    requests = [
        {'requestId': 1, 'command': 'inspect_workbook_state', 'inputPaths': [str(path)]},
        {'requestId': 2, 'command': 'save_workbook_state', 'inputPaths': [str(path)],
         'settings': dict(DEFAULT_SCHEDULE), 'overrides': [{'workDate': '2026-11-02', 'compensatoryLeaveMinutes': 480}],
         'expectedEmployee': {'name': source.employee_name, 'account': source.employee_account}, 'expectedSources': source.files},
        {'requestId': 3, 'command': 'inspect_workbook_state', 'inputPaths': [str(path)]},
    ]
    output = StringIO()
    cli.run_server(StringIO('\n'.join(json.dumps(value) for value in requests)), output, StringIO(), prefetch=False)
    replies = [json.loads(line) for line in output.getvalue().splitlines()][1:]
    assert replies[0]['files'][0]['status'] == 'none'
    assert replies[1]['allSaved']
    assert replies[2]['files'][0]['state']['overrides'][0]['compensatoryLeaveMinutes'] == 480
    assert [item['requestId'] for item in replies] == [1, 2, 3]
