use serde::Serialize;
use serde_json::{json, Value};
use std::io::Read;
use std::path::PathBuf;
use tauri::async_runtime::{Mutex, Receiver};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct EngineConnection {
    receiver: Receiver<CommandEvent>,
    child: CommandChild,
}

#[derive(Default)]
struct EngineState {
    connection: Mutex<Option<EngineConnection>>,
}

#[derive(Serialize)]
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
fn validate_input_file(input_path: String) -> Result<(), String> {
    let path = PathBuf::from(input_path);
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
    Ok(EngineConnection { receiver, child })
}

async fn send_calculation_request(
    connection: &mut EngineConnection,
    input_path: String,
    output_root: Option<String>,
    overrides: Option<Value>,
    lunch_start: Option<String>,
    lunch_end: Option<String>,
) -> Result<Value, String> {
    let mut request = serde_json::to_vec(&json!({
        "requestId": 1,
        "command": "calculate",
        "inputPath": input_path,
        "outputRoot": output_root,
        "overrides": overrides.unwrap_or_else(|| json!([])),
        "lunchStart": lunch_start.unwrap_or_else(|| "12:00".to_string()),
        "lunchEnd": lunch_end.unwrap_or_else(|| "13:30".to_string()),
    }))
    .map_err(|error| format!("无法序列化计算请求：{error}"))?;
    request.push(b'\n');
    connection
        .child
        .write(&request)
        .map_err(|error| format!("无法向考勤计算引擎发送任务：{error}"))?;

    let mut stderr_lines = Vec::new();
    while let Some(event) = connection.receiver.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                let text = std::str::from_utf8(&line)
                    .map_err(|error| format!("计算引擎返回了非 UTF-8 数据：{error}"))?;
                if text.trim().is_empty() {
                    continue;
                }
                let payload: Value = serde_json::from_str(text)
                    .map_err(|error| format!("无法解析计算结果：{error}。原始输出：{text}"))?;
                if payload.get("event") == Some(&Value::String("ready".to_string())) {
                    continue;
                }
                return Ok(payload);
            }
            CommandEvent::Stderr(line) => {
                let text = std::str::from_utf8(&line)
                    .map_err(|error| format!("计算引擎错误输出不是 UTF-8：{error}"))?;
                if !text.trim().is_empty() {
                    stderr_lines.push(text.trim().to_string());
                }
            }
            CommandEvent::Error(error) => {
                return Err(format!("考勤计算引擎通信失败：{error}"));
            }
            CommandEvent::Terminated(status) => {
                let detail = stderr_lines.join("\n");
                return Err(format!(
                    "考勤计算引擎意外退出（code={:?}）。{}",
                    status.code, detail
                ));
            }
            _ => {}
        }
    }
    Err("考勤计算引擎通信已关闭".to_string())
}

#[tauri::command]
async fn calculate_attendance(
    app: AppHandle,
    state: State<'_, EngineState>,
    input_path: String,
    output_root: Option<String>,
    overrides: Option<Value>,
    lunch_start: Option<String>,
    lunch_end: Option<String>,
) -> Result<Value, String> {
    let mut guard = state.connection.lock().await;
    if guard.is_none() {
        *guard = Some(spawn_engine(&app)?);
    }

    let response = send_calculation_request(
        guard.as_mut().expect("engine connection initialized"),
        input_path,
        output_root.filter(|value| !value.trim().is_empty()),
        overrides,
        lunch_start,
        lunch_end,
    )
    .await;

    let payload = match response {
        Ok(payload) => payload,
        Err(error) => {
            if let Some(connection) = guard.take() {
                let _ = connection.child.kill();
            }
            return Err(error);
        }
    };

    if payload.get("success") == Some(&Value::Bool(false)) {
        return Err(payload
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("考勤计算失败")
            .to_string());
    }
    Ok(payload)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
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
        .invoke_handler(tauri::generate_handler![pick_excel_file, validate_input_file, calculate_attendance])
        .run(tauri::generate_context!())
        .expect("error while running attendance desktop application");
}
