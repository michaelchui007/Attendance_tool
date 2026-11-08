import io
import json

from attendance_engine import cli


def test_server_handles_multiple_requests_without_restarting(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_calculate(args):
        calls.append((args.input, args.export_root))
        return {"success": True, "input": args.input}

    monkeypatch.setattr(cli, "run_calculate", fake_calculate)
    requests = "\n".join(
        [
            json.dumps({"requestId": 1, "command": "calculate", "inputPath": "a.xlsx"}),
            json.dumps(
                {
                    "requestId": 2,
                    "command": "calculate",
                    "inputPath": "b.xlsx",
                    "outputRoot": "exports",
                }
            ),
        ]
    )
    output = io.StringIO()

    assert cli.run_server(io.StringIO(requests), output, io.StringIO(), prefetch=False) == 0

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[0] == {"event": "ready", "protocolVersion": 1}
    assert messages[1] == {"success": True, "input": "a.xlsx", "requestId": 1}
    assert messages[2] == {"success": True, "input": "b.xlsx", "requestId": 2}
    assert calls == [("a.xlsx", None), ("b.xlsx", "exports")]


def test_server_returns_error_and_keeps_processing(monkeypatch):
    monkeypatch.setattr(cli, "run_calculate", lambda args: {"success": True})
    requests = "\n".join(
        [
            json.dumps({"requestId": 1, "command": "unknown"}),
            json.dumps({"requestId": 2, "command": "calculate", "inputPath": "ok.xlsx"}),
        ]
    )
    output = io.StringIO()

    cli.run_server(io.StringIO(requests), output, io.StringIO(), prefetch=False)

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[1]["success"] is False
    assert messages[1]["requestId"] == 1
    assert messages[2] == {"success": True, "requestId": 2}


def test_server_forwards_edits_and_lunch_rules(monkeypatch):
    """常驻协议将首尾修订和午休配置传给同一次计算。"""
    received = []
    monkeypatch.setattr(cli, "run_calculate", lambda args: received.append(args) or {"success": True})
    overrides = [{"workDate": "2026-07-01", "firstCheckIn": "2026-07-01T09:00", "lastCheckOut": "2026-07-01T18:00"}]
    request = {"requestId": 1, "command": "calculate", "inputPath": "input.xlsx",
               "overrides": overrides, "lunchStart": "12:00", "lunchEnd": "13:00"}
    cli.run_server(io.StringIO(json.dumps(request)), io.StringIO(), io.StringIO(), prefetch=False)
    assert received[0].overrides == overrides
    assert received[0].lunch_start == "12:00"
    assert received[0].lunch_end == "13:00"
