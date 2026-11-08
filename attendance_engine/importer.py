"""批量报表解析缓存、同员工合并及来源冲突识别。"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from typing import Callable

from .models import AttendanceSource, PunchType
from .parser import parse_attendance_workbook


def dates_between(start: date, end: date):
    """逐日遍历包含两端的日期区间。"""
    while start <= end:
        yield start
        start += timedelta(days=1)


class SourceCache:
    """在常驻引擎中复用内容未变的解析对象，限制最多32份缓存。"""

    def __init__(self):
        self.entries: OrderedDict[str, tuple[tuple, str, AttendanceSource]] = OrderedDict()

    def read(self, path: Path) -> tuple[AttendanceSource, str, bool]:
        """按文件元数据失效缓存；重新读取时以内容摘要识别改名副本。"""
        stat = path.stat()
        fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        key = str(path)
        cached = self.entries.get(key)
        if cached and cached[0] == fingerprint:
            self.entries.move_to_end(key)
            return deepcopy(cached[2]), cached[1], True
        digest = sha256(path.read_bytes()).hexdigest()[:16]
        source = parse_attendance_workbook(path)
        # 解析期间文件变化时不保存混合版本供后续重算使用。
        after = path.stat()
        if fingerprint != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f'读取期间文件发生变化，请重新导入：{path.name}')
        self.entries[key] = (fingerprint, digest, source)
        self.entries.move_to_end(key)
        while len(self.entries) > 32:
            self.entries.popitem(last=False)
        return deepcopy(source), digest, False


def _punch_key(record) -> tuple:
    """用于识别完全相同的原始打卡，不合并不同状态。"""
    return record.timestamp, record.punch_type, record.location, record.status


def _leave_key(record) -> tuple:
    """同区间申请按身份合并；无区间则保留日期和单项位置。"""
    if record.start_at is not None and record.end_at is not None:
        return record.start_at, record.end_at, record.minutes, record.parse_warning
    return record.work_date, record.description, record.item_index, record.minutes


def _daily_signature(source: AttendanceSource, day: date) -> tuple:
    """按原始记录判断同日版本差异，互补上下班允许合并。"""
    punches = {kind: {_punch_key(item) for item in source.records
                     if item.work_date == day and item.punch_type == kind} for kind in PunchType}
    leaves = {_leave_key(item) for item in source.compensatory_leave_records
              if (item.start_at is not None and item.end_at is not None and item.start_at.date() <= day <= item.end_at.date())
              or item.work_date == day}
    return punches, leaves


def merge_workbooks(
    paths: list[str], *, cache: SourceCache | None = None,
    source_choices: dict[str, str] | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> tuple[AttendanceSource, int]:
    """合并同员工的文件，去重同时保留所有来源。

    Args:
        paths: 用户选择的路径列表，输出目录仍以第一个文件为基准。
        cache: 当前服务会话的有界解析缓存。
        source_choices: 按日期选择的冲突来源内容标识。
        progress: 阶段、当前项、总项和说明回调。

    Returns:
        合并后的资料及本次命中的解析缓存数量。

    Raises:
        ValueError: 文件不可解析、员工不一致或来源选择无效。
    """
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError('请提供一个或多个有效的源文件路径')
    if source_choices is not None and (not isinstance(source_choices, dict) or
                                      any(not isinstance(k, str) or not isinstance(v, str) for k, v in source_choices.items())):
        raise ValueError('来源选择必须为日期到文件标识的映射')
    cache = cache or SourceCache()
    files, originals, seen = [], [], set()
    hits = 0
    for index, value in enumerate(paths):
        path = Path(value).expanduser().resolve()
        if progress:
            progress('parse', index + 1, len(paths), f'正在读取第 {index + 1}/{len(paths)} 个报表：{path.name}')
        try:
            source, ident, hit = cache.read(path)
        except Exception as exc:
            raise ValueError(f'无法读取文件 {path.name}：{exc}') from exc
        hits += hit
        duplicate = ident in seen
        files.append({'id': ident, 'path': str(path), 'inputPath': value, 'name': path.name,
                      'startDate': source.start_date.isoformat(), 'endDate': source.end_date.isoformat(),
                      'duplicateOf': ident if duplicate else None, 'cacheHit': hit})
        if duplicate:
            continue
        seen.add(ident)
        for record in source.records:
            record.sources = [{'sourceId': ident, 'path': str(path), 'name': path.name,
                               'sourceSheet': '打卡详情', 'sourceRow': record.source_row}]
        for record in source.compensatory_leave_records:
            record.sources = [{'sourceId': ident, 'path': str(path), 'name': path.name,
                               'sourceSheet': record.source_sheet, 'sourceRow': record.source_row}]
        originals.append((ident, source))
    identities = {(source.employee_name, source.employee_account) for _, source in originals}
    if len(identities) != 1:
        raise ValueError('所选报表包含不同员工，不能合并；请检查文件：' + '、'.join(item['name'] for item in files))
    start = min(source.start_date for _, source in originals)
    end = max(source.end_date for _, source in originals)
    covered = {day for _, source in originals for day in dates_between(source.start_date, source.end_date)}
    signatures = {(ident, day): _daily_signature(source, day) for ident, source in originals
                  for day in dates_between(source.start_date, source.end_date)}
    conflict_ids: dict[date, set[str]] = defaultdict(set)
    for (left_id, left), (right_id, right) in combinations(originals, 2):
        for day in dates_between(max(left.start_date, right.start_date), min(left.end_date, right.end_date)):
            left_punches, left_leaves = signatures[left_id, day]
            right_punches, right_leaves = signatures[right_id, day]
            different_punches = any(left_punches[kind] and right_punches[kind] and
                                    left_punches[kind] != right_punches[kind] for kind in PunchType)
            if different_punches or left_leaves != right_leaves:
                conflict_ids[day].update((left_id, right_id))
    # 冲突选择包含覆盖当天的所有有效来源，不能将原选项错误套给新文件。
    choices = dict(source_choices or {})
    conflicts = []
    for day in sorted(conflict_ids):
        ids = sorted(ident for ident, source in originals if source.start_date <= day <= source.end_date)
        chosen = choices.get(day.isoformat())
        if chosen is not None and chosen not in ids:
            raise ValueError(f'{day} 选择的来源已失效，请重新选择')
        conflicts.append({'workDate': day.isoformat(), 'sourceIds': ids,
                          'reason': '同日来源版本存在不同打卡或调休申请',
                          'resolved': chosen is not None, 'selectedSourceId': chosen})
    conflict_dates = {item['workDate'] for item in conflicts}
    choices = {key: value for key, value in choices.items() if key in conflict_dates}
    punches, leaves = {}, {}
    for _, source in originals:
        for record in source.records:
            key = _punch_key(record)
            if key in punches:
                punches[key].sources.extend(record.sources)
            else:
                punches[key] = record
        for record in source.compensatory_leave_records:
            key = _leave_key(record)
            if key in leaves:
                leaves[key].sources.extend(item for item in record.sources if item not in leaves[key].sources)
            else:
                leaves[key] = record
    employee_name, account = identities.pop()
    source = AttendanceSource(Path(paths[0]).expanduser().resolve(), employee_name, account, start, end,
                              sorted(punches.values(), key=lambda item: item.timestamp),
                              sorted(leaves.values(), key=lambda item: (item.work_date, item.description)),
                              files=files, covered_dates=covered, conflicts=conflicts, source_choices=choices)
    return source, hits
