from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .calculator import calculate_attendance
from .exporter import export_report
from .parser import parse_attendance_workbook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="attendance-engine", description="考勤统计计算引擎")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calculate = subparsers.add_parser("calculate", help="读取 Excel、计算并导出结果")
    calculate.add_argument("--input", required=True, help="源考勤 Excel 路径")
    calculate.add_argument("--export-root", help="导出根目录，默认源文件旁的 export_report")
    calculate.add_argument("--json-output", help="同时将结果 JSON 写入指定文件")
    return parser


def run_calculate(args: argparse.Namespace) -> dict:
    source = parse_attendance_workbook(args.input)
    report = calculate_attendance(source)
    export_report(report, args.export_root)
    payload = report.to_dict()
    if args.json_output:
        output = Path(args.json_output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "calculate":
            payload = run_calculate(args)
        else:
            parser.error("未知命令")
            return 2
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:  # CLI boundary: return structured failure to the desktop shell.
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
