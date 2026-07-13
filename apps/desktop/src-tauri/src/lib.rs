use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_shell::ShellExt;

#[tauri::command]
async fn calculate_attendance(
    app: AppHandle,
    input_path: String,
    output_root: Option<String>,
) -> Result<Value, String> {
    let mut args = vec!["calculate".to_string(), "--input".to_string(), input_path];
    if let Some(root) = output_root.filter(|value| !value.trim().is_empty()) {
        args.push("--export-root".to_string());
        args.push(root);
    }

    let command = app
        .shell()
        .sidecar("attendance-engine")
        .map_err(|error| format!("无法启动考勤计算引擎：{error}"))?
        .args(args);

    let output = command
        .output()
        .await
        .map_err(|error| format!("考勤计算引擎执行失败：{error}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let payload_line = stdout
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .ok_or_else(|| format!("计算引擎没有返回结果。{stderr}"))?;

    let payload: Value = serde_json::from_str(payload_line)
        .map_err(|error| format!("无法解析计算结果：{error}。原始输出：{payload_line}"))?;

    if !output.status.success() || payload.get("success") == Some(&Value::Bool(false)) {
        return Err(payload
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or(stderr.trim())
            .to_string());
    }
    Ok(payload)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![calculate_attendance])
        .run(tauri::generate_context!())
        .expect("error while running attendance desktop application");
}
