# 考勤统计工具

跨平台员工个人考勤统计桌面应用：Tauri + React/TypeScript UI，Python 计算 Sidecar。

桌面端会在启动阶段预热并复用 PyInstaller `--onedir` Sidecar，通过本地 JSON Lines 标准输入输出协议计算，避免每次统计都重新启动和解压 Python 运行时。

## 本地计算规则

- 工作日应计标准工时 8 小时；普通周末和节假日为 0。
- 工作日扣除与 12:00-13:30 重叠的午休时长。
- 工作日任意打卡地点包含“云岭东路586号”时，将 30 分钟地点福利计入加班计算，不从实际有效工时中扣除。
- 节假日和非调休普通周末不扣午休、不享受地点福利，实际跨度全部计入加班时长。
- 从假勤申请中读取“调休假 N 小时”，净剩加班时长 = 加班时长 - 调休时长。
- 03:00 前的打卡归属上一工作日。
- 缺卡工作日仍计入 8 小时应计标准工时，但实际和加班/调休标记为未计算。
- 节假日安排优先使用在线年度数据，24 小时缓存后可离线使用；在线和缓存都不可用时由内置库兜底，未知未来年份不会静默猜测。

## Python 计算引擎

```bash
source .venv/bin/activate
pip install -e '.[dev]'
pytest
python -m attendance_engine calculate --input /path/to/report.xlsx
```

## macOS Apple Silicon unsigned DMG

首次在另一台 Apple Silicon Mac 构建：

```bash
./scripts/bootstrap_and_build_macos.sh
```

开发环境已经准备好时：

```bash
./scripts/build_macos_dmg.sh
```

在网络需要镜像时可指定：

```bash
NPM_REGISTRY=https://registry.npmmirror.com ./scripts/build_macos_dmg.sh
```

输出目录：

```text
release/考勤统计_0.2.0_aarch64_unsigned.dmg
```

### 关于无签名应用

构建脚本可以让其他同事在自己的 Apple Silicon Mac 上生成相同的 unsigned DMG，但不能绕过 Apple Gatekeeper，也不能替代 Apple Developer ID 签名和公证。内部测试时可通过 Finder 右键“打开”确认运行；正式公开发布应配置 Developer ID 和 notarization。

## Windows x64

Windows 安装程序必须在 Windows x64 环境构建：

```powershell
.\scripts\build_windows.ps1
```

或者双击：

```text
scripts\build_windows.bat
```

输出目录：

```text
apps\desktop\src-tauri\target\release\bundle\nsis\
```

也可以在 GitHub Actions 中手动触发 `Build desktop installers` 工作流生成 Windows EXE 和 unsigned macOS DMG。
