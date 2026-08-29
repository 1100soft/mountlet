use crate::child_process::Command;
#[cfg(not(any(target_os = "windows", target_os = "macos")))]
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Stdio;

use serde::Serialize;

pub const SYSTEM_FILE_MANAGER_ID: &str = "system";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileManager {
    pub identifier: String,
    pub label: String,
    #[serde(skip)]
    pub command: Vec<String>,
    pub is_system_default: bool,
    pub supports_new_window: bool,
}

pub fn discover() -> Vec<FileManager> {
    #[cfg(target_os = "windows")]
    {
        windows_file_managers()
    }
    #[cfg(target_os = "macos")]
    {
        macos_file_managers()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        linux_file_managers()
    }
}

pub fn resolve(identifier: &str) -> FileManager {
    let managers = discover();
    let requested = if identifier.trim().is_empty() {
        default_id()
    } else {
        identifier.trim().to_string()
    };
    managers
        .iter()
        .find(|manager| manager.identifier == requested)
        .cloned()
        .or_else(|| {
            managers
                .iter()
                .find(|manager| manager.identifier == default_id())
                .cloned()
        })
        .or_else(|| managers.first().cloned())
        .unwrap_or(FileManager {
            identifier: SYSTEM_FILE_MANAGER_ID.into(),
            label: "System folder handler".into(),
            command: Vec::new(),
            is_system_default: true,
            supports_new_window: false,
        })
}

pub fn open_folder(
    path: &Path,
    manager_id: &str,
    behavior: &str,
    focus: bool,
) -> Result<(), String> {
    let path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    if !path.is_dir() {
        return Err("This remote folder is not currently mounted.".into());
    }
    let manager = resolve(manager_id);
    if behavior == "file-manager-service"
        && manager.identifier == SYSTEM_FILE_MANAGER_ID
        && show_folder_with_file_manager_service(&path)
    {
        return Ok(());
    }
    if matches!(behavior, "current_desktop" | "existing_window")
        && open_with_known_file_manager(&path, behavior, focus, &manager)
    {
        return Ok(());
    }
    if open_with_file_manager(&manager, &path, behavior == "new_window") {
        return Ok(());
    }
    crate::open_local_path(&path)
}

fn default_id() -> String {
    if cfg!(target_os = "windows") {
        "explorer".into()
    } else if cfg!(target_os = "macos") {
        "finder".into()
    } else {
        SYSTEM_FILE_MANAGER_ID.into()
    }
}

fn expand_command(template: &[String], path: &Path) -> Vec<String> {
    let mut command = Vec::new();
    let mut inserted = false;
    let path_text = path.to_string_lossy().into_owned();
    for part in template {
        if matches!(part.as_str(), "%f" | "%F" | "%u" | "%U") {
            command.push(path_text.clone());
            inserted = true;
        } else if part.starts_with('%') {
            continue;
        } else {
            command.push(part.clone());
        }
    }
    if !inserted {
        command.push(path_text);
    }
    command
}

fn new_window_command(identifier: &str, command: Vec<String>) -> Vec<String> {
    let normalized = identifier.to_ascii_lowercase();
    if normalized == "explorer" && !command.is_empty() {
        let mut result = vec![command[0].clone(), "/n,".into()];
        result.extend(command.into_iter().skip(1));
        return result;
    }
    if !command.is_empty()
        && (normalized.contains("dolphin")
            || ["nautilus", "nemo", "thunar", "pcmanfm", "caja"]
                .iter()
                .any(|name| normalized.contains(name)))
        && !command.iter().any(|part| part == "--new-window")
    {
        let mut result = vec![command[0].clone(), "--new-window".into()];
        result.extend(command.into_iter().skip(1));
        return result;
    }
    command
}

fn open_with_file_manager(manager: &FileManager, path: &Path, new_window: bool) -> bool {
    if manager.command.is_empty() {
        return false;
    }
    let mut command = expand_command(&manager.command, path);
    if new_window && manager.supports_new_window {
        command = new_window_command(&manager.identifier, command);
    }
    spawn_detached(&command)
}

fn open_with_known_file_manager(
    path: &Path,
    behavior: &str,
    focus: bool,
    manager: &FileManager,
) -> bool {
    let selected = if manager.identifier == SYSTEM_FILE_MANAGER_ID {
        default_directory_app()
    } else {
        manager.identifier.clone()
    }
    .to_ascii_lowercase();
    if selected.contains("dolphin") {
        if behavior == "new_window" {
            return false;
        }
        if open_dolphin_tab(path, behavior == "current_desktop", focus) {
            return true;
        }
        if behavior == "current_desktop" {
            return spawn_detached(&[
                "dolphin".into(),
                "--new-window".into(),
                path.display().to_string(),
            ]);
        }
    }
    false
}

fn default_directory_app() -> String {
    Command::new("xdg-mime")
        .args(["query", "default", "inode/directory"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_string())
        .unwrap_or_default()
}

fn show_folder_with_file_manager_service(path: &Path) -> bool {
    let uri = format!("file://{}", path.display());
    spawn_detached(&[
        "dbus-send".into(),
        "--session".into(),
        "--type=method_call".into(),
        "--dest=org.freedesktop.FileManager1".into(),
        "/org/freedesktop/FileManager1".into(),
        "org.freedesktop.FileManager1.ShowFolders".into(),
        format!("array:string:{uri}"),
        "string:".into(),
    ])
}

fn open_dolphin_tab(path: &Path, current_desktop: bool, _focus: bool) -> bool {
    let uri = format!("file://{}", path.display());
    let output = Command::new("qdbus")
        .args(["org.kde.dolphin*"])
        .output()
        .ok();
    let Some(text) = output
        .filter(|value| value.status.success())
        .and_then(|value| String::from_utf8(value.stdout).ok())
    else {
        return false;
    };
    for service in text.lines().filter(|line| line.contains("org.kde.dolphin")) {
        let windows = Command::new("qdbus")
            .args([
                service.trim(),
                "/dolphin",
                "org.freedesktop.DBus.Introspectable.Introspect",
            ])
            .output()
            .ok()
            .and_then(|value| String::from_utf8(value.stdout).ok())
            .unwrap_or_default();
        if !windows.contains("dolphinwindow") && current_desktop {
            continue;
        }
        let method = "org.kde.dolphin.MainWindow.openDirectories";
        if spawn_detached(&[
            "qdbus".into(),
            service.trim().into(),
            "/dolphin/Dolphin_1".into(),
            method.into(),
            uri.clone(),
            "false".into(),
        ]) {
            return true;
        }
    }
    false
}

fn spawn_detached(command: &[String]) -> bool {
    if command.is_empty() {
        return false;
    }
    Command::new(&command[0])
        .args(&command[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .is_ok()
}

#[cfg(target_os = "windows")]
fn windows_file_managers() -> Vec<FileManager> {
    let mut managers = vec![FileManager {
        identifier: "explorer".into(),
        label: "File Explorer".into(),
        command: vec!["explorer.exe".into()],
        is_system_default: true,
        supports_new_window: true,
    }];
    for (identifier, label, executable) in [
        ("files", "Files", "Files.exe"),
        ("directory-opus", "Directory Opus", "dopus.exe"),
        ("total-commander", "Total Commander", "TOTALCMD64.EXE"),
        ("freecommander", "FreeCommander", "FreeCommander.exe"),
    ] {
        if which(executable).is_some() {
            managers.push(FileManager {
                identifier: identifier.into(),
                label: label.into(),
                command: vec![executable.into()],
                is_system_default: false,
                supports_new_window: true,
            });
        }
    }
    managers.push(FileManager {
        identifier: SYSTEM_FILE_MANAGER_ID.into(),
        label: "System folder handler".into(),
        command: Vec::new(),
        is_system_default: false,
        supports_new_window: false,
    });
    managers
}

#[cfg(target_os = "macos")]
fn macos_file_managers() -> Vec<FileManager> {
    let mut managers = vec![FileManager {
        identifier: "finder".into(),
        label: "Finder".into(),
        command: vec!["/usr/bin/open".into()],
        is_system_default: true,
        supports_new_window: true,
    }];
    let roots = [
        PathBuf::from("/Applications"),
        dirs_home().join("Applications"),
    ];
    for (identifier, label, app_name) in [
        ("path-finder", "Path Finder", "Path Finder"),
        ("forklift", "ForkLift", "ForkLift"),
        ("commander-one", "Commander One", "Commander One"),
    ] {
        if roots
            .iter()
            .any(|root| root.join(format!("{app_name}.app")).exists())
        {
            managers.push(FileManager {
                identifier: identifier.into(),
                label: label.into(),
                command: vec!["/usr/bin/open".into(), "-a".into(), app_name.into()],
                is_system_default: false,
                supports_new_window: true,
            });
        }
    }
    managers.push(FileManager {
        identifier: SYSTEM_FILE_MANAGER_ID.into(),
        label: "System folder handler".into(),
        command: Vec::new(),
        is_system_default: false,
        supports_new_window: false,
    });
    managers
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn linux_file_managers() -> Vec<FileManager> {
    let default_id = default_directory_app();
    let mut discovered: Vec<FileManager> = Vec::new();
    for path in linux_desktop_files() {
        if let Some(manager) = parse_linux_file_manager(&path, &default_id) {
            if !discovered
                .iter()
                .any(|existing| existing.identifier == manager.identifier)
            {
                discovered.push(manager);
            }
        }
    }
    discovered.sort_by(|left, right| {
        (
            left.identifier != default_id,
            left.label.to_ascii_lowercase(),
        )
            .cmp(&(
                right.identifier != default_id,
                right.label.to_ascii_lowercase(),
            ))
    });
    let default_label = discovered
        .iter()
        .find(|manager| manager.identifier == default_id)
        .map(|manager| manager.label.clone())
        .unwrap_or_else(|| desktop_id_label(&default_id));
    let mut managers = vec![FileManager {
        identifier: SYSTEM_FILE_MANAGER_ID.into(),
        label: if default_label.is_empty() {
            "System default".into()
        } else {
            format!("System default ({default_label})")
        },
        command: Vec::new(),
        is_system_default: true,
        supports_new_window: false,
    }];
    managers.extend(discovered);
    managers
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn linux_desktop_files() -> Vec<PathBuf> {
    let home = dirs_home();
    let data_home = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".local/share"));
    let data_dirs =
        std::env::var("XDG_DATA_DIRS").unwrap_or_else(|_| "/usr/local/share:/usr/share".into());
    let mut files = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for root in std::iter::once(data_home).chain(data_dirs.split(':').map(PathBuf::from)) {
        let applications = root.join("applications");
        let Ok(entries) = fs::read_dir(&applications) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) != Some("desktop") {
                continue;
            }
            let identifier = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            if seen.insert(identifier.to_string()) {
                files.push(path);
            }
        }
    }
    files
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn parse_linux_file_manager(path: &Path, default_id: &str) -> Option<FileManager> {
    let text = fs::read_to_string(path).ok()?;
    let mut in_entry = false;
    let mut name = String::new();
    let mut exec = String::new();
    let mut try_exec = String::new();
    let mut mime = String::new();
    let mut categories = String::new();
    let mut hidden = false;
    let mut no_display = false;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') {
            in_entry = line == "[Desktop Entry]";
            continue;
        }
        if !in_entry {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            match key {
                "Name" if name.is_empty() => name = value.to_string(),
                "Exec" => exec = value.to_string(),
                "TryExec" => try_exec = value.to_string(),
                "MimeType" => mime = value.to_string(),
                "Categories" => categories = value.to_string(),
                "Hidden" if value.eq_ignore_ascii_case("true") => hidden = true,
                "NoDisplay" if value.eq_ignore_ascii_case("true") => no_display = true,
                _ => {}
            }
        }
    }
    if hidden || no_display || !mime.split(';').any(|item| item == "inode/directory") {
        return None;
    }
    let identifier = path.file_name()?.to_string_lossy().into_owned();
    if !categories.split(';').any(|item| item == "FileManager") && identifier != default_id {
        return None;
    }
    if !try_exec.is_empty() && which(&try_exec).is_none() {
        return None;
    }
    let command = split_desktop_exec(&exec);
    if command.is_empty() || (which(&command[0]).is_none() && !Path::new(&command[0]).exists()) {
        return None;
    }
    Some(FileManager {
        identifier: identifier.clone(),
        label: if name.is_empty() {
            desktop_id_label(&identifier)
        } else {
            name
        },
        command,
        is_system_default: identifier == default_id,
        supports_new_window: true,
    })
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn split_desktop_exec(value: &str) -> Vec<String> {
    let mut command = Vec::new();
    let mut word = String::new();
    let mut quote = None;
    let mut escaped = false;
    for character in value.chars() {
        if escaped {
            word.push(character);
            escaped = false;
        } else if character == '\\' {
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
                command.push(std::mem::take(&mut word));
            }
        } else {
            word.push(character);
        }
    }
    if !word.is_empty() {
        command.push(word);
    }
    command
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn desktop_id_label(identifier: &str) -> String {
    identifier
        .trim_end_matches(".desktop")
        .rsplit('.')
        .next()
        .unwrap_or(identifier)
        .replace(['-', '_'], " ")
}

#[cfg(not(target_os = "macos"))]
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

#[cfg(not(target_os = "windows"))]
fn dirs_home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expands_desktop_exec_placeholders() {
        let command = expand_command(&["dolphin".into(), "%f".into()], Path::new("/tmp/cloud"));
        assert_eq!(command, vec!["dolphin", "/tmp/cloud"]);
    }

    #[test]
    fn adds_new_window_flags_for_known_linux_managers() {
        let command = new_window_command(
            "org.kde.dolphin.desktop",
            vec!["dolphin".into(), "/tmp".into()],
        );
        assert_eq!(command, vec!["dolphin", "--new-window", "/tmp"]);
    }
}
