# 考勤统计工具

跨平台员工个人考勤统计桌面应用：Tauri + React/TypeScript UI，Python 计算 Sidecar。

桌面端会在启动阶段预热并复用 PyInstaller `--onedir` Sidecar，通过本地 JSON Lines 标准输入输出协议计算，避免每次统计都重新启动和解压 Python 运行时。

## 本地计算规则

- 工作日应计标准工时 8 小时；普通周末和节假日为 0。
- 工作日默认扣除与 12:00-13:30 重叠的午休时长；调整午休后，按每天 8 小时标准同步推导请假分摊工作时段。
- 已取消半小时地点福利，打卡地址不再影响加班结果。
- 节假日和非调休普通周末不扣午休，实际跨度全部计入加班时长。
- 调休申请按起止区间与正常工作时段分摊，默认时段为 09:00-12:00、13:30-18:30；休息日不扣调休，同一跨日申请不能每天重复扣总量。
- 工作日打卡完整时，实际调休扣减 = min(当日申请量, max(480 分钟 - 实际有效工时, 0))；净剩加班 = 加班 - 实际调休扣减。原始申请仍保留追溯。
- 无法分配的申请显示待核对，不把未知扣减当 0；缺卡时不使用完整打卡公式。
- 04:00 前的打卡归属上一考勤日，正好 04:00 归新一天。
- 缺卡工作日仍计入 8 小时应计标准工时，但实际和加班/调休标记为未计算。
- 节假日安排优先使用在线年度数据，24 小时缓存后可离线使用；在线和缓存都不可用时由内置库兜底，未知未来年份不会静默猜测。

## v0.3.0

本版增加真实报表形态的脱敏回归用例，并实现调休分摊、时间修订和页面改进。变更与使用边界见 [v0.3.0 发布说明](docs/releases/v0.3.0.md)。

- [需求文档](docs/v0.3.0需求文档.md)
- [报表分析与测试用例](docs/v0.3.0报表分析与测试用例.md)
- [页面规划与迭代计划](docs/v0.3.0页面规划与迭代计划.md)

真实考勤报表不纳入测试仓库；测试使用匿名员工和临时生成的最小 Excel，节假日场景使用固定日历，避免测试依赖网络。

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
release/考勤统计_0.3.0_aarch64_unsigned.dmg
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

## GitHub Release 自动发布

`Build desktop installers` 工作流会在 Windows EXE 和 macOS DMG 都构建成功后，按照项目版本号自动创建 GitHub Release：

```text
v0.2.0
v0.3.0
v1.0.0
```

每个 Release 包含 Windows NSIS EXE、macOS unsigned DMG、DMG 独立校验文件以及统一的 `SHA256SUMS.txt`。发布说明来自 `docs/releases/v版本号.md`。可通过以下方式触发：

1. 正式确认后推送与项目版本一致的分支，如 `release/v0.3.0`；双平台构建成功后创建对应标签和 Release。
2. 在 GitHub Actions 页面选择对应版本分支，手动运行 `Build desktop installers`。
3. 推送与项目版本一致的标签，例如 `v0.3.0`。

发布前必须同步修改以下五处版本号，工作流会自动校验一致性：

```text
apps/desktop/package.json
apps/desktop/src-tauri/tauri.conf.json
apps/desktop/src-tauri/Cargo.toml
pyproject.toml
attendance_engine/__init__.py
```

失败构建可以重试；正式 Release 的附件不会被自动覆盖。如果版本已经发布，或标签已指向其他提交，应升级版本号后再发布。macOS 构建使用 `macos-15` arm64 runner，替代已退役的 `macos-14`。
