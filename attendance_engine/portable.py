"""在源工作簿的独立工作表保存可移植规则，不重写原始工作表。"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import tempfile
from uuid import uuid4
from xml.sax.saxutils import quoteattr
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from .models import AttendanceSource
from .parser import parse_attendance_workbook
from .schedules import DEFAULT_SCHEDULE, normalize_settings

SHEET_NAME = '_考勤工具配置'
MAGIC = 'AttendanceToolPortableState'
SCHEMA_VERSION = 1
MAX_STATE_BYTES = 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
CHUNK_SIZE = 20000
MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CONTENT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
WORKBOOK = 'xl/workbook.xml'
RELATIONS = 'xl/_rels/workbook.xml.rels'
CONTENT_TYPES = '[Content_Types].xml'


def _package(data: bytes) -> tuple[dict[str, bytes], list, object]:
    """读取有界且无重复条目的OOXML包，拒绝模糊成员路径。"""
    with ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or sum(item.file_size for item in infos) > MAX_PACKAGE_BYTES:
            raise ValueError('工作簿包重复或解压后过大，无法安全保存配置')
        if any(name.startswith('/') or '..' in name.split('/') for name in names):
            raise ValueError('工作簿包包含无效内部路径')
        return {item.filename: archive.read(item) for item in infos}, infos, archive.comment


def _layout(entries: dict[str, bytes]):
    """解析工作表注册信息，配置表必须是包内的普通worksheet。"""
    workbook = ET.fromstring(entries[WORKBOOK])
    relationships = ET.fromstring(entries[RELATIONS])
    content_types = ET.fromstring(entries[CONTENT_TYPES])
    sheets = workbook.find(f'{{{MAIN_NS}}}sheets')
    if sheets is None:
        raise ValueError('工作簿缺少工作表注册信息')
    links = {item.get('Id'): item for item in relationships}
    rows = []
    for sheet in sheets:
        link = links.get(sheet.get(f'{{{REL_NS}}}id'))
        if link is None:
            raise ValueError('工作表引用不存在')
        target = link.get('Target', '')
        target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        if not target.startswith('xl/') or '..' in target.split('/') or link.get('TargetMode') == 'External':
            raise ValueError('不支持外部工作表引用')
        rows.append((sheet, link, target))
    configured = [row for row in rows if row[0].get('name') == SHEET_NAME]
    if len(configured) > 1:
        raise ValueError('配置工作表名称重复')
    if configured and (configured[0][1].get('Type') != REL_NS + '/worksheet' or
                       sum(row[2] == configured[0][2] for row in rows) != 1):
        raise ValueError('配置工作表引用无效或与其他表共用数据')
    return workbook, relationships, content_types, sheets, rows, configured[0] if configured else None


def workbook_fingerprint(data: bytes) -> str:
    """计算不受本工具配置表影响的原始包摘要，改名或跨电脑仍稳定。

    Args:
        data: 完整源xlsx字节。

    Returns:
        排除配置专用部件后的SHA-256摘要。原工作表、样式、公式等变化会改变摘要。
    """
    entries, _, _ = _package(data)
    workbook, relationships, content_types, sheets, rows, configured = _layout(entries)
    excluded = {WORKBOOK, RELATIONS, CONTENT_TYPES}
    if configured:
        sheet, link, target = configured
        sheets.remove(sheet)
        relationships.remove(link)
        excluded.add(target)
        for item in list(content_types):
            if item.get('PartName', '').lstrip('/') == target:
                content_types.remove(item)
    digest = sha256()
    # XML结构化后忽略命名空间前缀及排版差异，保留属性和实际内容。
    def structure(node):
        return [node.tag, sorted(node.attrib.items()), node.text.strip() if node.text and node.text.strip() else '',
                [structure(child) for child in node]]
    for name, tree in [(WORKBOOK, workbook), (RELATIONS, relationships), (CONTENT_TYPES, content_types)]:
        digest.update(name.encode())
        digest.update(json.dumps(structure(tree), ensure_ascii=False, separators=(',', ':')).encode())
    for name in sorted(entries):
        if name not in excluded:
            digest.update(name.encode())
            digest.update(bytes([0]))
            digest.update(entries[name])
    return digest.hexdigest()


def _strict_json(text: str) -> dict:
    """解析纯数据JSON并拒绝重复键、非有限数值及深度异常。"""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('配置JSON包含重复字段')
            result[key] = value
        return result
    def invalid_constant(_value):
        raise ValueError('配置JSON包含非有限数值')
    result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError('配置内容必须为对象')
    return result


def _read_state(entries: dict[str, bytes], target: str) -> dict:
    """读取专用表的固定单元格，不执行公式或信任任意说明文本。"""
    xml = entries.get(target, b'')
    if len(xml) > MAX_STATE_BYTES * 8:
        raise ValueError('Excel配置工作表内容过大')
    tree = ET.fromstring(xml)
    cells = {}
    shared_strings = None
    for cell in tree.iter(f'{{{MAIN_NS}}}c'):
        coordinate = cell.get('r')
        if coordinate in cells or cell.find(f'{{{MAIN_NS}}}f') is not None:
            raise ValueError('配置工作表存在重复单元格或公式')
        if cell.get('t') == 'inlineStr':
            cells[coordinate] = ''.join(node.text or '' for node in cell.iter(f'{{{MAIN_NS}}}t'))
        elif cell.get('t') == 's':
            if shared_strings is None:
                shared_tree = ET.fromstring(entries.get('xl/sharedStrings.xml', b''))
                shared_strings = [''.join(node.text or '' for node in item.iter(f'{{{MAIN_NS}}}t'))
                                  for item in shared_tree.findall(f'{{{MAIN_NS}}}si')]
            try:
                index = int(cell.findtext(f'{{{MAIN_NS}}}v', ''))
                if index < 0:
                    raise ValueError('negative index')
                cells[coordinate] = shared_strings[index]
            except (ValueError, IndexError) as exc:
                raise ValueError('配置工作表共享字符串引用无效') from exc
        else:
            cells[coordinate] = cell.findtext(f'{{{MAIN_NS}}}v', '')
    if cells.get('A1') != MAGIC:
        raise ValueError(f'同名工作表“{SHEET_NAME}”不是本工具配置，不能覆盖或应用')
    if cells.get('B1') != str(SCHEMA_VERSION):
        raise ValueError('Excel配置版本不受当前软件支持')
    try:
        count = int(cells.get('B4', ''))
    except ValueError as exc:
        raise ValueError('配置数据分块信息无效') from exc
    if not 1 <= count <= (MAX_STATE_BYTES // CHUNK_SIZE + 1):
        raise ValueError('配置数据分块数量无效')
    chunks = [cells.get(f'B{number + 5}') for number in range(count)]
    if any(not isinstance(chunk, str) or len(chunk) > CHUNK_SIZE for chunk in chunks):
        raise ValueError('配置数据分块缺失或过大')
    value = ''.join(chunks)
    if len(value.encode('utf-8')) > MAX_STATE_BYTES:
        raise ValueError('配置数据超过大小限制')
    return _strict_json(value)


def _settings(value: object) -> dict:
    """仅保存计算设置，拒绝输出路径及未声明的行为字段。"""
    allowed = set(DEFAULT_SCHEDULE) | {'standardMinutes', 'dateRules'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Excel仅支持标准工时及作息设置，不保存路径或未知字段')
    for rule in value.get('dateRules', []) if isinstance(value.get('dateRules', []), list) else []:
        if not isinstance(rule, dict) or set(rule) - set(DEFAULT_SCHEDULE) - {'id', 'name', 'startDate', 'endDate'}:
            raise ValueError('日期规则包含未知字段')
    normalized = normalize_settings(value)
    return {**{key: normalized[key] for key in DEFAULT_SCHEDULE}, 'standardMinutes': normalized['standardMinutes'],
            'dateRules': [{key: rule[key] for key in (*DEFAULT_SCHEDULE, 'id', 'name', 'startDate', 'endDate')}
                          for rule in normalized['dateRules']]}


def _overrides(value: object, source: AttendanceSource) -> list[dict]:
    """复用计算入口校验修订，但只序列化白名单字段。"""
    from .calculator import _parse_overrides
    allowed = {'workDate', 'firstCheckIn', 'lastCheckOut', 'resetTimes',
               'compensatoryLeaveMinutes', 'leaveReason', 'schedule'}
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError('Excel修订须为有界日期列表')
    for item in value:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError('Excel修订包含未知字段')
        if item.get('schedule') is not None and (not isinstance(item['schedule'], dict) or set(item['schedule']) != set(DEFAULT_SCHEDULE)):
            raise ValueError('单日作息字段无效')
        if item.get('leaveReason') is not None and (not isinstance(item['leaveReason'], str) or len(item['leaveReason']) > 2000):
            raise ValueError('修订原因须为不超过2000字的文本')
    parsed = _parse_overrides(source, value)
    return [{key: item[key] for key in allowed if key in item} for _, item in sorted(parsed.items())]


def _validate_state(value: dict, source: AttendanceSource, fingerprint: str) -> dict:
    """验证配置与当前员工、统计期间及源内容绑定，防止误套用。"""
    if set(value) != {'schemaVersion', 'savedAt', 'employee', 'period', 'sourceFingerprint', 'settings', 'overrides'}:
        raise ValueError('Excel配置结构不完整或包含未知字段')
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != SCHEMA_VERSION:
        raise ValueError('Excel配置版本不受当前软件支持')
    if value['employee'] != {'name': source.employee_name, 'account': source.employee_account}:
        raise ValueError('Excel配置中的员工与当前报表不一致')
    if value['period'] != {'startDate': source.start_date.isoformat(), 'endDate': source.end_date.isoformat()}:
        raise ValueError('Excel配置中的统计期间与当前报表不一致')
    if value['sourceFingerprint'] != fingerprint:
        raise ValueError('源报表内容已变化，Excel中的旧配置不可自动套用')
    if not isinstance(value['savedAt'], str):
        raise ValueError('Excel配置保存时间无效')
    datetime.fromisoformat(value['savedAt'])
    return {'schemaVersion': SCHEMA_VERSION, 'savedAt': value['savedAt'],
            'settings': _settings(value['settings']), 'overrides': _overrides(value['overrides'], source)}


def _paths(value: object) -> list[str]:
    """校验用户明确选定的文件集合，不接受空路径或非xlsx。"""
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError('请提供一个或多个Excel路径')
    if len(value) > 1000:
        raise ValueError('单次配置操作最多支持1000个文件')
    return value


def inspect_workbook_state(input_paths: list[str]) -> dict:
    """只读检查随文件携带的配置，逐文件报告有效性供用户选择。

    Args:
        input_paths: 用户添加的源工作簿路径。

    Returns:
        各文件的配置、来源绑定和错误，不执行或自动应用配置。
    """
    files = []
    for selected in _paths(input_paths):
        path = Path(selected).expanduser().resolve()
        result = {'inputPath': selected, 'path': str(path), 'sourceId': None, 'employee': None,
                  'period': None, 'state': None, 'status': 'invalid', 'message': ''}
        try:
            raw = path.read_bytes()
            entries, _, _ = _package(raw)
            configured = _layout(entries)[-1]
            fingerprint = workbook_fingerprint(raw)
            # 普通文件无需重复解析业务数据；首次统计仍由有界SourceCache负责。
            if configured is None:
                result.update(sourceId=fingerprint[:16], status='none', message='未发现Excel配置')
                files.append(result)
                continue
            source = parse_attendance_workbook(path)
            if sha256(path.read_bytes()).digest() != sha256(raw).digest():
                raise ValueError('读取期间文件发生变化，请重新导入')
            result.update(sourceId=fingerprint[:16], employee={'name': source.employee_name, 'account': source.employee_account},
                          period={'startDate': source.start_date.isoformat(), 'endDate': source.end_date.isoformat()})
            state = _validate_state(_read_state(entries, configured[2]), source, fingerprint)
            result.update(status='valid', state=state, message='发现可使用的Excel配置与修订')
        except Exception as exc:
            result['message'] = f'无法使用Excel配置：{exc}'
        files.append(result)
    return {'success': True, 'files': files}


def _sheet_xml(value: dict) -> bytes:
    """将有界JSON以纯文本分块存储，避免单元格截断或公式执行。"""
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(text.encode('utf-8')) > MAX_STATE_BYTES:
        raise ValueError('配置数据超过大小限制')
    chunks = [text[index:index + CHUNK_SIZE] for index in range(0, len(text), CHUNK_SIZE)]
    root = ET.Element(f'{{{MAIN_NS}}}worksheet')
    data = ET.SubElement(root, f'{{{MAIN_NS}}}sheetData')
    values = [(MAGIC, str(SCHEMA_VERSION)),
              ('本表由考勤统计工具保存', '包含计算规则与人工修订；导入时由用户选择是否应用'),
              ('请勿手动修改JSON', '原始工作表不变；保存前原文件备份位于同目录'), ('JSON分块数', str(len(chunks)))]
    values.extend((str(index + 1), chunk) for index, chunk in enumerate(chunks))
    for number, values_row in enumerate(values, 1):
        row = ET.SubElement(data, f'{{{MAIN_NS}}}row', r=str(number))
        for column, value_text in zip(('A', 'B'), values_row):
            cell = ET.SubElement(row, f'{{{MAIN_NS}}}c', r=f'{column}{number}', t='inlineStr')
            inline = ET.SubElement(cell, f'{{{MAIN_NS}}}is')
            ET.SubElement(inline, f'{{{MAIN_NS}}}t', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}).text = value_text
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _updated_package(raw: bytes, value: dict) -> bytes:
    """定点修改配置表及注册XML，其余ZIP成员内容逐字节保留。"""
    entries, infos, comment = _package(raw)
    workbook, relationships, content_types, sheets, rows, configured = _layout(entries)
    protection = workbook.find(f'{{{MAIN_NS}}}workbookProtection')
    if protection is not None and protection.get('lockStructure') in {'1', 'true'}:
        raise ValueError('源工作簿结构已保护，不能添加或更新配置工作表')
    if configured:
        previous = _read_state(entries, configured[2])
        if type(previous.get('schemaVersion')) is not int or previous['schemaVersion'] != SCHEMA_VERSION:
            raise ValueError('不覆盖未知版本的Excel配置，请使用对应版本的软件处理')
        target = configured[2]
        changed = {}
    else:
        number = max([int(sheet.get('sheetId', '0')) for sheet, _, _ in rows], default=0) + 1
        target = f'xl/worksheets/attendance-state-{uuid4().hex}.xml'
        rid = 'rIdAttendance' + uuid4().hex
        # 只插入注册节点，保留原命名空间声明和兼容扩展；不重写原XML。
        def insert(name, closing, fragment):
            xml = entries[name].decode('utf-8-sig')
            match = re.search(r'</(?:[A-Za-z_][\w.-]*:)?' + closing + r'\s*>', xml)
            if match is None:
                raise ValueError('工作簿注册XML结构不受支持，未修改源文件')
            return (xml[:match.start()] + fragment + xml[match.start():]).encode('utf-8')
        changed = {
            WORKBOOK: insert(WORKBOOK, 'sheets', f'<sheet xmlns={quoteattr(MAIN_NS)} xmlns:attRel={quoteattr(REL_NS)} name={quoteattr(SHEET_NAME)} sheetId={quoteattr(str(number))} attRel:id={quoteattr(rid)} state="visible"/>'),
            RELATIONS: insert(RELATIONS, 'Relationships', f'<Relationship xmlns={quoteattr(PACKAGE_NS)} Id={quoteattr(rid)} Type={quoteattr(REL_NS + "/worksheet")} Target={quoteattr(target[3:])}/>'),
            CONTENT_TYPES: insert(CONTENT_TYPES, 'Types', f'<Override xmlns={quoteattr(CONTENT_NS)} PartName={quoteattr("/" + target)} ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'),
        }
    changed[target] = _sheet_xml(value)
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(info, changed.pop(info.filename, entries[info.filename]))
        for name, content in changed.items():
            archive.writestr(name, content)
    return output.getvalue()


def _save_one(path: Path, settings: dict, overrides: list[dict], expected_employee: dict, expected_id: str) -> tuple[str, str]:
    """验证旧版本、生成备份，并原子替换单个工作簿。"""
    if path.suffix.lower() != '.xlsx' or not path.is_file():
        raise ValueError('仅支持已存在的xlsx源文件')
    mode = path.stat().st_mode
    if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) or not os.access(path, os.W_OK):
        raise PermissionError('源文件为只读，不能保存配置')
    raw = path.read_bytes()
    fingerprint = workbook_fingerprint(raw)
    if fingerprint[:16] != expected_id:
        raise ValueError('源报表已变化，请重新统计后再保存配置')
    source = parse_attendance_workbook(path)
    if expected_employee != {'name': source.employee_name, 'account': source.employee_account}:
        raise ValueError('源报表员工与当前统计不一致，不能写入修订')
    date_range = (source.start_date.isoformat(), source.end_date.isoformat())
    local_overrides = [item for item in overrides if date_range[0] <= item['workDate'] <= date_range[1]]
    local_overrides = _overrides(local_overrides, source)
    value = {'schemaVersion': SCHEMA_VERSION, 'savedAt': datetime.now(timezone.utc).isoformat(),
             'employee': expected_employee, 'period': {'startDate': date_range[0], 'endDate': date_range[1]},
             'sourceFingerprint': fingerprint, 'settings': settings, 'overrides': local_overrides}
    updated = _updated_package(raw, value)
    if workbook_fingerprint(updated) != fingerprint:
        raise ValueError('配置写入改变了原始报表内容，已取消保存')
    original_stat = path.stat()
    original_digest = sha256(raw).digest()
    if sha256(path.read_bytes()).digest() != original_digest:
        raise ValueError('读取期间文件发生变化，请重试')
    backup = path.with_name(f'{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}{path.suffix}')
    temporary = None
    try:
        with backup.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        handle, name = tempfile.mkstemp(prefix='.attendance-state-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(handle, 'wb') as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.stat()
        if (current.st_mtime_ns, current.st_size, current.st_ino) != (original_stat.st_mtime_ns, original_stat.st_size, original_stat.st_ino) or sha256(path.read_bytes()).digest() != original_digest:
            raise ValueError('保存期间源文件已被其他程序修改，已保留原文件和备份')
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, path)
        return str(backup), fingerprint[:16]
    except Exception as exc:
        if backup.exists():
            raise ValueError(f'保存失败，源文件未替换；备份：{backup}；{exc}') from exc
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_workbook_state(input_paths: list[str], settings: dict, overrides: list[dict], expected_employee: dict, expected_sources: list[dict]) -> dict:
    """显式保存已确认设置，逐文件返回状态，不声称整批原子成功。

    Args:
        input_paths: 用户选择写入的源文件集合。
        settings: 不含本机路径的计算规则。
        overrides: 当前有效的每日修订，各文件仅保存本期间的日期。
        expected_employee: 最近成功统计的员工身份。
        expected_sources: 最近成功统计的来源路径和内容标识。

    Returns:
        每个文件的保存结果及备份路径；部分失败时allSaved为false。
    """
    paths = _paths(input_paths)
    clean_settings = _settings(settings)
    if not isinstance(expected_employee, dict) or set(expected_employee) != {'name', 'account'} or any(not isinstance(v, str) for v in expected_employee.values()):
        raise ValueError('保存配置前须先成功统计并确认员工身份')
    if not isinstance(overrides, list) or any(not isinstance(item, dict) or not isinstance(item.get('workDate'), str) for item in overrides):
        raise ValueError('修订必须为包含工作日期的列表')
    if len(overrides) > 10000:
        raise ValueError('修订列表超过允许范围')
    if not isinstance(expected_sources, list):
        raise ValueError('保存配置前须先成功统计并取得来源版本')
    mapping = {}
    for item in expected_sources:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise ValueError('来源版本格式无效')
        for key in ('inputPath', 'path'):
            if isinstance(item.get(key), str):
                mapping[str(Path(item[key]).expanduser().resolve())] = item['id']
    files, written = [], {}
    for selected in paths:
        path = Path(selected).expanduser().resolve()
        result = {'inputPath': selected, 'path': str(path), 'success': False}
        try:
            if str(path) not in mapping:
                raise ValueError('该文件尚无成功统计的来源版本，请先重新统计')
            if str(path) not in written:
                written[str(path)] = _save_one(path, clean_settings, overrides, expected_employee, mapping[str(path)])
            backup, source_id = written[str(path)]
            result.update(success=True, backupPath=backup, sourceId=source_id)
        except Exception as exc:
            result['error'] = str(exc)
        files.append(result)
    return {'success': True, 'allSaved': all(item['success'] for item in files), 'files': files}
