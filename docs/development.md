# 开发与验证

## 架构

应用采用 Tauri 桌面容器、React/TypeScript 界面和 Python 计算引擎。常驻 Python Sidecar 使用标准输入输出上的 JSON Lines 协议，不监听网络端口。发布构建将 PyInstaller onedir 运行时作为应用资源打包。

## 本地开发

Python 3.11 及以上：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest
```

前端需 Node.js 22 和项目锁定依赖；Tauri 需 Rust 和对应平台构建工具：

```bash
cd apps/desktop
npm ci
npm run dev
npm run build
```

测试在临时目录生成脱敏工作簿，不依赖用户真实报表；日历单元测试不依赖在线数据。

## 安装包构建

macOS Apple Silicon：

```bash
./scripts/bootstrap_and_build_macos.sh
# 已准备开发环境时使用：
./scripts/build_macos_dmg.sh
```

Windows x64：

```powershell
.\scripts\build_windows.ps1
```

macOS 产物在 `release/`；Windows NSIS 产物在 `apps/desktop/src-tauri/target/release/bundle/nsis/`。Windows 包必须在 Windows 环境构建。

## Windows 自动化验证

Windows 验证使用 GitHub Actions Windows runner，构建并安装真实 NSIS 包。测试包括冻结引擎在中文目录下的 JSON Lines 通信、主程序 GUI 子系统与控制台窗口检测，以及 Microsoft Edge WebDriver 驱动安装后的 WebView2 界面。原生文件对话框由 Windows UI 自动化处理。

测试依赖只安装在 CI 环境，不随应用分发。测试工作流上传检查报告、日志和截图；浏览器模拟不能代替原生文件选择、安装或 Windows 编码检查。具体执行结论以对应运行报告为准。

WebView2 150及以后会忽略管理员进程通过环境变量提供的调试参数。Windows CI通过经校验的gsudo以Medium完整性运行测试，并记录实际权限；先启动已安装应用，再由EdgeDriver附加本机调试端口。生产程序不包含测试调试开关，也不通过修改系统HKLM策略绕过该限制。

官方参考：

- `https://v2.tauri.app/develop/tests/webdriver/`
- `https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webdriver`
- `https://github.com/MicrosoftEdge/WebView2Feedback/issues/5645`
- `https://github.com/tauri-apps/tauri/blob/dev/.github/workflows/test-api-e2e.yml`

## 发布

`main` 保存最新已验证代码及用户文档，开发分支采用 `codex/` 前缀。每个版本保留对应的 `release/v版本号` 分支和 `v版本号` 标签，用于追溯该版本源码。

发布前同步以下软件版本及锁文件中的根包版本：

- `apps/desktop/package.json` 和 `package-lock.json`
- `apps/desktop/src-tauri/tauri.conf.json`
- `apps/desktop/src-tauri/Cargo.toml` 和 `Cargo.lock`
- `pyproject.toml`
- `attendance_engine/__init__.py`

发布流程：

1. 完成修复和相关回归验证，更新根目录 README、版本说明与版本号；运行 `python scripts/resolve_app_version.py` 校验一致性。
2. 获取远端最新状态，将已验证变更合并到 `main` 并推送 `origin/main`，确认远端代码和 README 均已更新。不得只更新 release 分支。
3. 从该次 `main` 提交建立对应 `release/v版本号` 分支并构建。版本发布说明保存在 `docs/releases/v版本号.md`；只有 Windows 安装验证和 macOS 构建均成功才创建 Release。
4. 核对版本标签、release 分支及本次发布提交一致，并确认安装包和校验文件完整。发布后的版本分支和标签保留，后续迭代从最新 `main` 开始。

发布过程中如果发现问题，先将修复和验证结果同步回 `main`，再更新尚未发布的版本分支。已发布标签及附件不得静默覆盖；后续修复使用新版本号和新的 release 分支。

发布工作流在构建前检查发布提交是否已包含在远端 `origin/main` 的历史中；未合入 `main` 的提交将被阻止发布。

开发分支的 Windows 验证工作流不发布 Release。
