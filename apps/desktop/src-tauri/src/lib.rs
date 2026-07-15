use serde_json::{json, Value};
use std::path::PathBuf;
use tauri::async_runtime::{Mutex, Receiver};
use tauri::{AppHandle, Manager, State};
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
) -> Result<Value, String> {
    let mut request = serde_json::to_vec(&json!({
        "requestId": 1,
        "command": "calculate",
        "inputPath": input_path,
        "outputRoot": output_root,
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
) -> Result<Value, String> {
    let mut guard = state.connection.lock().await;
    if guard.is_none() {
        *guard = Some(spawn_engine(&app)?);
    }

    let response = send_calculation_request(
        guard.as_mut().expect("engine connection initialized"),
        input_path,
        output_root.filter(|value| !value.trim().is_empty()),
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
        .invoke_handler(tauri::generate_handler![calculate_attendance])
        .run(tauri::generate_context!())
        .expect("error while running attendance desktop application");
}
