from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from time import perf_counter, monotonic
from pathlib import Path
from typing import TextIO

from .calculator import calculate_attendance
from .calendar import HolidayCalendar, prefetch_holiday_data
from .exporter import export_report
from .parser import parse_attendance_workbook
from .importer import SourceCache, merge_workbooks

_SOURCE_CACHE = SourceCache()
_CALENDAR_CACHE = {}


def _configure_utf8_stdio() -> None:
    """明确标准输入输出编码，避免冻结程序按 Windows 本地代码页误读路径。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="attendance-engine", description="考勤统计计算引擎")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calculate = subparsers.add_parser("calculate", help="读取 Excel、计算并导出结果")
    calculate.add_argument("--input", required=True, nargs="+", help="一个或多个源考勤 Excel 路径")
    calculate.add_argument("--export-root", help="导出根目录，默认源文件旁的 export_report")
    calculate.add_argument("--json-output", help="同时将结果 JSON 写入指定文件")
    calculate.add_argument("--lunch-start", help="午休开始时间 HH:mm，默认 12:00")
    calculate.add_argument("--lunch-end", help="午休结束时间 HH:mm，默认 13:30")
    subparsers.add_parser("serve", help="通过标准输入输出提供常驻 JSON Lines 服务")
    return parser


def run_calculate(args: argparse.Namespace) -> dict:
    """复用已解析文件及日历，批量合并后计算，冲突未处理时禁止导出。"""
    started = perf_counter()
    progress = getattr(args, 'progress', None)
    paths = getattr(args, 'input_paths', None) or args.input
    if isinstance(paths, str):
        paths = [paths]
    source, cache_hits = merge_workbooks(paths, cache=_SOURCE_CACHE,
                                        source_choices=getattr(args, 'source_choices', None), progress=progress)
    expected_employee = getattr(args, 'expected_employee', None)
    if expected_employee is not None:
        if not isinstance(expected_employee, dict) or (expected_employee.get('name'), expected_employee.get('account')) != (source.employee_name, source.employee_account):
            raise ValueError('源文件员工已改变，不能将原有人工修订应用到新员工；请清空文件集后重新导入')
    parsed = perf_counter()
    if progress:
        progress('calendar', len(paths), len(paths), '正在加载工作日历')
    key = (source.start_date, source.end_date)
    cached_calendar = _CALENDAR_CACHE.get(key)
    if cached_calendar and monotonic() - cached_calendar[0] < 86400:
        holiday_calendar = cached_calendar[1]
    else:
        holiday_calendar = HolidayCalendar.for_period(source.start_date, source.end_date)
        _CALENDAR_CACHE[key] = (monotonic(), holiday_calendar)
        if len(_CALENDAR_CACHE) > 16:
            del _CALENDAR_CACHE[next(iter(_CALENDAR_CACHE))]
    calendar_loaded = perf_counter()
    if progress:
        progress('calculate', len(paths), len(paths), '正在计算每日工时与调休')
    report = calculate_attendance(
        source, holiday_calendar, overrides=getattr(args, "overrides", None),
        lunch_start=getattr(args, "lunch_start", None), lunch_end=getattr(args, "lunch_end", None),
        settings=getattr(args, "settings", None),
    )
    calculated = perf_counter()
    if not any(not item['resolved'] for item in source.conflicts):
        if progress:
            progress('export', len(paths), len(paths), '正在导出统计结果')
        export_report(report, args.export_root)
    finished = perf_counter()
    payload = report.to_dict()
    payload['timings'] = {'parseMs': round((parsed - started) * 1000, 2),
                          'calendarMs': round((calendar_loaded - parsed) * 1000, 2),
                          'calculateMs': round((calculated - calendar_loaded) * 1000, 2),
                          'exportMs': round((finished - calculated) * 1000, 2),
                          'totalMs': round((finished - started) * 1000, 2), 'cacheHits': cache_hits}
    if getattr(args, 'json_output', None):
        output = Path(args.json_output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _write_payload(stream: TextIO, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def run_server(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    prefetch: bool = True,
) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    error_stream = error_stream or sys.stderr
    if prefetch:
        threading.Thread(target=prefetch_holiday_data, daemon=True).start()
    _write_payload(output_stream, {"event": "ready", "protocolVersion": 1})

    for raw_line in input_stream:
        if not raw_line.strip():
            continue
        request_id = None
        try:
            request = json.loads(raw_line)
            request_id = request.get("requestId")
            if request.get("command") != "calculate":
                raise ValueError("不支持的 Sidecar 命令")
            input_path = request.get("inputPath")
            input_paths = request.get("inputPaths")
            if input_paths is None:
                if not isinstance(input_path, str) or not input_path.strip():
                    raise ValueError("缺少源考勤 Excel 路径")
            elif not isinstance(input_paths, list) or not input_paths or any(not isinstance(p, str) or not p.strip() for p in input_paths):
                raise ValueError("源文件路径须为非空字符串列表")
            output_root = request.get("outputRoot")
            if output_root is not None and not isinstance(output_root, str):
                raise ValueError("输出目录必须是字符串")
            def report_progress(stage, current, total, message):
                """在最终结果前发送同一请求的进度事件。"""
                _write_payload(output_stream, {'event': 'progress', 'requestId': request_id,
                                               'stage': stage, 'current': current, 'total': total, 'message': message})

            payload = run_calculate(
                argparse.Namespace(
                    input=input_path, export_root=output_root, json_output=None,
                    overrides=request.get("overrides"), lunch_start=request.get("lunchStart"),
                    lunch_end=request.get("lunchEnd"),
                    input_paths=input_paths, settings=request.get('settings'),
                    source_choices=request.get('sourceChoices'), progress=report_progress,
                    expected_employee=request.get('expectedEmployee'),
                )
            )
        except Exception as exc:  # Protocol boundary: keep the server alive for later requests.
            traceback.print_exc(file=error_stream)
            payload = {"success": False, "error": str(exc)}
        if request_id is not None:
            payload["requestId"] = request_id
        _write_payload(output_stream, payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return run_server()
        if args.command == "calculate":
            payload = run_calculate(args)
        else:
            parser.error("未知命令")
            return 2
        _write_payload(sys.stdout, payload)
        return 0
    except Exception as exc:  # CLI boundary: return structured failure to the desktop shell.
        traceback.print_exc(file=sys.stderr)
        _write_payload(sys.stdout, {"success": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
