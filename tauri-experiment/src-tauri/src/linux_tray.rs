use std::sync::OnceLock;

use ksni::menu::{StandardItem, SubMenu};
use ksni::{Handle, Icon, MenuItem, ToolTip, Tray, TrayService};
use tauri::{AppHandle, Emitter, Manager};

use crate::{
    cache_tray_anchor, hide_window_stack, mark_clean_shutdown, show_window_stack, toggle_window_stack,
    tray_status_tooltip, AppState,
};

static HANDLE: OnceLock<Handle<LinuxTray>> = OnceLock::new();

pub fn install(app: AppHandle) {
    let tray = LinuxTray {
        tooltip: tray_status_tooltip(&app),
        app: app.clone(),
    };
    let service = TrayService::new(tray);
    let handle = service.handle();
    let _ = HANDLE.set(handle);
    std::thread::spawn(move || {
        if let Err(error) = service.run() {
            eprintln!("[mountlet] Linux tray failed: {error}");
            show_window_stack(&app);
        }
    });
}

pub fn refresh(app: &AppHandle) {
    if let Some(handle) = HANDLE.get() {
        let tooltip = tray_status_tooltip(app);
        let app = app.clone();
        handle.update(move |tray| {
            tray.app = app;
            tray.tooltip = tooltip;
        });
    }
}

fn pad_cascade_labels() -> bool {
    std::env::var_os("DISPLAY").is_some() && std::env::var_os("WAYLAND_DISPLAY").is_none()
}

fn cascade_label(label: &str) -> String {
    if pad_cascade_labels() {
        format!("{label}  ")
    } else {
        label.into()
    }
}

fn tray_icon() -> Vec<Icon> {
    let bytes = include_bytes!("../icons/icon.png");
    let decoder = png::Decoder::new(std::io::Cursor::new(bytes.as_slice()));
    let Ok(mut reader) = decoder.read_info() else {
        return Vec::new();
    };
    let mut buffer = vec![0; reader.output_buffer_size()];
    let Ok(info) = reader.next_frame(&mut buffer) else {
        return Vec::new();
    };
    let mut data = Vec::with_capacity((info.width * info.height * 4) as usize);
    match info.color_type {
        png::ColorType::Rgba => {
            for pixel in buffer.chunks_exact(4) {
                data.extend_from_slice(&[pixel[3], pixel[0], pixel[1], pixel[2]]);
            }
        }
        png::ColorType::Rgb => {
            for pixel in buffer.chunks_exact(3) {
                data.extend_from_slice(&[255, pixel[0], pixel[1], pixel[2]]);
            }
        }
        _ => return Vec::new(),
    }
    vec![Icon {
        width: info.width as i32,
        height: info.height as i32,
        data,
    }]
}

fn emit_command(app: &AppHandle, command: &str) {
    show_window_stack(app);
    let _ = app.emit("tray-command", command);
}

fn item<F>(label: &str, activate: F) -> MenuItem<LinuxTray>
where
    F: Fn(&mut LinuxTray) + Send + 'static,
{
    StandardItem {
        label: label.into(),
        activate: Box::new(activate),
        ..Default::default()
    }
    .into()
}

fn inert(label: &str) -> MenuItem<LinuxTray> {
    StandardItem {
        label: label.into(),
        enabled: false,
        ..Default::default()
    }
    .into()
}

fn submenu(label: &str, items: Vec<MenuItem<LinuxTray>>) -> MenuItem<LinuxTray> {
    SubMenu {
        label: cascade_label(label),
        submenu: items,
        ..Default::default()
    }
    .into()
}

pub struct LinuxTray {
    app: AppHandle,
    tooltip: String,
}

impl Clone for LinuxTray {
    fn clone(&self) -> Self {
        Self {
            app: self.app.clone(),
            tooltip: self.tooltip.clone(),
        }
    }
}

impl Tray for LinuxTray {
    fn id(&self) -> String {
        env!("CARGO_PKG_NAME").into()
    }

    fn title(&self) -> String {
        "Mountlet".into()
    }

    fn icon_name(&self) -> String {
        "folder-remote".into()
    }

    fn icon_pixmap(&self) -> Vec<Icon> {
        tray_icon()
    }

    fn tool_tip(&self) -> ToolTip {
        ToolTip {
            title: self.tooltip.clone(),
            description: String::new(),
            icon_name: "folder-remote".into(),
            icon_pixmap: tray_icon(),
        }
    }

    fn watcher_offine(&self) -> bool {
        eprintln!("[mountlet] StatusNotifierWatcher is offline; showing the window.");
        show_window_stack(&self.app);
        true
    }

    fn activate(&mut self, x: i32, y: i32) {
        cache_tray_anchor(&self.app, f64::from(x), f64::from(y));
        let _ = self.app.emit("tray-anchor-changed", ());
        toggle_window_stack(&self.app);
    }

    fn secondary_activate(&mut self, x: i32, y: i32) {
        cache_tray_anchor(&self.app, f64::from(x), f64::from(y));
    }

    fn menu(&self) -> Vec<MenuItem<Self>> {
        let remotes = self
            .app
            .state::<AppState>()
            .remotes
            .read()
            .map(|remotes| remotes.clone())
            .unwrap_or_default();
        let remotes = remotes
            .into_iter()
            .filter(|remote| crate::remote_is_configured(&remote.id, &remote.provider))
            .collect::<Vec<_>>();
        let status = self
            .tooltip
            .strip_prefix("Mountlet - ")
            .unwrap_or(&self.tooltip)
            .to_string();
        let remote_items = if remotes.is_empty() {
            vec![inert("No rclone remotes found")]
        } else {
            remotes
                .iter()
                .map(|remote| {
                    let id = remote.id.clone();
                    submenu(
                        &format!("{} ({})", remote.name, remote.provider_label),
                        vec![
                            item("Open in Mountlet", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:select:{id}"))
                            }),
                            item(if remote.mounted { "Unmount" } else { "Mount" }, {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:mount:{id}"))
                            }),
                            item("Open mounted folder", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:folder:{id}"))
                            }),
                            item("Open in web", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:web:{id}"))
                            }),
                            MenuItem::Separator,
                            item("Sync cached files now", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:sync:{id}"))
                            }),
                            item("Remove offline files", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:remove-offline:{id}"))
                                }
                            }),
                            item("Clear resolved cache", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:clear-cache:{id}"))
                            }),
                            MenuItem::Separator,
                            item("Config", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:config:{id}"))
                            }),
                            item("Reauthenticate", {
                                let id = id.clone();
                                move |tray| emit_command(&tray.app, &format!("remote-action:reauth:{id}"))
                            }),
                        ],
                    )
                })
                .collect()
        };
        vec![
            item("Open Mountlet", |tray| show_window_stack(&tray.app)),
            submenu(
                "More",
                vec![
                    submenu(
                        "App",
                        vec![
                            inert(&status),
                            MenuItem::Separator,
                            item("Update status", |tray| emit_command(&tray.app, "refresh")),
                            item("Sync cached files now", |tray| emit_command(&tray.app, "sync-all")),
                            item("Remove all offline files", |tray| {
                                emit_command(&tray.app, "remove-all-offline")
                            }),
                            item("Clear all resolved cache", |tray| {
                                emit_command(&tray.app, "clear-all-cache")
                            }),
                            item("Debug cache sync", |tray| emit_command(&tray.app, "cache-debug")),
                            item("Report bug", |tray| emit_command(&tray.app, "report-bug")),
                            MenuItem::Separator,
                            item("License", |tray| emit_command(&tray.app, "license")),
                            item("About Mountlet", |tray| emit_command(&tray.app, "about")),
                        ],
                    ),
                    submenu(
                        "Mount",
                        vec![
                            item("Mount all", |tray| emit_command(&tray.app, "mount-all")),
                            item("Unmount all", |tray| emit_command(&tray.app, "unmount-all")),
                            item("Add remote", |tray| emit_command(&tray.app, "add-remote")),
                            MenuItem::Separator,
                            submenu("Remotes", remote_items),
                        ],
                    ),
                    submenu(
                        "Config",
                        vec![
                            item("App settings", |tray| emit_command(&tray.app, "settings")),
                            item("Keyboard shortcuts", |tray| emit_command(&tray.app, "shortcuts")),
                            MenuItem::Separator,
                            item("Export config bundle", |tray| {
                                emit_command(&tray.app, "export-config")
                            }),
                            item("Import config bundle", |tray| {
                                emit_command(&tray.app, "import-config")
                            }),
                            item("Open config backup folder", |tray| {
                                emit_command(&tray.app, "open-config-backup")
                            }),
                        ],
                    ),
                ],
            ),
            MenuItem::Separator,
            item("Quit", |tray| {
                hide_window_stack(&tray.app);
                mark_clean_shutdown();
                tray.app.exit(0);
            }),
        ]
    }
}
