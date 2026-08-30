use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, VecDeque};
use std::env;
use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock as StdRwLock};
use std::time::{Duration, Instant, SystemTime};
#[cfg(not(target_os = "linux"))]
use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
#[cfg(not(target_os = "linux"))]
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager};
use tauri::{LogicalPosition, LogicalSize};
use tokio::sync::RwLock;

mod child_process;
mod config_bundle;
mod file_managers;
mod license;
#[cfg(target_os = "linux")]
mod linux_tray;
mod platform;
mod work_area;
use child_process::Command;
use work_area::WorkArea;

const SECRET_FIELD_MASK: &str = "••••••";
const RUNTIME_SESSION_MARKER: &str = "Mountlet Rust runtime started";
const BUILD_CHANNEL: &str = match option_env!("MOUNTLET_BUILD_CHANNEL") {
    Some(channel) => channel,
    None => "production",
};

fn build_revision() -> &'static str {
    option_env!("GITHUB_SHA")
        .and_then(|revision| revision.get(..7))
        .unwrap_or("local")
}

fn build_id() -> String {
    format!(
        "{}-{BUILD_CHANNEL}-{}",
        env!("CARGO_PKG_VERSION"),
        build_revision()
    )
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Remote {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) provider: String,
    pub(crate) provider_label: String,
    pub(crate) mounted: bool,
    pub(crate) used_bytes: Option<u64>,
    pub(crate) total_bytes: Option<u64>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct FileEntry {
    id: String,
    remote_id: String,
    path: String,
    name: String,
    is_dir: bool,
    size: u64,
    modified: String,
    cache: CacheState,
}

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct CacheState {
    cached: bool,
    offline: bool,
    partial: bool,
}

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct ConfigSyncStatus {
    local_changed: bool,
    remote_changed: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct OfflineConflict {
    remote_id: String,
    path: String,
    local_modified: i64,
    cloud_modified: i64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExportRequest {
    remote_id: String,
    path: String,
    is_dir: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteMetadataEntry {
    path: String,
    size: u64,
    modified: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct NoticeView {
    key: String,
    title: String,
    message: String,
    level: String,
    url: String,
    seen: bool,
    deletable: bool,
    received_at: i64,
    updated_at: String,
    critical: bool,
    archived: bool,
}

#[derive(Clone, Debug)]
struct RemoteMountSettings {
    mount_path: Option<String>,
    remote_path: String,
    mount_flags: Vec<String>,
    auto_mount: Option<bool>,
    enabled: bool,
    order: Option<i64>,
}

impl Default for RemoteMountSettings {
    fn default() -> Self {
        Self {
            mount_path: None,
            remote_path: String::new(),
            mount_flags: Vec::new(),
            auto_mount: None,
            enabled: true,
            order: None,
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct FolderSnapshot {
    remote_id: String,
    path: String,
    revision: u64,
    selected_index: usize,
    entries: Vec<FileEntry>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UiPreferences {
    mode: String,
    theme: String,
    zoom_step: i32,
    integrated_file_edits: bool,
    file_list_max_items: usize,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppSettings {
    mount_base: String,
    auto_mount: bool,
    auto_mount_delay: f64,
    start_at_login: bool,
    integrated_file_edits: bool,
    file_manager: String,
    open_folder_behavior: String,
    focus_file_manager: bool,
    window_mode: String,
    theme: String,
    zoom_steps: i32,
    file_list_max_items: usize,
    remote_check_interval: f64,
    notice_info_display: String,
    notice_important_display: String,
    notice_check_interval: f64,
    config_sync_remote: String,
    config_sync_path: String,
    shortcuts: HashMap<String, Vec<String>>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct FolderRequest {
    remote_id: String,
    path: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct EntryRequest {
    remote_id: String,
    path: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RenameRequest {
    remote_id: String,
    path: String,
    new_name: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct TransferRequest {
    source_remote_id: String,
    source_path: String,
    destination_remote_id: String,
    destination_path: String,
    move_entry: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct UploadRequest {
    remote_id: String,
    destination_path: String,
    local_paths: Vec<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CreateFileRequest {
    remote_id: String,
    path: String,
    contents: String,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WindowLayoutRequest {
    mode: String,
    selected_index: usize,
    remote_count: usize,
    browser_items: usize,
    remote_card_top: u32,
    global_search_height: u32,
    browser_search_height: u32,
    remote_chrome_height: u32,
    remote_row_height: u32,
    remote_pane_width: u32,
    single_window_width: u32,
    browser_chrome_height: u32,
    browser_row_height: u32,
    browser_width: u32,
    browser_min_height: u32,
    available_x: f64,
    available_y: f64,
    available_width: f64,
    available_height: f64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SearchRequest {
    query: String,
    remote_id: Option<String>,
    limit: usize,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SearchEntry {
    remote_id: String,
    remote_display: String,
    provider: String,
    name: String,
    path: String,
    parent_path: String,
    is_dir: bool,
    size: u64,
    modified: String,
    quality: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ConfigWizardStep {
    state: String,
    option: serde_json::Value,
    error: String,
    result: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ConfigWizardRequest {
    remote_id: String,
    state: String,
    result: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteConfigData {
    id: String,
    alias: String,
    provider: String,
    provider_label: String,
    mount_path: String,
    remote_path: String,
    mount_flags: String,
    auto_mount: Option<bool>,
    fields: HashMap<String, String>,
    secret_fields: Vec<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveRemoteConfigRequest {
    remote_id: String,
    alias: String,
    mount_path: String,
    remote_path: String,
    mount_flags: String,
    auto_mount: Option<bool>,
    fields: HashMap<String, String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CreateRemoteRequest {
    alias: String,
    provider: String,
    provider_label: String,
    fields: HashMap<String, String>,
    google_account: String,
    #[serde(default)]
    #[allow(dead_code)]
    mount_after: bool,
    #[serde(default)]
    remote_path: String,
}

#[derive(Default)]
pub(crate) struct AppState {
    pub(crate) remotes: StdRwLock<Vec<Remote>>,
    registration_order: StdRwLock<Vec<String>>,
    folders: RwLock<HashMap<(String, String), Vec<FileEntry>>>,
    folder_refreshes: Mutex<std::collections::HashSet<(String, String)>>,
    selections: RwLock<HashMap<(String, String), usize>>,
    rclone: Option<String>,
    rclone_config: Option<PathBuf>,
    browser_state: RwLock<(String, String)>,
    instance_listener: Option<TcpListener>,
    window_anchor: Mutex<WindowAnchor>,
    last_layout: Mutex<Option<WindowLayoutRequest>>,
    last_tray_activate: Mutex<Option<Instant>>,
    last_focused_window: Mutex<String>,
    window_stack_shown: Mutex<bool>,
    rclone_log: Mutex<VecDeque<String>>,
    mount_pids: Mutex<HashMap<String, u32>>,
    listing_generation: Arc<AtomicU64>,
}

#[derive(Default)]
struct WindowAnchor {
    physical_x: f64,
    physical_y: f64,
    physical_width: f64,
    physical_height: f64,
    scale_factor: f64,
    valid: bool,
    user_placed: bool,
    last_set_physical: Option<(i32, i32)>,
    ignore_moved_until: Option<Instant>,
    edge: String,
    area_signature: (i64, i64, i64, i64),
}

const PANEL_ICON_MARGIN: f64 = 8.0;
const DEFAULT_TRAY_ICON_SIZE: f64 = 24.0;
const PROGRAMMATIC_MOVE_GRACE: Duration = Duration::from_millis(400);
const TRAY_ACTIVATE_DEBOUNCE: Duration = Duration::from_millis(280);
static BROWSER_MEMORY_WRITE_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeLayoutEvent {
    browser_side: String,
    browser_inner_height: f64,
}

#[cfg(test)]
fn entry(remote: &str, path: &str, is_dir: bool, size: u64, modified: &str) -> FileEntry {
    FileEntry {
        id: format!("{remote}:{path}"),
        remote_id: remote.into(),
        path: path.into(),
        name: path.rsplit('/').next().unwrap_or(path).into(),
        is_dir,
        size,
        modified: modified.into(),
        cache: CacheState::default(),
    }
}

#[cfg(test)]
fn sample_state() -> AppState {
    let remotes: Vec<Remote> = vec![
        (
            "drive-ehol",
            "EHol",
            "drive",
            "Drive",
            true,
            Some(5_400_000_000),
            Some(15_000_000_000),
        ),
        (
            "photos-ehol",
            "EHol",
            "gphotos",
            "Google Photos",
            false,
            None,
            None,
        ),
        (
            "drive-ehil",
            "EHil",
            "drive",
            "Drive",
            true,
            Some(2_900_000_000),
            Some(15_000_000_000),
        ),
        (
            "dropbox",
            "EHol",
            "dropbox",
            "Dropbox",
            true,
            Some(180_000_000),
            Some(2_000_000_000),
        ),
        (
            "onedrive",
            "EHil",
            "onedrive",
            "OneDrive",
            true,
            Some(60_000_000),
            Some(5_000_000_000),
        ),
        (
            "mega",
            "MEGA Personal",
            "mega",
            "MEGA",
            true,
            Some(250_000_000),
            Some(25_000_000_000),
        ),
        (
            "proton",
            "EH Proton",
            "protondrive",
            "Proton Drive",
            false,
            Some(0),
            Some(2_000_000_000),
        ),
        (
            "icloud-personal",
            "Personal",
            "iclouddrive",
            "iCloud",
            true,
            None,
            None,
        ),
        (
            "icloud-photos",
            "Photos",
            "iclouddrive",
            "iCloud",
            false,
            None,
            None,
        ),
    ]
    .into_iter()
    .map(
        |(id, name, provider, provider_label, mounted, used_bytes, total_bytes)| Remote {
            id: id.into(),
            name: name.into(),
            provider: provider.into(),
            provider_label: provider_label.into(),
            mounted,
            used_bytes,
            total_bytes,
        },
    )
    .collect();

    let mut folders = HashMap::new();
    for remote in &remotes {
        let mut root = vec![
            entry(&remote.id, "Archive", true, 0, "2026-08-12 09:14"),
            entry(&remote.id, "Books", true, 0, "2026-08-10 18:21"),
            entry(&remote.id, "Medical records", true, 0, "2026-07-29 07:20"),
            entry(&remote.id, "Projects", true, 0, "2026-08-15 22:44"),
            entry(&remote.id, "Notes.docx", false, 5_325, "2026-08-15 09:05"),
            entry(
                &remote.id,
                "Release checklist.pdf",
                false,
                201_934,
                "2026-08-16 00:48",
            ),
        ];
        if let Some(file) = root.get_mut(4) {
            file.cache.cached = true;
        }
        if let Some(file) = root.get_mut(5) {
            file.cache.offline = true;
        }
        folders.insert((remote.id.clone(), String::new()), root);
        folders.insert(
            (remote.id.clone(), "Projects".into()),
            (0..2500)
                .map(|index| {
                    entry(
                        &remote.id,
                        &format!("Projects/Project {:04}.txt", index + 1),
                        false,
                        1200 + index * 37,
                        "2026-08-15 21:30",
                    )
                })
                .collect(),
        );
    }
    let registration_order = remotes.iter().map(|remote| remote.id.clone()).collect();
    AppState {
        remotes: StdRwLock::new(remotes),
        registration_order: StdRwLock::new(registration_order),
        folders: RwLock::new(folders),
        folder_refreshes: Mutex::new(std::collections::HashSet::new()),
        selections: RwLock::new(HashMap::new()),
        rclone: None,
        rclone_config: None,
        browser_state: RwLock::new((String::new(), String::new())),
        instance_listener: None,
        window_anchor: Mutex::new(WindowAnchor::default()),
        last_layout: Mutex::new(None),
        last_tray_activate: Mutex::new(None),
        last_focused_window: Mutex::new("main".into()),
        window_stack_shown: Mutex::new(false),
        rclone_log: Mutex::new(VecDeque::new()),
        mount_pids: Mutex::new(HashMap::new()),
        listing_generation: Arc::new(AtomicU64::new(0)),
    }
}

fn empty_state() -> AppState {
    AppState {
        remotes: StdRwLock::new(Vec::new()),
        registration_order: StdRwLock::new(Vec::new()),
        folders: RwLock::new(HashMap::new()),
        folder_refreshes: Mutex::new(std::collections::HashSet::new()),
        selections: RwLock::new(HashMap::new()),
        rclone: Some(find_rclone()),
        rclone_config: default_rclone_config_path(),
        browser_state: RwLock::new((String::new(), String::new())),
        instance_listener: None,
        window_anchor: Mutex::new(WindowAnchor::default()),
        last_layout: Mutex::new(None),
        last_tray_activate: Mutex::new(None),
        last_focused_window: Mutex::new("main".into()),
        window_stack_shown: Mutex::new(false),
        rclone_log: Mutex::new(VecDeque::new()),
        mount_pids: Mutex::new(HashMap::new()),
        listing_generation: Arc::new(AtomicU64::new(0)),
    }
}

fn find_rclone() -> String {
    if let Some(configured) = env::var_os("RCLONE_BINARY").or_else(|| env::var_os("RCLONE_PATH")) {
        return PathBuf::from(configured).to_string_lossy().into_owned();
    }
    let executable = if cfg!(target_os = "windows") {
        "rclone.exe"
    } else {
        "rclone"
    };
    if let Ok(current) = env::current_exe() {
        let version = env!("CARGO_PKG_VERSION");
        let target = format!("{}-{}", env::consts::OS, env::consts::ARCH);
        for ancestor in current.ancestors().take(6) {
            for candidate in [
                ancestor
                    .join("rclone")
                    .join(version)
                    .join(&target)
                    .join(executable),
                ancestor
                    .join("resources")
                    .join("rclone")
                    .join(version)
                    .join(&target)
                    .join(executable),
                ancestor
                    .join("resources")
                    .join("vendor")
                    .join("rclone")
                    .join(version)
                    .join(&target)
                    .join(executable),
                ancestor
                    .join("vendor")
                    .join("rclone")
                    .join(version)
                    .join(&target)
                    .join(executable),
                ancestor.join("vendor").join("rclone").join(executable),
                ancestor.join(executable),
            ] {
                if candidate.is_file() {
                    return prepare_bundled_rclone(&candidate)
                        .to_string_lossy()
                        .into_owned();
                }
            }
        }
    }
    executable.into()
}

fn prepare_bundled_rclone(source: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        // Mount processes can outlive the UI. Run the bundled binary from an
        // app-versioned, installer-external directory so an upgrade never has
        // to terminate those rclone processes or overwrite their executable.
        if let Some(root) = env::var_os("LOCALAPPDATA").map(PathBuf::from) {
            let destination = root
                .join("Mountlet/runtime")
                .join(env!("CARGO_PKG_VERSION"))
                .join("rclone.exe");
            if !destination.is_file() {
                if let Some(parent) = destination.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                if fs::copy(source, &destination).is_err() {
                    return source.to_path_buf();
                }
            }
            return destination;
        }
    }
    source.to_path_buf()
}

fn home_relative(parts: &[&str]) -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let home = env::var_os("USERPROFILE").or_else(|| env::var_os("HOME"));
    #[cfg(not(target_os = "windows"))]
    let home = env::var_os("HOME").or_else(|| env::var_os("USERPROFILE"));
    let mut path = PathBuf::from(home?);
    for part in parts {
        path.push(part);
    }
    Some(path)
}

fn mountlet_config_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        env::var_os("APPDATA")
            .map(PathBuf::from)
            .map(|path| path.join("Mountlet"))
    }
    #[cfg(target_os = "macos")]
    {
        env::var_os("HOME")
            .map(PathBuf::from)
            .map(|path| path.join("Library/Application Support/Mountlet"))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| home_relative(&[".config"]))
            .map(|path| path.join("mountlet"))
    }
}

fn app_config_path() -> Option<PathBuf> {
    mountlet_config_dir().map(|path| path.join("config.toml"))
}

fn mountlet_state_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .map(|path| path.join("Mountlet/State"))
    }
    #[cfg(target_os = "macos")]
    {
        home_relative(&["Library", "Application Support", "Mountlet"])
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .or_else(|| home_relative(&[".local", "state"]))
            .map(|path| path.join("mountlet"))
    }
}

fn notice_state_path() -> Option<PathBuf> {
    mountlet_state_dir().map(|directory| directory.join(format!("notices-{BUILD_CHANNEL}.json")))
}

fn load_notice_state() -> Result<(PathBuf, serde_json::Value), String> {
    let path = notice_state_path().ok_or("Mountlet state directory is unavailable")?;
    let mut value = fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_else(|| serde_json::json!({"seen": {}, "deleted": {}, "history": {}}));
    if let Some(object) = value.as_object_mut() {
        if !object.contains_key("seen")
            && !object.contains_key("deleted")
            && !object.contains_key("history")
        {
            let legacy_seen = serde_json::Value::Object(std::mem::take(object));
            value = serde_json::json!({"seen": legacy_seen, "deleted": {}, "history": {}});
        } else {
            for group in ["seen", "deleted", "history"] {
                if !object.get(group).is_some_and(serde_json::Value::is_object) {
                    object.insert(group.to_string(), serde_json::json!({}));
                }
            }
        }
    } else {
        value = serde_json::json!({"seen": {}, "deleted": {}, "history": {}});
    }
    Ok((path, value))
}

fn save_notice_state(path: &Path, value: &serde_json::Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn notice_time(value: Option<&serde_json::Value>) -> Option<f64> {
    let value = value?;
    if let Some(number) = value.as_f64() {
        return Some(number);
    }
    let text = value.as_str()?.trim();
    text.parse::<f64>().ok().or_else(|| {
        time::OffsetDateTime::parse(text, &time::format_description::well_known::Rfc3339)
            .ok()
            .map(|value| value.unix_timestamp() as f64)
    })
}

fn notice_text(value: Option<&serde_json::Value>, fallback: &str) -> String {
    match value {
        Some(serde_json::Value::String(value)) => value.trim().to_string(),
        Some(serde_json::Value::Number(value)) => value.to_string(),
        Some(serde_json::Value::Bool(value)) => value.to_string(),
        _ => fallback.to_string(),
    }
}

fn fetch_and_remember_notices() -> Result<Vec<NoticeView>, String> {
    let endpoint = env::var("MOUNTLET_NOTICE_API_URL")
        .unwrap_or_else(|_| "https://mountlet.app/api/notices".into());
    let response: serde_json::Value = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(12))
        .build()
        .map_err(|error| error.to_string())?
        .get(endpoint.trim_end_matches('/'))
        .query(&[
            ("appVersion", env!("CARGO_PKG_VERSION")),
            ("buildChannel", BUILD_CHANNEL),
            ("buildId", &build_id()),
        ])
        .header("Accept", "application/json")
        .header(
            "User-Agent",
            format!("Mountlet/{}", env!("CARGO_PKG_VERSION")),
        )
        .send()
        .map_err(|error| format!("Could not reach the notice server: {error}"))?
        .error_for_status()
        .map_err(|error| error.to_string())?
        .json()
        .map_err(|_| "The notice server returned invalid data.".to_string())?;
    let (path, mut state) = load_notice_state()?;
    let now = SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let state_object = state.as_object_mut().ok_or("Invalid notice state")?;
    let history = state_object
        .entry("history")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid notice history")?;
    let mut fetched_keys = Vec::new();
    for raw in response
        .get("notices")
        .and_then(|value| value.as_array())
        .into_iter()
        .flatten()
    {
        let Some(object) = raw.as_object() else {
            continue;
        };
        let id = object
            .get("id")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .trim();
        let title = object
            .get("title")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .trim();
        let message = object
            .get("message")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .trim();
        if id.is_empty() || title.is_empty() || message.is_empty() {
            continue;
        }
        let archived = object
            .get("archived")
            .and_then(|value| value.as_bool())
            .unwrap_or(false)
            || object
                .get("status")
                .and_then(|value| value.as_str())
                .is_some_and(|value| value.eq_ignore_ascii_case("archived"));
        let current = now as f64;
        if !archived
            && (notice_time(object.get("startsAt").or_else(|| object.get("starts_at")))
                .is_some_and(|value| current < value)
                || notice_time(object.get("endsAt").or_else(|| object.get("ends_at")))
                    .is_some_and(|value| current > value))
        {
            continue;
        }
        let version = notice_text(object.get("version"), "1");
        let key = format!("{id}:{version}");
        let received = history
            .values()
            .find(|value| value.get("id").and_then(|value| value.as_str()) == Some(id))
            .and_then(|value| value.get("receivedAt"))
            .and_then(|value| value.as_i64())
            .unwrap_or(now);
        history.retain(|_, value| value.get("id").and_then(|value| value.as_str()) != Some(id));
        let mut remembered = raw.clone();
        let level = object
            .get("level")
            .and_then(|value| value.as_str())
            .unwrap_or("info")
            .to_ascii_lowercase();
        remembered["level"] = serde_json::json!(if matches!(
            level.as_str(),
            "info" | "important" | "critical"
        ) {
            level.as_str()
        } else {
            "info"
        });
        remembered["type"] = serde_json::json!(object
            .get("type")
            .and_then(|value| value.as_str())
            .unwrap_or("general")
            .to_ascii_lowercase());
        remembered["version"] = serde_json::json!(version);
        remembered["archived"] = serde_json::json!(archived);
        remembered["receivedAt"] = serde_json::json!(received);
        history.insert(key.clone(), remembered);
        fetched_keys.push(key);
    }
    save_notice_state(&path, &state)?;
    notification_history().map(|values| {
        values
            .into_iter()
            .filter(|notice| fetched_keys.contains(&notice.key) && !notice.seen)
            .collect()
    })
}

fn rclone_config_path(rclone: &str) -> Option<PathBuf> {
    if let Some(path) = env::var_os("RCLONE_CONFIG")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
    {
        return Some(path);
    }
    let output = Command::new(rclone)
        .args(["config", "file"])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
        .find(|path| path.is_file())
        .or_else(|| {
            home_relative(&[".config", "rclone", "rclone.conf"]).filter(|path| path.is_file())
        })
}

fn default_rclone_config_path() -> Option<PathBuf> {
    if let Some(path) = env::var_os("RCLONE_CONFIG").map(PathBuf::from) {
        return Some(path);
    }
    #[cfg(target_os = "windows")]
    {
        env::var_os("APPDATA")
            .map(PathBuf::from)
            .map(|path| path.join("rclone/rclone.conf"))
    }
    #[cfg(target_os = "macos")]
    {
        home_relative(&[".config", "rclone", "rclone.conf"])
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| home_relative(&[".config"]))
            .map(|path| path.join("rclone/rclone.conf"))
    }
}

fn parse_toml_string(value: &str) -> String {
    let value = value.trim();
    if value.len() >= 2 && value.starts_with('"') && value.ends_with('"') {
        serde_json::from_str::<String>(value).unwrap_or_else(|_| value[1..value.len() - 1].into())
    } else {
        value.into()
    }
}

fn split_shell_words(value: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quote = None;
    let mut escaped = false;
    for character in value.chars() {
        if escaped {
            word.push(character);
            escaped = false;
        } else if character == '\\' && quote != Some('\'') {
            escaped = true;
        } else if matches!(character, '\'' | '"') {
            if quote == Some(character) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(character);
            } else {
                word.push(character);
            }
        } else if character.is_whitespace() && quote.is_none() {
            if !word.is_empty() {
                words.push(std::mem::take(&mut word));
            }
        } else {
            word.push(character);
        }
    }
    if escaped {
        word.push('\\');
    }
    if !word.is_empty() {
        words.push(word);
    }
    words
}

fn mount_settings() -> HashMap<String, RemoteMountSettings> {
    let Some(path) = mountlet_config_dir().map(|path| path.join("mounts.toml")) else {
        return HashMap::new();
    };
    let Ok(text) = fs::read_to_string(path) else {
        return HashMap::new();
    };
    let mut result: HashMap<String, RemoteMountSettings> = HashMap::new();
    let mut current: Option<String> = None;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with("[remotes.\"") && line.ends_with("\"]") {
            current = Some(line[10..line.len() - 2].to_string());
            if let Some(name) = &current {
                result.entry(name.clone()).or_default();
            }
        } else if let Some(name) = &current {
            let value = line.split_once('=').map(|(_, value)| value.trim());
            if line.starts_with("enabled") {
                if let Some(settings) = result.get_mut(name) {
                    settings.enabled = value != Some("false");
                }
            } else if line.starts_with("order") {
                if let Some(settings) = result.get_mut(name) {
                    settings.order = value.and_then(|value| value.parse().ok());
                }
            } else if line.starts_with("mount_path") {
                if let Some(settings) = result.get_mut(name) {
                    settings.mount_path = value
                        .map(parse_toml_string)
                        .filter(|value| !value.trim().is_empty());
                }
            } else if line.starts_with("remote_path") {
                if let Some(settings) = result.get_mut(name) {
                    settings.remote_path = value.map(parse_toml_string).unwrap_or_default();
                }
            } else if line.starts_with("mount_flags") {
                if let Some(settings) = result.get_mut(name) {
                    settings.mount_flags = value
                        .map(parse_toml_string)
                        .map(|value| split_shell_words(&value))
                        .unwrap_or_default();
                }
            } else if line.starts_with("auto_mount") {
                if let Some(settings) = result.get_mut(name) {
                    settings.auto_mount = value.and_then(|value| match value {
                        "true" => Some(true),
                        "false" => Some(false),
                        _ => None,
                    });
                }
            }
        }
    }
    result
}

fn save_remote_order_file(order: &[String]) -> Result<(), String> {
    let path = mountlet_config_dir()
        .map(|path| path.join("mounts.toml"))
        .ok_or("Mountlet configuration directory is unavailable")?;
    let source = fs::read_to_string(&path).unwrap_or_else(|_| {
        "# Per-remote Mountlet settings.\n# Remote names must match the names shown by rclone.\n\n".into()
    });
    let ranks: HashMap<&str, usize> = order
        .iter()
        .enumerate()
        .map(|(index, name)| (name.as_str(), index))
        .collect();
    let mut output = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut current = String::new();
    let mut inserted = false;
    for raw in source.lines() {
        let line = raw.trim();
        if line.starts_with("[remotes.\"") && line.ends_with("\"]") {
            if !current.is_empty() && !inserted {
                if let Some(rank) = ranks.get(current.as_str()) {
                    output.push(format!("order = {rank}"));
                }
            }
            current = line[10..line.len() - 2].to_string();
            inserted = false;
            seen.insert(current.clone());
            output.push(raw.to_string());
            continue;
        }
        if !current.is_empty() && line.starts_with("order") && line.contains('=') {
            if let Some(rank) = ranks.get(current.as_str()) {
                output.push(format!("order = {rank}"));
            }
            inserted = true;
            continue;
        }
        output.push(raw.to_string());
        if !current.is_empty() && line.starts_with("enabled") && !inserted {
            if let Some(rank) = ranks.get(current.as_str()) {
                output.push(format!("order = {rank}"));
                inserted = true;
            }
        }
    }
    if !current.is_empty() && !inserted {
        if let Some(rank) = ranks.get(current.as_str()) {
            output.push(format!("order = {rank}"));
        }
    }
    for name in order.iter().filter(|name| !seen.contains(*name)) {
        output.extend([
            String::new(),
            format!("[remotes.{}]", toml_string(name)),
            "enabled = true".into(),
            format!("order = {}", ranks[name.as_str()]),
            "mount_path = \"\"".into(),
            "remote_path = \"\"".into(),
            "mount_flags = \"\"".into(),
        ]);
    }
    output.push(String::new());
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("toml.tmp");
    fs::write(&temporary, output.join("\n")).map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn usage_values() -> HashMap<String, (Option<u64>, Option<u64>)> {
    let Some(path) = home_relative(&[".local", "state", "mountlet", "usage-cache.json"]) else {
        return HashMap::new();
    };
    let Ok(text) = fs::read_to_string(path) else {
        return HashMap::new();
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return HashMap::new();
    };
    value
        .get("remotes")
        .and_then(|value| value.as_object())
        .into_iter()
        .flatten()
        .map(|(name, value)| {
            (
                name.clone(),
                (
                    value.get("used").and_then(|value| value.as_u64()),
                    value.get("total").and_then(|value| value.as_u64()),
                ),
            )
        })
        .collect()
}

fn save_usage_value(remote_id: &str, used: Option<u64>, total: Option<u64>) -> Result<(), String> {
    let path = home_relative(&[".local", "state", "mountlet", "usage-cache.json"])
        .ok_or("Could not resolve usage cache")?;
    let mut value = fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .unwrap_or_else(|| serde_json::json!({"version": 1, "remotes": {}}));
    let remotes = value
        .as_object_mut()
        .ok_or("Invalid usage cache")?
        .entry("remotes")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid usage cache")?;
    let previous_connected = remotes
        .get(remote_id)
        .and_then(|item| item.get("connected"))
        .and_then(|item| item.as_bool())
        .unwrap_or(true);
    remotes.insert(remote_id.to_string(), serde_json::json!({"text": "", "used": used, "total": total, "connected": previous_connected}));
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(&value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn browser_memory_path() -> Option<PathBuf> {
    home_relative(&[".local", "state", "mountlet", "browser.json"])
}

fn browser_memory() -> serde_json::Value {
    let Some(path) = browser_memory_path() else {
        return serde_json::json!({});
    };
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

fn write_browser_memory(value: &serde_json::Value) -> Result<(), String> {
    let path = browser_memory_path().ok_or("Mountlet state directory is unavailable")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn persist_browser_memory_value(memory: serde_json::Value) -> Result<serde_json::Value, String> {
    let _write = BROWSER_MEMORY_WRITE_LOCK
        .lock()
        .map_err(|_| "Browser memory lock is unavailable")?;
    let mut current = browser_memory();
    if !current.is_object() {
        current = serde_json::json!({});
    }
    if let Some(object) = current.as_object_mut() {
        if let Some(paths) = memory.get("paths").and_then(serde_json::Value::as_object) {
            let current_paths = object
                .entry("paths")
                .or_insert_with(|| serde_json::json!({}));
            if let Some(current_paths) = current_paths.as_object_mut() {
                current_paths.extend(paths.clone());
            }
        }
        if let Some(selections) = memory
            .get("selections")
            .and_then(serde_json::Value::as_object)
        {
            let current_selections = object
                .entry("selections")
                .or_insert_with(|| serde_json::json!({}));
            if let Some(current_selections) = current_selections.as_object_mut() {
                for (remote_id, folders) in selections {
                    let current_remote = current_selections
                        .entry(remote_id)
                        .or_insert_with(|| serde_json::json!({}));
                    if let (Some(current_remote), Some(folders)) =
                        (current_remote.as_object_mut(), folders.as_object())
                    {
                        current_remote.extend(folders.clone());
                    }
                }
            }
        }
    }
    write_browser_memory(&current)?;
    Ok(current)
}

fn cached_selection_index(remote_id: &str, path: &str) -> Option<usize> {
    browser_memory()
        .get("selections")?
        .get(remote_id)?
        .get(path)?
        .get("index")?
        .as_u64()
        .map(|value| value as usize)
}

fn search_terms(query: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut current = String::new();
    let mut quoted = false;
    for character in query.chars() {
        if character == '"' {
            quoted = !quoted;
            continue;
        }
        if character.is_whitespace() && !quoted {
            if !current.is_empty() {
                result.push(current.to_lowercase());
                current.clear();
            }
        } else {
            current.push(character);
        }
    }
    if !current.is_empty() {
        result.push(current.to_lowercase());
    }
    result
}

fn sql_literal(value: &str) -> String {
    value.replace('\'', "''")
}

fn search_metadata(request: &SearchRequest) -> Result<Vec<SearchEntry>, String> {
    let terms = search_terms(&request.query);
    if terms.is_empty() || request.limit == 0 {
        return Ok(Vec::new());
    }
    let Some(database) = home_relative(&[".local", "state", "mountlet", "metadata-index.sqlite3"])
    else {
        return Ok(Vec::new());
    };
    if !database.is_file() {
        return Ok(Vec::new());
    }
    let matches = terms
        .iter()
        .map(|term| format!("instr(name_folded, '{}') > 0", sql_literal(term)))
        .collect::<Vec<_>>()
        .join(" AND ");
    let normalized = sql_literal(&terms.join(" "));
    let remote_clause = request
        .remote_id
        .as_ref()
        .map(|remote| format!(" AND remote_name = '{}'", sql_literal(remote)))
        .unwrap_or_default();
    let sql = format!(
        "SELECT remote_name AS remoteId, remote_display AS remoteDisplay, provider, name, path, parent_path AS parentPath, is_dir AS isDir, size, modified, CASE WHEN name_folded = '{normalized}' THEN 'exact' WHEN instr(name_folded, '{normalized}') > 0 THEN 'phrase' ELSE 'filename' END AS quality FROM entries WHERE {matches}{remote_clause} ORDER BY CASE WHEN name_folded = '{normalized}' THEN 0 WHEN instr(name_folded, '{normalized}') > 0 THEN 1 ELSE 2 END, is_dir DESC, name_folded ASC LIMIT {};",
        request.limit.saturating_add(1),
    );
    let connection =
        rusqlite::Connection::open_with_flags(database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .map_err(|error| error.to_string())?;
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| {
            Ok(SearchEntry {
                remote_id: row.get(0)?,
                remote_display: row.get(1)?,
                provider: row.get(2)?,
                name: row.get(3)?,
                path: row.get(4)?,
                parent_path: row.get(5)?,
                is_dir: row.get::<_, i64>(6)? != 0,
                size: row.get::<_, i64>(7)?.max(0) as u64,
                modified: row.get(8)?,
                quality: row.get(9)?,
            })
        })
        .map_err(|error| error.to_string())?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| error.to_string())
}

fn metadata_database() -> Option<PathBuf> {
    home_relative(&[".local", "state", "mountlet", "metadata-index.sqlite3"])
}

fn indexed_folder(remote_id: &str, path: &str) -> Result<Vec<FileEntry>, String> {
    let Some(database) = metadata_database().filter(|path| path.is_file()) else {
        return Ok(Vec::new());
    };
    let connection =
        rusqlite::Connection::open_with_flags(database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .map_err(|error| error.to_string())?;
    let mut statement = connection.prepare(
        "SELECT path, name, is_dir, size, modified FROM entries WHERE remote_name = ?1 AND parent_path = ?2 ORDER BY is_dir DESC, name_folded ASC"
    ).map_err(|error| error.to_string())?;
    let records = offline_records(remote_id);
    let remote_offline_root = offline_root().map(|root| root.join(mount_slug(remote_id)));
    let rows = statement
        .query_map(rusqlite::params![remote_id, path], |row| {
            let entry_path: String = row.get(0)?;
            let is_dir = row.get::<_, i64>(2)? != 0;
            Ok(FileEntry {
                id: format!("{remote_id}:{entry_path}"),
                remote_id: remote_id.into(),
                path: entry_path.clone(),
                name: row.get(1)?,
                is_dir,
                size: row.get::<_, i64>(3)?.max(0) as u64,
                modified: row.get(4)?,
                cache: cache_state(
                    &records,
                    remote_offline_root.as_deref(),
                    &entry_path,
                    is_dir,
                ),
            })
        })
        .map_err(|error| error.to_string())?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| error.to_string())
}

fn store_indexed_folder(
    remote_id: &str,
    remote_display: &str,
    provider: &str,
    path: &str,
    entries: &[FileEntry],
) -> Result<(), String> {
    let Some(database) = metadata_database() else {
        return Ok(());
    };
    if let Some(parent) = database.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let mut connection = rusqlite::Connection::open(database).map_err(|error| error.to_string())?;
    connection.execute_batch("CREATE TABLE IF NOT EXISTS entries (remote_name TEXT NOT NULL, remote_display TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', backend_type TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, parent_path TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, name_folded TEXT NOT NULL, is_dir INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0, modified TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (remote_name, path)); CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(remote_name, parent_path); CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name_folded);").map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    transaction
        .execute(
            "DELETE FROM entries WHERE remote_name = ?1 AND parent_path = ?2",
            rusqlite::params![remote_id, path],
        )
        .map_err(|error| error.to_string())?;
    {
        let mut insert = transaction.prepare("INSERT OR REPLACE INTO entries (remote_name, remote_display, provider, backend_type, path, parent_path, name, name_folded, is_dir, size, modified, updated_at) VALUES (?1, ?2, ?3, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, unixepoch())").map_err(|error| error.to_string())?;
        for entry in entries {
            insert
                .execute(rusqlite::params![
                    remote_id,
                    remote_display,
                    provider,
                    entry.path,
                    path,
                    entry.name,
                    entry.name.to_lowercase(),
                    entry.is_dir as i64,
                    entry.size as i64,
                    entry.modified
                ])
                .map_err(|error| error.to_string())?;
        }
    }
    transaction.commit().map_err(|error| error.to_string())
}

fn remote_fully_indexed(remote_id: &str) -> bool {
    let Some(database) = metadata_database().filter(|path| path.is_file()) else {
        return false;
    };
    rusqlite::Connection::open_with_flags(database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .ok()
        .and_then(|connection| {
            connection
                .query_row(
                    "SELECT complete FROM index_state WHERE remote_name = ?1",
                    rusqlite::params![remote_id],
                    |row| row.get::<_, i64>(0),
                )
                .ok()
        })
        == Some(1)
}

fn index_remote_tree(rclone: &str, config: &Path, remote: &Remote) -> Result<usize, String> {
    #[derive(Deserialize)]
    #[serde(rename_all = "PascalCase")]
    struct IndexedRcloneEntry {
        name: String,
        path: String,
        is_dir: bool,
        #[serde(default)]
        size: i64,
        #[serde(default)]
        mod_time: String,
    }

    let target = checked_remote_path(&remote.id, "")?;
    let output = Command::low_priority(rclone)
        .args(["--config"])
        .arg(config)
        .args(["lsjson", &target, "--recursive", "--no-mimetype"])
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let entries: Vec<IndexedRcloneEntry> =
        serde_json::from_slice(&output.stdout).map_err(|error| error.to_string())?;
    let database = metadata_database().ok_or("Metadata index path is unavailable")?;
    if let Some(parent) = database.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let mut connection = rusqlite::Connection::open(database).map_err(|error| error.to_string())?;
    connection.execute_batch("CREATE TABLE IF NOT EXISTS entries (remote_name TEXT NOT NULL, remote_display TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', backend_type TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, parent_path TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, name_folded TEXT NOT NULL, is_dir INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0, modified TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (remote_name, path)); CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(remote_name, parent_path); CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name_folded); CREATE TABLE IF NOT EXISTS index_state (remote_name TEXT PRIMARY KEY, complete INTEGER NOT NULL DEFAULT 0, completed_at REAL NOT NULL DEFAULT 0);").map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    transaction
        .execute(
            "DELETE FROM entries WHERE remote_name = ?1",
            rusqlite::params![remote.id],
        )
        .map_err(|error| error.to_string())?;
    {
        let mut insert = transaction.prepare("INSERT OR REPLACE INTO entries (remote_name, remote_display, provider, backend_type, path, parent_path, name, name_folded, is_dir, size, modified, updated_at) VALUES (?1, ?2, ?3, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, unixepoch())").map_err(|error| error.to_string())?;
        for entry in &entries {
            let path = entry.path.trim_matches('/');
            let parent = path
                .rsplit_once('/')
                .map(|(parent, _)| parent)
                .unwrap_or("");
            insert
                .execute(rusqlite::params![
                    remote.id,
                    remote.name,
                    remote.provider,
                    path,
                    parent,
                    entry.name,
                    entry.name.to_lowercase(),
                    entry.is_dir as i64,
                    entry.size.max(0),
                    entry
                        .mod_time
                        .get(..16)
                        .unwrap_or(&entry.mod_time)
                        .replace('T', " ")
                ])
                .map_err(|error| error.to_string())?;
        }
    }
    transaction.execute("INSERT OR REPLACE INTO index_state (remote_name, complete, completed_at) VALUES (?1, 1, unixepoch())", rusqlite::params![remote.id]).map_err(|error| error.to_string())?;
    transaction.commit().map_err(|error| error.to_string())?;
    Ok(entries.len())
}

#[tauri::command]
async fn start_initial_metadata_index(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let remotes = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        for remote in remotes {
            // Google Photos exposes virtual/date trees that cannot be reliably
            // represented by one recursive listing. It remains visit-indexed.
            if remote.provider == "gphotos" || remote_fully_indexed(&remote.id) {
                continue;
            }
            let _ = index_remote_tree(&rclone, &config, &remote);
            std::thread::sleep(Duration::from_millis(250));
        }
    })
    .await
    .map_err(|error| error.to_string())?;
    Ok(())
}

fn offline_root() -> Option<PathBuf> {
    let mut root = home_relative(&["Mountlet"])?;
    if let Some(path) = app_config_path() {
        if let Ok(text) = fs::read_to_string(path) {
            for raw in text.lines() {
                let line = raw.trim();
                if let Some((key, value)) = line.split_once('=') {
                    if key.trim() == "mount_base" {
                        let value = value.trim().trim_matches('"');
                        if !value.is_empty() {
                            root = PathBuf::from(value);
                        }
                        break;
                    }
                }
            }
        }
    }
    root.push("offline");
    Some(root)
}

fn mountlet_root() -> Option<PathBuf> {
    offline_root().and_then(|path| path.parent().map(Path::to_path_buf))
}

pub(crate) fn open_local_path(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut value = Command::new("cmd");
        value.args(["/C", "start", ""]);
        value
    };
    #[cfg(target_os = "macos")]
    let mut command = Command::new("open");
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let mut command = Command::new("xdg-open");
    command
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn open_external_target(target: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut value = Command::new("cmd");
        value.args(["/C", "start", "", target]);
        value
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut value = Command::new("open");
        value.arg(target);
        value
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut value = Command::new("xdg-open");
        value.arg(target);
        value
    };
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

fn url_query_value(value: &str) -> String {
    value
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
                (byte as char).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}

fn remote_browser_url(remote: &Remote) -> Option<String> {
    let mut base = match remote.provider.as_str() {
        "drive" => "https://drive.google.com/drive/my-drive",
        "gphotos" => "https://photos.google.com/",
        "dropbox" => "https://www.dropbox.com/home",
        "onedrive" => "https://onedrive.live.com/",
        "box" => "https://app.box.com/files",
        "pcloud" => "https://my.pcloud.com/",
        "iclouddrive" => "https://www.icloud.com/iclouddrive/",
        "koofr" => "https://app.koofr.net/",
        "protondrive" => "https://drive.proton.me/",
        "mega" => "https://mega.nz/fm",
        "s3" => match rclone_section_value(&remote.id, "provider")?
            .to_ascii_lowercase()
            .as_str()
        {
            "cloudflare" => "https://dash.cloudflare.com/?to=/:account/r2",
            "aws" => "https://console.aws.amazon.com/s3/",
            "wasabi" => "https://console.wasabisys.com/",
            "minio" => {
                return rclone_section_value(&remote.id, "endpoint")
                    .filter(|url| url.starts_with("http://") || url.starts_with("https://"))
            }
            _ => return None,
        },
        "webdav" => {
            let url = rclone_section_value(&remote.id, "url")?;
            if rclone_section_value(&remote.id, "vendor")
                .unwrap_or_default()
                .eq_ignore_ascii_case("nextcloud")
            {
                return url
                    .split("/remote.php/")
                    .next()
                    .map(|root| root.trim_end_matches('/').to_string());
            }
            return Some(url);
        }
        _ => return None,
    }
    .to_string();
    if matches!(remote.provider.as_str(), "drive" | "gphotos") {
        if remote.provider == "drive" {
            if let Some(folder) = rclone_section_value(&remote.id, "root_folder_id")
                .filter(|value| !value.is_empty())
                .or_else(|| {
                    rclone_section_value(&remote.id, "team_drive").filter(|value| !value.is_empty())
                })
            {
                let encoded = folder
                    .bytes()
                    .map(|byte| match byte {
                        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                            (byte as char).to_string()
                        }
                        _ => format!("%{byte:02X}"),
                    })
                    .collect::<String>();
                base = format!("https://drive.google.com/drive/folders/{encoded}");
            }
        }
        let account = [
            "mountlet_google_account",
            "account",
            "email",
            "user",
            "username",
        ]
        .iter()
        .find_map(|key| {
            rclone_section_value(&remote.id, key)
                .filter(|value| value.contains('@') && !value.chars().any(char::is_whitespace))
        })
        .or_else(|| remote.name.contains('@').then(|| remote.name.clone()));
        if let Some(account) = account {
            let encoded = account
                .bytes()
                .map(|byte| match byte {
                    b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                        (byte as char).to_string()
                    }
                    _ => format!("%{byte:02X}"),
                })
                .collect::<String>();
            let separator = if base.contains('?') { '&' } else { '?' };
            base = format!("{base}{separator}authuser={encoded}");
        }
    }
    Some(base)
}

fn rclone_section_value(remote_id: &str, key: &str) -> Option<String> {
    let rclone = env::var("RCLONE_BINARY").unwrap_or_else(|_| "rclone".into());
    let config = rclone_config_path(&rclone)?;
    let text = fs::read_to_string(config).ok()?;
    let mut current = false;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            current = &line[1..line.len() - 1] == remote_id;
        } else if current {
            if let Some((candidate, value)) = line.split_once('=') {
                if candidate.trim() == key {
                    return Some(value.trim().to_string());
                }
            }
        }
    }
    None
}

fn safe_rclone_keys(provider: &str) -> &'static [&'static str] {
    match provider {
        "drive" => &[
            "description",
            "mountlet_google_account",
            "client_id",
            "client_secret",
            "shared_with_me",
            "root_folder_id",
            "team_drive",
            "scope",
        ],
        "gphotos" => &[
            "description",
            "mountlet_google_account",
            "client_id",
            "client_secret",
            "read_only",
            "read_size",
            "include_archived",
            "start_year",
        ],
        "onedrive" => &["description", "drive_type", "region", "drive_id"],
        "webdav" => &[
            "description",
            "url",
            "vendor",
            "user",
            "pass",
            "bearer_token",
        ],
        "s3" => &[
            "description",
            "provider",
            "region",
            "endpoint",
            "env_auth",
            "access_key_id",
            "secret_access_key",
            "session_token",
            "storage_class",
            "acl",
        ],
        "koofr" => &["description", "provider", "user", "password", "mountid"],
        "protondrive" => &[
            "description",
            "username",
            "password",
            "2fa",
            "otp_secret_key",
            "mailbox_password",
            "enable_caching",
        ],
        "iclouddrive" => &[
            "description",
            "service",
            "apple_id",
            "password",
            "trust_token",
            "cookies",
        ],
        "mega" => &["description", "user", "pass", "2fa"],
        _ => &["description"],
    }
}

fn secret_rclone_key(key: &str) -> bool {
    matches!(
        key,
        "client_secret"
            | "secret_access_key"
            | "pass"
            | "password"
            | "otp_secret_key"
            | "mailbox_password"
    )
}

type RcloneSection = (String, HashMap<String, String>);

fn parse_rclone_config(config: &Path) -> Result<Vec<RcloneSection>, String> {
    let text = fs::read_to_string(config).map_err(|error| error.to_string())?;
    let mut sections = Vec::new();
    let mut current = String::new();
    let mut values = HashMap::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            if !current.is_empty() {
                sections.push((std::mem::take(&mut current), std::mem::take(&mut values)));
            }
            current = line[1..line.len() - 1].to_string();
        } else if let Some((key, value)) = line.split_once('=') {
            if !current.is_empty() {
                values.insert(key.trim().into(), value.trim().into());
            }
        }
    }
    if !current.is_empty() {
        sections.push((current, values));
    }
    Ok(sections)
}

fn read_rclone_section(config: &Path, remote_id: &str) -> Result<HashMap<String, String>, String> {
    Ok(parse_rclone_config(config)?
        .into_iter()
        .find(|(name, _)| name == remote_id)
        .map(|(_, values)| values)
        .unwrap_or_default())
}

fn write_rclone_section(
    config: &Path,
    remote_id: &str,
    new_remote_id: &str,
    updates: &HashMap<String, String>,
) -> Result<(), String> {
    let source = fs::read_to_string(config).map_err(|error| error.to_string())?;
    let mut output = Vec::new();
    let mut current = false;
    let mut found = false;
    let mut written = std::collections::HashSet::new();
    for raw in source.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            if current {
                for (key, value) in updates.iter().filter(|(key, _)| !written.contains(*key)) {
                    if !value.is_empty() {
                        output.push(format!("{key} = {value}"));
                    }
                }
            }
            let name = &line[1..line.len() - 1];
            current = name == remote_id;
            found |= current;
            output.push(if current {
                format!("[{new_remote_id}]")
            } else {
                raw.into()
            });
        } else if current {
            if let Some((raw_key, _)) = line.split_once('=') {
                let key = raw_key.trim();
                if let Some(value) = updates.get(key) {
                    written.insert(key.to_string());
                    if !value.is_empty() {
                        output.push(format!("{key} = {value}"));
                    }
                    continue;
                }
            }
            output.push(raw.into());
        } else {
            output.push(raw.into());
        }
    }
    if current {
        for (key, value) in updates.iter().filter(|(key, _)| !written.contains(*key)) {
            if !value.is_empty() {
                output.push(format!("{key} = {value}"));
            }
        }
    }
    if !found {
        return Err("Remote section is missing from rclone.conf".into());
    }
    output.push(String::new());
    let temporary = config.with_extension("conf.tmp");
    fs::write(&temporary, output.join("\n")).map_err(|error| error.to_string())?;
    fs::rename(temporary, config).map_err(|error| error.to_string())
}

fn update_mount_section(
    remote_id: &str,
    new_remote_id: &str,
    settings: &RemoteMountSettings,
) -> Result<(), String> {
    let mut all = mount_settings();
    let order = all
        .remove(remote_id)
        .and_then(|value| value.order)
        .or(settings.order);
    let mut replacement = settings.clone();
    replacement.order = order;
    all.insert(new_remote_id.into(), replacement);
    write_mount_settings(&all)
}

fn remove_mount_section(remote_id: &str) -> Result<(), String> {
    let mut all = mount_settings();
    all.remove(remote_id);
    write_mount_settings(&all)
}

fn write_mount_settings(all: &HashMap<String, RemoteMountSettings>) -> Result<(), String> {
    let path = mountlet_config_dir()
        .map(|path| path.join("mounts.toml"))
        .ok_or("Mountlet configuration directory is unavailable")?;
    let mut names = all.keys().cloned().collect::<Vec<_>>();
    names.sort_by_key(|name| all[name].order.unwrap_or(i64::MAX));
    let mut output = vec![
        "# Per-remote Mountlet settings.".to_string(),
        "# Remote names must match the names shown by rclone.".into(),
    ];
    for name in names {
        let value = &all[&name];
        output.push(String::new());
        output.push(format!("[remotes.{}]", toml_string(&name)));
        output.push(format!("enabled = {}", value.enabled));
        if let Some(order) = value.order {
            output.push(format!("order = {order}"));
        }
        output.push(format!(
            "mount_path = {}",
            toml_string(value.mount_path.as_deref().unwrap_or(""))
        ));
        output.push(format!("remote_path = {}", toml_string(&value.remote_path)));
        output.push(format!(
            "mount_flags = {}",
            toml_string(&value.mount_flags.join(" "))
        ));
        if let Some(enabled) = value.auto_mount {
            output.push(format!("auto_mount = {enabled}"));
        }
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("toml.tmp");
    fs::write(&temporary, output.join("\n")).map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn offline_records(remote_id: &str) -> serde_json::Map<String, serde_json::Value> {
    let Some(manifest_path) =
        home_relative(&[".local", "state", "mountlet", "offline_manifest.json"])
    else {
        return serde_json::Map::new();
    };
    let Ok(text) = fs::read_to_string(manifest_path) else {
        return serde_json::Map::new();
    };
    let Ok(manifest) = serde_json::from_str::<serde_json::Value>(&text) else {
        return serde_json::Map::new();
    };
    manifest
        .get("remotes")
        .and_then(|value| value.get(remote_id))
        .and_then(|value| value.as_object())
        .cloned()
        .unwrap_or_default()
}

fn offline_manifest_path() -> Option<PathBuf> {
    home_relative(&[".local", "state", "mountlet", "offline_manifest.json"])
}

fn update_offline_manifest(
    remote_id: &str,
    path: &str,
    local: &Path,
    protect: bool,
) -> Result<(), String> {
    let manifest_path = offline_manifest_path().ok_or("Could not resolve offline manifest")?;
    let mut manifest = fs::read_to_string(&manifest_path)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .unwrap_or_else(|| serde_json::json!({"version": 1, "remotes": {}}));
    let remotes = manifest
        .as_object_mut()
        .unwrap()
        .entry("remotes")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid offline manifest")?;
    let records = remotes
        .entry(remote_id)
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid remote manifest")?;
    let mut add = |relative: String, candidate: &Path, is_dir: bool| {
        let size = if is_dir {
            0
        } else {
            candidate.metadata().map(|value| value.len()).unwrap_or(0)
        };
        let digest = if !is_dir {
            file_sha256(candidate).unwrap_or_default()
        } else {
            String::new()
        };
        let modified_ns = candidate
            .metadata()
            .ok()
            .and_then(|value| value.modified().ok())
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
            .unwrap_or(0);
        let protected = protect
            || records
                .get(&relative)
                .and_then(|record| record.get("protected"))
                .and_then(|value| value.as_bool())
                .unwrap_or(false);
        records.insert(
            relative,
            serde_json::json!({
                "is_dir": is_dir, "size": size, "modified": "", "cached_at": "",
                "protected": protected, "complete": true, "local_size": size,
                "local_mtime_ns": modified_ns, "local_sha256": digest
            }),
        );
    };
    add(path.into(), local, local.is_dir());
    if local.is_dir() {
        let mut pending = vec![local.to_path_buf()];
        while let Some(directory) = pending.pop() {
            for child in fs::read_dir(&directory).map_err(|error| error.to_string())? {
                let child = child.map_err(|error| error.to_string())?.path();
                let suffix = child
                    .strip_prefix(local)
                    .map_err(|error| error.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                let relative = format!("{}/{}", path.trim_end_matches('/'), suffix)
                    .trim_matches('/')
                    .to_string();
                let is_dir = child.is_dir();
                add(relative, &child, is_dir);
                if is_dir {
                    pending.push(child);
                }
            }
        }
    }
    if let Some(parent) = manifest_path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = manifest_path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, manifest_path).map_err(|error| error.to_string())
}

fn file_sha256(path: &Path) -> Result<String, String> {
    let mut file = fs::File::open(path).map_err(|error| error.to_string())?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 128 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn update_offline_baseline(remote_id: &str, path: &str, local: &Path) -> Result<(), String> {
    let manifest_path = offline_manifest_path().ok_or("Could not resolve offline manifest")?;
    let mut manifest: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(&manifest_path).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let record = manifest
        .get_mut("remotes")
        .and_then(|value| value.get_mut(remote_id))
        .and_then(|value| value.get_mut(path))
        .and_then(|value| value.as_object_mut())
        .ok_or("Offline record is missing")?;
    let metadata = local.metadata().map_err(|error| error.to_string())?;
    record.insert(
        "local_sha256".into(),
        serde_json::json!(file_sha256(local)?),
    );
    record.insert("local_size".into(), serde_json::json!(metadata.len()));
    record.insert("size".into(), serde_json::json!(metadata.len()));
    record.insert(
        "local_mtime_ns".into(),
        serde_json::json!(metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
            .unwrap_or(0)),
    );
    save_notice_state(&manifest_path, &manifest)
}

fn remove_protected_offline_records(
    remote_id: Option<&str>,
    selected_path: Option<&str>,
) -> Result<usize, String> {
    let Some(manifest_path) = offline_manifest_path() else {
        return Ok(0);
    };
    let Ok(text) = fs::read_to_string(&manifest_path) else {
        return Ok(0);
    };
    let mut manifest: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| error.to_string())?;
    let Some(remotes) = manifest
        .get_mut("remotes")
        .and_then(|value| value.as_object_mut())
    else {
        return Ok(0);
    };
    let ids = remote_id
        .map(|id| vec![id.to_string()])
        .unwrap_or_else(|| remotes.keys().cloned().collect());
    let cache_root = offline_root().ok_or("Could not resolve offline folder")?;
    let selected = selected_path.map(|path| path.trim_matches('/').to_string());
    let mut removed = 0usize;
    for id in ids {
        let Some(records) = remotes.get_mut(&id).and_then(|value| value.as_object_mut()) else {
            continue;
        };
        let mut paths = records
            .iter()
            .filter(|(path, record)| {
                let in_scope = selected
                    .as_ref()
                    .map(|selected| {
                        selected.is_empty()
                            || path.as_str() == selected
                            || path.starts_with(&format!("{selected}/"))
                    })
                    .unwrap_or(true);
                in_scope
                    && record
                        .get("protected")
                        .and_then(|value| value.as_bool())
                        .unwrap_or(false)
            })
            .map(|(path, record)| {
                (
                    path.clone(),
                    record
                        .get("is_dir")
                        .and_then(|value| value.as_bool())
                        .unwrap_or(false),
                )
            })
            .collect::<Vec<_>>();
        paths.sort_by_key(|(path, _)| std::cmp::Reverse(path.matches('/').count()));
        let root = cache_root.join(mount_slug(&id));
        for (path, is_dir) in paths {
            let target = root.join(&path);
            if is_dir {
                // remove_dir intentionally preserves a directory containing temporary cache.
                let _ = fs::remove_dir(&target);
            } else if target.exists() {
                fs::remove_file(&target).map_err(|error| error.to_string())?;
                removed += 1;
            }
            records.remove(&path);
        }
        if records.is_empty() {
            remotes.remove(&id);
        }
    }
    let temporary = manifest_path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, manifest_path).map_err(|error| error.to_string())?;
    Ok(removed)
}

fn remove_cached_path(root: &Path, relative: &str) -> Result<(), String> {
    if relative.split('/').any(|part| part == "..") {
        return Err("Invalid cached path".into());
    }
    let target = root.join(relative);
    if target.is_dir() {
        fs::remove_dir_all(target).map_err(|error| error.to_string())?;
    } else if target.exists() {
        fs::remove_file(target).map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn clear_resolved_cache(
    remote_id: Option<&str>,
    selected_path: Option<&str>,
) -> Result<usize, String> {
    let Some(manifest_path) = offline_manifest_path() else {
        return Ok(0);
    };
    let Ok(text) = fs::read_to_string(&manifest_path) else {
        return Ok(0);
    };
    let mut manifest: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| error.to_string())?;
    let Some(remotes) = manifest
        .get_mut("remotes")
        .and_then(|value| value.as_object_mut())
    else {
        return Ok(0);
    };
    let ids: Vec<String> = match remote_id {
        Some(value) => vec![value.to_string()],
        None => remotes.keys().cloned().collect(),
    };
    let mut removed = 0usize;
    let cache_root = offline_root().ok_or("Could not resolve offline folder")?;
    for id in ids {
        let Some(records) = remotes.get_mut(&id).and_then(|value| value.as_object_mut()) else {
            continue;
        };
        let mut paths: Vec<String> = records
            .iter()
            .filter(|(path, record)| {
                let in_scope = selected_path
                    .map(|selected| {
                        let selected = selected.trim_matches('/');
                        selected.is_empty()
                            || path.as_str() == selected
                            || path.starts_with(&format!("{selected}/"))
                    })
                    .unwrap_or(true);
                let prefix = format!("{}/", path.trim_end_matches('/'));
                let protected_descendant = records.iter().any(|(candidate, descendant)| {
                    candidate.starts_with(&prefix)
                        && descendant
                            .get("protected")
                            .and_then(|value| value.as_bool())
                            .unwrap_or(false)
                });
                in_scope
                    && !protected_descendant
                    && !record
                        .get("protected")
                        .and_then(|value| value.as_bool())
                        .unwrap_or(false)
            })
            .map(|(path, _)| path.clone())
            .collect();
        paths.sort_by_key(|path| std::cmp::Reverse(path.matches('/').count()));
        let root = cache_root.join(mount_slug(&id));
        for path in &paths {
            remove_cached_path(&root, path)?;
            records.remove(path);
            removed += 1;
        }
        if records.is_empty() {
            remotes.remove(&id);
        }
    }
    let temporary = manifest_path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&manifest).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, manifest_path).map_err(|error| error.to_string())?;
    Ok(removed)
}

fn cache_state(
    records: &serde_json::Map<String, serde_json::Value>,
    root: Option<&Path>,
    path: &str,
    is_dir: bool,
) -> CacheState {
    let local = root.map(|root| root.join(path));
    if !is_dir {
        let record = records.get(path);
        let exists = local.as_ref().is_some_and(|candidate| candidate.is_file());
        let complete = record
            .and_then(|value| value.get("complete"))
            .and_then(|value| value.as_bool())
            // The filesystem is authoritative for temporary cache visibility.
            // Older/interrupted writes may leave a valid local file before its
            // manifest record reaches disk.
            .unwrap_or(exists);
        let protected = record
            .and_then(|value| value.get("protected"))
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        return CacheState {
            cached: exists && complete,
            offline: exists && complete && protected,
            partial: false,
        };
    }
    let prefix = if path.is_empty() {
        String::new()
    } else {
        format!("{path}/")
    };
    let mut any = false;
    let mut all_protected = true;
    for (candidate, value) in records {
        if candidate == path || candidate.starts_with(&prefix) {
            any = true;
            if !value
                .get("protected")
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
            {
                all_protected = false;
            }
        }
    }
    CacheState {
        cached: any,
        offline: any && all_protected,
        partial: any && !all_protected,
    }
}

fn ui_preferences() -> UiPreferences {
    let settings = app_settings();
    UiPreferences {
        mode: settings.window_mode,
        theme: settings.theme,
        zoom_step: settings.zoom_steps,
        integrated_file_edits: settings.integrated_file_edits,
        file_list_max_items: settings.file_list_max_items,
    }
}

fn unquote_toml(value: &str) -> String {
    let value = value.trim();
    if value.len() >= 2 && value.starts_with('"') && value.ends_with('"') {
        value[1..value.len() - 1]
            .replace("\\\"", "\"")
            .replace("\\\\", "\\")
    } else {
        value.to_string()
    }
}

fn simple_toml_values(text: &str) -> HashMap<(String, String), String> {
    let mut values = HashMap::new();
    let mut section = String::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            section = line[1..line.len() - 1].to_string();
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            values.insert(
                (section.clone(), key.trim().to_string()),
                unquote_toml(value),
            );
        }
    }
    values
}

fn setting<'a>(
    values: &'a HashMap<(String, String), String>,
    section: &str,
    key: &str,
) -> Option<&'a str> {
    values
        .get(&(section.to_string(), key.to_string()))
        .map(String::as_str)
}

fn setting_bool(value: Option<&str>, fallback: bool) -> bool {
    value
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "true" | "1" | "yes" | "on"
            )
        })
        .unwrap_or(fallback)
}

fn app_settings() -> AppSettings {
    let text = app_config_path()
        .and_then(|path| fs::read_to_string(path).ok())
        .unwrap_or_default();
    let values = simple_toml_values(&text);
    let shortcuts = shortcut_preferences();
    let choice = |section: &str, key: &str, fallback: &str, allowed: &[&str]| {
        setting(&values, section, key)
            .filter(|value| allowed.contains(value))
            .unwrap_or(fallback)
            .to_string()
    };
    AppSettings {
        mount_base: setting(&values, "app", "mount_base")
            .unwrap_or("")
            .to_string(),
        auto_mount: setting_bool(setting(&values, "app", "auto_mount"), false),
        auto_mount_delay: setting(&values, "app", "auto_mount_delay")
            .and_then(|value| value.parse().ok())
            .unwrap_or(2.0_f64)
            .max(0.0),
        start_at_login: setting_bool(setting(&values, "app", "start_at_login"), false),
        integrated_file_edits: setting_bool(
            setting(&values, "app", "integrated_file_edits"),
            false,
        ),
        file_manager: setting(&values, "tray", "file_manager")
            .unwrap_or("")
            .to_string(),
        open_folder_behavior: choice(
            "tray",
            "open_folder_behavior",
            "current_desktop",
            &[
                "current_desktop",
                "existing_window",
                "new_window",
                "file-manager-service",
                "default",
            ],
        ),
        focus_file_manager: setting_bool(setting(&values, "tray", "focus_file_manager"), true),
        window_mode: platform::effective_window_mode(&choice(
            "ui",
            "window_mode",
            "multiple",
            &["single", "multiple"],
        )),
        theme: choice("ui", "theme", "system", &["system", "light", "dark"]),
        zoom_steps: setting(&values, "ui", "zoom_steps")
            .and_then(|value| value.parse::<i32>().ok())
            .unwrap_or(0)
            .clamp(-4, 6),
        file_list_max_items: setting(&values, "ui", "file_list_max_items")
            .and_then(|value| value.parse().ok())
            .unwrap_or(0),
        remote_check_interval: setting(&values, "sync", "remote_check_interval")
            .and_then(|value| value.parse().ok())
            .unwrap_or(30.0_f64)
            .max(0.0),
        notice_info_display: choice("notices", "info", "tray", &["off", "tray", "dialog"]),
        notice_important_display: choice(
            "notices",
            "important",
            "dialog",
            &["off", "tray", "dialog"],
        ),
        notice_check_interval: setting(&values, "notices", "check_interval")
            .and_then(|value| value.parse().ok())
            .unwrap_or(14_400.0_f64)
            .max(0.0),
        config_sync_remote: setting(&values, "sync", "config_remote")
            .unwrap_or("")
            .to_string(),
        config_sync_path: setting(&values, "sync", "config_path")
            .filter(|value| !value.is_empty())
            .unwrap_or("Mountlet/config.mountlet")
            .to_string(),
        shortcuts,
    }
}

fn toml_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn save_app_settings_file(settings: &AppSettings) -> Result<(), String> {
    let path = app_config_path().ok_or("Mountlet configuration directory is unavailable")?;
    let parent = path
        .parent()
        .ok_or("Mountlet configuration directory is unavailable")?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let mut shortcut_order = settings.shortcuts.keys().collect::<Vec<_>>();
    shortcut_order.sort();
    let mut lines = vec![
        "# Mountlet app settings.".to_string(),
        "# rclone account credentials stay in rclone.conf.".to_string(),
        "".to_string(),
        "[app]".to_string(),
        format!("mount_base = {}", toml_string(&settings.mount_base)),
        format!("auto_mount = {}", settings.auto_mount),
        format!("auto_mount_delay = {}", settings.auto_mount_delay),
        format!("start_at_login = {}", settings.start_at_login),
        format!("integrated_file_edits = {}", settings.integrated_file_edits),
        "".to_string(),
        "[tray]".to_string(),
        format!("file_manager = {}", toml_string(&settings.file_manager)),
        format!(
            "open_folder_behavior = {}",
            toml_string(&settings.open_folder_behavior)
        ),
        format!("focus_file_manager = {}", settings.focus_file_manager),
        "".to_string(),
        "[ui]".to_string(),
        format!("window_mode = {}", toml_string(&settings.window_mode)),
        format!("theme = {}", toml_string(&settings.theme)),
        format!("zoom_steps = {}", settings.zoom_steps.clamp(-4, 6)),
        format!("file_list_max_items = {}", settings.file_list_max_items),
        "".to_string(),
        "[sync]".to_string(),
        format!(
            "remote_check_interval = {}",
            settings.remote_check_interval.max(0.0)
        ),
        format!(
            "config_remote = {}",
            toml_string(&settings.config_sync_remote)
        ),
        format!("config_path = {}", toml_string(&settings.config_sync_path)),
        "".to_string(),
        "[notices]".to_string(),
        format!("info = {}", toml_string(&settings.notice_info_display)),
        format!(
            "important = {}",
            toml_string(&settings.notice_important_display)
        ),
        format!(
            "check_interval = {}",
            settings.notice_check_interval.max(0.0)
        ),
        "".to_string(),
        "[shortcuts]".to_string(),
    ];
    for key in shortcut_order {
        let joined = settings
            .shortcuts
            .get(key)
            .cloned()
            .unwrap_or_default()
            .join(", ");
        lines.push(format!("{key} = {}", toml_string(&joined)));
    }
    lines.push(String::new());
    let temporary = path.with_extension("toml.tmp");
    fs::write(&temporary, lines.join("\n")).map_err(|error| error.to_string())?;
    fs::rename(&temporary, &path).map_err(|error| error.to_string())
}

fn apply_start_at_login(enabled: bool) -> Result<(), String> {
    let executable = env::current_exe().map_err(|error| error.to_string())?;
    #[cfg(target_os = "windows")]
    {
        let key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
        let mut command = Command::new("reg");
        if enabled {
            command
                .args(["add", key, "/v", "Mountlet", "/t", "REG_SZ", "/d"])
                .arg(format!("\"{}\"", executable.display()))
                .arg("/f");
        } else {
            command.args(["delete", key, "/v", "Mountlet", "/f"]);
        }
        let status = command.status().map_err(|error| error.to_string())?;
        if !status.success() {
            return Err("Windows could not update the login startup entry".into());
        }
    }
    #[cfg(target_os = "macos")]
    {
        let path = home_relative(&["Library", "LaunchAgents", "app.mountlet.desktop.plist"])
            .ok_or("Could not resolve LaunchAgents")?;
        if enabled {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            let executable = executable.to_string_lossy();
            fs::write(&path, format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"><plist version=\"1.0\"><dict><key>Label</key><string>app.mountlet.desktop</string><key>ProgramArguments</key><array><string>{executable}</string></array><key>RunAtLoad</key><true/></dict></plist>"))
                .map_err(|error| error.to_string())?;
        } else if path.exists() {
            fs::remove_file(path).map_err(|error| error.to_string())?;
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let path = env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| home_relative(&[".config"]))
            .ok_or("Could not resolve the autostart folder")?
            .join("autostart/mountlet.desktop");
        if enabled {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            fs::write(&path, format!("[Desktop Entry]\nType=Application\nName=Mountlet\nExec={}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n", executable.display()))
                .map_err(|error| error.to_string())?;
        } else if path.exists() {
            fs::remove_file(path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn shortcut_preferences() -> HashMap<String, Vec<String>> {
    let mut values: HashMap<String, Vec<String>> = HashMap::new();
    let Some(path) = app_config_path() else {
        return values;
    };
    let Ok(text) = fs::read_to_string(path) else {
        return values;
    };
    let mut in_shortcuts = false;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') {
            in_shortcuts = line == "[shortcuts]";
            continue;
        }
        if !in_shortcuts {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            continue;
        };
        let parsed = raw_value
            .trim()
            .trim_matches('"')
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .take(3)
            .map(str::to_string)
            .collect();
        values.insert(key.trim().into(), parsed);
    }
    values
}

fn mounted_remote_names() -> Vec<String> {
    let Ok(text) = fs::read_to_string("/proc/self/mountinfo") else {
        return Vec::new();
    };
    text.lines()
        .filter_map(|line| line.split_whitespace().nth(4))
        .map(|path| path.replace("\\040", " "))
        .filter_map(|path| {
            Path::new(&path)
                .file_name()
                .map(|name| name.to_string_lossy().to_string())
        })
        .collect()
}

#[cfg(target_os = "windows")]
fn cleanup_stale_mounts(_state: &AppState) {
    // WinFsp mount points can remain as blocking reparse points after an
    // interrupted rclone process. They are not safe to enumerate or remove as
    // ordinary directories; a later successful mount can reuse the path.
}

#[cfg(not(target_os = "windows"))]
fn cleanup_stale_mounts(_state: &AppState) {
    let Some(root) = mountlet_root().map(|root| root.join("mounted")) else {
        return;
    };
    #[cfg(target_os = "linux")]
    let expected = _state
        .remotes
        .read()
        .map(|remotes| {
            remotes
                .iter()
                .filter_map(|remote| remote_mount_path(&remote.id).ok())
                .map(|path| path.to_string_lossy().to_string())
                .collect::<std::collections::HashSet<_>>()
        })
        .unwrap_or_default();
    #[cfg(target_os = "linux")]
    if let Ok(mountinfo) = fs::read_to_string("/proc/self/mountinfo") {
        for target in mountinfo
            .lines()
            .filter_map(|line| line.split_whitespace().nth(4))
            .map(|path| path.replace("\\040", " "))
        {
            if target.starts_with(&root.to_string_lossy().to_string())
                && !expected.contains(&target)
            {
                let released = [
                    ("fusermount3", vec!["-u", "-z", target.as_str()]),
                    ("fusermount3", vec!["-u", target.as_str()]),
                    ("fusermount", vec!["-u", "-z", target.as_str()]),
                    ("fusermount", vec!["-u", target.as_str()]),
                    ("umount", vec![target.as_str()]),
                ]
                .iter()
                .any(|(program, arguments)| {
                    Command::new(program)
                        .args(arguments)
                        .status()
                        .map(|status| status.success())
                        .unwrap_or(false)
                });
                append_rclone_log(
                    _state,
                    if released {
                        format!("Released stale mount {target}")
                    } else {
                        format!("Could not release stale mount {target}")
                    },
                );
            }
        }
    }
    let directories = cleanup_directories(&root, path_is_mounted);
    for directory in directories {
        if !path_is_mounted(&directory) {
            let _ = fs::remove_dir(&directory);
        }
    }
}

#[cfg(any(not(target_os = "windows"), test))]
fn cleanup_directories(root: &Path, is_mounted: impl Fn(&Path) -> bool) -> Vec<PathBuf> {
    let mut directories = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        // Never walk into a live FUSE mount while cleaning empty mount-point
        // directories. A cloud provider can block directory enumeration and
        // hold the entire application startup indefinitely.
        if directory != root && is_mounted(&directory) {
            continue;
        }
        if let Ok(children) = fs::read_dir(&directory) {
            for child in children
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| path.is_dir())
            {
                pending.push(child.clone());
                directories.push(child);
            }
        }
    }
    directories.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
    directories
}

fn remote_mount_path(remote_id: &str) -> Result<PathBuf, String> {
    let configured = mount_settings()
        .remove(remote_id)
        .and_then(|settings| settings.mount_path);
    let root = mountlet_root().ok_or("Could not resolve Mountlet's data folder")?;
    Ok(match configured {
        Some(path) if Path::new(&path).is_absolute() => PathBuf::from(path),
        Some(path) => root.join("mounted").join(path),
        None => root.join("mounted").join(mount_slug(remote_id)),
    })
}

fn remote_source(remote_id: &str) -> String {
    let path = mount_settings()
        .remove(remote_id)
        .map(|settings| settings.remote_path)
        .unwrap_or_default();
    let path = path.trim().trim_start_matches('/');
    if path.is_empty() {
        format!("{remote_id}:")
    } else {
        format!("{remote_id}:{path}")
    }
}

fn effective_mount_flags(remote_id: &str, provider: &str) -> Vec<String> {
    let mut flags = default_mount_flags(provider)
        .iter()
        .map(|value| (*value).to_string())
        .collect::<Vec<_>>();
    if provider == "drive" && !flags.iter().any(|value| value == "--links") {
        flags.push("--links".into());
    }
    if let Some(value) = rclone_section_value(remote_id, "mount_flags") {
        flags.extend(split_shell_words(&value));
    }
    if let Some(settings) = mount_settings().remove(remote_id) {
        flags.extend(settings.mount_flags);
    }
    flags
}

fn path_is_mounted(path: &Path) -> bool {
    #[cfg(target_os = "linux")]
    {
        let expected = path.to_string_lossy();
        fs::read_to_string("/proc/self/mountinfo")
            .ok()
            .map(|text| {
                text.lines().any(|line| {
                    line.split_whitespace()
                        .nth(4)
                        .map(|value| value.replace("\\040", " ") == expected)
                        .unwrap_or(false)
                })
            })
            .unwrap_or(false)
    }
    #[cfg(target_os = "windows")]
    {
        let path = path.to_path_buf();
        let (sender, receiver) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            use std::os::windows::fs::MetadataExt;
            const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
            let mounted = path
                .symlink_metadata()
                .map(|metadata| metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
                .unwrap_or(false);
            let _ = sender.send(mounted);
        });
        receiver
            .recv_timeout(Duration::from_millis(200))
            .unwrap_or(false)
    }
    #[cfg(target_os = "macos")]
    {
        let expected = path.to_string_lossy();
        Command::new("/sbin/mount")
            .output()
            .ok()
            .map(|output| {
                String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .any(|line| line.contains(&format!(" on {expected} (")))
            })
            .unwrap_or(false)
    }
}

fn default_mount_flags(provider: &str) -> &'static [&'static str] {
    match provider {
        "drive" => &[
            "--vfs-cache-mode",
            "full",
            "--vfs-read-ahead",
            "64M",
            "--vfs-cache-max-size",
            "2G",
            "--buffer-size",
            "32M",
            "--dir-cache-time",
            "72h",
            "--poll-interval",
            "30s",
            "--attr-timeout",
            "1s",
            "--no-modtime",
            "--vfs-fast-fingerprint",
        ],
        "dropbox" | "koofr" | "mega" | "webdav" => {
            &["--vfs-cache-mode", "full", "--buffer-size", "16M"]
        }
        "protondrive" => &[
            "--vfs-cache-mode",
            "full",
            "--buffer-size",
            "16M",
            "--dir-cache-time",
            "30s",
        ],
        "s3" => &[
            "--vfs-cache-mode",
            "full",
            "--buffer-size",
            "32M",
            "--attr-timeout",
            "1s",
        ],
        _ => &["--vfs-cache-mode", "full"],
    }
}

fn mount_slug(value: &str) -> String {
    let mut result = String::new();
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-') {
            result.push(character);
        } else if !result.ends_with('_') {
            result.push('_');
        }
    }
    result.trim_matches(&['.', '_'][..]).to_string()
}

fn provider_label(name: &str, backend: &str, values: &HashMap<String, String>) -> String {
    let suffix = name
        .rsplit_once("__")
        .map(|(_, suffix)| suffix)
        .or_else(|| name.split_once('@').map(|(_, suffix)| suffix));
    if backend == "s3" {
        return match values
            .get("provider")
            .map(String::as_str)
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str()
        {
            "cloudflare" => "Cloudflare R2".into(),
            "aws" => "Amazon S3".into(),
            "minio" => "MinIO".into(),
            "wasabi" => "Wasabi".into(),
            _ => suffix.unwrap_or("S3").into(),
        };
    }
    suffix.unwrap_or(backend).into()
}

fn discover_state() -> Option<AppState> {
    let rclone = find_rclone();
    let config = rclone_config_path(&rclone)?;
    let text = fs::read_to_string(&config).ok()?;
    let settings = mount_settings();
    let usage = usage_values();
    let mounted = mounted_remote_names();
    let mut sections: Vec<(usize, String, HashMap<String, String>)> = Vec::new();
    let mut current: Option<(usize, String, HashMap<String, String>)> = None;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            if let Some(section) = current.take() {
                sections.push(section);
            }
            current = Some((
                sections.len(),
                line[1..line.len() - 1].trim().to_string(),
                HashMap::new(),
            ));
        } else if let (Some((_, _, values)), Some((key, value))) =
            (current.as_mut(), line.split_once('='))
        {
            let key = key.trim();
            if matches!(key, "type" | "provider" | "vendor") {
                values.insert(key.into(), value.trim().into());
            }
        }
    }
    if let Some(section) = current {
        sections.push(section);
    }
    let mut ordered: Vec<(Option<i64>, usize, Remote)> = sections
        .into_iter()
        .filter_map(|(index, name, values)| {
            let backend = values.get("type")?.to_ascii_lowercase();
            let remote_settings = settings.get(&name).cloned().unwrap_or_default();
            if !remote_settings.enabled {
                return None;
            }
            let alias = name
                .rsplit_once("__")
                .map(|(alias, _)| alias)
                .or_else(|| name.split_once('@').map(|(alias, _)| alias))
                .unwrap_or(&name)
                .trim()
                .to_string();
            let label = provider_label(&name, &backend, &values);
            let (used_bytes, total_bytes) = usage.get(&name).copied().unwrap_or((None, None));
            let is_mounted = remote_mount_path(&name)
                .map(|path| path_is_mounted(&path))
                .unwrap_or_else(|_| {
                    mounted
                        .iter()
                        .any(|candidate| candidate == &mount_slug(&name))
                });
            Some((
                remote_settings.order,
                index,
                Remote {
                    id: name,
                    name: alias,
                    provider: backend,
                    provider_label: label,
                    mounted: is_mounted,
                    used_bytes,
                    total_bytes,
                },
            ))
        })
        .collect();
    let registration_order = ordered
        .iter()
        .map(|(_, _, remote)| remote.id.clone())
        .collect();
    ordered
        .sort_by_key(|(order, index, _)| (order.is_none(), order.unwrap_or(*index as i64), *index));
    Some(AppState {
        remotes: StdRwLock::new(ordered.into_iter().map(|(_, _, remote)| remote).collect()),
        registration_order: StdRwLock::new(registration_order),
        folders: RwLock::new(HashMap::new()),
        folder_refreshes: Mutex::new(std::collections::HashSet::new()),
        selections: RwLock::new(HashMap::new()),
        rclone: Some(rclone),
        rclone_config: Some(config),
        browser_state: RwLock::new((String::new(), String::new())),
        instance_listener: None,
        window_anchor: Mutex::new(WindowAnchor::default()),
        last_layout: Mutex::new(None),
        last_tray_activate: Mutex::new(None),
        last_focused_window: Mutex::new("main".into()),
        window_stack_shown: Mutex::new(false),
        rclone_log: Mutex::new(VecDeque::new()),
        mount_pids: Mutex::new(HashMap::new()),
        listing_generation: Arc::new(AtomicU64::new(0)),
    })
}

pub(crate) fn remote_is_configured(remote_id: &str, provider: &str) -> bool {
    let Some(config) = default_rclone_config_path() else {
        return true;
    };
    let Ok(values) = read_rclone_section(&config, remote_id) else {
        return true;
    };
    platform::remote_section_is_configured(provider, &values)
}

#[tauri::command]
async fn list_remotes(state: tauri::State<'_, AppState>) -> Result<Vec<Remote>, String> {
    let remotes = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .cloned()
        .collect::<Vec<_>>();
    tauri::async_runtime::spawn_blocking(move || {
        remotes
            .into_iter()
            .filter(|remote| remote_is_configured(&remote.id, &remote.provider))
            .map(|mut remote| {
                remote.mounted = remote_mount_path(&remote.id)
                    .map(|path| path_is_mounted(&path))
                    .unwrap_or(false);
                remote
            })
            .collect()
    })
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn refresh_remote_usage(
    remote_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<Remote, String> {
    let remote = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .find(|remote| remote.id == remote_id)
        .cloned()
        .ok_or("Remote not found")?;
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let source = remote_source(&remote_id);
    let output = tauri::async_runtime::spawn_blocking(move || {
        Command::new(rclone)
            .args(["--config"])
            .arg(config)
            .args(["about", &source, "--json"])
            .output()
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).map_err(|error| error.to_string())?;
    let used = value.get("used").and_then(|item| item.as_u64());
    let total = value.get("total").and_then(|item| item.as_u64());
    save_usage_value(&remote_id, used, total)?;
    let mut updated = remote;
    updated.mounted = remote_mount_path(&remote_id)
        .map(|path| path_is_mounted(&path))
        .unwrap_or(false);
    updated.used_bytes = used;
    updated.total_bytes = total;
    if let Ok(mut remotes) = state.remotes.write() {
        if let Some(item) = remotes.iter_mut().find(|item| item.id == remote_id) {
            item.used_bytes = used;
            item.total_bytes = total;
        }
    }
    Ok(updated)
}

#[tauri::command]
fn remote_registration_order(state: tauri::State<'_, AppState>) -> Vec<String> {
    state
        .registration_order
        .read()
        .map(|order| order.clone())
        .unwrap_or_default()
}

#[tauri::command]
fn auto_mount_remote_ids(state: tauri::State<'_, AppState>) -> Vec<String> {
    let defaults = app_settings();
    let settings = mount_settings();
    state
        .remotes
        .read()
        .map(|remotes| {
            remotes
                .iter()
                .filter(|remote| {
                    settings
                        .get(&remote.id)
                        .and_then(|value| value.auto_mount)
                        .unwrap_or(defaults.auto_mount)
                        && remote_mount_path(&remote.id)
                            .map(|path| !path_is_mounted(&path))
                            .unwrap_or(false)
                })
                .map(|remote| remote.id.clone())
                .collect()
        })
        .unwrap_or_default()
}

#[tauri::command]
fn reorder_remotes(order: Vec<String>, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let remotes = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?;
    let configured: std::collections::HashSet<&str> =
        remotes.iter().map(|remote| remote.id.as_str()).collect();
    let supplied: std::collections::HashSet<&str> = order.iter().map(String::as_str).collect();
    if order.len() != configured.len() || supplied != configured {
        return Err("Remote order does not match the configured remotes".into());
    }
    save_remote_order_file(&order)
}

#[tauri::command]
fn load_remote_config(
    remote_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<RemoteConfigData, String> {
    let remote = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .find(|remote| remote.id == remote_id)
        .cloned()
        .ok_or("Remote not found")?;
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    let raw = read_rclone_section(config, &remote.id)?;
    let keys = safe_rclone_keys(&remote.provider);
    let fields = keys
        .iter()
        .map(|key| {
            (
                (*key).to_string(),
                if secret_rclone_key(key) {
                    raw.get(*key)
                        .filter(|value| !value.is_empty())
                        .map(|_| SECRET_FIELD_MASK.into())
                        .unwrap_or_default()
                } else {
                    raw.get(*key).cloned().unwrap_or_default()
                },
            )
        })
        .collect();
    let mount = mount_settings().remove(&remote.id).unwrap_or_default();
    Ok(RemoteConfigData {
        id: remote.id,
        alias: remote.name,
        provider: remote.provider,
        provider_label: remote.provider_label,
        mount_path: mount.mount_path.unwrap_or_default(),
        remote_path: mount.remote_path,
        mount_flags: mount.mount_flags.join(" "),
        auto_mount: mount.auto_mount,
        fields,
        secret_fields: keys
            .iter()
            .filter(|key| secret_rclone_key(key))
            .map(|key| (*key).to_string())
            .collect(),
    })
}

fn obscure_secret(rclone: &str, value: &str) -> Result<String, String> {
    let output = Command::new(rclone)
        .args(["obscure", value])
        .output()
        .map_err(|error| error.to_string())?;
    let obscured = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if output.status.success() && !obscured.is_empty() {
        Ok(obscured)
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

fn refresh_remote_state(state: &AppState) -> Result<(), String> {
    if let Some(discovered) = discover_state() {
        *state
            .remotes
            .write()
            .map_err(|_| "Remote state is unavailable")? =
            discovered.remotes.into_inner().unwrap_or_default();
        *state
            .registration_order
            .write()
            .map_err(|_| "Remote state is unavailable")? = discovered
            .registration_order
            .into_inner()
            .unwrap_or_default();
    }
    Ok(())
}

fn rename_json_object_key(
    path: Option<PathBuf>,
    container: &str,
    old: &str,
    new: &str,
) -> Result<(), String> {
    let Some(path) = path else {
        return Ok(());
    };
    let Ok(text) = fs::read_to_string(&path) else {
        return Ok(());
    };
    let mut value: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| error.to_string())?;
    if let Some(values) = value
        .get_mut(container)
        .and_then(|item| item.as_object_mut())
    {
        if let Some(item) = values.remove(old) {
            values.insert(new.into(), item);
        }
    }
    let temporary = path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn rename_persistent_remote(old: &str, new: &str, display: &str) -> Result<(), String> {
    if old == new {
        return Ok(());
    }
    rename_json_object_key(offline_manifest_path(), "remotes", old, new)?;
    rename_json_object_key(
        home_relative(&[".local", "state", "mountlet", "usage-cache.json"]),
        "remotes",
        old,
        new,
    )?;
    let browser_path = home_relative(&[".local", "state", "mountlet", "browser.json"]);
    rename_json_object_key(browser_path.clone(), "paths", old, new)?;
    rename_json_object_key(browser_path, "selections", old, new)?;
    if let Some(root) = offline_root() {
        let source = root.join(mount_slug(old));
        let destination = root.join(mount_slug(new));
        if source.exists() && !destination.exists() {
            fs::rename(source, destination).map_err(|error| error.to_string())?;
        }
    }
    if let Some(database) = metadata_database().filter(|path| path.exists()) {
        let connection = rusqlite::Connection::open(database).map_err(|error| error.to_string())?;
        connection.execute_batch("CREATE TABLE IF NOT EXISTS index_state (remote_name TEXT PRIMARY KEY, complete INTEGER NOT NULL DEFAULT 0, completed_at REAL NOT NULL DEFAULT 0);").map_err(|error| error.to_string())?;
        connection
            .execute(
                "UPDATE entries SET remote_name = ?1, remote_display = ?2 WHERE remote_name = ?3",
                rusqlite::params![new, display, old],
            )
            .map_err(|error| error.to_string())?;
        connection
            .execute(
                "UPDATE index_state SET remote_name = ?1 WHERE remote_name = ?2",
                rusqlite::params![new, old],
            )
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn remove_persistent_remote(remote_id: &str) -> Result<(), String> {
    if let Some(database) = metadata_database().filter(|path| path.exists()) {
        let connection = rusqlite::Connection::open(database).map_err(|error| error.to_string())?;
        connection.execute_batch("CREATE TABLE IF NOT EXISTS index_state (remote_name TEXT PRIMARY KEY, complete INTEGER NOT NULL DEFAULT 0, completed_at REAL NOT NULL DEFAULT 0);").map_err(|error| error.to_string())?;
        connection
            .execute("DELETE FROM entries WHERE remote_name = ?1", [remote_id])
            .map_err(|error| error.to_string())?;
        connection
            .execute(
                "DELETE FROM index_state WHERE remote_name = ?1",
                [remote_id],
            )
            .map_err(|error| error.to_string())?;
    }
    let browser_path = home_relative(&[".local", "state", "mountlet", "browser.json"]);
    let Some(path) = browser_path else {
        return Ok(());
    };
    let Ok(text) = fs::read_to_string(&path) else {
        return Ok(());
    };
    let mut value: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| error.to_string())?;
    for container in ["paths", "selections"] {
        if let Some(values) = value
            .get_mut(container)
            .and_then(|item| item.as_object_mut())
        {
            values.remove(remote_id);
        }
    }
    let temporary = path.with_extension("tmp");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&value).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn append_rclone_log(state: &AppState, text: impl Into<String>) {
    if let Ok(mut log) = state.rclone_log.lock() {
        for line in text.into().lines() {
            log.push_back(line.to_string());
        }
        while log.len() > 2_000 {
            log.pop_front();
        }
    }
}

#[tauri::command]
fn rclone_output(state: tauri::State<'_, AppState>) -> String {
    state
        .rclone_log
        .lock()
        .map(|log| log.iter().cloned().collect::<Vec<_>>().join("\n"))
        .unwrap_or_default()
}

fn app_diagnostics(window: tauri::WebviewWindow, state: tauri::State<'_, AppState>) -> String {
    let rclone_version = state
        .rclone
        .as_ref()
        .map(|binary| format!("configured ({binary})"))
        .unwrap_or_else(|| "unavailable".into());
    let remote_count = state
        .remotes
        .read()
        .map(|remotes| remotes.len())
        .unwrap_or(0);
    let work_area = window
        .current_monitor()
        .ok()
        .flatten()
        .map(|monitor| {
            let position = monitor.position();
            let size = monitor.size();
            let area = work_area::resolve(work_area::WorkArea {
                x: position.x as f64,
                y: position.y as f64,
                width: size.width as f64,
                height: size.height as f64,
            });
            format!("{},{} {}x{}", area.x, area.y, area.width, area.height)
        })
        .unwrap_or_else(|| "unknown".into());
    format!(
        "Mountlet {} (Tauri)\nPlatform: {} {}\nRust target: {}\nrclone: {}\n{}\nRemotes: {}\nConfig: {}\nMountlet config: {}\nWork area: {}\n",
        env!("CARGO_PKG_VERSION"), env::consts::OS, env::consts::ARCH, env::consts::FAMILY,
        rclone_version, platform::mount_driver_about_line(), remote_count,
        state.rclone_config.as_ref().map(|path| path.display().to_string()).unwrap_or_else(|| "unavailable".into()),
        mountlet_config_dir().map(|path| path.display().to_string()).unwrap_or_else(|| "unavailable".into()), work_area,
    )
}

async fn run_on_main_async<T, F>(app: tauri::AppHandle, work: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> T + Send + 'static,
{
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.run_on_main_thread(move || {
        let _ = sender.send(work());
    })
    .map_err(|error| error.to_string())?;
    receiver
        .await
        .map_err(|_| "The UI thread did not run the request.".into())
}

#[tauri::command]
async fn app_version() -> String {
    if BUILD_CHANNEL == "production" && build_revision() == "local" {
        env!("CARGO_PKG_VERSION").into()
    } else {
        format!(
            "{} ({BUILD_CHANNEL} {})",
            env!("CARGO_PKG_VERSION"),
            build_revision()
        )
    }
}

#[tauri::command]
async fn show_startup_windows(app: tauri::AppHandle) -> Result<(), String> {
    let ui = app.clone();
    run_on_main_async(app, move || {
        if ui.get_webview_window("main").is_none() {
            return Err("main window is unavailable".into());
        }
        show_window_stack(&ui);
        schedule_startup_tray_layout(&ui);
        Ok(())
    })
    .await?
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
fn schedule_startup_tray_layout(app: &tauri::AppHandle) {
    let handle = app.clone();
    std::thread::spawn(move || {
        // Shell_NotifyIcon and NSStatusItem registration become observable
        // asynchronously. Retry from the native event loop and reapply the
        // cached frontend layout as soon as the real tray rectangle is present.
        for delay in [50, 150, 300, 600] {
            std::thread::sleep(Duration::from_millis(delay));
            let dispatch = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                seed_tray_anchor_from_os(&dispatch);
                relayout_from_cache(&dispatch);
            });
        }
    });
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn schedule_startup_tray_layout(_app: &tauri::AppHandle) {}

#[tauri::command]
fn startup_smoke_enabled() -> bool {
    env::var_os("MOUNTLET_STARTUP_SMOKE").is_some()
}

#[tauri::command]
fn complete_startup_smoke(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    checks: Vec<String>,
) -> Result<bool, String> {
    let Some(marker) = env::var_os("MOUNTLET_STARTUP_SMOKE") else {
        return Ok(false);
    };
    let main_window_ready = app.get_webview_window("main").is_some();
    let main_window_visible = app
        .get_webview_window("main")
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    let remote_count = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .len();
    #[cfg(target_os = "macos")]
    let expected_checks = [
        "app-version",
        "remote-state",
        "preferences",
        "settings",
        "settings-compatibility",
        "shortcuts",
        "license",
        "report-preview",
        "tray-menu",
        "add-remote-fields",
        "frontend-render",
        "startup-window-visible",
    ];
    #[cfg(not(target_os = "macos"))]
    let expected_checks = [
        "app-version",
        "remote-state",
        "preferences",
        "settings",
        "settings-compatibility",
        "shortcuts",
        "license",
        "report-preview",
        "tray-menu",
        "add-remote-fields",
        "frontend-render",
        "startup-window-visible",
        "desktop-hints",
        "prerequisites",
    ];
    if !expected_checks
        .iter()
        .all(|expected| checks.iter().any(|check| check == expected))
    {
        return Err(format!("Startup smoke checks are incomplete: {checks:?}"));
    }
    let settings = app_settings();
    let result = serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "buildId": build_id(),
        "frontendReady": true,
        "mainWindowReady": main_window_ready,
        "mainWindowVisible": main_window_visible,
        "remoteStateReady": true,
        "remoteCount": remote_count,
        "windowMode": settings.window_mode,
        "theme": settings.theme,
        "offlineRootReady": offline_root().is_some(),
        "behaviorChecks": checks,
        "behaviorComplete": true,
    });
    let marker = PathBuf::from(marker);
    if let Some(parent) = marker.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    fs::write(
        marker,
        serde_json::to_vec_pretty(&result).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(100));
        handle.exit(0);
    });
    Ok(true)
}

#[tauri::command]
async fn clipboard_text(app: tauri::AppHandle) -> Result<String, String> {
    #[cfg(target_os = "linux")]
    {
        run_on_main_async(app, || {
            Ok(gtk::Clipboard::get(&gdk::SELECTION_CLIPBOARD)
                .wait_for_text()
                .map(|text| text.to_string())
                .unwrap_or_default())
        })
        .await?
    }

    #[cfg(not(target_os = "linux"))]
    {
        let _ = app;
        tauri::async_runtime::spawn_blocking(read_clipboard)
            .await
            .map_err(|error| error.to_string())?
    }
}

#[cfg(not(target_os = "linux"))]
fn read_clipboard() -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("pbpaste")
            .output()
            .map_err(|error| format!("Could not read the clipboard: {error}"))?;
        if !output.status.success() {
            Err("Could not read the clipboard.".into())
        } else {
            String::from_utf8(output.stdout)
                .map_err(|error| format!("The clipboard does not contain text: {error}"))
        }
    }

    #[cfg(target_os = "windows")]
    {
        let output = Command::new("powershell")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Clipboard -Raw",
            ])
            .output()
            .map_err(|error| format!("Could not read the clipboard: {error}"))?;
        if !output.status.success() {
            Err("Could not read the clipboard.".into())
        } else {
            String::from_utf8(output.stdout)
                .map_err(|error| format!("The clipboard does not contain text: {error}"))
        }
    }
}

#[tauri::command]
async fn license_default_device_label() -> String {
    license::default_device_label()
}

fn redact_config(text: &str) -> String {
    text.lines()
        .map(|line| {
            let Some((key, _)) = line.split_once('=') else {
                return line.to_string();
            };
            if [
                "token", "pass", "password", "secret", "cookie", "key", "bearer", "otp", "trust",
            ]
            .iter()
            .any(|word| key.trim().to_ascii_lowercase().contains(word))
            {
                format!("{} = <redacted>", key.trim())
            } else {
                line.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn redact_sensitive_text(text: &str) -> String {
    use std::sync::OnceLock;
    static LICENSE: OnceLock<regex::Regex> = OnceLock::new();
    static ASSIGNMENT: OnceLock<regex::Regex> = OnceLock::new();
    static URL_SECRET: OnceLock<regex::Regex> = OnceLock::new();
    let value = LICENSE
        .get_or_init(|| regex::Regex::new(r"(?i)\bMNT-[A-Z0-9-]{8,}\b").unwrap())
        .replace_all(text, "MNT-[redacted]");
    let value = URL_SECRET
        .get_or_init(|| {
            regex::Regex::new(r"(?i)([?&](?:token|key|secret|password|code)=)[^&\s]+").unwrap()
        })
        .replace_all(&value, "$1[redacted]");
    let value = ASSIGNMENT.get_or_init(|| regex::Regex::new(r#"(?i)\b(token|secret|password|pass|access_key|secret_key|client_secret)\b([\"'\s:=]+)([^\"'\s,;]+)"#).unwrap())
        .replace_all(&value, "$1$2[redacted]");
    redact_config(&value)
}

fn runtime_log_path() -> Result<PathBuf, String> {
    Ok(mountlet_state_dir()
        .ok_or("Mountlet state directory is unavailable")?
        .join("tauri-runtime.log"))
}

fn begin_runtime_log_session() {
    if let Ok(path) = runtime_log_path() {
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
            let timestamp = SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let _ = writeln!(
                file,
                "\n{RUNTIME_SESSION_MARKER}: Mountlet {} at {timestamp}\n",
                env!("CARGO_PKG_VERSION")
            );
        }
    }
}

fn current_runtime_session(text: &str) -> &str {
    text.rfind(RUNTIME_SESSION_MARKER)
        .map(|start| &text[start..])
        .unwrap_or("")
}

fn previous_runtime_session(text: &str) -> &str {
    let current_start = text.rfind(RUNTIME_SESSION_MARKER).unwrap_or(text.len());
    let previous = &text[..current_start];
    previous
        .rfind(RUNTIME_SESSION_MARKER)
        .map(|start| &previous[start..])
        .unwrap_or("")
}

fn crash_fingerprint(text: &str) -> String {
    let normalized = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    hex::encode(Sha256::digest(normalized.as_bytes()))
}

#[tauri::command]
fn unreported_crash() -> Result<String, String> {
    let text = fs::read_to_string(runtime_log_path()?).unwrap_or_default();
    let previous = previous_runtime_session(&text);
    let Some(marker) = previous.rfind("Unhandled Rust panic") else {
        return Ok(String::new());
    };
    if previous
        .rfind("Mountlet shutdown cleanly")
        .is_some_and(|clean| clean > marker)
    {
        return Ok(String::new());
    }
    let crash = previous[marker..]
        .chars()
        .rev()
        .take(18_000)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    let reported = mountlet_state_dir()
        .and_then(|path| fs::read_to_string(path.join("last-crash-report.json")).ok())
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .and_then(|value| {
            value
                .get("fingerprint")
                .and_then(|item| item.as_str())
                .map(str::to_string)
        });
    Ok(if reported.as_deref() == Some(&crash_fingerprint(&crash)) {
        String::new()
    } else {
        crash
    })
}

#[tauri::command]
fn mark_crash_reported(crash: String) -> Result<(), String> {
    if crash.is_empty() {
        return Ok(());
    }
    let path = mountlet_state_dir()
        .ok_or("Mountlet state directory is unavailable")?
        .join("last-crash-report.json");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    fs::write(path, serde_json::to_vec(&serde_json::json!({"fingerprint":crash_fingerprint(&crash),"reportedAt":SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()})).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn submit_bug_report(
    kind: String,
    message: String,
    contact: String,
    include_logs: bool,
    crash: String,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let diagnostics = app_diagnostics(window, state.clone());
    let rclone = if include_logs {
        rclone_output(state)
    } else {
        String::new()
    };
    tauri::async_runtime::spawn_blocking(move || {
        let payload = bug_report_payload(
            &kind,
            &message,
            &contact,
            include_logs,
            &crash,
            &diagnostics,
            &rclone,
        );
        let endpoint = env::var("MOUNTLET_REPORT_API_URL")
            .unwrap_or_else(|_| "https://mountlet.app/api/report".into());
        let response = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(|error| error.to_string())?
            .post(endpoint)
            .header(
                "User-Agent",
                format!(
                    "Mountlet/{} (+https://mountlet.app)",
                    env!("CARGO_PKG_VERSION")
                ),
            )
            .json(&payload)
            .send()
            .map_err(|error| format!("Could not reach report server: {error}"))?;
        let status = response.status();
        let value: serde_json::Value = response
            .json()
            .map_err(|_| "Report server returned an invalid response.")?;
        if !status.is_success()
            || !value
                .get("ok")
                .and_then(|item| item.as_bool())
                .unwrap_or(false)
        {
            return Err(value
                .get("error")
                .and_then(|item| item.as_str())
                .unwrap_or("Report was not accepted.")
                .to_string());
        }
        if kind == "crash" {
            mark_crash_reported(crash)?;
        }
        Ok(value
            .get("issueUrl")
            .or_else(|| value.get("url"))
            .and_then(|item| item.as_str())
            .unwrap_or("")
            .to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

fn bug_report_payload(
    kind: &str,
    message: &str,
    contact: &str,
    include_logs: bool,
    crash: &str,
    diagnostics: &str,
    rclone: &str,
) -> serde_json::Value {
    let runtime = if crash.is_empty() {
        let session = fs::read_to_string(runtime_log_path().unwrap_or_default())
            .map(|text| current_runtime_session(&text).to_string())
            .unwrap_or_default();
        format!("{diagnostics}\nCurrent runtime session:\n{session}")
    } else {
        crash.into()
    };
    serde_json::json!({
        "kind": if kind == "crash" { "crash" } else { "bug" },
        "message": redact_sensitive_text(message.trim()).chars().take(8000).collect::<String>(),
        "contact": contact.trim().chars().take(240).collect::<String>(),
        "metadata": {"appVersion":env!("CARGO_PKG_VERSION"),"buildChannel":BUILD_CHANNEL,"buildId":build_id(),"platform":format!("{} {}",env::consts::OS,env::consts::ARCH),"rust":true,"node":env::var("HOSTNAME").unwrap_or_default(),"frozen":true},
        "logs": if include_logs { serde_json::json!({"runtime":redact_sensitive_text(&runtime.chars().rev().take(18_000).collect::<String>().chars().rev().collect::<String>()),"rclone":redact_sensitive_text(&rclone.chars().rev().take(18_000).collect::<String>().chars().rev().collect::<String>())}) } else { serde_json::json!({}) }
    })
}

#[tauri::command]
fn bug_report_preview(
    kind: String,
    message: String,
    contact: String,
    include_logs: bool,
    crash: String,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    serde_json::to_string_pretty(&bug_report_payload(
        &kind,
        &message,
        &contact,
        include_logs,
        &crash,
        &app_diagnostics(window, state.clone()),
        &rclone_output(state),
    ))
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn license_status() -> Result<license::Status, String> {
    // Local status evaluation can touch replicated trial files. Keep it off the
    // WebView IPC thread so a slow disk or leftover mount cannot freeze About,
    // Buy license, or other commands.
    tauri::async_runtime::spawn_blocking(license::status)
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn activate_license(key: String, device_label: String) -> Result<license::Status, String> {
    tauri::async_runtime::spawn_blocking(move || license::activate(&key, &device_label))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn license_devices() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(license::devices)
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn deactivate_license_device(device_id: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || license::deactivate(&device_id))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
fn notification_history() -> Result<Vec<NoticeView>, String> {
    let (_, state) = load_notice_state()?;
    let seen = state.get("seen").and_then(|value| value.as_object());
    let deleted = state.get("deleted").and_then(|value| value.as_object());
    let Some(history) = state.get("history").and_then(|value| value.as_object()) else {
        return Ok(Vec::new());
    };
    let mut notices = history
        .iter()
        .filter_map(|(key, raw)| {
            if deleted.is_some_and(|values| values.contains_key(key)) {
                return None;
            }
            let level = raw
                .get("level")
                .and_then(|value| value.as_str())
                .unwrap_or("info");
            let notice_type = raw
                .get("type")
                .and_then(|value| value.as_str())
                .unwrap_or("general");
            let archived = raw
                .get("archived")
                .and_then(|value| value.as_bool())
                .unwrap_or(false);
            let critical = level == "critical" || notice_type == "price";
            Some(NoticeView {
                key: key.clone(),
                title: raw.get("title")?.as_str()?.to_string(),
                message: raw.get("message")?.as_str()?.to_string(),
                level: level.to_string(),
                url: raw
                    .get("url")
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string(),
                seen: archived || seen.is_some_and(|values| values.contains_key(key)),
                deletable: !critical,
                received_at: raw
                    .get("receivedAt")
                    .and_then(|value| value.as_i64())
                    .unwrap_or(0),
                updated_at: raw
                    .get("updatedAt")
                    .or_else(|| raw.get("updated_at"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string(),
                critical,
                archived,
            })
        })
        .collect::<Vec<_>>();
    notices.sort_by_key(|notice| std::cmp::Reverse(notice.received_at));
    Ok(notices)
}

#[tauri::command]
async fn poll_notifications() -> Result<Vec<NoticeView>, String> {
    tauri::async_runtime::spawn_blocking(fetch_and_remember_notices)
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
fn mark_notification_seen(key: String) -> Result<(), String> {
    mark_notifications_seen(vec![key])
}

#[tauri::command]
fn mark_notifications_seen(keys: Vec<String>) -> Result<(), String> {
    let (path, mut state) = load_notice_state()?;
    let seen = state
        .as_object_mut()
        .ok_or("Invalid notice state")?
        .entry("seen")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid seen notice state")?;
    let now = SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    for key in keys {
        seen.insert(key, serde_json::json!(now));
    }
    save_notice_state(&path, &state)
}

#[tauri::command]
fn delete_notification(key: String) -> Result<bool, String> {
    let history = notification_history()?;
    let Some(notice) = history.iter().find(|notice| notice.key == key) else {
        return Ok(false);
    };
    if !notice.deletable {
        return Ok(false);
    }
    let (path, mut state) = load_notice_state()?;
    state
        .as_object_mut()
        .ok_or("Invalid notice state")?
        .entry("deleted")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("Invalid deleted notice state")?
        .insert(
            key.clone(),
            serde_json::json!(SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()),
        );
    if let Some(seen) = state
        .get_mut("seen")
        .and_then(|value| value.as_object_mut())
    {
        seen.remove(&key);
    }
    save_notice_state(&path, &state)?;
    Ok(true)
}

#[tauri::command]
fn save_remote_config(
    request: SaveRemoteConfigRequest,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let remote = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .find(|remote| remote.id == request.remote_id)
        .cloned()
        .ok_or("Remote not found")?;
    let alias = request.alias.trim();
    if alias.is_empty()
        || alias
            .chars()
            .any(|character| matches!(character, ':' | '@' | '/' | '\\' | '\n' | '\r'))
    {
        return Err("Use a name without ':', '@', line breaks, or path separators.".into());
    }
    let new_id = remote
        .id
        .rsplit_once("__")
        .map(|(_, suffix)| format!("{alias}__{suffix}"))
        .unwrap_or_else(|| alias.into());
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    if new_id != remote.id
        && read_rclone_section(config, &new_id)
            .map(|values| !values.is_empty())
            .unwrap_or(false)
    {
        return Err(format!("{new_id} already exists in rclone.conf."));
    }
    let old_values = read_rclone_section(config, &remote.id)?;
    let mut updates = HashMap::new();
    for key in safe_rclone_keys(&remote.provider) {
        let value = request.fields.get(*key).cloned().unwrap_or_default();
        if secret_rclone_key(key) && (value.is_empty() || value == SECRET_FIELD_MASK) {
            continue;
        }
        updates.insert(
            (*key).into(),
            if secret_rclone_key(key) {
                obscure_secret(
                    state.rclone.as_deref().ok_or("rclone is unavailable")?,
                    &value,
                )?
            } else {
                value
            },
        );
    }
    if matches!(remote.provider.as_str(), "drive" | "gphotos") {
        let account = updates
            .get("mountlet_google_account")
            .cloned()
            .or_else(|| old_values.get("mountlet_google_account").cloned())
            .unwrap_or_default();
        updates.insert(
            "auth_url".into(),
            if account.is_empty() {
                String::new()
            } else {
                format!(
                    "https://accounts.google.com/o/oauth2/auth?login_hint={}",
                    url_query_value(&account)
                )
            },
        );
    }
    write_rclone_section(config, &remote.id, &new_id, &updates)?;
    let previous_mount = mount_settings().remove(&remote.id).unwrap_or_default();
    update_mount_section(
        &remote.id,
        &new_id,
        &RemoteMountSettings {
            mount_path: (!request.mount_path.trim().is_empty())
                .then(|| request.mount_path.trim().into()),
            remote_path: request.remote_path.trim().trim_matches('/').into(),
            mount_flags: split_shell_words(&request.mount_flags),
            auto_mount: request.auto_mount,
            enabled: previous_mount.enabled,
            order: previous_mount.order,
        },
    )?;
    rename_persistent_remote(&remote.id, &new_id, alias)?;
    refresh_remote_state(&state)?;
    Ok(new_id)
}

#[tauri::command]
fn create_remote(
    request: CreateRemoteRequest,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let alias = request.alias.trim();
    if alias.is_empty()
        || alias
            .chars()
            .any(|character| matches!(character, ':' | '@' | '/' | '\\' | '\n' | '\r'))
    {
        return Err("Use a name without ':', '@', line breaks, or path separators.".into());
    }
    let suffix = request.provider_label.trim();
    let remote_id = if suffix.is_empty() {
        alias.into()
    } else {
        format!("{alias}__{suffix}")
    };
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    if let Some(parent) = config.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    if !config.exists() {
        fs::write(config, "").map_err(|error| error.to_string())?;
    }
    if !read_rclone_section(config, &remote_id)?.is_empty() {
        return Err(format!("{remote_id} already exists in rclone.conf."));
    }
    let rclone = state.rclone.as_deref().ok_or("rclone is unavailable")?;
    let mut fields = request.fields.clone();
    if let Some(source) = fields.remove("reuse_client_from") {
        if let Ok(section) = read_rclone_section(config, source.trim()) {
            if let (Some(client_id), Some(client_secret)) =
                (section.get("client_id"), section.get("client_secret"))
            {
                fields
                    .entry("client_id".into())
                    .or_insert_with(|| client_id.clone());
                fields
                    .entry("client_secret".into())
                    .or_insert_with(|| client_secret.clone());
            }
        }
    }
    let mut command = Command::new(rclone);
    command.args(["--config"]).arg(config).args([
        "config",
        "create",
        &remote_id,
        &request.provider,
    ]);
    for (key, value) in &fields {
        if !value.trim().is_empty() {
            command.arg(key).arg(value);
        }
    }
    if matches!(
        request.provider.as_str(),
        "drive" | "gphotos" | "dropbox" | "onedrive" | "box" | "pcloud"
    ) && !fields.contains_key("config_is_local")
    {
        command.args(["config_is_local", "true"]);
    }
    command.arg("--non-interactive");
    let output = command.output().map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    if !request.google_account.trim().is_empty()
        && matches!(request.provider.as_str(), "drive" | "gphotos")
    {
        let account = request.google_account.trim();
        write_rclone_section(
            config,
            &remote_id,
            &remote_id,
            &HashMap::from([
                ("mountlet_google_account".into(), account.into()),
                (
                    "auth_url".into(),
                    format!(
                        "https://accounts.google.com/o/oauth2/auth?login_hint={}",
                        url_query_value(account)
                    ),
                ),
            ]),
        )?;
    }
    update_mount_section(
        &remote_id,
        &remote_id,
        &RemoteMountSettings {
            remote_path: request.remote_path.trim().trim_matches('/').into(),
            ..RemoteMountSettings::default()
        },
    )?;
    refresh_remote_state(&state)?;
    Ok(remote_id)
}

#[tauri::command]
fn delete_remote(remote_id: String, state: tauri::State<'_, AppState>) -> Result<(), String> {
    if remote_mount_path(&remote_id)
        .map(|path| path_is_mounted(&path))
        .unwrap_or(false)
    {
        return Err("Unmount this remote before deleting it.".into());
    }
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    let source = fs::read_to_string(config).map_err(|error| error.to_string())?;
    let mut output = Vec::new();
    let mut current = false;
    for raw in source.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            current = line[1..line.len() - 1] == remote_id;
        }
        if !current {
            output.push(raw);
        }
    }
    let temporary = config.with_extension("conf.tmp");
    fs::write(&temporary, output.join("\n")).map_err(|error| error.to_string())?;
    fs::rename(temporary, config).map_err(|error| error.to_string())?;
    remove_mount_section(&remote_id)?;
    remove_persistent_remote(&remote_id)?;
    refresh_remote_state(&state)
}

#[tauri::command]
fn load_preferences() -> UiPreferences {
    ui_preferences()
}

#[tauri::command]
fn load_app_settings() -> AppSettings {
    app_settings()
}

#[tauri::command]
fn save_app_settings(settings: AppSettings) -> Result<AppSettings, String> {
    let mut normalized = settings;
    let stored_mode = if normalized.window_mode == "single" {
        "single"
    } else {
        "multiple"
    };
    normalized.window_mode = stored_mode.into();
    if !matches!(normalized.theme.as_str(), "system" | "light" | "dark") {
        normalized.theme = "system".into();
    }
    normalized.zoom_steps = normalized.zoom_steps.clamp(-4, 6);
    normalized.auto_mount_delay = normalized.auto_mount_delay.max(0.0);
    normalized.remote_check_interval = normalized.remote_check_interval.max(0.0);
    normalized.notice_check_interval = normalized.notice_check_interval.max(0.0);
    let previous = app_settings();
    save_app_settings_file(&normalized)?;
    if previous.start_at_login != normalized.start_at_login {
        apply_start_at_login(normalized.start_at_login)?;
    }
    normalized.window_mode = platform::effective_window_mode(&normalized.window_mode);
    Ok(normalized)
}

#[tauri::command]
fn load_shortcuts() -> HashMap<String, Vec<String>> {
    shortcut_preferences()
}

#[tauri::command]
fn open_config_file(kind: String, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let path = match kind.as_str() {
        "rclone" => state.rclone_config.clone(),
        "app" => app_config_path(),
        "mounts" => mountlet_config_dir().map(|path| path.join("mounts.toml")),
        _ => None,
    }
    .ok_or("Configuration file is unavailable")?;
    open_local_path(&path)
}

#[tauri::command]
fn open_config_backup_folder() -> Result<(), String> {
    let path = mountlet_config_dir()
        .ok_or("Mountlet configuration directory is unavailable")?
        .join("backups");
    fs::create_dir_all(&path).map_err(|error| error.to_string())?;
    open_local_path(&path)
}

fn user_path(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value.trim());
    if path.is_absolute() {
        return Ok(path);
    }
    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .ok_or("Home directory is unavailable")?;
    Ok(if value.trim().starts_with("~/") {
        home.join(value.trim().trim_start_matches("~/"))
    } else {
        home.join(path)
    })
}

fn config_fingerprint(config: &Path) -> String {
    let mut digest = Sha256::new();
    let mut paths = [
        Some(config.to_path_buf()),
        app_config_path(),
        mountlet_config_dir().map(|root| root.join("mounts.toml")),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>();
    if let Some(parent) = config.parent() {
        paths.extend(
            fs::read_dir(parent)
                .into_iter()
                .flatten()
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| {
                            name.starts_with("client_secret") && name.ends_with(".json")
                        })
                }),
        );
    }
    paths.sort();
    paths.dedup();
    for path in paths {
        digest.update(path.to_string_lossy().as_bytes());
        if let Ok(bytes) = fs::read(&path) {
            digest.update(bytes);
        }
    }
    hex::encode(digest.finalize())
}

fn offline_record_changed(record: &serde_json::Value, metadata: &fs::Metadata) -> bool {
    let size_changed = record
        .get("local_size")
        .and_then(|value| value.as_u64())
        .is_some_and(|value| value != metadata.len());
    let modified = metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or(0);
    size_changed
        || record
            .get("local_mtime_ns")
            .and_then(|value| value.as_u64())
            .is_some_and(|value| value != modified)
}

fn config_sync_state() -> serde_json::Value {
    mountlet_state_dir()
        .and_then(|root| fs::read_to_string(root.join("config-sync-state.json")).ok())
        .and_then(|value| serde_json::from_str(&value).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

fn save_config_sync_state(state: &serde_json::Value) -> Result<(), String> {
    let path = mountlet_state_dir()
        .ok_or("Mountlet state directory is unavailable")?
        .join("config-sync-state.json");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    fs::write(
        path,
        serde_json::to_vec(state).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())
}

fn record_config_sync(config: &Path, remote_signature: Option<&str>) -> Result<(), String> {
    let mut state = serde_json::json!({"fingerprint":config_fingerprint(config)});
    if let Some(signature) = remote_signature {
        state["remote_signature"] = serde_json::json!(signature);
    }
    save_config_sync_state(&state)
}

fn remote_config_signature(rclone: &str, config: &Path, source: &str) -> Result<String, String> {
    let output = Command::new(rclone)
        .args(["--config"])
        .arg(config)
        .args(["lsjson", source, "--stat", "--hash"])
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "The synced configuration metadata is invalid.".to_string())?;
    Ok(serde_json::json!({
        "size": value.get("Size"),
        "modified": value.get("ModTime"),
        "hashes": value.get("Hashes"),
    })
    .to_string())
}

fn config_fingerprint_changed(recorded: Option<&str>, current: &str) -> bool {
    recorded.is_none_or(|value| value != current)
}

#[tauri::command]
async fn config_sync_status(state: tauri::State<'_, AppState>) -> Result<ConfigSyncStatus, String> {
    let settings = app_settings();
    if settings.config_sync_remote.is_empty() || settings.config_sync_path.is_empty() {
        return Ok(ConfigSyncStatus::default());
    }
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let saved = config_sync_state();
    let local_changed = config_fingerprint_changed(
        saved.get("fingerprint").and_then(|value| value.as_str()),
        &config_fingerprint(&config),
    );
    let previous_remote = saved
        .get("remote_signature")
        .and_then(|value| value.as_str())
        .map(str::to_string);
    let source = format!(
        "{}:{}",
        settings.config_sync_remote,
        settings.config_sync_path.trim_start_matches('/')
    );
    let remote_signature = tauri::async_runtime::spawn_blocking(move || {
        remote_config_signature(&rclone, &config, &source).ok()
    })
    .await
    .map_err(|error| error.to_string())?;
    let remote_changed = match (previous_remote.as_deref(), remote_signature.as_deref()) {
        (Some(previous), Some(current)) => previous != current,
        _ => false,
    };
    if previous_remote.is_none() {
        if let Some(signature) = remote_signature {
            let mut initialized = saved;
            initialized["remote_signature"] = serde_json::json!(signature);
            let _ = save_config_sync_state(&initialized);
        }
    }
    Ok(ConfigSyncStatus {
        local_changed,
        remote_changed,
    })
}

#[tauri::command]
fn export_config_bundle(
    destination: String,
    password: String,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    config_bundle::export(&user_path(&destination)?, config, &password, false)
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn import_config_bundle(
    source: String,
    password: String,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    let backup = config_bundle::import(&user_path(&source)?, config, &password)?;
    refresh_remote_state(&state)?;
    Ok(backup
        .map(|path| path.display().to_string())
        .unwrap_or_default())
}

#[tauri::command]
async fn push_config_sync(
    password: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let settings = app_settings();
    if settings.config_sync_remote.is_empty() || settings.config_sync_path.is_empty() {
        return Err("Set a config sync remote and path in App configuration first.".into());
    }
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?
        .clone();
    let rclone = state
        .rclone
        .as_ref()
        .ok_or("rclone is unavailable")?
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        let temporary = env::temp_dir().join(format!(
            "mountlet-config-sync-{}.mountlet",
            std::process::id()
        ));
        config_bundle::export(&temporary, &config, &password, false)?;
        let destination = format!(
            "{}:{}",
            settings.config_sync_remote,
            settings.config_sync_path.trim_start_matches('/')
        );
        let output = Command::new(&rclone)
            .args(["--config"])
            .arg(&config)
            .args(["copyto"])
            .arg(&temporary)
            .arg(&destination)
            .output()
            .map_err(|error| error.to_string())?;
        let _ = fs::remove_file(temporary);
        if output.status.success() {
            let signature = remote_config_signature(&rclone, &config, &destination).ok();
            record_config_sync(&config, signature.as_deref())?;
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
        }
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn pull_config_sync(password: String, state: tauri::State<'_, AppState>) -> Result<String, String> {
    let settings = app_settings();
    if settings.config_sync_remote.is_empty() || settings.config_sync_path.is_empty() {
        return Err("Set a config sync remote and path in App configuration first.".into());
    }
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?
        .clone();
    let rclone = state.rclone.as_ref().ok_or("rclone is unavailable")?;
    let temporary = env::temp_dir().join(format!(
        "mountlet-config-sync-{}.mountlet",
        std::process::id()
    ));
    let source = format!(
        "{}:{}",
        settings.config_sync_remote,
        settings.config_sync_path.trim_start_matches('/')
    );
    let output = Command::new(rclone)
        .args(["--config"])
        .arg(&config)
        .args(["copyto"])
        .arg(&source)
        .arg(&temporary)
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let backup = config_bundle::import(&temporary, &config, &password);
    let _ = fs::remove_file(temporary);
    let backup = backup?;
    refresh_remote_state(&state)?;
    let signature = remote_config_signature(rclone, &config, &source).ok();
    record_config_sync(&config, signature.as_deref())?;
    Ok(backup
        .map(|path| path.display().to_string())
        .unwrap_or_default())
}

#[tauri::command]
async fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("Only web links can be opened".into());
    }
    tauri::async_runtime::spawn_blocking(move || open_external_target(&url))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    mark_clean_shutdown();
    app.exit(0);
}

#[tauri::command]
fn load_browser_memory() -> serde_json::Value {
    browser_memory()
}

#[tauri::command]
fn persist_browser_memory(memory: serde_json::Value) -> Result<serde_json::Value, String> {
    persist_browser_memory_value(memory)
}

#[tauri::command]
async fn search_index(request: SearchRequest) -> Result<Vec<SearchEntry>, String> {
    tauri::async_runtime::spawn_blocking(move || search_metadata(&request))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn list_folder(
    request: FolderRequest,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<FolderSnapshot, String> {
    let key = (request.remote_id.clone(), request.path.clone());
    let cached = state.folders.read().await.get(&key).cloned();
    let mut entries = if let Some(entries) = cached {
        entries
    } else {
        let remote_id = request.remote_id.clone();
        let path = request.path.clone();
        tauri::async_runtime::spawn_blocking(move || indexed_folder(&remote_id, &path))
            .await
            .map_err(|error| error.to_string())??
    };
    // Folder snapshots cache remote metadata, not local availability. External
    // editors can close and trigger a repaint long before the next remote
    // listing, so always derive badges from the manifest and actual files.
    let records = offline_records(&request.remote_id);
    let local_root = offline_root().map(|root| root.join(mount_slug(&request.remote_id)));
    for entry in &mut entries {
        entry.cache = cache_state(&records, local_root.as_deref(), &entry.path, entry.is_dir);
    }
    let cached_file_bytes: u64 = entries
        .iter()
        .filter(|entry| !entry.is_dir)
        .map(|entry| entry.size)
        .sum();
    if !state.folders.read().await.contains_key(&key) {
        state
            .folders
            .write()
            .await
            .insert(key.clone(), entries.clone());
    }
    let should_refresh = state
        .folder_refreshes
        .lock()
        .map(|mut pending| pending.insert(key.clone()))
        .unwrap_or(false);
    if should_refresh {
        if let (Some(rclone), Some(config)) = (state.rclone.clone(), state.rclone_config.clone()) {
            let generation = state.listing_generation.fetch_add(1, Ordering::SeqCst) + 1;
            let listing_generation = state.listing_generation.clone();
            let request_copy = request.clone();
            let key_copy = key.clone();
            let remote = state.remotes.read().ok().and_then(|remotes| {
                remotes
                    .iter()
                    .find(|remote| remote.id == request.remote_id)
                    .cloned()
            });
            tauri::async_runtime::spawn(async move {
                let state_handle = app.state::<AppState>();
                let remote_id = request_copy.remote_id.clone();
                let path = request_copy.path.clone();
                let listed = tauri::async_runtime::spawn_blocking(move || {
                    list_rclone_folder(
                        &rclone,
                        &config,
                        &remote_id,
                        &path,
                        generation,
                        &listing_generation,
                    )
                })
                .await;
                let refreshed = if let Ok(Ok(listed)) = listed {
                    let refreshed_file_bytes: u64 = listed
                        .iter()
                        .filter(|entry| !entry.is_dir)
                        .map(|entry| entry.size)
                        .sum();
                    state_handle
                        .folders
                        .write()
                        .await
                        .insert(key_copy.clone(), listed.clone());
                    if let Some(remote) = remote {
                        let remote_id = request_copy.remote_id.clone();
                        let path = request_copy.path.clone();
                        let display = remote.name;
                        let provider = remote.provider;
                        let copy = listed.clone();
                        let _ = tauri::async_runtime::spawn_blocking(move || {
                            store_indexed_folder(&remote_id, &display, &provider, &path, &copy)
                        })
                        .await;
                    }
                    let _ = app.emit("folder-updated", request_copy.clone());
                    if refreshed_file_bytes != cached_file_bytes {
                        let _ = app.emit("remote-usage-dirty", request_copy.remote_id.clone());
                    }
                    true
                } else {
                    false
                };
                if !refreshed {
                    if let Ok(mut pending) = state_handle.folder_refreshes.lock() {
                        pending.remove(&key_copy);
                    }
                }
            });
        } else if let Ok(mut pending) = state.folder_refreshes.lock() {
            pending.remove(&key);
        }
    }
    let selected_index = state
        .selections
        .read()
        .await
        .get(&key)
        .copied()
        .or_else(|| cached_selection_index(&request.remote_id, &request.path))
        .unwrap_or(0);
    Ok(FolderSnapshot {
        remote_id: request.remote_id,
        path: request.path,
        revision: 1,
        selected_index,
        entries,
    })
}

fn list_rclone_folder(
    rclone: &str,
    config: &Path,
    remote_id: &str,
    path: &str,
    generation: u64,
    current_generation: &AtomicU64,
) -> Result<Vec<FileEntry>, String> {
    #[derive(Deserialize)]
    #[serde(rename_all = "PascalCase")]
    struct RcloneEntry {
        name: String,
        path: String,
        is_dir: bool,
        #[serde(default)]
        size: i64,
        #[serde(default)]
        mod_time: String,
    }
    let relative = path.trim_matches('/');
    let parts = relative.split('/').collect::<Vec<_>>();
    let provider = rclone_section_value(remote_id, "type").unwrap_or_default();
    let date_prefix =
        if provider == "gphotos" && parts.len() == 3 && parts[..2] == ["media", "by-year"] {
            Some(parts[2].to_string())
        } else if provider == "gphotos"
            && parts.len() == 4
            && (parts[..2] == ["media", "by-month"] || parts[..2] == ["media", "by-day"])
        {
            Some(parts[3].to_string())
        } else {
            None
        };
    let listing_path = if date_prefix.is_some() {
        "media/all"
    } else {
        path
    };
    let target = checked_remote_path(remote_id, listing_path)?;
    let mut child = Command::new(rclone)
        .args(["--config"])
        .arg(config)
        .args(["lsjson", &target, "--no-mimetype"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut stdout_pipe = child.stdout.take().ok_or("Could not read rclone output")?;
    let mut stderr_pipe = child.stderr.take().ok_or("Could not read rclone errors")?;
    let stdout_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout_pipe.read_to_end(&mut bytes).map(|_| bytes)
    });
    let stderr_reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr_pipe.read_to_end(&mut bytes).map(|_| bytes)
    });
    let timeout = if provider == "gphotos"
        && (relative.starts_with("album/") || relative.starts_with("feature/"))
    {
        Duration::from_secs(30)
    } else {
        Duration::from_secs(60)
    };
    let started = Instant::now();
    let status = loop {
        if current_generation.load(Ordering::SeqCst) != generation {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Folder listing was superseded".into());
        }
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            break status;
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(if provider == "gphotos" {
                format!("Google Photos did not return this virtual folder within {} seconds. Try Google Photos in your browser instead.", timeout.as_secs())
            } else {
                format!(
                    "Folder listing timed out after {} seconds",
                    timeout.as_secs()
                )
            });
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let stdout = stdout_reader
        .join()
        .map_err(|_| "Could not join rclone output reader")?
        .map_err(|error| error.to_string())?;
    let stderr = stderr_reader
        .join()
        .map_err(|_| "Could not join rclone error reader")?
        .map_err(|error| error.to_string())?;
    if !status.success() {
        let detail = String::from_utf8_lossy(&stderr).trim().to_string();
        if provider == "gphotos"
            && (detail.to_lowercase().contains("resource_exhausted")
                || detail.to_lowercase().contains("quota exceeded"))
        {
            return Err("Google Photos API request quota is exhausted. Google resets the quota daily; try again after the reset or raise the Photos Library API quota for this OAuth client.".into());
        }
        if provider == "gphotos"
            && relative.eq_ignore_ascii_case("upload")
            && detail.to_lowercase().contains("directory not found")
        {
            return Ok(Vec::new());
        }
        return Err(detail);
    }
    let mut values: Vec<RcloneEntry> =
        serde_json::from_slice(&stdout).map_err(|error| error.to_string())?;
    values.sort_by(|left, right| {
        (!left.is_dir, left.name.to_lowercase()).cmp(&(!right.is_dir, right.name.to_lowercase()))
    });
    let records = offline_records(remote_id);
    let remote_offline_root = offline_root().map(|root| root.join(mount_slug(remote_id)));
    Ok(values
        .into_iter()
        .filter(|value| {
            date_prefix
                .as_ref()
                .map(|prefix| !value.is_dir && value.mod_time.starts_with(prefix))
                .unwrap_or(true)
        })
        .map(|value| {
            let entry_path = if date_prefix.is_some() {
                format!("media/all/{}", value.name)
            } else if path.is_empty() {
                value.path
            } else {
                format!("{path}/{}", value.name)
            };
            let cache = cache_state(
                &records,
                remote_offline_root.as_deref(),
                &entry_path,
                value.is_dir,
            );
            FileEntry {
                id: format!("{remote_id}:{entry_path}"),
                remote_id: remote_id.into(),
                path: entry_path,
                name: value.name,
                is_dir: value.is_dir,
                size: value.size.max(0) as u64,
                modified: value
                    .mod_time
                    .get(..16)
                    .unwrap_or(&value.mod_time)
                    .replace('T', " "),
                cache,
            }
        })
        .collect())
}

fn frame_overhead(window: &tauri::WebviewWindow) -> (f64, f64) {
    let scale = window.scale_factor().unwrap_or(1.0);
    let outer = window.outer_size().ok();
    let inner = window.inner_size().ok();
    match (outer, inner) {
        (Some(outer), Some(inner)) => (
            f64::from(outer.width.saturating_sub(inner.width)) / scale,
            f64::from(outer.height.saturating_sub(inner.height)) / scale,
        ),
        _ => (0.0, 0.0),
    }
}

fn logical_outer_rect(window: &tauri::WebviewWindow) -> Option<(f64, f64, f64, f64)> {
    let scale = window.scale_factor().ok()?;
    let position = window.outer_position().ok()?;
    let size = window.outer_size().ok()?;
    Some((
        f64::from(position.x) / scale,
        f64::from(position.y) / scale,
        f64::from(size.width) / scale,
        f64::from(size.height) / scale,
    ))
}

#[allow(dead_code)]
fn anchored_axis(old_start: f64, old_size: f64, new_size: f64, start: f64, span: f64) -> f64 {
    let end = start + span;
    let old_end = old_start + old_size;
    let candidate = if (old_start - start).abs() <= (end - old_end).abs() {
        old_start
    } else {
        old_end - new_size
    };
    candidate.max(start).min((end - new_size).max(start))
}

fn clamped_origin(x: f64, y: f64, width: f64, height: f64, area: WorkArea) -> (f64, f64) {
    let max_x = area.x.max(area.x + area.width - width);
    let max_y = area.y.max(area.y + area.height - height);
    (x.max(area.x).min(max_x), y.max(area.y).min(max_y))
}

fn remember_set_position(
    window: &tauri::WebviewWindow,
    state: &AppState,
    logical_position: (f64, f64),
) {
    let scale = window.scale_factor().unwrap_or(1.0);
    let expected =
        LogicalPosition::new(logical_position.0, logical_position.1).to_physical::<i32>(scale);
    if let Ok(mut anchor) = state.window_anchor.lock() {
        // set_position is asynchronous on several window managers. Reading
        // outer_position immediately afterwards can therefore return the old
        // coordinate and make the eventual Moved event look like a user drag.
        anchor.last_set_physical = Some((expected.x, expected.y));
    }
}

fn begin_programmatic_layout(state: &AppState) {
    if let Ok(mut anchor) = state.window_anchor.lock() {
        anchor.ignore_moved_until = Some(Instant::now() + PROGRAMMATIC_MOVE_GRACE);
    }
}

fn moved_is_programmatic(anchor: &WindowAnchor) -> bool {
    anchor
        .ignore_moved_until
        .is_some_and(|until| Instant::now() < until)
}

fn moved_matches_requested_position(anchor: &WindowAnchor, position: (i32, i32)) -> bool {
    anchor.last_set_physical.is_some_and(|expected| {
        (position.0 - expected.0).abs() <= 24 && (position.1 - expected.1).abs() <= 24
    })
}

pub(crate) fn tray_activate_is_duplicate(app: &tauri::AppHandle) -> bool {
    let now = Instant::now();
    let state = app.state::<AppState>();
    let Ok(mut last) = state.last_tray_activate.lock() else {
        return false;
    };
    if last.is_some_and(|previous| now.saturating_duration_since(previous) < TRAY_ACTIVATE_DEBOUNCE)
    {
        return true;
    }
    *last = Some(now);
    false
}

fn relayout_from_cache(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    let Some(request) = state
        .last_layout
        .lock()
        .ok()
        .and_then(|guard| guard.clone())
    else {
        return;
    };
    let _ = layout_windows(app, state.inner(), &request);
}

fn seed_tray_anchor_from_os(app: &tauri::AppHandle) {
    let valid = app
        .state::<AppState>()
        .window_anchor
        .lock()
        .ok()
        .is_some_and(|anchor| anchor.valid);
    if valid {
        return;
    }
    let native_anchor = app
        .tray_by_id("mountlet")
        .and_then(|tray| tray.rect().ok().flatten());
    if let Some(rect) = native_anchor {
        let (x, y) = tray_rect_center(&rect);
        cache_tray_anchor(app, x, y);
    } else {
        fallback_tray_anchor(app);
    }
}

#[cfg(target_os = "linux")]
fn fallback_tray_anchor(app: &tauri::AppHandle) {
    // Windows and macOS expose the actual notification/status item rectangle.
    // During process startup the shell can need another event-loop turn before
    // it returns that rectangle. Do not permanently replace it with the cursor:
    // leaving the anchor invalid lets the final layout pass retry the native API.
    if let Ok(position) = app.cursor_position() {
        if position.x.abs() > 1.0 || position.y.abs() > 1.0 {
            cache_tray_anchor(app, position.x, position.y);
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn fallback_tray_anchor(_app: &tauri::AppHandle) {}

fn tray_rect_box(rect: &tauri::Rect) -> (f64, f64, f64, f64) {
    let position = match rect.position {
        tauri::Position::Physical(position) => (f64::from(position.x), f64::from(position.y)),
        tauri::Position::Logical(position) => (position.x, position.y),
    };
    let size = match rect.size {
        tauri::Size::Physical(size) => (f64::from(size.width), f64::from(size.height)),
        tauri::Size::Logical(size) => (size.width, size.height),
    };
    (position.0, position.1, size.0.max(1.0), size.1.max(1.0))
}

fn tray_rect_center(rect: &tauri::Rect) -> (f64, f64) {
    let (left, top, width, height) = tray_rect_box(rect);
    (left + width / 2.0, top + height / 2.0)
}

pub(crate) fn cache_tray_anchor(app: &tauri::AppHandle, x: f64, y: f64) {
    let tray_box = app
        .tray_by_id("mountlet")
        .and_then(|tray| tray.rect().ok().flatten());
    let scale_factor = app
        .available_monitors()
        .ok()
        .and_then(|monitors| {
            monitors
                .into_iter()
                .find(|monitor| {
                    let position = monitor.position();
                    let size = monitor.size();
                    x >= f64::from(position.x)
                        && y >= f64::from(position.y)
                        && x < f64::from(position.x) + f64::from(size.width)
                        && y < f64::from(position.y) + f64::from(size.height)
                })
                .map(|monitor| monitor.scale_factor())
        })
        .or_else(|| {
            app.primary_monitor()
                .ok()
                .flatten()
                .map(|monitor| monitor.scale_factor())
        })
        .unwrap_or(1.0);
    let state = app.state::<AppState>();
    if let Ok(mut anchor) = state.window_anchor.lock() {
        let suspicious = x.abs() <= 1.0 && y.abs() <= 1.0;
        if suspicious && (tray_box.is_none() || anchor.valid) {
            return;
        }
        let (center_x, center_y, width, height) = if let Some(rect) = tray_box.as_ref() {
            let (left, top, width, height) = tray_rect_box(rect);
            if suspicious {
                (left + width / 2.0, top + height / 2.0, width, height)
            } else {
                (x, y, width, height)
            }
        } else {
            (x, y, DEFAULT_TRAY_ICON_SIZE, DEFAULT_TRAY_ICON_SIZE)
        };
        // A new tray activation is a new anchor. Reclassify its edge instead
        // of retaining an edge inferred from an older click on the same screen.
        anchor.edge.clear();
        anchor.area_signature = (0, 0, 0, 0);
        anchor.physical_x = center_x;
        anchor.physical_y = center_y;
        anchor.physical_width = width;
        anchor.physical_height = height;
        anchor.scale_factor = scale_factor;
        anchor.valid = true;
    };
}

fn tray_edge_for_point(point: (f64, f64), area: (f64, f64, f64, f64), remembered: &str) -> String {
    let (left, top, width, height) = area;
    let right = left + width;
    let bottom = top + height;
    if point.0 <= left {
        return "left".into();
    }
    if point.0 >= right {
        return "right".into();
    }
    if point.1 <= top {
        return "top".into();
    }
    if point.1 >= bottom {
        return "bottom".into();
    }
    if matches!(remembered, "left" | "right" | "top" | "bottom") {
        return remembered.into();
    }
    let distances = [
        ("left", (point.0 - left).abs()),
        ("right", (point.0 - right).abs()),
        ("top", (point.1 - top).abs()),
        ("bottom", (point.1 - bottom).abs()),
    ];
    distances
        .iter()
        .min_by(|left, right| left.1.total_cmp(&right.1))
        .map(|value| value.0.to_string())
        .unwrap_or_else(|| "right".into())
}

fn exclude_tray_panel(
    work_area: (f64, f64, f64, f64),
    tray_rect: (f64, f64, f64, f64),
    edge: &str,
) -> (f64, f64, f64, f64) {
    let (mut left, mut top, width, height) = work_area;
    let mut right = left + width;
    let mut bottom = top + height;
    let (tray_left, tray_top, tray_width, tray_height) = tray_rect;
    let center_x = tray_left + tray_width / 2.0;
    let center_y = tray_top + tray_height / 2.0;
    if !(left <= center_x && center_x < right && top <= center_y && center_y < bottom) {
        return work_area;
    }
    match edge {
        "right" => right = right.min(tray_left - PANEL_ICON_MARGIN),
        "left" => left = left.max(tray_left + tray_width + PANEL_ICON_MARGIN),
        "top" => top = top.max(tray_top + tray_height + PANEL_ICON_MARGIN),
        "bottom" => bottom = bottom.min(tray_top - PANEL_ICON_MARGIN),
        _ => {}
    }
    // A corner tray icon is often nearer the side than the panel edge. Still
    // carve out every strip the icon occupies so the window cannot sit under
    // the panel when the work area still includes it (common on Wayland).
    let tray_right = tray_left + tray_width;
    let tray_bottom = tray_top + tray_height;
    if tray_top < work_area.1 + tray_height {
        top = top.max(tray_bottom + PANEL_ICON_MARGIN);
    }
    if tray_bottom > work_area.1 + height - tray_height {
        bottom = bottom.min(tray_top - PANEL_ICON_MARGIN);
    }
    if tray_left < work_area.0 + tray_width {
        left = left.max(tray_right + PANEL_ICON_MARGIN);
    }
    if tray_right > work_area.0 + width - tray_width {
        right = right.min(tray_left - PANEL_ICON_MARGIN);
    }
    (left, top, (right - left).max(1.0), (bottom - top).max(1.0))
}

fn anchored_popup_position(
    anchor: (f64, f64),
    edge: &str,
    area: (f64, f64, f64, f64),
    size: (f64, f64),
) -> (f64, f64) {
    let (left, top, width, height) = area;
    let (window_width, window_height) = size;
    let right = left + width;
    let bottom = top + height;
    let (x, y) = match edge {
        "left" => (anchor.0 + PANEL_ICON_MARGIN, anchor.1 - window_height / 2.0),
        "top" => (anchor.0 - window_width / 2.0, anchor.1 + PANEL_ICON_MARGIN),
        "bottom" => (
            anchor.0 - window_width / 2.0,
            anchor.1 - window_height - PANEL_ICON_MARGIN,
        ),
        _ => (
            anchor.0 - window_width - PANEL_ICON_MARGIN,
            anchor.1 - window_height / 2.0,
        ),
    };
    (
        x.max(left).min((right - window_width).max(left)),
        y.max(top).min((bottom - window_height).max(top)),
    )
}

fn emit_native_layout(app: &tauri::AppHandle, browser_side: &str, browser_inner_height: f64) {
    let _ = app.emit(
        "native-layout",
        NativeLayoutEvent {
            browser_side: browser_side.into(),
            browser_inner_height,
        },
    );
}

fn fallback_tray_popup_position(
    available_x: f64,
    available_y: f64,
    available_width: f64,
    available_height: f64,
    window_width: f64,
    window_height: f64,
) -> Option<(f64, f64)> {
    let right = (available_x + available_width - window_width - 8.0).max(available_x);
    let bottom = (available_y + available_height - window_height - 8.0).max(available_y);
    let top = available_y + 8.0;
    Some(if platform::is_wayland() && platform::is_gnome() {
        (right, top)
    } else {
        (right, bottom)
    })
}

#[tauri::command]
async fn apply_window_layout(
    request: WindowLayoutRequest,
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let stored = if window.label() == "browser" {
        let Some(mut stored) = state
            .last_layout
            .lock()
            .ok()
            .and_then(|guard| guard.clone())
        else {
            return Ok(());
        };
        stored.browser_items = request.browser_items;
        stored.browser_search_height = request.browser_search_height;
        if let Ok(mut last) = state.last_layout.lock() {
            *last = Some(stored.clone());
        }
        stored
    } else {
        if let Ok(mut last) = state.last_layout.lock() {
            *last = Some(request.clone());
        }
        request
    };
    let ui = app.clone();
    run_on_main_async(app, move || {
        let state = ui.state::<AppState>();
        layout_windows(&ui, state.inner(), &stored)
    })
    .await?
}

fn layout_windows(
    app: &tauri::AppHandle,
    state: &AppState,
    request: &WindowLayoutRequest,
) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("main window is unavailable")?;
    // TrayIcon::rect() talks to the shell. Querying it from a command handler
    // can stall WebView IPC on Windows. Startup retries seed the cache from
    // the native event loop instead.
    let requested_area = WorkArea {
        x: request.available_x,
        y: request.available_y,
        width: request.available_width.max(1.0),
        height: request.available_height.max(1.0),
    };
    let tray_point = state.window_anchor.lock().ok().and_then(|anchor| {
        anchor
            .valid
            .then_some((anchor.physical_x, anchor.physical_y))
    });
    let monitor = tray_point
        .and_then(|point| {
            app.available_monitors().ok().and_then(|monitors| {
                monitors.into_iter().find(|monitor| {
                    let position = monitor.position();
                    let size = monitor.size();
                    point.0 >= f64::from(position.x)
                        && point.1 >= f64::from(position.y)
                        && point.0 < f64::from(position.x) + f64::from(size.width)
                        && point.1 < f64::from(position.y) + f64::from(size.height)
                })
            })
        })
        .or_else(|| main.current_monitor().ok().flatten());
    let monitor_area = monitor.map(|monitor| {
        let scale = monitor.scale_factor();
        let position = monitor.position();
        let size = monitor.size();
        WorkArea {
            x: f64::from(position.x) / scale,
            y: f64::from(position.y) / scale,
            width: f64::from(size.width) / scale,
            height: f64::from(size.height) / scale,
        }
    });
    let combined = match monitor_area {
        Some(monitor) if monitor.overlaps(requested_area) => monitor.intersect(requested_area),
        Some(monitor) => monitor,
        None => requested_area,
    };
    let platform_area = work_area::resolve(combined);
    let scale = main.scale_factor().unwrap_or(1.0);
    let signature = (
        platform_area.x.round() as i64,
        platform_area.y.round() as i64,
        platform_area.width.round() as i64,
        platform_area.height.round() as i64,
    );
    let anchored = state.window_anchor.lock().ok().and_then(|mut anchor| {
        if !anchor.valid {
            return None;
        }
        let anchor_scale = if anchor.scale_factor > 0.0 {
            anchor.scale_factor
        } else {
            scale
        };
        let point = (
            anchor.physical_x / anchor_scale,
            anchor.physical_y / anchor_scale,
        );
        let tray_width = (if anchor.physical_width > 0.0 {
            anchor.physical_width
        } else {
            DEFAULT_TRAY_ICON_SIZE
        }) / anchor_scale;
        let tray_height = (if anchor.physical_height > 0.0 {
            anchor.physical_height
        } else {
            DEFAULT_TRAY_ICON_SIZE
        }) / anchor_scale;
        let tray_rect = (
            point.0 - tray_width / 2.0,
            point.1 - tray_height / 2.0,
            tray_width,
            tray_height,
        );
        if anchor.edge.is_empty() || anchor.area_signature != signature {
            anchor.edge = tray_edge_for_point(
                point,
                (
                    platform_area.x,
                    platform_area.y,
                    platform_area.width,
                    platform_area.height,
                ),
                &anchor.edge,
            );
            anchor.area_signature = signature;
        }
        Some((point, tray_rect, anchor.edge.clone()))
    });
    let available = if let Some((_, tray_rect, edge)) = anchored.as_ref() {
        let (x, y, width, height) = exclude_tray_panel(
            (
                platform_area.x,
                platform_area.y,
                platform_area.width,
                platform_area.height,
            ),
            *tray_rect,
            edge,
        );
        WorkArea {
            x,
            y,
            width,
            height,
        }
    } else {
        platform_area
    };
    let available_width = available.width;
    let available_height = available.height;
    let (main_frame_w, main_frame_h) = frame_overhead(&main);
    let main_content_width = if request.mode == "single" {
        f64::from(request.single_window_width)
    } else {
        f64::from(request.remote_pane_width)
    };
    let remote_content_height = f64::from(request.remote_chrome_height)
        + f64::from(request.remote_row_height) * request.remote_count as f64
        + f64::from(request.global_search_height);
    let main_content_height = if request.mode == "single" {
        // Single-window height follows the remote list. The file viewport
        // fills leftover space and scrolls; file count must not grow the frame.
        remote_content_height.max(f64::from(request.browser_min_height))
    } else {
        remote_content_height
    };
    let target_main_outer_width = (main_content_width + main_frame_w).min(available_width);
    let target_main_outer_height = (main_content_height + main_frame_h).min(available_height);
    let visible = main.is_visible().unwrap_or(false);
    let user_placed = visible
        && state
            .window_anchor
            .lock()
            .ok()
            .is_some_and(|anchor| anchor.user_placed);
    let (main_x, main_y) = if user_placed {
        let current = logical_outer_rect(&main).unwrap_or((
            available.x,
            available.y,
            target_main_outer_width,
            target_main_outer_height,
        ));
        clamped_origin(
            current.0,
            current.1,
            target_main_outer_width,
            target_main_outer_height,
            available,
        )
    } else if let Some((point, _, edge)) = anchored {
        anchored_popup_position(
            point,
            &edge,
            (available.x, available.y, available_width, available_height),
            (target_main_outer_width, target_main_outer_height),
        )
    } else {
        fallback_tray_popup_position(
            available.x,
            available.y,
            available_width,
            available_height,
            target_main_outer_width,
            target_main_outer_height,
        )
        .unwrap_or((
            available.x + available_width - target_main_outer_width - 8.0,
            available.y + 8.0,
        ))
    };
    begin_programmatic_layout(state);
    let main_max_inner_width = (available_width - main_frame_w).max(1.0);
    let main_max_inner_height = (available_height - main_frame_h).max(1.0);
    let _ = main.set_min_size(Some(LogicalSize::new(
        360.0_f64.min(main_max_inner_width),
        120.0_f64.min(main_max_inner_height),
    )));
    let _ = main.set_max_size(Some(LogicalSize::new(
        main_max_inner_width,
        main_max_inner_height,
    )));
    main.set_size(LogicalSize::new(
        (target_main_outer_width - main_frame_w).max(1.0),
        (target_main_outer_height - main_frame_h).max(1.0),
    ))
    .map_err(|error| error.to_string())?;
    main.set_position(LogicalPosition::new(main_x, main_y))
        .map_err(|error| error.to_string())?;
    remember_set_position(&main, state, (main_x, main_y));

    let main_inner_height = (target_main_outer_height - main_frame_h).max(1.0);
    if request.mode != "multiple" {
        emit_native_layout(app, "right", main_inner_height);
        return Ok(());
    }
    let Some(browser) = app.get_webview_window("browser") else {
        emit_native_layout(app, "right", main_inner_height);
        return Ok(());
    };
    let (browser_frame_w, browser_frame_h) = frame_overhead(&browser);
    let desired_browser_outer_width =
        (f64::from(request.browser_width) + browser_frame_w).min(available_width);
    let desired_browser_content_height = f64::from(request.browser_chrome_height)
        + f64::from(request.browser_row_height) * request.browser_items.max(1) as f64
        + f64::from(request.browser_search_height);
    let desired_browser_outer_height = (desired_browser_content_height + browser_frame_h)
        .max(f64::from(request.browser_min_height) + browser_frame_h)
        .min(available_height);
    let browser_max_inner_width = (available_width - browser_frame_w).max(1.0);
    let browser_max_inner_height = (available_height - browser_frame_h).max(1.0);
    let _ = browser.set_min_size(Some(LogicalSize::new(
        200.0_f64.min(browser_max_inner_width),
        120.0_f64.min(browser_max_inner_height),
    )));
    let _ = browser.set_max_size(Some(LogicalSize::new(
        browser_max_inner_width,
        browser_max_inner_height,
    )));
    browser
        .set_size(LogicalSize::new(
            (desired_browser_outer_width - browser_frame_w).max(1.0),
            (desired_browser_outer_height - browser_frame_h).max(1.0),
        ))
        .map_err(|error| error.to_string())?;

    let available_right = available.x + available_width;
    let main_right = main_x + target_main_outer_width;
    let right_space = available_right - main_right;
    let left_space = main_x - available.x;
    let browser_x = if right_space >= desired_browser_outer_width || right_space >= left_space {
        main_right
    } else {
        main_x - desired_browser_outer_width
    }
    .max(available.x)
    .min((available_right - desired_browser_outer_width).max(available.x));
    let card_top = main_y
        + f64::from(request.remote_card_top)
        + f64::from(request.remote_row_height) * request.selected_index as f64;
    let browser_y = card_top
        .max(available.y)
        .min((available.y + available_height - desired_browser_outer_height).max(available.y));
    browser
        .set_position(LogicalPosition::new(browser_x, browser_y))
        .map_err(|error| error.to_string())?;
    let browser_side = if browser_x + desired_browser_outer_width / 2.0 < main_x {
        "left"
    } else {
        "right"
    };
    emit_native_layout(
        app,
        browser_side,
        (desired_browser_outer_height - browser_frame_h).max(1.0),
    );
    Ok(())
}

#[tauri::command]
async fn invalidate_folder(
    request: FolderRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let key = (request.remote_id, request.path);
    state.folders.write().await.remove(&key);
    if let Ok(mut refreshed) = state.folder_refreshes.lock() {
        refreshed.remove(&key);
    }
    Ok(())
}

fn checked_remote_path(remote_id: &str, path: &str) -> Result<String, String> {
    if remote_id.trim().is_empty() || path.split('/').any(|part| part == "..") {
        return Err("Invalid remote path".into());
    }
    let base = remote_source(remote_id);
    let relative = path.trim_matches('/');
    Ok(if relative.is_empty() {
        base
    } else {
        format!("{}/{relative}", base.trim_end_matches('/'))
    })
}

fn gphotos_mutation_error(remote_id: &str, path: &str, action: &str) -> Result<(), String> {
    let provider = rclone_section_value(remote_id, "type").unwrap_or_default();
    if provider != "gphotos" {
        return Ok(());
    }
    if action == "create" {
        return Err("Google Photos does not support creating ordinary files.".into());
    }
    if action == "mkdir" {
        let parent = path
            .trim_matches('/')
            .rsplit_once('/')
            .map(|(value, _)| value)
            .unwrap_or("");
        if parent.eq_ignore_ascii_case("album")
            || path.trim_matches('/').eq_ignore_ascii_case("album")
        {
            return Ok(());
        }
        return Err("Albums can be created only inside the album folder.".into());
    }
    if !platform::gphotos_album_writable(path) {
        return Err(if action == "delete" {
            "Google Photos can remove media only from albums created through rclone.".into()
        } else {
            "Google Photos media can be moved only from albums created through rclone.".into()
        });
    }
    Ok(())
}

async fn run_rclone_mutation(
    state: &AppState,
    operation: &'static str,
    source: String,
    destination: Option<String>,
) -> Result<(), String> {
    let (Some(rclone), Some(config)) = (state.rclone.clone(), state.rclone_config.clone()) else {
        return Err("rclone is unavailable".into());
    };
    append_rclone_log(state, format!("$ rclone {operation} {source}"));
    let result = tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new(rclone);
        command
            .args(["--config"])
            .arg(config)
            .arg(operation)
            .arg(source);
        if let Some(destination) = destination {
            command.arg(destination);
        }
        let output = command.output().map_err(|error| error.to_string())?;
        if output.status.success() {
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
        }
    })
    .await
    .map_err(|error| error.to_string())?;
    if let Err(error) = &result {
        append_rclone_log(state, error);
    }
    result
}

#[tauri::command]
async fn rename_entry(
    request: RenameRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let name = request.new_name.trim();
    if name.is_empty() || name.contains('/') || matches!(name, "." | "..") {
        return Err("Enter a valid file or folder name".into());
    }
    gphotos_mutation_error(&request.remote_id, &request.path, "move")?;
    let parent = request
        .path
        .rsplit_once('/')
        .map(|(value, _)| value)
        .unwrap_or("");
    let destination_path = if parent.is_empty() {
        name.to_string()
    } else {
        format!("{parent}/{name}")
    };
    let source = checked_remote_path(&request.remote_id, &request.path)?;
    let destination = checked_remote_path(&request.remote_id, &destination_path)?;
    run_rclone_mutation(&state, "moveto", source, Some(destination)).await
}

#[tauri::command]
async fn create_folder(
    request: EntryRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    gphotos_mutation_error(&request.remote_id, &request.path, "mkdir")?;
    let target = checked_remote_path(&request.remote_id, &request.path)?;
    run_rclone_mutation(&state, "mkdir", target, None).await
}

#[tauri::command]
async fn create_file(
    request: CreateFileRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    if request.path.trim_matches('/').is_empty() || request.contents.len() > 1_000_000 {
        return Err("Enter a valid file name".into());
    }
    gphotos_mutation_error(&request.remote_id, &request.path, "create")?;
    let destination = checked_remote_path(&request.remote_id, &request.path)?;
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let contents = request.contents;
    tauri::async_runtime::spawn_blocking(move || {
        let path = env::temp_dir().join(format!(
            "mountlet-new-file-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        fs::write(&path, contents).map_err(|error| error.to_string())?;
        let output = Command::new(rclone)
            .args(["--config"])
            .arg(config)
            .arg("copyto")
            .arg(&path)
            .arg(destination)
            .output()
            .map_err(|error| error.to_string());
        let _ = fs::remove_file(path);
        let output = output?;
        if output.status.success() {
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
        }
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn delete_entry(
    request: EntryRequest,
    is_dir: bool,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    gphotos_mutation_error(&request.remote_id, &request.path, "delete")?;
    let target = checked_remote_path(&request.remote_id, &request.path)?;
    run_rclone_mutation(
        &state,
        if is_dir { "purge" } else { "deletefile" },
        target,
        None,
    )
    .await
}

#[tauri::command]
async fn transfer_entry(
    request: TransferRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    if request.move_entry {
        gphotos_mutation_error(&request.source_remote_id, &request.source_path, "move")?;
    }
    let destination_provider =
        rclone_section_value(&request.destination_remote_id, "type").unwrap_or_default();
    if destination_provider == "gphotos"
        && !platform::gphotos_album_writable(&request.destination_path)
    {
        return Err(
            "Google Photos can receive media only in albums created through rclone.".into(),
        );
    }
    let source = checked_remote_path(&request.source_remote_id, &request.source_path)?;
    let destination =
        checked_remote_path(&request.destination_remote_id, &request.destination_path)?;
    run_rclone_mutation(
        &state,
        if request.move_entry {
            "moveto"
        } else {
            "copyto"
        },
        source,
        Some(destination),
    )
    .await
}

#[tauri::command]
async fn upload_local_paths(
    request: UploadRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    if request.local_paths.is_empty() {
        return Ok(());
    }
    let destination_root = checked_remote_path(&request.remote_id, &request.destination_path)?;
    let (rclone, config) = (
        state.rclone.clone().ok_or("rclone is unavailable")?,
        state
            .rclone_config
            .clone()
            .ok_or("rclone configuration is unavailable")?,
    );
    tauri::async_runtime::spawn_blocking(move || {
        for local in request.local_paths {
            let source = PathBuf::from(&local);
            if !source.exists() {
                return Err(format!("Local item does not exist: {local}"));
            }
            let name = source
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or_else(|| format!("Invalid local filename: {local}"))?;
            let destination = format!("{}/{}", destination_root.trim_end_matches('/'), name);
            let output = Command::new(&rclone)
                .args(["--config"])
                .arg(&config)
                .arg(if source.is_dir() { "copy" } else { "copyto" })
                .arg(&source)
                .arg(destination)
                .output()
                .map_err(|error| error.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
            }
        }
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn make_offline(
    request: EntryRequest,
    is_dir: bool,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let source = checked_remote_path(&request.remote_id, &request.path)?;
    let destination = offline_root()
        .ok_or("Could not resolve offline folder")?
        .join(mount_slug(&request.remote_id))
        .join(&request.path);
    let remote_id = request.remote_id;
    let relative = request.path;
    let (rclone, config) = (
        state.rclone.clone().ok_or("rclone is unavailable")?,
        state
            .rclone_config
            .clone()
            .ok_or("rclone configuration is unavailable")?,
    );
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let output = Command::new(rclone)
            .args(["--config"])
            .arg(config)
            .arg(if is_dir { "copy" } else { "copyto" })
            .arg(source)
            .arg(&destination)
            .output()
            .map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
        }
        update_offline_manifest(&remote_id, &relative, &destination, true)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn materialize_entries_for_drag(
    requests: Vec<ExportRequest>,
    state: tauri::State<'_, AppState>,
) -> Result<Vec<String>, String> {
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    tauri::async_runtime::spawn_blocking(move || {
        let mut paths = Vec::new();
        for request in requests {
            let source = checked_remote_path(&request.remote_id, &request.path)?;
            let destination = offline_root()
                .ok_or("Could not resolve offline folder")?
                .join(mount_slug(&request.remote_id))
                .join(&request.path);
            if !destination.exists() {
                if let Some(parent) = destination.parent() {
                    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                }
                let output = Command::new(&rclone)
                    .args(["--config"])
                    .arg(&config)
                    .arg(if request.is_dir { "copy" } else { "copyto" })
                    .arg(&source)
                    .arg(&destination)
                    .output()
                    .map_err(|error| error.to_string())?;
                if !output.status.success() {
                    return Err(String::from_utf8_lossy(&output.stderr).trim().into());
                }
            }
            update_offline_manifest(&request.remote_id, &request.path, &destination, false)?;
            paths.push(destination.to_string_lossy().into_owned());
        }
        Ok(paths)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn remove_offline(request: EntryRequest) -> Result<(), String> {
    checked_remote_path(&request.remote_id, &request.path)?;
    remove_protected_offline_records(Some(&request.remote_id), Some(&request.path)).map(|_| ())
}

#[tauri::command]
async fn remove_all_offline(remote_id: Option<String>) -> Result<usize, String> {
    remove_protected_offline_records(remote_id.as_deref(), None)
}

#[tauri::command]
async fn clear_cache(remote_id: Option<String>, path: Option<String>) -> Result<usize, String> {
    if path.is_some() && remote_id.is_none() {
        return Err("A remote is required when clearing one path".into());
    }
    clear_resolved_cache(remote_id.as_deref(), path.as_deref())
}

fn cloud_snapshot_path(remote_id: &str, path: &str) -> Result<PathBuf, String> {
    Ok(offline_root()
        .ok_or("Could not resolve offline folder")?
        .join(".current")
        .join(mount_slug(remote_id))
        .join(path))
}

fn is_complete_managed_file(record: &serde_json::Value) -> bool {
    !record
        .get("is_dir")
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
        && record
            .get("complete")
            .and_then(|value| value.as_bool())
            .unwrap_or(true)
}

fn managed_file_records(remote_id: &str) -> Vec<(String, String)> {
    offline_records(remote_id)
        .into_iter()
        .filter_map(|(path, record)| {
            if !is_complete_managed_file(&record) {
                return None;
            }
            Some((
                path,
                record
                    .get("local_sha256")
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string(),
            ))
        })
        .collect()
}

#[tauri::command]
async fn detect_remote_cache_changes(
    remote_id: String,
    entries: Vec<RemoteMetadataEntry>,
    accept: bool,
) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let manifest_path = offline_manifest_path().ok_or("Could not resolve offline manifest")?;
        let mut manifest: serde_json::Value = fs::read_to_string(&manifest_path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_else(|| serde_json::json!({}));
        let Some(records) = manifest
            .get_mut("remotes")
            .and_then(|value| value.get_mut(&remote_id))
            .and_then(|value| value.as_object_mut())
        else {
            return Ok(Vec::new());
        };
        let mut changed = Vec::new();
        let mut wrote = false;
        for entry in entries {
            let Some(record) = records
                .get_mut(&entry.path)
                .and_then(|value| value.as_object_mut())
            else {
                continue;
            };
            if !is_complete_managed_file(&serde_json::Value::Object(record.clone())) {
                continue;
            }
            let old_size = record.get("remote_size").and_then(|value| value.as_u64());
            let old_modified = record
                .get("remote_modified")
                .and_then(|value| value.as_str());
            if old_size.is_some()
                && (old_size != Some(entry.size) || old_modified != Some(entry.modified.as_str()))
            {
                changed.push(entry.path.clone());
            }
            if accept || old_size.is_none() {
                record.insert("remote_size".into(), serde_json::json!(entry.size));
                record.insert("remote_modified".into(), serde_json::json!(entry.modified));
                wrote = true;
            }
        }
        if wrote {
            save_notice_state(&manifest_path, &manifest)?;
        }
        Ok(changed)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn changed_offline_remotes() -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let manifest_path = offline_manifest_path().ok_or("Could not resolve offline manifest")?;
        let manifest: serde_json::Value = fs::read_to_string(manifest_path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_else(|| serde_json::json!({}));
        let mut changed = Vec::new();
        for (remote_id, records) in manifest
            .get("remotes")
            .and_then(|value| value.as_object())
            .into_iter()
            .flatten()
        {
            let root = offline_root()
                .ok_or("Could not resolve offline folder")?
                .join(mount_slug(remote_id));
            let dirty = records
                .as_object()
                .into_iter()
                .flatten()
                .any(|(path, record)| {
                    if !is_complete_managed_file(record) {
                        return false;
                    }
                    let Ok(metadata) = root.join(path).metadata() else {
                        return false;
                    };
                    offline_record_changed(record, &metadata)
                });
            if dirty {
                changed.push(remote_id.clone());
            }
        }
        Ok(changed)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn sync_offline(
    remote_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<Vec<OfflineConflict>, String> {
    checked_remote_path(&remote_id, "")?;
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let records = managed_file_records(&remote_id);
    let remote_id_copy = remote_id.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let local_root = offline_root()
            .ok_or("Could not resolve offline folder")?
            .join(mount_slug(&remote_id_copy));
        let mut conflicts = Vec::new();
        let mut failures = Vec::new();
        for (path, baseline) in records {
            let local = local_root.join(&path);
            if !local.is_file() {
                continue;
            }
            if baseline.is_empty() {
                update_offline_baseline(&remote_id_copy, &path, &local)?;
                continue;
            }
            let local_hash = file_sha256(&local)?;
            let cloud = cloud_snapshot_path(&remote_id_copy, &path)?;
            if let Some(parent) = cloud.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            let source = checked_remote_path(&remote_id_copy, &path)?;
            let output = Command::new(&rclone)
                .args(["--config"])
                .arg(&config)
                .args(["copyto", &source])
                .arg(&cloud)
                .output()
                .map_err(|error| error.to_string())?;
            if !output.status.success() {
                failures.push(format!(
                    "{path}: {}",
                    String::from_utf8_lossy(&output.stderr).trim()
                ));
                continue;
            }
            let cloud_hash = file_sha256(&cloud)?;
            let local_changed = local_hash != baseline;
            let cloud_changed = cloud_hash != baseline;
            if !local_changed && cloud_changed {
                fs::copy(&cloud, &local).map_err(|error| error.to_string())?;
                update_offline_baseline(&remote_id_copy, &path, &local)?;
            } else if local_changed && !cloud_changed {
                let output = Command::new(&rclone)
                    .args(["--config"])
                    .arg(&config)
                    .args(["copyto"])
                    .arg(&local)
                    .arg(&source)
                    .output()
                    .map_err(|error| error.to_string())?;
                if output.status.success() {
                    update_offline_baseline(&remote_id_copy, &path, &local)?;
                } else {
                    failures.push(format!(
                        "{path}: {}",
                        String::from_utf8_lossy(&output.stderr).trim()
                    ));
                }
            } else if local_changed && cloud_changed && local_hash != cloud_hash {
                let modified = |candidate: &Path| {
                    candidate
                        .metadata()
                        .ok()
                        .and_then(|value| value.modified().ok())
                        .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
                        .map(|value| value.as_secs() as i64)
                        .unwrap_or(0)
                };
                conflicts.push(OfflineConflict {
                    remote_id: remote_id_copy.clone(),
                    path,
                    local_modified: modified(&local),
                    cloud_modified: modified(&cloud),
                });
            } else {
                update_offline_baseline(&remote_id_copy, &path, &local)?;
                let _ = fs::remove_file(&cloud);
            }
        }
        if failures.is_empty() {
            Ok(conflicts)
        } else {
            Err(format!(
                "Some local changes could not be synced: {}",
                failures.join("; ")
            ))
        }
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn resolve_offline_conflict(
    conflict: OfflineConflict,
    choice: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    if !matches!(choice.as_str(), "newer" | "older" | "keep_both") {
        return Err("Unknown offline conflict choice".into());
    }
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    tauri::async_runtime::spawn_blocking(move || {
        let local = offline_root()
            .ok_or("Could not resolve offline folder")?
            .join(mount_slug(&conflict.remote_id))
            .join(&conflict.path);
        let cloud = cloud_snapshot_path(&conflict.remote_id, &conflict.path)?;
        if !local.is_file() || !cloud.is_file() {
            return Err("The conflict copies are no longer available".into());
        }
        let source = checked_remote_path(&conflict.remote_id, &conflict.path)?;
        if choice == "keep_both" {
            let (parent, name) = conflict
                .path
                .rsplit_once('/')
                .unwrap_or(("", conflict.path.as_str()));
            let (stem, suffix) = name
                .rsplit_once('.')
                .map(|(stem, suffix)| (stem, format!(".{suffix}")))
                .unwrap_or((name, String::new()));
            let copy_path = format!(
                "{}{stem} (Mountlet offline {}){suffix}",
                if parent.is_empty() {
                    "".into()
                } else {
                    format!("{parent}/")
                },
                SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs()
            );
            let destination = checked_remote_path(&conflict.remote_id, &copy_path)?;
            let output = Command::new(&rclone)
                .args(["--config"])
                .arg(&config)
                .args(["copyto"])
                .arg(&local)
                .arg(destination)
                .output()
                .map_err(|error| error.to_string())?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).trim().into());
            }
            fs::copy(&cloud, &local).map_err(|error| error.to_string())?;
        } else {
            let choose_local = (choice == "newer"
                && conflict.local_modified >= conflict.cloud_modified)
                || (choice == "older" && conflict.local_modified < conflict.cloud_modified);
            if choose_local {
                let output = Command::new(&rclone)
                    .args(["--config"])
                    .arg(&config)
                    .args(["copyto"])
                    .arg(&local)
                    .arg(&source)
                    .output()
                    .map_err(|error| error.to_string())?;
                if !output.status.success() {
                    return Err(String::from_utf8_lossy(&output.stderr).trim().into());
                }
            } else {
                fs::copy(&cloud, &local).map_err(|error| error.to_string())?;
            }
        }
        update_offline_baseline(&conflict.remote_id, &conflict.path, &local)?;
        let _ = fs::remove_file(cloud);
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn open_entry(
    request: EntryRequest,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let remote_id = request.remote_id;
    let path = request.path;
    let offline = offline_root().map(|root| root.join(mount_slug(&remote_id)).join(&path));
    if let Some(local) = offline.filter(|candidate| candidate.exists()) {
        return open_local_path(&local);
    }
    let (Some(rclone), Some(config)) = (state.rclone.clone(), state.rclone_config.clone()) else {
        return Err("The file is neither mounted nor available locally.".into());
    };
    let destination = offline_root()
        .ok_or("Could not resolve the cache folder")?
        .join(mount_slug(&remote_id))
        .join(&path);
    let remote_target = checked_remote_path(&remote_id, &path)?;
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let output = Command::new(rclone)
            .args(["--config"])
            .arg(config)
            .args(["copyto", &remote_target])
            .arg(&destination)
            .output()
            .map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
        }
        update_offline_manifest(&remote_id, &path, &destination, false)?;
        open_local_path(&destination)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn open_mounted_folder(request: EntryRequest) -> Result<(), String> {
    if request.path.split('/').any(|part| part == "..") {
        return Err("Invalid mounted folder path.".into());
    }
    let root = remote_mount_path(&request.remote_id)?;
    if !path_is_mounted(&root) {
        return Err("This remote folder is not currently mounted.".into());
    }
    let path = root.join(request.path);
    let settings = app_settings();
    file_managers::open_mounted_folder(
        &path,
        &settings.file_manager,
        &settings.open_folder_behavior,
        settings.focus_file_manager,
    )
}

#[tauri::command]
async fn remember_selection(
    request: FolderRequest,
    index: usize,
    item_path: Option<String>,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    state
        .selections
        .write()
        .await
        .insert((request.remote_id.clone(), request.path.clone()), index);
    let _write = BROWSER_MEMORY_WRITE_LOCK
        .lock()
        .map_err(|_| "Browser memory lock is unavailable")?;
    let mut memory = browser_memory();
    if let Some(object) = memory.as_object_mut() {
        if let Some(paths) = object
            .entry("paths")
            .or_insert_with(|| serde_json::json!({}))
            .as_object_mut()
        {
            paths.insert(
                request.remote_id.clone(),
                serde_json::json!(request.path.clone()),
            );
        }
        let selections = object
            .entry("selections")
            .or_insert_with(|| serde_json::json!({}));
        if let Some(folders) = selections
            .as_object_mut()
            .map(|map| {
                map.entry(request.remote_id.clone())
                    .or_insert_with(|| serde_json::json!({}))
            })
            .and_then(|value| value.as_object_mut())
        {
            folders.insert(
                request.path,
                serde_json::json!({
                    "path": item_path.unwrap_or_default(),
                    "index": index,
                }),
            );
        }
    }
    write_browser_memory(&memory)
}

#[tauri::command]
async fn toggle_mount(
    remote_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<bool, String> {
    let remote = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .find(|remote| remote.id == remote_id)
        .cloned()
        .ok_or("Remote not found")?;
    let mount_path = remote_mount_path(&remote.id)?;
    let mounted = path_is_mounted(&mount_path);
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    #[cfg(target_os = "windows")]
    let tracked_pid = state
        .mount_pids
        .lock()
        .ok()
        .and_then(|pids| pids.get(&remote.id).copied());
    append_rclone_log(
        &state,
        format!(
            "{} {}",
            if mounted { "Unmounting" } else { "Mounting" },
            remote.id
        ),
    );
    let result: Result<(bool, Option<u32>), String> =
        tauri::async_runtime::spawn_blocking(move || {
            if mounted {
                #[cfg(target_os = "windows")]
                if let Some(pid) = tracked_pid {
                    let _ = Command::new("taskkill")
                        .args(["/PID", &pid.to_string(), "/T", "/F"])
                        .status();
                    for _ in 0..25 {
                        if !path_is_mounted(&mount_path) {
                            return Ok((false, None));
                        }
                        std::thread::sleep(std::time::Duration::from_millis(200));
                    }
                }
                #[cfg(target_os = "windows")]
                {
                    let _ = fs::remove_dir(&mount_path);
                    if !path_is_mounted(&mount_path) {
                        return Ok((false, None));
                    }
                }
                #[cfg(target_os = "linux")]
                let attempts: &[(&str, &[&str])] = &[
                    ("fusermount3", &["-u", "-z"]),
                    ("fusermount3", &["-u"]),
                    ("fusermount", &["-u", "-z"]),
                    ("fusermount", &["-u"]),
                    ("umount", &[]),
                ];
                #[cfg(target_os = "macos")]
                let attempts: &[(&str, &[&str])] = &[("umount", &[])];
                #[cfg(target_os = "windows")]
                let attempts: &[(&str, &[&str])] = &[];
                for (program, arguments) in attempts {
                    if Command::new(program)
                        .args(*arguments)
                        .arg(&mount_path)
                        .status()
                        .map(|status| status.success())
                        .unwrap_or(false)
                    {
                        return Ok((false, None));
                    }
                }
                return Err("The mounted filesystem could not be released".into());
            }
            #[cfg(target_os = "windows")]
            {
                if let Some(parent) = mount_path.parent() {
                    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                }
                if mount_path.is_dir() {
                    if fs::read_dir(&mount_path)
                        .map_err(|error| error.to_string())?
                        .next()
                        .is_some()
                    {
                        return Err(format!(
                            "Mount folder {} contains local files. Move them before mounting.",
                            mount_path.display()
                        ));
                    }
                    fs::remove_dir(&mount_path).map_err(|error| error.to_string())?;
                }
            }
            #[cfg(not(target_os = "windows"))]
            fs::create_dir_all(&mount_path).map_err(|error| error.to_string())?;
            let source = remote_source(&remote.id);
            let flags = effective_mount_flags(&remote.id, &remote.provider);
            let mut command = Command::new(rclone);
            command
                .args(["--config"])
                .arg(config)
                .arg("mount")
                .arg(source)
                .arg(&mount_path)
                .args(flags);
            #[cfg(not(target_os = "windows"))]
            command.arg("--daemon");
            #[cfg(target_os = "windows")]
            let pid = command.spawn().map_err(|error| error.to_string())?.id();
            #[cfg(not(target_os = "windows"))]
            let pid = {
                let output = command.output().map_err(|error| error.to_string())?;
                if !output.status.success() {
                    return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
                }
                0
            };
            for _ in 0..50 {
                if path_is_mounted(&mount_path) {
                    return Ok((true, (pid != 0).then_some(pid)));
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            Err("rclone started but the mount did not become available".into())
        })
        .await
        .map_err(|error| error.to_string())?;
    match result {
        Ok((mounted, pid)) => {
            if let Ok(mut pids) = state.mount_pids.lock() {
                if let Some(pid) = pid {
                    pids.insert(remote_id.clone(), pid);
                } else {
                    pids.remove(&remote_id);
                }
            }
            Ok(mounted)
        }
        Err(error) => {
            append_rclone_log(&state, &error);
            Err(error)
        }
    }
}

#[tauri::command]
fn open_remote_web(remote_id: String, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let remote = state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .find(|remote| remote.id == remote_id)
        .cloned()
        .ok_or("Remote not found")?;
    let url = remote_browser_url(&remote).ok_or("This provider has no known web interface")?;
    open_external_target(&url)
}

fn parse_wizard_step(output: &str) -> Result<ConfigWizardStep, String> {
    if output.trim().is_empty() {
        return Ok(ConfigWizardStep {
            state: String::new(),
            option: serde_json::json!({}),
            error: String::new(),
            result: String::new(),
        });
    }
    for (offset, character) in output.char_indices() {
        if character != '{' {
            continue;
        }
        let mut values =
            serde_json::Deserializer::from_str(&output[offset..]).into_iter::<serde_json::Value>();
        let Some(Ok(value)) = values.next() else {
            continue;
        };
        if !value.is_object() {
            continue;
        }
        return Ok(ConfigWizardStep {
            state: value
                .get("State")
                .and_then(|item| item.as_str())
                .unwrap_or("")
                .to_string(),
            option: value
                .get("Option")
                .cloned()
                .unwrap_or_else(|| serde_json::json!({})),
            error: value
                .get("Error")
                .and_then(|item| item.as_str())
                .unwrap_or("")
                .to_string(),
            result: value
                .get("Result")
                .and_then(|item| item.as_str())
                .unwrap_or("")
                .to_string(),
        });
    }
    Err(output.trim().to_string())
}

#[tauri::command]
async fn config_wizard_step(
    request: ConfigWizardRequest,
    state: tauri::State<'_, AppState>,
) -> Result<ConfigWizardStep, String> {
    if !state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .any(|remote| remote.id == request.remote_id)
    {
        return Err("Remote not found".into());
    }
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    let remote_id = request.remote_id;
    let wizard_state = request.state;
    let result = request.result;
    let output = tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new(rclone);
        command.args(["--config"]).arg(config).args([
            "config",
            "update",
            &remote_id,
            "--non-interactive",
        ]);
        if !wizard_state.is_empty() {
            command.args(["--continue", "--state", &wizard_state, "--result", &result]);
        }
        command.output().map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;
    let combined = [
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    ]
    .into_iter()
    .filter(|part| !part.trim().is_empty())
    .map(|part| part.into_owned())
    .collect::<Vec<_>>()
    .join("\n");
    if !output.status.success() {
        append_rclone_log(&state, &combined);
        return Err(if combined.trim().is_empty() {
            "Remote configuration failed".into()
        } else {
            combined
        });
    }
    parse_wizard_step(&combined)
}

#[tauri::command]
async fn reauthenticate_remote(
    remote_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    if !state
        .remotes
        .read()
        .map_err(|_| "Remote state is unavailable")?
        .iter()
        .any(|remote| remote.id == remote_id)
    {
        return Err("Remote not found".into());
    }
    let rclone = state.rclone.clone().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .clone()
        .ok_or("rclone configuration is unavailable")?;
    append_rclone_log(&state, format!("$ rclone config reconnect {remote_id}:"));
    let result = tauri::async_runtime::spawn_blocking(move || {
        let output = Command::new(rclone)
            .args(["--config"])
            .arg(config)
            .args([
                "config",
                "reconnect",
                &format!("{remote_id}:"),
                "--auto-confirm",
            ])
            .output()
            .map_err(|error| error.to_string())?;
        if output.status.success() {
            Ok(())
        } else {
            let detail = [
                String::from_utf8_lossy(&output.stdout).trim(),
                String::from_utf8_lossy(&output.stderr).trim(),
            ]
            .into_iter()
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join("\n");
            Err(if detail.is_empty() {
                "Reauthentication failed".into()
            } else {
                detail
            })
        }
    })
    .await
    .map_err(|error| error.to_string())?;
    if let Err(error) = &result {
        append_rclone_log(&state, error);
    }
    result
}

#[tauri::command]
async fn set_browser_window(enabled: bool, app: tauri::AppHandle) -> Result<(), String> {
    let ui = app.clone();
    run_on_main_async(app, move || set_browser_window_on_main(&ui, enabled)).await?
}

fn set_browser_window_on_main(app: &tauri::AppHandle, enabled: bool) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("browser") {
        if enabled {
            let main_visible = app
                .get_webview_window("main")
                .and_then(|main| main.is_visible().ok())
                .unwrap_or(false);
            if main_visible {
                window.show().map_err(|error| error.to_string())?;
            }
        } else {
            window.hide().map_err(|error| error.to_string())?;
        }
        return Ok(());
    }
    if enabled {
        let main = app
            .get_webview_window("main")
            .ok_or("main window is unavailable")?;
        tauri::WebviewWindowBuilder::new(
            app,
            "browser",
            tauri::WebviewUrl::App("index.html?browser=1".into()),
        )
        .parent(&main)
        .map_err(|error| error.to_string())?
        .title("Mountlet Files")
        .inner_size(620.0, 540.0)
        .min_inner_size(200.0, 120.0)
        .decorations(false)
        .skip_taskbar(true)
        .visible(false)
        .focused(false)
        .build()
        .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
async fn set_browser_state(
    remote_id: String,
    path: String,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let remote = state.remotes.read().ok().and_then(|remotes| {
        remotes
            .iter()
            .find(|remote| remote.id == remote_id)
            .cloned()
    });
    if let (Some(remote), Some(browser)) = (remote, app.get_webview_window("browser")) {
        browser
            .set_title(&format!(
                "{} ({}) — Mountlet Files",
                remote.name, remote.provider_label
            ))
            .map_err(|error| error.to_string())?;
    }
    *state.browser_state.write().await = (remote_id, path);
    Ok(())
}

#[tauri::command]
async fn get_browser_state(state: tauri::State<'_, AppState>) -> Result<(String, String), String> {
    Ok(state.browser_state.read().await.clone())
}

#[tauri::command]
async fn focus_window(label: String, app: tauri::AppHandle) -> Result<(), String> {
    let ui = app.clone();
    run_on_main_async(app, move || {
        let window = ui
            .get_webview_window(&label)
            .ok_or_else(|| format!("{label} window is unavailable"))?;
        if let Ok(mut focused) = ui.state::<AppState>().last_focused_window.lock() {
            *focused = label.clone();
        }
        window.show().map_err(|error| error.to_string())?;
        activate_window_keyboard(&window);
        Ok(())
    })
    .await?
}

fn activate_window_keyboard(window: &tauri::WebviewWindow) {
    let _ = window.set_focus();
    #[cfg(all(target_os = "linux", not(target_os = "android")))]
    activate_linux_keyboard(window);
}

#[cfg(all(target_os = "linux", not(target_os = "android")))]
fn activate_linux_keyboard(window: &tauri::WebviewWindow) {
    use gtk::prelude::{Cast, GtkWindowExt, WidgetExt};
    let Ok(gtk_window) = window.gtk_window() else {
        return;
    };
    // Tao's set_focus is a no-op while get_visible() is still false, which is
    // the first-show case: show() is queued and the window has never been mapped.
    // Present and grab WebKit focus synchronously on the GTK thread instead.
    gtk_window.show_all();
    gtk_window.present();
    grab_webkit_focus(gtk_window.upcast_ref());
    // `show_all` only queues the initial map. Activate again from GTK's next
    // idle turn, after the compositor has received the mapped surface. This is
    // lifecycle-driven rather than an arbitrary timeout and fixes the first
    // tray opening, where WebKit otherwise has DOM focus but no key events.
    let mapped_window = gtk_window.clone();
    gtk::glib::idle_add_local_once(move || {
        mapped_window.present();
        grab_webkit_focus(mapped_window.upcast_ref());
    });
}

#[cfg(all(target_os = "linux", not(target_os = "android")))]
fn grab_webkit_focus(widget: &gtk::Widget) {
    use gtk::glib::ObjectExt;
    use gtk::prelude::{Cast, ContainerExt, WidgetExt};
    if widget.type_().name() == "WebKitWebView" {
        widget.set_can_focus(true);
        widget.grab_focus();
        return;
    }
    let Some(container) = widget.downcast_ref::<gtk::Container>() else {
        return;
    };
    for child in container.children() {
        grab_webkit_focus(&child);
    }
}

#[tauri::command]
async fn set_window_pinned(pinned: bool, app: tauri::AppHandle) -> Result<(), String> {
    if pinned && !platform::desktop_hints().pin_supported {
        return Err("GNOME on Wayland does not allow apps to pin their own windows.".into());
    }
    let ui = app.clone();
    run_on_main_async(app, move || {
        for label in ["main", "browser"] {
            if let Some(window) = ui.get_webview_window(label) {
                window
                    .set_always_on_top(pinned)
                    .map_err(|error| error.to_string())?;
            }
        }
        Ok(())
    })
    .await?
}

#[tauri::command]
fn browser_window_side(app: tauri::AppHandle) -> String {
    let Some(main) = app.get_webview_window("main") else {
        return "right".into();
    };
    let Some(browser) = app.get_webview_window("browser") else {
        return "right".into();
    };
    match (main.outer_position(), browser.outer_position()) {
        (Ok(main), Ok(browser)) if browser.x < main.x => "left".into(),
        _ => "right".into(),
    }
}

pub(crate) fn hide_window_stack(app: &tauri::AppHandle) {
    // Capture the native truth immediately before hiding. Focus events emitted
    // while two windows are being shown/hidden are not a reliable indication
    // of the window the user last interacted with.
    let browser_focused = app
        .get_webview_window("browser")
        .and_then(|window| window.is_focused().ok())
        .unwrap_or(false);
    let main_focused = app
        .get_webview_window("main")
        .and_then(|window| window.is_focused().ok())
        .unwrap_or(false);
    let focused_label = focused_window_label(browser_focused, main_focused);
    if let Some(label) = focused_label {
        if let Ok(mut focused) = app.state::<AppState>().last_focused_window.lock() {
            *focused = label.to_string();
        }
    }
    if let Some(browser) = app.get_webview_window("browser") {
        let _ = browser.hide();
    }
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.hide();
    }
}

fn focused_window_label(browser_focused: bool, main_focused: bool) -> Option<&'static str> {
    if browser_focused {
        Some("browser")
    } else if main_focused {
        Some("main")
    } else {
        None
    }
}

pub(crate) fn show_window_stack(app: &tauri::AppHandle) {
    let Some(main) = app.get_webview_window("main") else {
        eprintln!("[mountlet] Main window is unavailable.");
        return;
    };
    let was_visible = main.is_visible().unwrap_or(false);
    let state = app.state::<AppState>();
    let first_show = state
        .window_stack_shown
        .lock()
        .map(|mut shown| {
            let first = !*shown;
            *shown = true;
            first
        })
        .unwrap_or(true);
    let mut focus_label = if first_show {
        "main".to_string()
    } else {
        state
            .last_focused_window
            .lock()
            .map(|focused| focused.clone())
            .unwrap_or_else(|_| "main".into())
    };
    if app_settings().window_mode != "multiple" {
        focus_label = "main".into();
    }
    if !was_visible {
        if let Ok(mut anchor) = app.state::<AppState>().window_anchor.lock() {
            anchor.user_placed = false;
        }
        seed_tray_anchor_from_os(app);
    }
    if let Err(error) = main.show() {
        eprintln!("[mountlet] Could not show main window: {error}");
    }
    if let Err(error) = main.unminimize() {
        eprintln!("[mountlet] Could not unminimize main window: {error}");
    }
    if !was_visible {
        relayout_from_cache(app);
    }
    if app_settings().window_mode == "multiple" {
        if let Some(browser) = app.get_webview_window("browser") {
            let _ = browser.show();
        }
    }
    let focus_window = app
        .get_webview_window(&focus_label)
        .unwrap_or_else(|| main.clone());
    activate_window_keyboard(&focus_window);
    if let Ok(mut focused) = state.last_focused_window.lock() {
        *focused = focus_label.clone();
    }
    let _ = focus_window.emit("restore-keyboard-focus", first_show);
    if !was_visible {
        let _ = app.emit("tray-anchor-changed", ());
    }
}

pub(crate) fn toggle_window_stack(app: &tauri::AppHandle) {
    let visible = app
        .get_webview_window("main")
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    if visible {
        hide_window_stack(app);
    } else {
        show_window_stack(app);
    }
}

fn install_panic_log() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Ok(path) = runtime_log_path() {
            if let Some(parent) = path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
                let _ = writeln!(file, "\nUnhandled Rust panic\n{info}\n");
            }
        }
        previous(info);
    }));
}

pub(crate) fn mark_clean_shutdown() {
    if let Ok(path) = runtime_log_path() {
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(file, "\nMountlet shutdown cleanly\n");
        }
    }
}

#[tauri::command]
fn drag_preview_icon() -> Result<String, String> {
    let path = mountlet_state_dir()
        .ok_or("Mountlet state directory is unavailable")?
        .join("drag-preview.png");
    if !path.is_file() {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::write(&path, include_bytes!("../icons/icon.png")).map_err(|error| error.to_string())?;
    }
    Ok(path.to_string_lossy().into_owned())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DriveOauthSource {
    remote_id: String,
    label: String,
}

#[tauri::command]
async fn check_prerequisites(
    state: tauri::State<'_, AppState>,
) -> Result<Vec<platform::Prerequisite>, String> {
    let rclone = state.rclone.clone();
    tauri::async_runtime::spawn_blocking(move || platform::check_prerequisites(rclone.as_deref()))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn desktop_hints() -> platform::DesktopHints {
    platform::desktop_hints()
}

#[tauri::command]
fn oauth_port_status() -> platform::OauthPortStatus {
    platform::oauth_port_status()
}

#[tauri::command]
fn terminate_oauth_rclone() -> bool {
    platform::oauth_port_status()
        .rclone_pid
        .is_some_and(platform::terminate_process_id)
}

#[tauri::command]
fn drive_oauth_sources(state: tauri::State<'_, AppState>) -> Vec<DriveOauthSource> {
    let Some(config) = state.rclone_config.as_ref() else {
        return Vec::new();
    };
    let Ok(sections) = parse_rclone_config(config) else {
        return Vec::new();
    };
    let mut groups: HashMap<(String, String), Vec<String>> = HashMap::new();
    for (name, values) in sections {
        if values.get("type").map(String::as_str) != Some("drive") {
            continue;
        }
        let client_id = values
            .get("client_id")
            .map(|value| value.trim().to_string())
            .unwrap_or_default();
        let client_secret = values
            .get("client_secret")
            .map(|value| value.trim().to_string())
            .unwrap_or_default();
        if client_id.is_empty() || client_secret.is_empty() {
            continue;
        }
        groups
            .entry((client_id, client_secret))
            .or_default()
            .push(name);
    }
    groups
        .into_values()
        .map(|names| {
            let remote_id = names[0].clone();
            let label = if names.len() == 1 {
                names[0].clone()
            } else {
                format!("{}, +{}", names[0], names.len() - 1)
            };
            DriveOauthSource { remote_id, label }
        })
        .collect()
}

#[tauri::command]
fn list_file_managers() -> Vec<file_managers::FileManager> {
    file_managers::discover()
}

#[tauri::command]
fn open_rclone_config_terminal(state: tauri::State<'_, AppState>) -> Result<String, String> {
    let rclone = state.rclone.as_deref().ok_or("rclone is unavailable")?;
    let config = state
        .rclone_config
        .as_ref()
        .ok_or("rclone configuration is unavailable")?;
    platform::open_rclone_config_terminal(rclone, config)
}

#[tauri::command]
fn pick_config_bundle_path(save: bool, suggested: String) -> Option<String> {
    platform::pick_bundle_path(save, &suggested)
}

#[tauri::command]
fn show_desktop_notification(title: String, message: String) {
    platform::show_desktop_notification(&title, &message);
}

#[tauri::command]
fn file_icon_data_url(name: String, is_dir: bool) -> Option<String> {
    platform::file_icon_data_url(&name, is_dir)
}

pub(crate) fn tray_status_tooltip(app: &tauri::AppHandle) -> String {
    let remotes = app
        .state::<AppState>()
        .remotes
        .read()
        .map(|remotes| remotes.clone())
        .unwrap_or_default();
    let configured = remotes
        .iter()
        .filter(|remote| remote_is_configured(&remote.id, &remote.provider))
        .cloned()
        .collect::<Vec<_>>();
    let mounted = configured
        .iter()
        .filter(|remote| {
            remote_mount_path(&remote.id)
                .map(|path| path_is_mounted(&path))
                .unwrap_or(false)
        })
        .map(|remote| remote.name.as_str())
        .collect::<Vec<_>>();
    if configured.is_empty() {
        "Mountlet".into()
    } else if mounted.is_empty() {
        format!("Mountlet - {} remotes", configured.len())
    } else {
        format!(
            "Mountlet - {} mounted: {}",
            mounted.len(),
            mounted.join(", ")
        )
    }
}

#[cfg(not(target_os = "linux"))]
fn build_tray_menu<M: Manager<tauri::Wry>>(app: &M) -> tauri::Result<Menu<tauri::Wry>> {
    let show = MenuItem::with_id(app, "show", "Show Mountlet", true, None::<&str>)?;
    let refresh = MenuItem::with_id(app, "refresh", "Update status", true, None::<&str>)?;
    let mount_all = MenuItem::with_id(app, "mount-all", "Mount all", true, None::<&str>)?;
    let unmount_all = MenuItem::with_id(app, "unmount-all", "Unmount all", true, None::<&str>)?;
    let add_remote = MenuItem::with_id(app, "add-remote", "Add remote", true, None::<&str>)?;
    let license = MenuItem::with_id(app, "license", "License", true, None::<&str>)?;
    let about = MenuItem::with_id(app, "about", "About Mountlet", true, None::<&str>)?;
    let report_bug = MenuItem::with_id(app, "report-bug", "Report bug", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "App settings", true, None::<&str>)?;
    let shortcuts = MenuItem::with_id(app, "shortcuts", "Keyboard shortcuts", true, None::<&str>)?;
    let export_config = MenuItem::with_id(
        app,
        "export-config",
        "Export config bundle",
        true,
        None::<&str>,
    )?;
    let import_config = MenuItem::with_id(
        app,
        "import-config",
        "Import config bundle",
        true,
        None::<&str>,
    )?;
    let app_menu = Submenu::with_items(app, "App", true, &[&license, &about, &report_bug])?;
    let config_menu = Submenu::with_items(
        app,
        "Config",
        true,
        &[&settings, &shortcuts, &export_config, &import_config],
    )?;
    let remote_items = app
        .state::<AppState>()
        .remotes
        .read()
        .map(|remotes| {
            remotes
                .iter()
                .filter(|remote| remote_is_configured(&remote.id, &remote.provider))
                .map(|remote| {
                    let select = MenuItem::with_id(
                        app,
                        format!("remote-action:select:{}", remote.id),
                        "Open in Mountlet",
                        true,
                        None::<&str>,
                    )?;
                    let mount = MenuItem::with_id(
                        app,
                        format!("remote-action:mount:{}", remote.id),
                        if remote.mounted { "Unmount" } else { "Mount" },
                        true,
                        None::<&str>,
                    )?;
                    let folder = MenuItem::with_id(
                        app,
                        format!("remote-action:folder:{}", remote.id),
                        "Open mounted folder",
                        remote.mounted,
                        None::<&str>,
                    )?;
                    let web = MenuItem::with_id(
                        app,
                        format!("remote-action:web:{}", remote.id),
                        "Open in web",
                        true,
                        None::<&str>,
                    )?;
                    let config = MenuItem::with_id(
                        app,
                        format!("remote-action:config:{}", remote.id),
                        "Config",
                        true,
                        None::<&str>,
                    )?;
                    let reauth = MenuItem::with_id(
                        app,
                        format!("remote-action:reauth:{}", remote.id),
                        "Reauthenticate",
                        true,
                        None::<&str>,
                    )?;
                    let sync = MenuItem::with_id(
                        app,
                        format!("remote-action:sync:{}", remote.id),
                        "Sync cached files now",
                        true,
                        None::<&str>,
                    )?;
                    let remove_offline = MenuItem::with_id(
                        app,
                        format!("remote-action:remove-offline:{}", remote.id),
                        "Remove offline files",
                        true,
                        None::<&str>,
                    )?;
                    let clear_cache = MenuItem::with_id(
                        app,
                        format!("remote-action:clear-cache:{}", remote.id),
                        "Clear resolved cache",
                        true,
                        None::<&str>,
                    )?;
                    Submenu::with_items(
                        app,
                        format!("{} ({})", remote.name, remote.provider_label),
                        true,
                        &[
                            &select,
                            &mount,
                            &folder,
                            &web,
                            &sync,
                            &remove_offline,
                            &clear_cache,
                            &config,
                            &reauth,
                        ],
                    )
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .unwrap_or_else(|_| Ok(Vec::new()))?;
    let remote_refs = remote_items
        .iter()
        .map(|item| item as &dyn IsMenuItem<_>)
        .collect::<Vec<_>>();
    let remotes_menu = Submenu::with_items(app, "Remotes", !remote_refs.is_empty(), &remote_refs)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Mountlet", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let separator_two = PredefinedMenuItem::separator(app)?;
    let separator_three = PredefinedMenuItem::separator(app)?;
    Menu::with_items(
        app,
        &[
            &show,
            &refresh,
            &separator,
            &mount_all,
            &unmount_all,
            &remotes_menu,
            &add_remote,
            &separator_two,
            &app_menu,
            &config_menu,
            &separator_three,
            &quit,
        ],
    )
}

#[tauri::command]
async fn refresh_tray_menu(app: tauri::AppHandle) -> Result<(), String> {
    let ui = app.clone();
    run_on_main_async(app, move || refresh_tray_menu_on_main(&ui)).await?
}

fn refresh_tray_menu_on_main(app: &tauri::AppHandle) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    {
        linux_tray::refresh(app);
        Ok(())
    }
    #[cfg(not(target_os = "linux"))]
    {
        let menu = build_tray_menu(app).map_err(|error| error.to_string())?;
        if let Some(tray) = app.tray_by_id("mountlet") {
            tray.set_menu(Some(menu))
                .map_err(|error| error.to_string())?;
            tray.set_tooltip(Some(tray_status_tooltip(app)))
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }
}

fn install_native_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "linux")]
    {
        linux_tray::install(app.handle().clone());
        Ok(())
    }
    #[cfg(not(target_os = "linux"))]
    {
        let menu = build_tray_menu(app)?;
        let icon = app
            .default_window_icon()
            .cloned()
            .ok_or("the application icon is unavailable")?;
        TrayIconBuilder::with_id("mountlet")
            .icon(icon)
            .tooltip(tray_status_tooltip(app.handle()))
            .menu(&menu)
            .show_menu_on_left_click(false)
            .on_menu_event(|app, event| match event.id().as_ref() {
                "show" => show_window_stack(app),
                "refresh" | "mount-all" | "unmount-all" | "add-remote" | "settings" | "license"
                | "about" | "report-bug" | "shortcuts" | "export-config" | "import-config" => {
                    show_window_stack(app);
                    let _ = app.emit("tray-command", event.id().as_ref());
                }
                "quit" => {
                    mark_clean_shutdown();
                    app.exit(0)
                }
                id if id.starts_with("select-remote:") || id.starts_with("remote-action:") => {
                    show_window_stack(app);
                    let _ = app.emit("tray-command", id);
                }
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click {
                    position,
                    button,
                    button_state: MouseButtonState::Up,
                    ..
                } = event
                {
                    let app = tray.app_handle();
                    cache_tray_anchor(app, position.x, position.y);
                    let _ = app.emit("tray-anchor-changed", ());
                    if button == MouseButton::Left {
                        if tray_activate_is_duplicate(app) {
                            return;
                        }
                        toggle_window_stack(app);
                    }
                }
            })
            .build(app)?;
        Ok(())
    }
}

pub fn write_license_diagnostics(path: &Path) -> Result<(), String> {
    license::write_diagnostics(path)
}

const INSTANCE_ADDRESS: &str = "127.0.0.1:47653";
const INSTANCE_SHOW_REQUEST: &[u8] = b"mountlet-v1 show\n";
const INSTANCE_SHOW_RESPONSE: &[u8] = b"mountlet-v1 ok\n";

fn acquire_instance_listener() -> Option<TcpListener> {
    match TcpListener::bind(INSTANCE_ADDRESS) {
        Ok(listener) => Some(listener),
        Err(error) if error.kind() == ErrorKind::AddrInUse => {
            let existing = TcpStream::connect_timeout(
                &INSTANCE_ADDRESS.parse().expect("valid instance address"),
                Duration::from_millis(500),
            )
            .and_then(|mut stream| {
                stream.set_read_timeout(Some(Duration::from_millis(500)))?;
                stream.write_all(INSTANCE_SHOW_REQUEST)?;
                let mut response = [0u8; 15];
                stream.read_exact(&mut response)?;
                Ok(response == INSTANCE_SHOW_RESPONSE)
            })
            .unwrap_or(false);
            if existing {
                eprintln!("[mountlet] The running Mountlet instance was asked to show.");
                None
            } else {
                // A stale/foreign listener must not make a desktop application
                // silently disappear. This process still runs, but does not
                // claim the fixed single-instance endpoint.
                eprintln!("[mountlet] {INSTANCE_ADDRESS} belongs to another process; continuing without the fixed listener.");
                TcpListener::bind("127.0.0.1:0").ok()
            }
        }
        Err(error) => {
            eprintln!("[mountlet] Could not create the instance listener: {error}");
            TcpListener::bind("127.0.0.1:0").ok()
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let Some(listener) = acquire_instance_listener() else {
        return;
    };
    // Only the process that owns the single-instance listener starts a log
    // session. A second launch must not split the running process's report log.
    begin_runtime_log_session();
    install_panic_log();
    // Sample remotes belong exclusively to the browser preview and tests. A
    // native launch with no rclone configuration must never display invented
    // accounts or let the user act on them.
    let mut state = discover_state().unwrap_or_else(empty_state);
    state.instance_listener = listener.try_clone().ok();
    tauri::Builder::default()
        .plugin(tauri_plugin_drag::init())
        .manage(state)
        .setup(move |app| {
            // Disconnected FUSE/WinFsp mounts can block directory enumeration.
            // Keep housekeeping away from Tauri's command registration and event
            // loop so a stale mount cannot leave a visible but inert webview.
            let cleanup_handle = app.handle().clone();
            std::thread::spawn(move || {
                cleanup_stale_mounts(&cleanup_handle.state::<AppState>());
            });
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            install_native_tray(app)?;
            if let Some(main) = app.get_webview_window("main") {
                main.set_skip_taskbar(true)?;
            }
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                for connection in listener.incoming() {
                    let Ok(mut connection) = connection else {
                        break;
                    };
                    let mut request = [0u8; 17];
                    if connection.read_exact(&mut request).is_err()
                        || request != INSTANCE_SHOW_REQUEST
                    {
                        continue;
                    }
                    let _ = connection.write_all(INSTANCE_SHOW_RESPONSE);
                    eprintln!("[mountlet] Existing instance received a show request.");
                    let app = handle.clone();
                    if let Err(error) = handle.run_on_main_thread(move || show_window_stack(&app)) {
                        eprintln!("[mountlet] Could not dispatch show request: {error}");
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::Focused(true) => {
                if let Ok(mut focused) = window.state::<AppState>().last_focused_window.lock() {
                    *focused = window.label().to_string();
                }
            }
            tauri::WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                if window.label() == "main" {
                    hide_window_stack(window.app_handle());
                } else {
                    let _ = window.hide();
                }
            }
            tauri::WindowEvent::Moved(position) if window.label() == "main" => {
                if !window.is_visible().unwrap_or(false) {
                    return;
                }
                let state = window.state::<AppState>();
                if let Ok(mut anchor) = state.window_anchor.lock() {
                    if moved_is_programmatic(&anchor)
                        || moved_matches_requested_position(&anchor, (position.x, position.y))
                    {
                        return;
                    }
                    if position.x.abs() <= 8 && position.y.abs() <= 8 {
                        return;
                    }
                    if anchor.last_set_physical.is_some() {
                        anchor.user_placed = true;
                    }
                };
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            startup_smoke_enabled,
            complete_startup_smoke,
            list_remotes,
            refresh_remote_usage,
            remote_registration_order,
            auto_mount_remote_ids,
            reorder_remotes,
            load_remote_config,
            save_remote_config,
            create_remote,
            delete_remote,
            rclone_output,
            app_version,
            show_startup_windows,
            clipboard_text,
            license_default_device_label,
            bug_report_preview,
            submit_bug_report,
            unreported_crash,
            mark_crash_reported,
            refresh_tray_menu,
            license_status,
            activate_license,
            license_devices,
            deactivate_license_device,
            notification_history,
            poll_notifications,
            mark_notification_seen,
            mark_notifications_seen,
            delete_notification,
            load_preferences,
            load_app_settings,
            save_app_settings,
            load_shortcuts,
            open_config_file,
            open_config_backup_folder,
            export_config_bundle,
            import_config_bundle,
            push_config_sync,
            pull_config_sync,
            config_sync_status,
            open_external,
            quit_app,
            load_browser_memory,
            persist_browser_memory,
            list_folder,
            search_index,
            start_initial_metadata_index,
            remember_selection,
            toggle_mount,
            open_remote_web,
            config_wizard_step,
            reauthenticate_remote,
            set_browser_window,
            set_browser_state,
            get_browser_state,
            focus_window,
            set_window_pinned,
            browser_window_side,
            apply_window_layout,
            invalidate_folder,
            rename_entry,
            create_folder,
            create_file,
            delete_entry,
            transfer_entry,
            upload_local_paths,
            make_offline,
            materialize_entries_for_drag,
            drag_preview_icon,
            remove_offline,
            remove_all_offline,
            clear_cache,
            sync_offline,
            changed_offline_remotes,
            detect_remote_cache_changes,
            resolve_offline_conflict,
            open_entry,
            open_mounted_folder,
            check_prerequisites,
            desktop_hints,
            oauth_port_status,
            terminate_oauth_rclone,
            drive_oauth_sources,
            list_file_managers,
            open_rclone_config_terminal,
            pick_config_bundle_path,
            show_desktop_notification,
            file_icon_data_url,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Mountlet");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn large_folder_is_an_immutable_backend_snapshot() {
        let state = sample_state();
        let folders = state.folders.blocking_read();
        let entries = folders
            .get(&("drive-ehol".into(), "Projects".into()))
            .unwrap();
        assert_eq!(entries.len(), 2500);
        assert_eq!(entries.first().unwrap().name, "Project 0001.txt");
        assert_eq!(entries.last().unwrap().name, "Project 2500.txt");
    }

    #[tokio::test]
    async fn selections_are_constant_size_state_not_folder_rewrites() {
        let state = sample_state();
        let key = ("drive-ehol".to_string(), "Projects".to_string());
        state.selections.write().await.insert(key.clone(), 2499);
        assert_eq!(state.selections.read().await.get(&key), Some(&2499));
        assert_eq!(state.folders.read().await.get(&key).unwrap().len(), 2500);
    }

    #[test]
    fn quoted_search_terms_and_window_anchors_are_deterministic() {
        assert_eq!(
            search_terms("alpha \"two words\" beta"),
            vec!["alpha", "two words", "beta"]
        );
        assert_eq!(anchored_axis(900.0, 100.0, 200.0, 0.0, 1000.0), 800.0);
        assert_eq!(anchored_axis(0.0, 100.0, 200.0, 0.0, 1000.0), 0.0);
        assert_eq!(
            anchored_popup_position(
                (995.0, 500.0),
                "right",
                (0.0, 30.0, 1000.0, 700.0),
                (400.0, 800.0)
            ),
            (587.0, 30.0)
        );
        assert_eq!(
            anchored_popup_position(
                (5.0, 500.0),
                "left",
                (0.0, 30.0, 1000.0, 700.0),
                (400.0, 300.0)
            ),
            (13.0, 350.0)
        );
        assert_eq!(
            exclude_tray_panel(
                (0.0, 0.0, 1000.0, 800.0),
                (976.0, 10.0, 24.0, 24.0),
                "right"
            ),
            (0.0, 42.0, 968.0, 758.0)
        );
        assert_eq!(
            exclude_tray_panel(
                (0.0, 30.0, 1000.0, 770.0),
                (976.0, 4.0, 24.0, 24.0),
                "right"
            ),
            (0.0, 30.0, 1000.0, 770.0)
        );
        assert_eq!(
            tray_edge_for_point((1900.0, 16.0), (0.0, 32.0, 1920.0, 1048.0), ""),
            "top"
        );
        assert_eq!(
            tray_edge_for_point((1900.0, 500.0), (0.0, 0.0, 1880.0, 1080.0), ""),
            "right"
        );
        assert_eq!(
            tray_edge_for_point((960.0, 540.0), (0.0, 32.0, 1920.0, 1048.0), "top"),
            "top"
        );
        let requested = WindowAnchor {
            last_set_physical: Some((5040, 2800)),
            ..Default::default()
        };
        assert!(moved_matches_requested_position(&requested, (5040, 2800)));
        assert!(moved_matches_requested_position(&requested, (5058, 2824)));
        assert!(!moved_matches_requested_position(&requested, (4980, 2800)));
    }

    #[test]
    fn notice_times_accept_server_numbers_and_rfc3339_strings() {
        assert_eq!(
            notice_time(Some(&serde_json::json!(1_800_000_000.5))),
            Some(1_800_000_000.5)
        );
        assert_eq!(
            notice_time(Some(&serde_json::json!("1970-01-01T00:00:10Z"))),
            Some(10.0)
        );
        assert_eq!(notice_time(Some(&serde_json::json!(null))), None);
    }

    #[test]
    fn hide_preserves_the_native_focus_owner() {
        assert_eq!(focused_window_label(true, false), Some("browser"));
        assert_eq!(focused_window_label(false, true), Some("main"));
        assert_eq!(focused_window_label(false, false), None);
        // Defensive priority if a backend transiently reports both focused.
        assert_eq!(focused_window_label(true, true), Some("browser"));
    }

    #[test]
    fn diagnostic_redaction_never_leaks_credentials() {
        let source = "[remote]\ntype = drive\ntoken = secret-token\nclient_secret = secret\nuser = visible@example.com\n";
        let redacted = redact_config(source);
        assert!(!redacted.contains("secret-token"));
        assert!(!redacted.contains("client_secret = secret"));
        assert!(redacted.contains("type = drive"));
        assert!(redacted.contains("user = visible@example.com"));
        let arbitrary =
            redact_sensitive_text("MNT-ABCDEFGH token: abc https://x.test/?code=123&safe=yes");
        assert!(!arbitrary.contains("ABCDEFGH"));
        assert!(!arbitrary.contains("token: abc"));
        assert!(!arbitrary.contains("code=123"));
    }

    #[test]
    fn runtime_reports_only_the_current_rust_session() {
        let log = "old Qt warning\nMountlet Rust runtime started: Mountlet 0.7.0 at 1\nfirst session\nMountlet shutdown cleanly\nMountlet Rust runtime started: Mountlet 0.7.0 at 2\ncurrent session\n";
        assert_eq!(
            current_runtime_session(log),
            "Mountlet Rust runtime started: Mountlet 0.7.0 at 2\ncurrent session\n"
        );
        assert_eq!(
            previous_runtime_session(log),
            "Mountlet Rust runtime started: Mountlet 0.7.0 at 1\nfirst session\nMountlet shutdown cleanly\n"
        );
        assert!(!current_runtime_session(log).contains("Qt warning"));
    }

    #[test]
    fn offline_change_detection_uses_integer_file_state() {
        let path = env::temp_dir().join(format!("mountlet-offline-state-{}", std::process::id()));
        fs::write(&path, b"baseline").unwrap();
        let metadata = fs::metadata(&path).unwrap();
        let modified = metadata
            .modified()
            .unwrap()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
            .min(u64::MAX as u128) as u64;
        let record = serde_json::json!({"local_size": metadata.len(), "local_mtime_ns": modified});
        assert!(!offline_record_changed(&record, &metadata));
        fs::write(&path, b"changed and larger").unwrap();
        assert!(offline_record_changed(
            &record,
            &fs::metadata(&path).unwrap()
        ));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn cache_badge_follows_an_existing_local_file() {
        let root = env::temp_dir().join(format!("mountlet-cache-badge-{}", std::process::id()));
        fs::create_dir_all(root.join("folder")).unwrap();
        fs::write(root.join("folder/document.txt"), b"cached").unwrap();
        let state = cache_state(
            &serde_json::Map::new(),
            Some(&root),
            "folder/document.txt",
            false,
        );
        assert!(state.cached);
        assert!(!state.offline);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn temporary_cache_files_participate_in_sync_and_conflict_detection() {
        assert!(is_complete_managed_file(
            &serde_json::json!({"is_dir": false, "protected": false, "complete": true})
        ));
        assert!(is_complete_managed_file(
            &serde_json::json!({"is_dir": false, "protected": true, "complete": true})
        ));
        assert!(!is_complete_managed_file(
            &serde_json::json!({"is_dir": true, "protected": false, "complete": true})
        ));
        assert!(!is_complete_managed_file(
            &serde_json::json!({"is_dir": false, "protected": false, "complete": false})
        ));
    }

    #[test]
    fn config_sync_detects_changes_before_and_after_first_push() {
        assert!(config_fingerprint_changed(None, "current"));
        assert!(!config_fingerprint_changed(Some("current"), "current"));
        assert!(config_fingerprint_changed(Some("previous"), "current"));
    }

    #[test]
    fn file_hashes_are_stable_and_content_sensitive() {
        let path = env::temp_dir().join(format!("mountlet-hash-{}", std::process::id()));
        fs::write(&path, b"one").unwrap();
        let first = file_sha256(&path).unwrap();
        assert_eq!(first, file_sha256(&path).unwrap());
        fs::write(&path, b"two").unwrap();
        assert_ne!(first, file_sha256(&path).unwrap());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn stale_mount_cleanup_never_descends_into_live_mounts() {
        let root = env::temp_dir().join(format!("mountlet-cleanup-{}", std::process::id()));
        let mounted = root.join("mounted-remote");
        let mounted_child = mounted.join("provider-folder");
        let ordinary_child = root.join("ordinary").join("empty");
        fs::create_dir_all(&mounted_child).unwrap();
        fs::create_dir_all(&ordinary_child).unwrap();

        let directories = cleanup_directories(&root, |path| path == mounted);
        assert!(directories.contains(&mounted));
        assert!(!directories.contains(&mounted_child));
        assert!(directories.contains(&ordinary_child));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn default_license_device_label_identifies_the_machine_and_platform() {
        let label = license::default_device_label();
        let (machine, platform) = label.rsplit_once(" (").expect("platform suffix");
        assert!(!machine.trim().is_empty());
        assert!(platform.ends_with(')'));
    }

    #[test]
    #[ignore = "requires the user's active metadata index"]
    fn active_metadata_search_smoke_test() {
        let results = search_metadata(&SearchRequest {
            query: "pdf".into(),
            remote_id: None,
            limit: 10,
        })
        .expect("metadata search should complete");
        assert!(results.len() <= 11);
        assert!(results
            .iter()
            .all(|result| result.name.to_lowercase().contains("pdf")));
    }

    #[test]
    #[ignore = "requires the user's active rclone configuration"]
    fn active_config_discovery_smoke_test() {
        let state = discover_state().expect("active rclone configuration should be readable");
        let remotes = state.remotes.into_inner().unwrap();
        assert!(!remotes.is_empty());
        println!("discovered {} remotes", remotes.len());
        for remote in remotes {
            println!("{} [{}]", remote.id, remote.provider);
        }
    }

    #[test]
    fn rclone_section_keeps_token_for_completeness_checks() {
        let path = env::temp_dir().join(format!("mountlet-rclone-section-{}", std::process::id()));
        fs::write(
            &path,
            "[drive]\ntype = drive\ntoken = {\"access_token\":\"x\"}\n",
        )
        .unwrap();
        let values = read_rclone_section(&path, "drive").unwrap();
        assert_eq!(values.get("type").map(String::as_str), Some("drive"));
        assert!(values
            .get("token")
            .is_some_and(|value| value.contains("access_token")));
        assert!(platform::remote_section_is_configured("drive", &values));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn tray_fallback_avoids_origin() {
        let expected = if platform::is_wayland() && platform::is_gnome() {
            (592.0, 8.0)
        } else {
            (592.0, 492.0)
        };
        assert_eq!(
            fallback_tray_popup_position(0.0, 0.0, 1000.0, 800.0, 400.0, 300.0),
            Some(expected)
        );
        assert_eq!(
            clamped_origin(
                50.0,
                40.0,
                400.0,
                300.0,
                WorkArea {
                    x: 0.0,
                    y: 30.0,
                    width: 1000.0,
                    height: 700.0
                }
            ),
            (50.0, 40.0)
        );
        assert_eq!(
            clamped_origin(
                900.0,
                600.0,
                400.0,
                300.0,
                WorkArea {
                    x: 0.0,
                    y: 30.0,
                    width: 1000.0,
                    height: 700.0
                }
            ),
            (600.0, 430.0)
        );
    }
}
