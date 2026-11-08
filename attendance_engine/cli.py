from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import TextIO

from .calculator import calculate_attendance
from .calendar import HolidayCalendar, prefetch_holiday_data
from .exporter import export_report
from .parser import parse_attendance_workbook


def _configure_utf8_stdio() -> None:
    """Keep the frozen Windows sidecar protocol independent of the active code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="attendance-engine", description="考勤统计计算引擎")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calculate = subparsers.add_parser("calculate", help="读取 Excel、计算并导出结果")
    calculate.add_argument("--input", required=True, help="源考勤 Excel 路径")
    calculate.add_argument("--export-root", help="导出根目录，默认源文件旁的 export_report")
    calculate.add_argument("--json-output", help="同时将结果 JSON 写入指定文件")
    calculate.add_argument("--lunch-start", help="午休开始时间 HH:mm，默认 12:00")
    calculate.add_argument("--lunch-end", help="午休结束时间 HH:mm，默认 13:30")
    subparsers.add_parser("serve", help="通过标准输入输出提供常驻 JSON Lines 服务")
    return parser


def run_calculate(args: argparse.Namespace) -> dict:
    """按当前会话规则计算并导出，成功后返回同一份结果。"""
    source = parse_attendance_workbook(args.input)
    holiday_calendar = HolidayCalendar.for_period(source.start_date, source.end_date)
    report = calculate_attendance(
        source, holiday_calendar, overrides=getattr(args, "overrides", None),
        lunch_start=getattr(args, "lunch_start", None), lunch_end=getattr(args, "lunch_end", None),
    )
    export_report(report, args.export_root)
    payload = report.to_dict()
    if args.json_output:
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
            if not isinstance(input_path, str) or not input_path.strip():
                raise ValueError("缺少源考勤 Excel 路径")
            output_root = request.get("outputRoot")
            if output_root is not None and not isinstance(output_root, str):
                raise ValueError("输出目录必须是字符串")
            payload = run_calculate(
                argparse.Namespace(
                    input=input_path, export_root=output_root, json_output=None,
                    overrides=request.get("overrides"), lunch_start=request.get("lunchStart"),
                    lunch_end=request.get("lunchEnd"),
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
