use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;
#[cfg(not(target_os = "macos"))]
use std::process::Stdio;

use serde::Serialize;

pub const RCLONE_OAUTH_LOCAL_PORT: u16 = 53682;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Prerequisite {
    pub key: String,
    pub label: String,
    pub ready: bool,
    pub detail: String,
    pub help_url: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopHints {
    pub wayland: bool,
    pub gnome: bool,
    pub pin_supported: bool,
    pub system_name: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OauthPortStatus {
    pub available: bool,
    pub owner: String,
    pub rclone_pid: Option<u32>,
}

pub fn desktop_hints() -> DesktopHints {
    let wayland = is_wayland();
    let gnome = is_gnome();
    DesktopHints {
        pin_supported: !(wayland && gnome),
        wayland,
        gnome,
        system_name: if cfg!(target_os = "windows") {
            "Windows".into()
        } else if cfg!(target_os = "macos") {
            "Darwin".into()
        } else {
            "Linux".into()
        },
    }
}

pub fn is_wayland() -> bool {
    cfg!(target_os = "linux") && std::env::var_os("WAYLAND_DISPLAY").is_some()
}

pub fn is_gnome() -> bool {
    if !cfg!(target_os = "linux") {
        return false;
    }
    let desktop = format!(
        "{}:{}",
        std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default(),
        std::env::var("DESKTOP_SESSION").unwrap_or_default()
    )
    .to_ascii_lowercase();
    desktop.contains("gnome")
}

pub fn effective_window_mode(requested: &str) -> String {
    if is_wayland() || requested == "single" {
        "single".into()
    } else {
        "multiple".into()
    }
}

pub fn check_prerequisites(rclone: Option<&str>) -> Vec<Prerequisite> {
    let rclone_ready = rclone
        .map(|path| Path::new(path).is_file() || which(path).is_some())
        .unwrap_or(false);
    let driver = mount_driver_name();
    let driver_ready = mount_driver_available();
    let (rclone_help, driver_help) = match std::env::consts::OS {
        "windows" => (
            "https://rclone.org/install/",
            "https://winfsp.dev/rel/",
        ),
        "macos" => (
            "https://rclone.org/install/",
            "https://macfuse.github.io/",
        ),
        _ => (
            "https://rclone.org/install/",
            "https://rclone.org/install/#installing-on-linux",
        ),
    };
    vec![
        Prerequisite {
            key: "rclone".into(),
            label: "rclone".into(),
            ready: rclone_ready,
            detail: if rclone_ready {
                format!("Found {}", rclone.unwrap_or("rclone"))
            } else {
                rclone_guidance()
            },
            help_url: rclone_help.into(),
        },
        Prerequisite {
            key: "mount_driver".into(),
            label: driver.into(),
            ready: driver_ready,
            detail: if driver_ready {
                format!("Found {driver} mount support.")
            } else {
                mount_driver_guidance()
            },
            help_url: driver_help.into(),
        },
    ]
}

pub fn mount_driver_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "WinFsp"
    } else if cfg!(target_os = "macos") {
        "macFUSE"
    } else {
        "FUSE"
    }
}

pub fn mount_driver_available() -> bool {
    if cfg!(target_os = "windows") {
        which("winfsp-x64.dll").is_some()
            || Path::new(r"C:\Program Files (x86)\WinFsp\bin\winfsp-x64.dll").exists()
            || Path::new(r"C:\Program Files\WinFsp\bin\winfsp-x64.dll").exists()
    } else if cfg!(target_os = "macos") {
        Path::new("/Library/Filesystems/macfuse.fs").exists()
            || Path::new("/Library/Filesystems/osxfuse.fs").exists()
    } else {
        which("fusermount3").is_some() || which("fusermount").is_some()
    }
}

pub fn mount_driver_about_line() -> String {
    if cfg!(target_os = "windows") {
        format!(
            "WinFsp: {}",
            windows_winfsp_version().unwrap_or_else(|| {
                if mount_driver_available() {
                    "installed".into()
                } else {
                    "unavailable".into()
                }
            })
        )
    } else if cfg!(target_os = "macos") {
        format!(
            "macFUSE: {}",
            if mount_driver_available() {
                "installed"
            } else {
                "unavailable"
            }
        )
    } else {
        let binary = which("fusermount3")
            .or_else(|| which("fusermount"))
            .map(|path| path.display().to_string())
            .unwrap_or_else(|| "unavailable".into());
        format!("FUSE: {binary}")
    }
}

fn rclone_guidance() -> String {
    if cfg!(target_os = "linux") {
        "Install rclone: sudo apt install rclone".into()
    } else {
        "Install rclone.".into()
    }
}

fn mount_driver_guidance() -> String {
    if cfg!(target_os = "windows") {
        "Install WinFsp to enable filesystem mounts.".into()
    } else if cfg!(target_os = "macos") {
        "Install macFUSE to enable filesystem mounts.".into()
    } else {
        "Install FUSE: sudo apt install fuse3".into()
    }
}

fn windows_winfsp_version() -> Option<String> {
    Command::new("reg")
        .args([
            "query",
            r"HKLM\SOFTWARE\WinFsp",
            "/v",
            "Version",
        ])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|text| {
            text.lines()
                .find(|line| line.contains("Version"))
                .and_then(|line| line.split_whitespace().last())
                .map(str::to_string)
        })
}

pub fn oauth_port_status() -> OauthPortStatus {
    let available = TcpListener::bind(("127.0.0.1", RCLONE_OAUTH_LOCAL_PORT)).is_ok();
    if available {
        return OauthPortStatus {
            available: true,
            owner: String::new(),
            rclone_pid: None,
        };
    }
    let owner = port_owner_hint(RCLONE_OAUTH_LOCAL_PORT);
    let rclone_pid = rclone_pid_from_owner(&owner);
    OauthPortStatus {
        available: false,
        owner,
        rclone_pid,
    }
}

pub fn terminate_process_id(pid: u32) -> bool {
    if cfg!(target_os = "windows") {
        Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    } else {
        Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
}

fn port_owner_hint(port: u16) -> String {
    for command in [
        vec![
            "ss".into(),
            "-ltnp".into(),
            format!("sport = :{port}"),
        ],
        vec![
            "lsof".into(),
            "-nP".into(),
            format!("-iTCP:{port}"),
            "-sTCP:LISTEN".into(),
        ],
    ] {
        if which(&command[0]).is_none() {
            continue;
        }
        if let Ok(output) = Command::new(&command[0]).args(&command[1..]).output() {
            let text = String::from_utf8_lossy(&output.stdout);
            if output.status.success() && !text.trim().is_empty() {
                return text
                    .lines()
                    .find(|line| line.contains("pid="))
                    .or_else(|| text.lines().nth(1))
                    .unwrap_or(text.trim())
                    .trim()
                    .to_string();
            }
        }
    }
    String::new()
}

fn rclone_pid_from_owner(owner: &str) -> Option<u32> {
    if !owner.to_ascii_lowercase().contains("rclone") {
        return None;
    }
    owner
        .split("pid=")
        .nth(1)
        .or_else(|| {
            owner
                .split(|character: char| !character.is_ascii_digit())
                .find(|value| !value.is_empty())
        })
        .and_then(|value| {
            value
                .chars()
                .take_while(|character| character.is_ascii_digit())
                .collect::<String>()
                .parse()
                .ok()
        })
}

pub fn open_rclone_config_terminal(rclone: &str, config: &Path) -> Result<String, String> {
    if let Some(parent) = config.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    if !config.exists() {
        fs::write(config, "").map_err(|error| error.to_string())?;
    }
    #[cfg(not(target_os = "macos"))]
    let title = "Mountlet rclone config";
    #[cfg(target_os = "windows")]
    {
        Command::new("cmd")
            .args([
                "/c",
                "start",
                title,
                "cmd.exe",
                "/k",
                &format!("\"{}\" --config \"{}\" config", rclone, config.display()),
            ])
            .spawn()
            .map_err(|error| error.to_string())?;
        Ok(config.display().to_string())
    }
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "tell application \"Terminal\" to do script \"{} --config '{}' config\"",
            rclone,
            config.display()
        );
        Command::new("osascript")
            .args(["-e", &script])
            .spawn()
            .map_err(|error| error.to_string())?;
        Ok(config.display().to_string())
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let command_line = format!(
            "{} --config {} config; printf '\\nPress Enter to close this terminal...'; read _",
            shell_quote(rclone),
            shell_quote(&config.display().to_string())
        );
        let candidates: &[Vec<String>] = &[
            vec![
                "x-terminal-emulator".into(),
                "-T".into(),
                title.into(),
                "-e".into(),
                "sh".into(),
                "-lc".into(),
                command_line.clone(),
            ],
            vec![
                "konsole".into(),
                "--title".into(),
                title.into(),
                "-e".into(),
                "sh".into(),
                "-lc".into(),
                command_line.clone(),
            ],
            vec![
                "gnome-terminal".into(),
                "--title".into(),
                title.into(),
                "--".into(),
                "sh".into(),
                "-lc".into(),
                command_line.clone(),
            ],
            vec![
                "xfce4-terminal".into(),
                "--title".into(),
                title.into(),
                "-e".into(),
                format!("sh -lc {}", shell_quote(&command_line)),
            ],
            vec![
                "xterm".into(),
                "-T".into(),
                title.into(),
                "-e".into(),
                "sh".into(),
                "-lc".into(),
                command_line,
            ],
        ];
        for candidate in candidates {
            if which(&candidate[0]).is_none() {
                continue;
            }
            if Command::new(&candidate[0])
                .args(&candidate[1..])
                .spawn()
                .is_ok()
            {
                return Ok(config.display().to_string());
            }
        }
        Err("No terminal emulator was found. Install x-terminal-emulator, Konsole, GNOME Terminal, XFCE Terminal, or xterm.".into())
    }
}

pub fn pick_bundle_path(save: bool, suggested: &str) -> Option<String> {
    #[cfg_attr(
        not(any(target_os = "windows", target_os = "macos")),
        allow(unused_variables)
    )]
    let filename = Path::new(suggested)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("config.mountlet");
    #[cfg(target_os = "macos")]
    {
        let script = if save {
            format!(
                "POSIX path of (choose file name with prompt \"Mountlet config bundle\" default name \"{filename}\")"
            )
        } else {
            "POSIX path of (choose file with prompt \"Mountlet config bundle\")".into()
        };
        Command::new("osascript")
            .args(["-e", &script])
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    }
    #[cfg(target_os = "windows")]
    {
        let kind = if save { "SaveFileDialog" } else { "OpenFileDialog" };
        let script = format!(
            "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.{kind}; $d.Filter = 'Mountlet bundle (*.mountlet)|*.mountlet|All files (*.*)|*.*'; $d.FileName = '{filename}'; if ($d.ShowDialog() -eq 'OK') {{ $d.FileName }}"
        );
        Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let mut command = if which("zenity").is_some() {
            let mut value = Command::new("zenity");
            value.args(["--file-selection", "--title=Mountlet config bundle", "--file-filter=*.mountlet"]);
            if save {
                value.args(["--save", "--confirm-overwrite"]);
            }
            value.arg(format!("--filename={suggested}"));
            value
        } else if which("kdialog").is_some() {
            let mut value = Command::new("kdialog");
            if save {
                value.args(["--getsavefilename", suggested, "*.mountlet"]);
            } else {
                value.args(["--getopenfilename", suggested, "*.mountlet"]);
            }
            value
        } else {
            return None;
        };
        command
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    }
}

pub fn show_desktop_notification(title: &str, message: &str) {
    #[cfg(target_os = "linux")]
    {
        let _ = Command::new("notify-send")
            .args(["-a", "Mountlet", title, message])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display notification \"{}\" with title \"{}\"",
            message.replace('"', "\\\""),
            title.replace('"', "\\\"")
        );
        let _ = Command::new("osascript").args(["-e", &script]).spawn();
    }
    #[cfg(target_os = "windows")]
    {
        let script = format!(
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); $text = $template.GetElementsByTagName('text'); $text.Item(0).AppendChild($template.CreateTextNode('{}')) > $null; $text.Item(1).AppendChild($template.CreateTextNode('{}')) > $null; [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Mountlet').Show($template)",
            title.replace('\'', "''"),
            message.replace('\'', "''")
        );
        let _ = Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }
}

pub fn remote_section_is_configured(backend: &str, values: &std::collections::HashMap<String, String>) -> bool {
    let backend = backend.to_ascii_lowercase();
    let get = |key: &str| values.get(key).map(|value| value.trim().to_string()).unwrap_or_default();
    match backend.as_str() {
        "drive" | "gphotos" | "dropbox" | "box" | "pcloud" => {
            !get("token").is_empty() || !get("service_account_file").is_empty()
        }
        "onedrive" => {
            !get("token").is_empty() && !get("drive_id").is_empty() && !get("drive_type").is_empty()
        }
        "s3" => {
            let provider = get("provider");
            let env_auth = matches!(get("env_auth").to_ascii_lowercase().as_str(), "true" | "1" | "yes" | "on");
            let has_keys = !get("access_key_id").is_empty() && !get("secret_access_key").is_empty();
            if provider.is_empty() || !(env_auth || has_keys) {
                return false;
            }
            provider.eq_ignore_ascii_case("aws") || !get("endpoint").is_empty()
        }
        "webdav" => {
            let url = get("url");
            url.starts_with("http://") || url.starts_with("https://")
        }
        "koofr" => !get("provider").is_empty() && !get("user").is_empty() && !get("password").is_empty(),
        "protondrive" => {
            let user = if get("username").is_empty() { get("user") } else { get("username") };
            let password = if get("password").is_empty() { get("pass") } else { get("password") };
            !user.is_empty() && !password.is_empty()
        }
        "iclouddrive" => !get("apple_id").is_empty() && !get("password").is_empty(),
        "mega" => !get("user").is_empty() && !get("pass").is_empty(),
        other => !other.is_empty(),
    }
}

pub fn gphotos_album_writable(path: &str) -> bool {
    let parts = path
        .trim_matches('/')
        .split('/')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>();
    parts.first().is_some_and(|part| part.eq_ignore_ascii_case("album")) && parts.len() >= 2
}

pub fn file_icon_data_url(name: &str, is_dir: bool) -> Option<String> {
    if is_dir {
        return load_theme_icon(&["places/folder", "mimetypes/inode-directory", "mimetypes/inode_directory"]);
    }
    let extension = Path::new(name)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = mime_for_extension(&extension);
    let icon = mime.replace('/', "-");
    load_theme_icon(&[
        &format!("mimetypes/{icon}"),
        &format!("mimetypes/{}", icon.replace('-', "_")),
        "mimetypes/text-x-generic",
        "mimetypes/application-octet-stream",
    ])
}

fn mime_for_extension(extension: &str) -> &'static str {
    match extension {
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "svg" | "heic" => "image/jpeg",
        "mp3" | "ogg" | "wav" | "flac" | "m4a" => "audio/mpeg",
        "mp4" | "mkv" | "webm" | "mov" => "video/mp4",
        "pdf" => "application/pdf",
        "zip" | "7z" | "rar" | "tar" | "gz" => "application/zip",
        "txt" | "md" => "text/plain",
        "html" | "htm" => "text/html",
        "rs" | "py" | "ts" | "js" | "c" | "h" => "text/x-script",
        "docx" | "odt" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx" | "ods" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _ => "application/octet-stream",
    }
}

fn load_theme_icon(candidates: &[&str]) -> Option<String> {
    let themes = [
        PathBuf::from("/usr/share/icons/Adwaita"),
        PathBuf::from("/usr/share/icons/hicolor"),
        PathBuf::from("/usr/share/icons/breeze"),
        PathBuf::from("/usr/share/icons/Papirus"),
    ];
    let sizes = ["48x48", "32x32", "24x24", "scalable"];
    for theme in themes {
        for size in sizes {
            for candidate in candidates {
                for extension in ["png", "svg"] {
                    let path = theme.join(size).join(format!("{candidate}.{extension}"));
                    if path.is_file() {
                        return file_to_data_url(&path);
                    }
                }
            }
        }
    }
    None
}

fn file_to_data_url(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let mime = if path.extension().and_then(|value| value.to_str()) == Some("svg") {
        "image/svg+xml"
    } else {
        "image/png"
    };
    Some(format!("data:{mime};base64,{}", base64_encode(&bytes)))
}

fn base64_encode(bytes: &[u8]) -> String {
    const TABLE: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::new();
    for chunk in bytes.chunks(3) {
        let a = chunk[0] as u32;
        let b = chunk.get(1).copied().unwrap_or(0) as u32;
        let c = chunk.get(2).copied().unwrap_or(0) as u32;
        let triple = (a << 16) | (b << 8) | c;
        output.push(TABLE[((triple >> 18) & 63) as usize] as char);
        output.push(TABLE[((triple >> 12) & 63) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((triple >> 6) & 63) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(triple & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

fn which(name: &str) -> Option<PathBuf> {
    if name.contains('/') || name.contains('\\') {
        let path = PathBuf::from(name);
        return path.exists().then_some(path);
    }
    let path_var = std::env::var_os("PATH")?;
    std::env::split_paths(&path_var).find_map(|root| {
        let candidate = root.join(name);
        candidate.is_file().then_some(candidate)
    })
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn shell_quote(value: &str) -> String {
    if value
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.' | '/' | ':'))
    {
        value.into()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn drive_without_token_is_incomplete() {
        let mut values = HashMap::new();
        values.insert("type".into(), "drive".into());
        assert!(!remote_section_is_configured("drive", &values));
        values.insert("token".into(), "{}".into());
        assert!(remote_section_is_configured("drive", &values));
    }

    #[test]
    fn google_photos_albums_are_writable_only_inside_album() {
        assert!(gphotos_album_writable("album/Vacation"));
        assert!(gphotos_album_writable("album/Vacation/file.jpg"));
        assert!(!gphotos_album_writable("media/all/file.jpg"));
        assert!(!gphotos_album_writable(""));
    }

    #[test]
    fn wayland_forces_single_window_mode() {
        if is_wayland() {
            assert_eq!(effective_window_mode("multiple"), "single");
        } else {
            assert_eq!(effective_window_mode("multiple"), "multiple");
            assert_eq!(effective_window_mode("single"), "single");
        }
    }
}
