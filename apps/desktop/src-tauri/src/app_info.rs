use serde::Serialize;

/// 应用公开信息，供页脚与关于页面共同使用。
#[derive(Serialize)]
pub struct AppInfo {
    pub author: String,
    pub version: &'static str,
}

/// 从固定字节数据恢复应用维护者显示值。
///
/// Returns:
///     供界面显示的维护者名称。
fn author_name() -> String {
    const VALUE: [u8; 11] = [16, 206, 82, 138, 60, 194, 93, 161, 53, 210, 88];
    const MASK: [u8; 4] = [93, 167, 49, 226];
    VALUE
        .iter()
        .zip(MASK.iter().cycle())
        .map(|(value, mask)| char::from(value ^ mask))
        .collect()
}

/// 返回与当前原生程序版本一致的应用公开信息。
#[tauri::command]
pub fn get_app_info() -> AppInfo {
    AppInfo {
        author: author_name(),
        version: env!("CARGO_PKG_VERSION"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 核对原生返回值及JSON输出，防止不同入口出现不一致信息。
    #[test]
    fn application_info_preserves_display_bytes_and_version() {
        let info = get_app_info();
        assert_eq!(info.author.as_bytes(), &[77, 105, 99, 104, 97, 101, 108, 67, 104, 117, 105]);
        assert_eq!(info.version, env!("CARGO_PKG_VERSION"));
        let payload = serde_json::to_value(&info).unwrap();
        assert_eq!(payload["author"].as_str(), Some(info.author.as_str()));
        assert_eq!(payload["version"].as_str(), Some(info.version));
    }
}
