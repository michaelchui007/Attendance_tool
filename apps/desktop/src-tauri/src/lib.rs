use serde::Serialize;
use serde_json::{json, Value};
use std::io::Read;
use std::path::PathBuf;
use std::sync::{atomic::{AtomicU64, Ordering}, Mutex as StdMutex};
use tauri::async_runtime::{Mutex, Receiver};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct EngineConnection {
    receiver: Receiver<CommandEvent>,
}

#[derive(Default)]
struct EngineState {
    connection: Mutex<Option<EngineConnection>>,
    child: StdMutex<Option<CommandChild>>,
    generation: AtomicU64,
    request_id: AtomicU64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct NativePathSelection {
    path: Option<String>,
    path_utf16: Option<Vec<u16>>,
}

#[cfg(target_os = "windows")]
fn native_path_selection(path: PathBuf) -> NativePathSelection {
    use std::os::windows::ffi::OsStrExt;

    NativePathSelection {
        path: None,
        path_utf16: Some(path.as_os_str().encode_wide().collect()),
    }
}

#[cfg(not(target_os = "windows"))]
fn native_path_selection(path: PathBuf) -> NativePathSelection {
    NativePathSelection {
        path: path.to_str().map(str::to_owned),
        path_utf16: None,
    }
}

#[tauri::command]
async fn pick_excel_file(app: AppHandle) -> Result<Option<NativePathSelection>, String> {
    let selected = app
        .dialog()
        .file()
        .add_filter("Excel", &["xlsx"])
        .blocking_pick_file();
    selected
        .map(|value| {
            value
                .into_path()
                .map(native_path_selection)
                .map_err(|error| format!("无法读取所选 Excel 路径：{error}"))
        })
        .transpose()
}

/// 校验浏览或拖放的源文件，避免目录、不可读文件和非工作簿替换当前报表。
///
/// Args:
///     input_path: 用户明确选择的本地工作簿路径。
/// Returns:
///     基础校验成功返回空值；完整工作表结构仍由计算引擎验证。
#[tauri::command]
fn validate_input_file(input_path: Option<String>, input_path_utf16: Option<Vec<u16>>) -> Result<(), String> {
    let path = PathBuf::from(resolve_path(input_path, input_path_utf16)?);
    if !path.is_file() || path.extension().and_then(|value| value.to_str())
        .map(|value| !value.eq_ignore_ascii_case("xlsx")).unwrap_or(true) {
        return Err("请选择单个可读取的 .xlsx 文件，不支持文件夹。".to_string());
    }
    let mut file = std::fs::File::open(&path).map_err(|_| "无法读取报表，请检查文件权限。".to_string())?;
    let mut signature = [0; 4];
    file.read_exact(&mut signature).map_err(|_| "工作簿为空或已损坏。".to_string())?;
    if signature != [0x50, 0x4b, 0x03, 0x04] {
        return Err("文件不是有效的 .xlsx 工作簿，可能已损坏或受到密码保护。".to_string());
    }
    Ok(())
}

fn engine_executable(app: &AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录：{error}"))?;
    #[cfg(target_os = "windows")]
    let executable_name = "attendance-engine.exe";
    #[cfg(not(target_os = "windows"))]
    let executable_name = "attendance-engine";
    let executable = resource_dir
        .join("resources")
        .join("attendance-engine")
        .join(executable_name);
    if !executable.exists() {
        return Err(format!("考勤计算引擎不存在：{}", executable.display()));
    }
    Ok(executable)
}

fn spawn_engine(app: &AppHandle) -> Result<EngineConnection, String> {
    let executable = engine_executable(app)?;
    let holiday_cache_dir = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("无法定位节假日缓存目录：{error}"))?
        .join("holidays");
    std::fs::create_dir_all(&holiday_cache_dir)
        .map_err(|error| format!("无法创建节假日缓存目录：{error}"))?;
    let command = app
        .shell()
        .command(executable)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("ATTENDANCE_HOLIDAY_CACHE_DIR", holiday_cache_dir)
        .arg("serve");
    let (receiver, child) = command
        .spawn()
        .map_err(|error| format!("考勤计算引擎启动失败：{error}"))?;
    *app.state::<EngineState>().child.lock().map_err(|_| "计算引擎状态异常")? = Some(child);
    Ok(EngineConnection { receiver })
}

/// 独立于接收锁终止引擎，取消时无需等待长时间计算完成。
fn stop_engine(state: &EngineState) {
    state.generation.fetch_add(1, Ordering::SeqCst);
    if let Ok(mut child) = state.child.lock() {
        if let Some(process) = child.take() { let _ = process.kill(); }
    }
}

/// 将请求编码成 ASCII JSON，路径 Unicode 通过转义完整保留。
fn encode_request(request: &Value) -> Result<Vec<u8>, String> {
    let json = serde_json::to_string(request).map_err(|error| error.to_string())?;
    let mut ascii = String::with_capacity(json.len());
    for ch in json.chars() {
        if ch.is_ascii() { ascii.push(ch); } else {
            for unit in ch.encode_utf16(&mut [0; 2]) { ascii.push_str(&format!("\\u{:04x}", unit)); }
        }
    }
    ascii.push('\n');
    Ok(ascii.into_bytes())
}

/// 数字形式的 UTF-16 路径优先于字符串，避免 Windows 代码页转换。
fn resolve_path(path: Option<String>, path_utf16: Option<Vec<u16>>) -> Result<String, String> {
    let value = if let Some(units) = path_utf16 {
        String::from_utf16(&units).map_err(|_| "路径包含无效的 UTF-16 字符".to_string())?
    } else { path.unwrap_or_default() };
    if value.is_empty() { return Err("文件路径不能为空".to_string()); }
    Ok(value)
}

/// 读取进度事件并等待同一次请求的完整结果。
async fn send_calculation_request(
    app: &AppHandle, state: &EngineState, connection: &mut EngineConnection,
    request: Value, generation: u64,
) -> Result<Value, String> {
    let bytes = encode_request(&request)?;
    {
        let mut child = state.child.lock().map_err(|_| "计算引擎状态异常")?;
        child.as_mut().ok_or("统计已取消")?.write(&bytes)
            .map_err(|error| format!("无法向考勤计算引擎发送任务：{error}"))?;
    }
    let mut stderr_lines = Vec::new();
    while let Some(event) = connection.receiver.recv().await {
        if generation != state.generation.load(Ordering::SeqCst) { return Err("统计已取消".into()); }
        match event {
            CommandEvent::Stdout(line) => {
                let text = std::str::from_utf8(&line).map_err(|_| "计算引擎返回了非 UTF-8 数据")?;
                if text.trim().is_empty() { continue; }
                let payload: Value = serde_json::from_str(text).map_err(|error| format!("无法解析计算结果：{error}"))?;
                match payload.get("event").and_then(Value::as_str) {
                    Some("ready") => continue,
                    Some("progress") => { let _ = app.emit("calculation-progress", &payload); continue; }
                    _ => {}
                }
                if payload.get("requestId") != request.get("requestId") { return Err("计算引擎返回了不匹配的请求编号".into()); }
                return Ok(payload);
            }
            CommandEvent::Stderr(line) => {
                let text = String::from_utf8_lossy(&line);
                if !text.trim().is_empty() && stderr_lines.len() < 30 { stderr_lines.push(text.trim().to_string()); }
            }
            CommandEvent::Error(error) => return Err(format!("考勤计算引擎通信失败：{error}")),
            CommandEvent::Terminated(status) => return Err(format!("考勤计算引擎意外退出（code={:?}）。{}", status.code, stderr_lines.join("\n"))),
            _ => {}
        }
    }
    Err(if generation != state.generation.load(Ordering::SeqCst) { "统计已取消" } else { "考勤计算引擎通信已关闭" }.into())
}

/// 接收多报表、独立系统配置及人工修订，串行复用计算引擎。
#[tauri::command]
async fn calculate_attendance(
    app: AppHandle, state: State<'_, EngineState>,
    input_paths: Option<Vec<String>>, input_paths_utf16: Option<Vec<Vec<u16>>>, input_path: Option<String>,
    output_root: Option<String>, output_root_utf16: Option<Vec<u16>>,
    settings: Option<Value>, overrides: Option<Value>, source_choices: Option<Value>,
    expected_employee: Option<Value>,
    lunch_start: Option<String>, lunch_end: Option<String>,
) -> Result<Value, String> {
    let paths = if let Some(items) = input_paths_utf16 {
        items.into_iter().map(|units| resolve_path(None, Some(units))).collect::<Result<Vec<_>, _>>()?
    } else { input_paths.unwrap_or_else(|| input_path.into_iter().collect()) };
    if paths.is_empty() { return Err("请至少选择一个 Excel 报表".into()); }
    let output = if output_root_utf16.is_some() { Some(resolve_path(None, output_root_utf16)?) }
        else { output_root.filter(|value| !value.is_empty()) };
    let request = json!({
        "command": "calculate",
        "inputPaths": paths, "outputRoot": output, "settings": settings, "expectedEmployee": expected_employee,
        "overrides": overrides.unwrap_or_else(|| json!([])), "sourceChoices": source_choices.unwrap_or_else(|| json!({})),
        "lunchStart": lunch_start, "lunchEnd": lunch_end,
    });
    run_engine_request(&app, &state, request).await
}

/// 串行处理计算与工作簿配置请求，共享无损编码和进程恢复。
///
/// Args:
///     app: 当前桌面应用，用于启动引擎与发送进度。
///     state: 应用生命周期内的引擎连接。
///     request: 白名单命令构造的请求，不接受任意命令执行。
/// Returns:
///     引擎确认的结果或可展示的错误。
async fn run_engine_request(app: &AppHandle, state: &EngineState, mut request: Value) -> Result<Value, String> {
    let mut guard = state.connection.lock().await;
    if state.child.lock().map_err(|_| "计算引擎状态异常")?.is_none() { *guard = None; }
    if guard.is_none() { *guard = Some(spawn_engine(&app)?); }
    let generation = state.generation.load(Ordering::SeqCst);
    request["requestId"] = json!(state.request_id.fetch_add(1, Ordering::SeqCst) + 1);
    let response = send_calculation_request(&app, &state, guard.as_mut().expect("engine initialized"), request, generation).await;
    match response {
        Ok(payload) => {
            if payload.get("success") == Some(&Value::Bool(false)) {
                Err(payload.get("error").and_then(Value::as_str).unwrap_or("考勤计算失败").into())
            } else { Ok(payload) }
        }
        Err(error) => { stop_engine(&state); *guard = None; Err(error) }
    }
}

/// 将工作簿列表按原生UTF-16优先规则还原，不猜测损坏路径。
fn workbook_paths(paths: Option<Vec<String>>, utf16: Option<Vec<Vec<u16>>>) -> Result<Vec<String>, String> {
    let result = if let Some(items) = utf16 {
        items.into_iter().map(|units| resolve_path(None, Some(units))).collect::<Result<Vec<_>, _>>()?
    } else { paths.unwrap_or_default() };
    if result.is_empty() || result.iter().any(|path| path.trim().is_empty()) {
        return Err("请至少选择一个有效的源报表".into());
    }
    Ok(result)
}

/// 只读检查源Excel是否携带可用配置，是否采用由用户在界面选择。
#[tauri::command]
async fn inspect_workbook_state(
    app: AppHandle, state: State<'_, EngineState>,
    input_paths: Option<Vec<String>>, input_paths_utf16: Option<Vec<Vec<u16>>>,
) -> Result<Value, String> {
    let paths = workbook_paths(input_paths, input_paths_utf16)?;
    run_engine_request(&app, &state, json!({
        "command": "inspect_workbook_state", "inputPaths": paths,
    })).await
}

/// 用户明确保存时，把已核对的规则与修订写入源文件专用工作表。
#[tauri::command]
async fn save_workbook_state(
    app: AppHandle, state: State<'_, EngineState>,
    input_paths: Option<Vec<String>>, input_paths_utf16: Option<Vec<Vec<u16>>>,
    settings: Value, overrides: Option<Value>, expected_employee: Value, expected_sources: Value,
) -> Result<Value, String> {
    let paths = workbook_paths(input_paths, input_paths_utf16)?;
    run_engine_request(&app, &state, json!({
        "command": "save_workbook_state", "inputPaths": paths,
        "settings": settings, "overrides": overrides.unwrap_or_else(|| json!([])),
        "expectedEmployee": expected_employee, "expectedSources": expected_sources,
    })).await
}

/// 取消当前任务；下一次统计会重新启动引擎。
#[tauri::command]
fn cancel_calculation(state: State<'_, EngineState>) { stop_engine(&state); }

/// 多选报表使用同一原生路径表示。
#[tauri::command]
async fn pick_excel_files(app: AppHandle) -> Result<Vec<NativePathSelection>, String> {
    app.dialog().file().add_filter("Excel", &["xlsx"]).blocking_pick_files().unwrap_or_default()
        .into_iter().map(|value| value.into_path().map(native_path_selection).map_err(|error| error.to_string())).collect()
}

/// 输出目录也使用无损路径协议。
#[tauri::command]
async fn pick_output_directory(app: AppHandle) -> Result<Option<NativePathSelection>, String> {
    app.dialog().file().blocking_pick_folder().map(|value| value.into_path().map(native_path_selection).map_err(|error| error.to_string())).transpose()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = event {
                let paths: Vec<_> = paths.iter().cloned().map(native_path_selection).collect();
                let _ = window.emit("native-file-drop", json!({"paths": paths}));
            }
            if matches!(event, tauri::WindowEvent::Destroyed) { stop_engine(&window.state::<EngineState>()); }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let state = handle.state::<EngineState>();
                let mut guard = state.connection.lock().await;
                if guard.is_none() {
                    match spawn_engine(&handle) {
                        Ok(connection) => *guard = Some(connection),
                        Err(error) => eprintln!("Sidecar warm-up failed: {error}"),
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![pick_excel_file, pick_excel_files, pick_output_directory, validate_input_file, calculate_attendance, cancel_calculation, inspect_workbook_state, save_workbook_state])
        .build(tauri::generate_context!())
        .expect("error while building attendance desktop application")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) { stop_engine(&app.state::<EngineState>()); }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unicode_paths_round_trip_through_ascii_protocol() {
        let path = r"C:\用户\张三 😀\考勤 (11月).xlsx";
        let request = json!({"inputPaths": [path], "reason": "公司统一调休"});
        let encoded = encode_request(&request).unwrap();
        assert!(encoded.is_ascii());
        assert_eq!(serde_json::from_slice::<Value>(&encoded).unwrap(), request);
        assert_eq!(resolve_path(None, Some(path.encode_utf16().collect())).unwrap(), path);
    }

    #[test]
    fn invalid_utf16_is_rejected_instead_of_repaired() {
        assert!(resolve_path(Some("fallback.xlsx".into()), Some(vec![0xd800])).is_err());
        assert!(resolve_path(None, None).is_err());
    }
}
