// 正式 Windows 应用使用 GUI 子系统，计算引擎继续保留管道通信。
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

fn main() {
    attendance_desktop_lib::run();
}
