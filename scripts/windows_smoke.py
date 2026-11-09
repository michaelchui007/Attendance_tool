"""在 Windows 上验证冻结引擎、安装后程序及中文路径的真实行为。"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import socket
from urllib.request import ProxyHandler, build_opener
import statistics
import struct
import subprocess
import threading
import time
from datetime import date

from openpyxl import Workbook, load_workbook


def make_report(path: Path, month: int) -> None:
    """创建匿名月报，保留企业微信实际列位置。"""
    workbook = Workbook()
    summary = workbook.active
    summary.title = "概况统计与打卡明细"
    detail = workbook.create_sheet("打卡详情")
    detail["A2"] = f"统计时间:{month:02d}-01 ～ {month:02d}-28 制表时间:2026-{month:02d}-28 18:30(UTC+8)"
    row = 5
    for day in range(1, 29):
        current = date(2026, month, day)
        summary.cell(day + 4, 1, current.strftime("%Y/%m/%d"))
        summary.cell(day + 4, 2, "测试员工")
        summary.cell(day + 4, 3, "test-account")
        if current.weekday() >= 5:
            continue
        for kind, stamp in [("上班", "09:00"), ("下班", "18:30")]:
            values = [current.strftime("%Y/%m/%d"), "测试员工", "test-account", None, None, None,
                      kind, None, stamp, "正常", "测试办公地点", None, None, None, "--"]
            for col, value in enumerate(values, 1):
                detail.cell(row, col, value)
            row += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()


class Engine:
    """用真实 UTF-8 标准管道驱动安装目录中的冻结引擎。"""

    def __init__(self, executable: Path, output: Path):
        self.started = time.perf_counter()
        env = dict(os.environ)
        env.pop("PYTHONUTF8", None)
        env.pop("PYTHONIOENCODING", None)
        env["ATTENDANCE_HOLIDAY_CACHE_DIR"] = str(output / "日历缓存")
        self.process = subprocess.Popen([str(executable), "serve"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()
        self.errors = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()
        ready = self.messages.get(timeout=60)
        assert ready["event"] == "ready", ready
        self.cold_start = time.perf_counter() - self.started

    def _read(self):
        """持续读取响应，避免依赖 Windows 控制台代码页。"""
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line.decode("utf-8")))
            except Exception as error:
                self.messages.put({"success": False, "error": str(error)})

    def _errors(self):
        """排空错误管道以避免输出阻塞。"""
        for line in self.process.stderr:
            self.errors.append(line.decode("utf-8", errors="replace"))

    def request(self, payload):
        """发送包含原生中文的JSON，验证stdin无需环境变量也正确解码。"""
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        events = []
        while True:
            value = self.messages.get(timeout=120)
            if value.get("event") == "progress":
                events.append(value)
                continue
            assert value.get("success"), (value, self.errors[-10:])
            assert value.get("requestId") == payload["requestId"]
            return value, events

    def close(self):
        """关闭标准输入并等待进程正常结束。"""
        self.process.stdin.close()
        self.process.wait(timeout=15)
        assert self.process.returncode == 0


def check_pe_gui(executable: Path) -> None:
    """解析主程序PE可选头，要求正式构建使用GUI子系统。"""
    with executable.open("rb") as stream:
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        assert stream.read(4) == b"PE\0\0"
        stream.seek(pe_offset + 4 + 20 + 68)
        assert struct.unpack("<H", stream.read(2))[0] == 2, "主程序仍为控制台子系统"


def test_engine(engine_path: Path, root: Path) -> dict:
    """测量1/3/12个月及中文导出，重复请求验证解析缓存。"""
    paths = [root / "中文路径 (测试)" / f"考勤_{month:02d}月.xlsx" for month in range(1, 13)]
    for month, path in enumerate(paths, 1):
        make_report(path, month)
    result = {"groups": [], "inputUnicode": True, "environmentEncodingForced": False}
    for count in [1, 3, 12]:
        engine = Engine(engine_path, root)
        times = []
        samples = []
        try:
            for repeat in range(5):
                payload = {"requestId": repeat + 1, "command": "calculate",
                           "inputPaths": [str(path) for path in paths[:count]],
                           "outputRoot": str(root / "中文导出目录"),
                           "settings": {"standardMinutes": 480, "workdayStart": "09:00", "workdayEnd": "18:30",
                                        "lunchStart": "12:00", "lunchEnd": "13:30", "dateRules": []}}
                import psutil
                memory_samples = []
                monitoring = threading.Event()
                def sample_memory():
                    while not monitoring.is_set():
                        try:
                            memory_samples.append(psutil.Process(engine.process.pid).memory_info().rss)
                        except psutil.Error:
                            break
                        monitoring.wait(0.02)
                sampler = threading.Thread(target=sample_memory, daemon=True)
                sampler.start()
                before = time.perf_counter()
                try:
                    report, events = engine.request(payload)
                finally:
                    monitoring.set()
                    sampler.join(2)
                elapsed = time.perf_counter() - before
                times.append(elapsed)
                assert report["employee"]["name"] == "测试员工"
                assert len(report["sources"]) == count
                if repeat > 0:
                    assert report["timings"]["cacheHits"] == count, "重算未复用全部解析缓存"
                output_path = Path(report["outputPath"])
                assert output_path.exists() and "测试员工" in output_path.name
                workbook = load_workbook(output_path, read_only=True, data_only=True)
                assert workbook["月度汇总"]["B2"].value == "测试员工"
                workbook.close()
                samples.append({"seconds": elapsed, "timings": report.get("timings"), "progressEvents": len(events), "peakRssBytes": max(memory_samples, default=0)})
            result["groups"].append({"months": count, "coldStartSeconds": engine.cold_start,
                                     "medianSeconds": statistics.median(times), "maxSeconds": max(times),
                                     "samples": samples})
        finally:
            engine.close()
    return result


def test_portable_state(engine_path: Path, root: Path) -> dict:
    """用冻结引擎验证无原因扣减、处理状态及跨目录恢复。

    Args:
        engine_path: 已安装的计算引擎。
        root: 仅含匿名样本的测试目录。

    Returns:
        配置写入、备份及新进程恢复的检查记录。
    """
    from shutil import copyfile
    from zipfile import ZipFile

    source = root / "便携配置" / "十一月公司调休.xlsx"
    make_report(source, 11)
    workbook = load_workbook(source)
    for row in workbook["打卡详情"].iter_rows(min_row=5):
        if str(row[0].value) in {"2026/11/02", "2026/11/03"}:
            row[8].value = "--"
            row[9].value = "缺卡"
    workbook.save(source)
    workbook.close()
    original = source.read_bytes()
    with ZipFile(source) as archive:
        sheets = {name: archive.read(name) for name in archive.namelist() if name.startswith('xl/worksheets/')}
    settings = {"standardMinutes": 480, "workdayStart": "09:00", "workdayEnd": "18:30",
                "lunchStart": "12:00", "lunchEnd": "13:30", "dateRules": []}
    overrides = [{"workDate": f"2026-11-0{day}", "compensatoryLeaveMinutes": 480} for day in (2, 3)]
    engine = Engine(engine_path, root)
    try:
        calculated, _ = engine.request({"requestId": 101, "command": "calculate", "inputPaths": [str(source)],
                                       "settings": settings, "overrides": overrides, "outputRoot": str(root / '便携导出')})
        days = [day for day in calculated['days'] if day['workDate'] in {'2026-11-02', '2026-11-03'}]
        assert all(day['netExtraMinutes'] == -480 and day['leaveReason'] == '手动调整' for day in days)
        assert all(not day['needsAttention'] and day['resolutionStatus'] == 'resolved' and day['actualEffectiveMinutes'] is None for day in days)
        saved, _ = engine.request({"requestId": 102, "command": "save_workbook_state", "inputPaths": [str(source)],
                                  "settings": settings, "overrides": overrides, "expectedEmployee": calculated['employee'],
                                  "expectedSources": calculated['sources']})
        assert saved['allSaved'], saved
        assert Path(saved['files'][0]['backupPath']).read_bytes() == original
        with ZipFile(source) as archive:
            assert all(archive.read(name) == content for name, content in sheets.items())
    finally:
        engine.close()
    moved = root / '另一台电脑' / '改名后的考勤.xlsx'
    moved.parent.mkdir(parents=True, exist_ok=True)
    copyfile(source, moved)
    other = Engine(engine_path, root)
    try:
        inspected, _ = other.request({"requestId": 103, "command": "inspect_workbook_state", "inputPaths": [str(moved)]})
        assert inspected['files'][0]['status'] == 'valid', inspected
        state = inspected['files'][0]['state']
        restored, _ = other.request({"requestId": 104, "command": "calculate", "inputPaths": [str(moved)],
                                    "settings": state['settings'], "overrides": state['overrides'],
                                    "expectedEmployee": calculated['employee'], "outputRoot": str(root / '便携恢复导出')})
        restored_days = [day for day in restored['days'] if day['workDate'] in {'2026-11-02', '2026-11-03'}]
        assert all(day['netExtraMinutes'] == -480 and not day['needsAttention'] for day in restored_days)
        assert restored['sources'][0]['id'] == calculated['sources'][0]['id']
    finally:
        other.close()
    return {'optionalReason': True, 'handledDaysRemoved': True, 'backupMatchesOriginal': True,
            'rawSheetsUnchanged': True, 'newProcessAndPathRestore': True}


def visible_console_windows(pids: set[int]) -> list[int]:
    """枚举可见窗口，定位应用进程树产生的控制台窗口。"""
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    handles = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def inspect(hwnd, _param):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if pid.value in pids and user32.IsWindowVisible(hwnd) and name.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}:
            handles.append(int(hwnd))
        return True
    user32.EnumWindows(callback_type(inspect), 0)
    return handles


def test_explorer_drop(browser, application_pid: int, root: Path) -> None:
    """从真实资源管理器拖动中文Excel到应用，禁止用JS事件模拟替代。"""
    from pywinauto import Desktop, mouse
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    drop_dir = (root / "真实拖放样例").resolve()
    dropped = drop_dir / "拖放中文考勤 三月.xlsx"
    make_report(dropped, 3)
    subprocess.Popen(["explorer.exe", str(drop_dir)])
    wait = WebDriverWait(browser, 45)
    explorer = Desktop(backend="win32").window(class_name="CabinetWClass", title_re=".*真实拖放样例.*")
    explorer.wait("visible", timeout=30)
    explorer.move_window(x=0, y=20, width=400, height=600)
    app_window = Desktop(backend="win32").window(process=application_pid, title="考勤统计")
    app_window.move_window(x=420, y=0, width=980, height=750)
    item = Desktop(backend="uia").window(handle=explorer.handle).child_window(
        title_re="拖放中文考勤 三月(?:\\.xlsx)?", control_type="ListItem")
    item.wait("visible", timeout=20)
    rectangle = item.rectangle()
    start = (rectangle.left + min(50, rectangle.width() // 2), rectangle.top + rectangle.height() // 2)
    panel = browser.find_element(By.CSS_SELECTOR, ".import-panel")
    bounds = browser.execute_script("const r=arguments[0].getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}", panel)
    import win32gui
    origin = win32gui.ClientToScreen(app_window.handle, (0, 0))
    end = (origin[0] + int(bounds["x"]) + 100, origin[1] + int(bounds["y"]) + 40)
    explorer.set_focus()
    browser.save_screenshot(str(root / "before-native-drop.png"))
    mouse.move(coords=start)
    mouse.press(button="left", coords=start)
    try:
        for step in range(1, 21):
            position = (int(start[0] + (end[0] - start[0]) * step / 20),
                        int(start[1] + (end[1] - start[1]) * step / 20))
            mouse.move(coords=position)
            time.sleep(0.05)
    finally:
        mouse.release(button="left", coords=end)
    wait.until(lambda d: dropped.name in d.find_element(By.TAG_NAME, "body").text)
    browser.save_screenshot(str(root / "after-native-drop.png"))
    explorer.close()


def test_gui(application: Path, driver_path: Path, root: Path) -> dict:
    """通过WebView2驱动及原生文件对话框验证已安装主程序。"""
    import psutil
    from pywinauto import Desktop
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    # WebView2 150+ 在高完整性进程中忽略调试环境变量，测试须以Medium运行。
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)[0]
    integrity = sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1)
    security_context = {"integrityRid": integrity, "isUserAnAdmin": bool(ctypes.windll.shell32.IsUserAnAdmin())}
    (root / "security-context.json").write_text(json.dumps(security_context, indent=2), encoding="utf-8")
    assert integrity == 0x2000 and not security_context["isUserAnAdmin"], "WebView2 GUI测试必须以Medium完整性运行"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    local_http = build_opener(ProxyHandler({}))
    options = Options()
    options.use_webview = True
    # 从启动之前开始采样，既检查主程序也检查计算引擎产生的可见控制台。
    audit_stop = threading.Event()
    audit = {"samples": 0, "visibleConsoleHandles": [], "observedPids": []}
    expected_path = str(application.resolve()).casefold()
    def monitor_windows():
        observed = set()
        while not audit_stop.is_set():
            tree = set()
            for process in psutil.process_iter(["pid", "exe"]):
                try:
                    if process.info["exe"] and str(Path(process.info["exe"]).resolve()).casefold() == expected_path:
                        tree.add(process.pid)
                        tree.update(child.pid for child in process.children(recursive=True))
                except (psutil.Error, OSError):
                    continue
            observed.update(tree)
            audit["samples"] += 1
            audit["visibleConsoleHandles"].extend(visible_console_windows(tree))
            audit["observedPids"] = sorted(observed)
            audit_stop.wait(0.1)
    monitor = threading.Thread(target=monitor_windows, daemon=True)
    monitor.start()
    browser = None
    application_process = None
    launch_log = {"debuggerAvailable": False}
    checked = []
    stdout_log = (root / "application-stdout.log").open("wb")
    stderr_log = (root / "application-stderr.log").open("wb")
    try:
        # 直接启动真实安装程序，再附加到固定调试端口，避免依赖DevToolsActivePort临时文件。
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            debug_port = listener.getsockname()[1]
        debugger_address = f"127.0.0.1:{debug_port}"
        app_env = dict(os.environ)
        app_env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1 --enable-automation"
        app_env["WEBVIEW2_USER_DATA_FOLDER"] = str((root / "webview-profile").resolve())
        application_process = subprocess.Popen([str(application)], env=app_env, stdout=stdout_log, stderr=stderr_log)
        launch_log.update({"pid": application_process.pid, "debuggerAddress": debugger_address})
        deadline = time.monotonic() + 60
        last_error = ""
        while time.monotonic() < deadline:
            if application_process.poll() is not None:
                raise AssertionError(f"安装后主程序提前退出：{application_process.returncode}")
            try:
                with local_http.open(f"http://{debugger_address}/json/version", timeout=2) as response:
                    launch_log["browserMetadata"] = json.load(response)
                launch_log["debuggerAvailable"] = True
                break
            except (OSError, ValueError) as error:
                last_error = str(error)
                time.sleep(0.25)
        launch_log["lastDebuggerError"] = last_error
        assert launch_log["debuggerAvailable"], f"WebView2调试端口未就绪：{last_error}"
        options.debugger_address = debugger_address
        browser = webdriver.Edge(service=Service(str(driver_path), log_output=str(root / "edgedriver.log")), options=options)
        wait = WebDriverWait(browser, 90)
        author = ''.join(chr(value) for value in [77, 105, 99, 104, 97, 101, 108, 67, 104, 117, 105])
        wait.until(lambda d: author in d.find_element(By.CSS_SELECTOR, '.author-credit').text)
        browser.find_element(By.CSS_SELECTOR, '.author-credit').click()
        about = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '.about-dialog[open]'))
        assert author in about.text and '0.3.1' in about.text
        about.find_element(By.XPATH, ".//button[normalize-space(.)='关闭']").click()
        checked.append('原生应用信息与关于页面显示一致')
        wait.until(lambda d: d.find_elements(By.XPATH, "//button[contains(.,'系统配置')]"))
        browser.find_element(By.XPATH, "//button[contains(.,'系统配置')]").click()
        standard = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '[aria-label="每日标准工时（小时）"]'))
        standard.clear()
        standard.send_keys("7")
        browser.find_element(By.XPATH, "//button[normalize-space(.)='保存配置']").click()
        browser.find_element(By.XPATH, "//button[normalize-space(.)='返回工作台']").click()
        checked.append("系统配置真实WebView保存")
        paths = [root / "界面样例" / f"中文考勤 {month}月.xlsx" for month in (1, 2)]
        for month, path in enumerate(paths, 1):
            make_report(path, month)
        dialog_errors = []
        def select_files():
            try:
                # runner可能同时存在系统属性弹窗，只操作本应用创建的文件对话框。
                dialog = Desktop(backend="win32").window(process=application_process.pid, class_name="#32770")
                dialog.wait("visible", timeout=30)
                # 使用原生文件名快捷键和Unicode剪贴板，避免误定位到搜索框。
                import win32clipboard
                from pywinauto.keyboard import send_keys
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT,
                                                   " ".join('"' + str(path.resolve()) + '"' for path in paths))
                finally:
                    win32clipboard.CloseClipboard()
                dialog.set_focus()
                send_keys("%n")
                send_keys("^v")
                dialog.child_window(control_id=1).click()
            except Exception as error:
                dialog_errors.append(str(error))
        selector = threading.Thread(target=select_files, daemon=True)
        selector.start()
        browser.find_element(By.XPATH, "//button[contains(.,'浏览') or contains(.,'添加报表')]").click()
        selector.join(35)
        assert not dialog_errors and not selector.is_alive(), dialog_errors
        wait.until(lambda d: all(path.name in d.find_element(By.TAG_NAME, "body").text for path in paths))
        checked.append("原生多选中文路径及页面显示")
        browser.find_element(By.CSS_SELECTOR, ".import-actions .primary-button").click()
        wait.until(lambda d: "测试员工" in d.find_element(By.CSS_SELECTOR, ".result-heading").text and "2026-02-28" in d.find_element(By.CSS_SELECTOR, ".result-heading").text)
        assert "源文件不存在" not in browser.find_element(By.TAG_NAME, "body").text
        checked.append("已安装程序中文多文件真实统计")
        (root / "gui-progress.json").write_text(json.dumps({"checks": checked}, ensure_ascii=False), encoding="utf-8")
        browser.save_screenshot(str(root / "installed-app.png"))
        processes = [p for p in psutil.process_iter(["pid", "exe"]) if p.info["exe"] and Path(p.info["exe"]).resolve() == application.resolve()]
        assert processes, "未发现安装后主程序进程"
        test_explorer_drop(browser, processes[0].pid, root)
        checked.append("真实Explorer拖入中文Excel")
        browser.find_element(By.CSS_SELECTOR, ".import-actions .primary-button").click()
        wait.until(lambda d: "2026-03-28" in d.find_element(By.CSS_SELECTOR, ".result-heading").text)
        assert "源文件不存在" not in browser.find_element(By.TAG_NAME, "body").text
        checked.append("拖放后真实统计")
        # 留空原因保存、写回源配置，再模拟关闭页面后重新导入选择配置。
        from selenium.webdriver.support.ui import Select
        target_row = "//section[@id='daily-workspace']//tbody/tr[.//strong[contains(.,'01-05')]]"
        browser.find_element(By.XPATH, target_row).click()
        browser.find_element(By.XPATH, "//button[normalize-space(.)='编辑考勤']").click()
        Select(browser.find_element(By.CSS_SELECTOR, '[aria-label="扣减方式"]')).select_by_value('manual')
        deduction = browser.find_element(By.CSS_SELECTOR, '[aria-label="扣减调休（小时）"]')
        deduction.clear()
        deduction.send_keys('8')
        browser.find_element(By.CSS_SELECTOR, '.editing-toolbar .primary-button').click()
        wait.until(lambda d: '手动调整' in d.find_element(By.CSS_SELECTOR, '.detail-panel').text)
        assert '已处理' in browser.find_element(By.CSS_SELECTOR, '.detail-panel').text
        checked.append('真实界面原因留空保存并标记已处理')
        browser.find_element(By.XPATH, "//button[normalize-space(.)='保存配置到源Excel']").click()
        wait.until(lambda d: '保存成功' in d.find_element(By.CSS_SELECTOR, '[aria-label="源文件保存结果"]').text)
        assert '备份' in browser.find_element(By.CSS_SELECTOR, '[aria-label="源文件保存结果"]').text
        browser.refresh()
        wait.until(lambda d: d.find_elements(By.XPATH, "//button[normalize-space(.)='添加报表']"))
        dialog_errors.clear()
        selector = threading.Thread(target=select_files, daemon=True)
        selector.start()
        browser.find_element(By.XPATH, "//button[normalize-space(.)='添加报表']").click()
        selector.join(35)
        assert not dialog_errors and not selector.is_alive(), dialog_errors
        wait.until(lambda d: d.find_elements(By.XPATH, "//h2[normalize-space(.)='选择本次统计的配置']"))
        browser.save_screenshot(str(root / 'workbook-config-choice.png'))
        browser.find_element(By.XPATH, "//button[normalize-space(.)='使用 Excel 配置']").click()
        browser.find_element(By.CSS_SELECTOR, '.import-actions .primary-button').click()
        wait.until(lambda d: '测试员工' in d.find_element(By.CSS_SELECTOR, '.result-heading').text)
        browser.find_element(By.XPATH, target_row).click()
        assert '手动调整' in browser.find_element(By.CSS_SELECTOR, '.detail-panel').text
        assert '已处理' in browser.find_element(By.CSS_SELECTOR, '.detail-panel').text
        checked.append('真实界面备份写回源Excel并重新导入选择恢复')
        browser.save_screenshot(str(root / 'restored-manual-adjustment.png'))
        tree = set()
        for process in processes:
            tree.add(process.pid)
            tree.update(child.pid for child in process.children(recursive=True))
        assert not visible_console_windows(tree), "应用进程树存在可见控制台"
        checked.append("主程序及子进程无可见控制台")
        # 通过原生关闭事件验证正常退出清理，而非仅由WebDriver强制杀进程。
        main_window = Desktop(backend="win32").window(process=processes[0].pid, title="考勤统计")
        main_window.close()
        gone, alive = psutil.wait_procs([psutil.Process(pid) for pid in tree if psutil.pid_exists(pid)], timeout=15)
        assert not [p.pid for p in alive if p.is_running()], "关闭后仍有应用子进程"
        checked.append("关闭后清理子进程")
        assert not audit["visibleConsoleHandles"], "启动或计算期间观察到可见控制台"
        assert audit["observedPids"], "窗口采样没有观察到实际应用进程"
        checked.append("启动至退出期间控制台采样")
        return {"checks": checked, "nativeDrop": "Explorer真实鼠标拖放", "windowAudit": audit}
    except Exception:
        # 即使WebDriver会话尚未创建，也保留实际Windows桌面与启动证据。
        launch_log["captureErrors"] = []
        try:
            windows = [{"title": window.window_text(), "class": window.class_name(), "handle": window.handle}
                       for window in Desktop(backend="win32").windows()]
            (root / "failure-windows.json").write_text(json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as capture_error:
            launch_log["captureErrors"].append("windows: " + str(capture_error))
        try:
            from PIL import ImageGrab
            ImageGrab.grab(all_screens=True).save(root / "failure-desktop.png")
        except Exception as capture_error:
            launch_log["captureErrors"].append("desktop: " + str(capture_error))
        try:
            if browser is not None:
                browser.save_screenshot(str(root / "failure.png"))
                (root / "failure-page.txt").write_text(browser.find_element(By.TAG_NAME, "body").text, encoding="utf-8")
        except Exception as capture_error:
            launch_log["captureErrors"].append("webview: " + str(capture_error))
        raise
    finally:
        audit_stop.set()
        monitor.join(5)
        launch_log["returnCode"] = application_process.poll() if application_process else None
        # 仅记录本测试应用及其子进程，避免收集runner中其他程序的命令行。
        launch_log["applicationProcesses"] = []
        if application_process is not None and application_process.poll() is None:
            try:
                parent = psutil.Process(application_process.pid)
                allowed_names = {application.name.casefold(), "attendance-engine.exe", "msedgewebview2.exe", "conhost.exe"}
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        if process.name().casefold() in allowed_names:
                            launch_log["applicationProcesses"].append({"pid": process.pid, "exe": process.exe(), "commandLine": process.cmdline()})
                    except psutil.Error:
                        continue
                launch_log["debuggerListeners"] = [{"pid": connection.pid, "address": list(connection.laddr), "status": connection.status}
                                                  for connection in psutil.net_connections(kind="tcp")
                                                  if connection.laddr and connection.laddr.port == debug_port]
            except psutil.Error as diagnostic_error:
                launch_log["processDiagnosticError"] = str(diagnostic_error)
        (root / "application-launch.json").write_text(json.dumps(launch_log, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "window-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        if browser is not None:
            browser.quit()
        if application_process is not None and application_process.poll() is None:
            try:
                process = psutil.Process(application_process.pid)
                children = process.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                psutil.wait_procs([process, *children], timeout=10)
            except psutil.NoSuchProcess:
                pass
        stdout_log.close()
        stderr_log.close()


def main() -> None:
    """运行所有指定检查并将证据保存为CI构建产物。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    check_pe_gui(args.app)
    report = {"peSubsystem": "Windows GUI", "engine": test_engine(args.engine, args.output),
              "portableState": test_portable_state(args.engine, args.output)}
    (args.output / "engine-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.driver:
        report["gui"] = test_gui(args.app, args.driver, args.output)
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": list(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
