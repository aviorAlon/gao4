//! Desktop shell: a native window around the web UI that also owns the API backend process.
//!
//! On startup it launches the frozen backend (`resources/backend/exam-diagnosis-backend[.exe]`,
//! produced by `scripts/build_backend.py`) on a free port with the user's app-data folder as
//! its data directory, reads the `LISTENING host:port` line the backend prints once bound, and
//! hands that address to the frontend through the `api_base` command. When the app exits the
//! backend is killed, so no orphaned server keeps running.
//!
//! If no bundled backend is found (a `tauri dev` session before `build_backend.py` was run, or a
//! developer who runs the backend themselves) nothing is spawned and `api_base` returns an empty
//! string, which makes the frontend fall back to its default `127.0.0.1:8765`.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

const BACKEND_EXE: &str = if cfg!(windows) { "exam-diagnosis-backend.exe" } else { "exam-diagnosis-backend" };
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

struct Backend {
    child: Mutex<Option<Child>>,
    api_base: String,
}

#[tauri::command]
fn api_base(state: tauri::State<'_, Backend>) -> String {
    state.api_base.clone()
}

/// Write bytes the frontend already holds (an export it fetched from the backend) to a path the
/// user picked in the native save dialog. The webview has no download manager of its own, and
/// navigating it to an attachment URL would replace the app with the raw response.
#[tauri::command]
fn save_file(path: String, data_base64: String) -> Result<(), String> {
    let bytes = base64_decode(&data_base64).ok_or("无法解码要保存的内容")?;
    std::fs::write(&path, bytes).map_err(|e| format!("写入 {path} 失败: {e}"))
}

fn base64_decode(input: &str) -> Option<Vec<u8>> {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::with_capacity(input.len() * 3 / 4);
    let mut buffer: u32 = 0;
    let mut bits = 0;
    for byte in input.bytes() {
        if byte == b'=' || byte == b'\n' || byte == b'\r' {
            continue;
        }
        let value = TABLE.iter().position(|&c| c == byte)? as u32;
        buffer = (buffer << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((buffer >> bits) & 0xFF) as u8);
        }
    }
    Some(out)
}

/// Where the backend executable is. Release: inside the bundled resources. Debug (`tauri dev`):
/// the same folder under src-tauri/, or whatever EXAM_DIAGNOSIS_BACKEND_EXE points at.
fn locate_backend(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("EXAM_DIAGNOSIS_BACKEND_EXE") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    let mut candidates = Vec::new();
    if let Ok(resources) = app.path().resource_dir() {
        candidates.push(resources.join("resources").join("backend").join(BACKEND_EXE));
        candidates.push(resources.join("backend").join(BACKEND_EXE));
    }
    candidates.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources").join("backend").join(BACKEND_EXE));
    candidates.into_iter().find(|path| path.is_file())
}

/// Spawn the backend and wait for its LISTENING line. Errors are strings because the only thing
/// to do with them is show them.
fn spawn_backend(exe: &PathBuf, data_dir: &PathBuf) -> Result<(Child, String), String> {
    std::fs::create_dir_all(data_dir).map_err(|e| format!("无法创建数据目录 {}: {e}", data_dir.display()))?;
    let mut command = Command::new(exe);
    // --parent-pid: the backend watches this process and exits on its own if we die without
    // reaching the RunEvent::Exit handler (crash, Task Manager, forced close).
    command
        .args(["--port", "0", "--data-dir"])
        .arg(data_dir)
        .args(["--log-level", "warning", "--parent-pid", &std::process::id().to_string()])
        .current_dir(exe.parent().unwrap_or(exe))
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    // The backend must not inherit a developer's model keys from the shell's environment by
    // accident — in the desktop build keys come from the settings page (config.json).
    for (key, _) in std::env::vars() {
        if key.starts_with("EXAM_DIAGNOSIS_") && key.ends_with("_API_KEY") {
            command.env_remove(key);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|e| format!("无法启动本地服务 {}: {e}", exe.display()))?;
    let stdout = child.stdout.take().ok_or("本地服务没有标准输出")?;
    let mut reader = BufReader::new(stdout);
    let started = Instant::now();
    let mut line = String::new();
    loop {
        if started.elapsed() > STARTUP_TIMEOUT {
            let _ = child.kill();
            return Err("本地服务启动超时".into());
        }
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => {
                let status = child.wait().map(|s| s.to_string()).unwrap_or_default();
                return Err(format!("本地服务在就绪前退出（{status}）"));
            }
            Ok(_) => {
                if let Some(address) = line.trim().strip_prefix("LISTENING ") {
                    let base = format!("http://{}", address.trim());
                    // Keep draining stdout in the background so the child never blocks on a
                    // full pipe once it starts logging.
                    std::thread::spawn(move || {
                        let mut sink = String::new();
                        while let Ok(n) = reader.read_line(&mut sink) {
                            if n == 0 {
                                break;
                            }
                            sink.clear();
                        }
                    });
                    return Ok((child, base));
                }
            }
            Err(e) => {
                let _ = child.kill();
                return Err(format!("读取本地服务输出失败: {e}"));
            }
        }
    }
}

fn stop_backend(backend: &Backend) {
    if let Ok(mut guard) = backend.child.lock() {
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = None;
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![api_base, save_file])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())?;
            }
            let handle = app.handle();
            let backend = match locate_backend(handle) {
                Some(exe) => {
                    let data_dir = handle.path().app_data_dir().map_err(|e| e.to_string())?;
                    match spawn_backend(&exe, &data_dir) {
                        Ok((child, base)) => {
                            log::info!("backend {} listening at {base}, data in {}", exe.display(), data_dir.display());
                            Backend { child: Mutex::new(Some(child)), api_base: base }
                        }
                        Err(message) => {
                            log::error!("{message}");
                            Backend { child: Mutex::new(None), api_base: String::new() }
                        }
                    }
                }
                None => {
                    log::warn!("no bundled backend found; frontend will use its default API base");
                    Backend { child: Mutex::new(None), api_base: String::new() }
                }
            };
            app.manage(backend);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<Backend>() {
                    stop_backend(&backend);
                }
            }
        });
}
