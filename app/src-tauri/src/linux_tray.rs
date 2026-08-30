use std::sync::OnceLock;

use ksni::menu::{StandardItem, SubMenu};
use ksni::{Handle, Icon, MenuItem, ToolTip, Tray, TrayService};
use tauri::{AppHandle, Emitter, Manager};

use crate::{
    cache_tray_anchor, hide_window_stack, mark_clean_shutdown, show_window_stack,
    toggle_window_stack, tray_activate_is_duplicate, tray_status_tooltip, AppState,
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
        // Register the connection's unique DBus name directly. Some hosts
        // accept the connection but never grant ksni's generated well-known
        // name, leaving a stale-looking icon whose actions cannot reach us.
        if let Err(error) = service.run_without_dbus_name() {
            eprintln!("[mountlet] Linux tray failed: {error}");
            on_ui(app, show_window_stack);
        }
    });
}

fn on_ui(app: AppHandle, f: impl FnOnce(&AppHandle) + Send + 'static) {
    let _ = app.clone().run_on_main_thread(move || f(&app));
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

static TRAY_PIXMAP: OnceLock<Vec<Icon>> = OnceLock::new();

fn tray_icon() -> Vec<Icon> {
    TRAY_PIXMAP
        .get_or_init(|| {
            let Some((width, height, rgba)) = load_source_rgba() else {
                return Vec::new();
            };
            [22_u32, 24, 32, 48]
                .into_iter()
                .map(|size| Icon {
                    width: size as i32,
                    height: size as i32,
                    data: rgba_to_argb(&box_downscale_rgba(width, height, &rgba, size)),
                })
                .collect()
        })
        .clone()
}

fn load_source_rgba() -> Option<(u32, u32, Vec<u8>)> {
    let bytes = include_bytes!("../icons/icon.png");
    let decoder = png::Decoder::new(std::io::Cursor::new(bytes.as_slice()));
    let mut reader = decoder.read_info().ok()?;
    let mut buffer = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buffer).ok()?;
    let mut rgba = Vec::with_capacity((info.width * info.height * 4) as usize);
    match info.color_type {
        png::ColorType::Rgba => rgba.extend_from_slice(&buffer[..info.buffer_size()]),
        png::ColorType::Rgb => {
            for pixel in buffer.as_chunks::<3>().0 {
                rgba.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 255]);
            }
        }
        _ => return None,
    }
    Some((info.width, info.height, rgba))
}

fn box_downscale_rgba(width: u32, height: u32, rgba: &[u8], target: u32) -> Vec<u8> {
    let mut out = vec![0u8; (target * target * 4) as usize];
    if width == 0 || height == 0 || rgba.len() < (width * height * 4) as usize {
        return out;
    }
    for y in 0..target {
        let y0 = y * height / target;
        let y1 = ((y + 1) * height / target).max(y0 + 1).min(height);
        for x in 0..target {
            let x0 = x * width / target;
            let x1 = ((x + 1) * width / target).max(x0 + 1).min(width);
            let mut r = 0u64;
            let mut g = 0u64;
            let mut b = 0u64;
            let mut a = 0u64;
            let mut count = 0u64;
            for sy in y0..y1 {
                for sx in x0..x1 {
                    let index = ((sy * width + sx) * 4) as usize;
                    r += u64::from(rgba[index]);
                    g += u64::from(rgba[index + 1]);
                    b += u64::from(rgba[index + 2]);
                    a += u64::from(rgba[index + 3]);
                    count += 1;
                }
            }
            let dest = ((y * target + x) * 4) as usize;
            if let (Some(r), Some(g), Some(b), Some(a)) = (
                r.checked_div(count),
                g.checked_div(count),
                b.checked_div(count),
                a.checked_div(count),
            ) {
                out[dest] = r as u8;
                out[dest + 1] = g as u8;
                out[dest + 2] = b as u8;
                out[dest + 3] = a as u8;
            }
        }
    }
    out
}

fn rgba_to_argb(rgba: &[u8]) -> Vec<u8> {
    let mut argb = Vec::with_capacity(rgba.len());
    for pixel in rgba.as_chunks::<4>().0 {
        argb.extend_from_slice(&[pixel[3], pixel[0], pixel[1], pixel[2]]);
    }
    argb
}

fn emit_command(app: &AppHandle, command: &str) {
    let command = command.to_string();
    on_ui(app.clone(), move |app| {
        show_window_stack(app);
        let _ = app.emit("tray-command", command);
    });
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
        "mountlet".into()
    }

    fn title(&self) -> String {
        "Mountlet".into()
    }

    fn icon_pixmap(&self) -> Vec<Icon> {
        tray_icon()
    }

    fn tool_tip(&self) -> ToolTip {
        ToolTip {
            title: self.tooltip.clone(),
            description: String::new(),
            icon_name: String::new(),
            icon_pixmap: tray_icon(),
        }
    }

    fn watcher_offine(&self) -> bool {
        eprintln!("[mountlet] StatusNotifierWatcher is offline; showing the window.");
        on_ui(self.app.clone(), show_window_stack);
        true
    }

    fn activate(&mut self, x: i32, y: i32) {
        let x = f64::from(x);
        let y = f64::from(y);
        on_ui(self.app.clone(), move |app| {
            if tray_activate_is_duplicate(app) {
                return;
            }
            cache_tray_anchor(app, x, y);
            let _ = app.emit("tray-anchor-changed", ());
            toggle_window_stack(app);
        });
    }

    fn secondary_activate(&mut self, x: i32, y: i32) {
        let x = f64::from(x);
        let y = f64::from(y);
        on_ui(self.app.clone(), move |app| cache_tray_anchor(app, x, y));
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
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:select:{id}"))
                                }
                            }),
                            item(if remote.mounted { "Unmount" } else { "Mount" }, {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:mount:{id}"))
                                }
                            }),
                            item("Open mounted folder", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:folder:{id}"))
                                }
                            }),
                            item("Open in web", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:web:{id}"))
                                }
                            }),
                            MenuItem::Separator,
                            item("Sync cached files now", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:sync:{id}"))
                                }
                            }),
                            item("Remove offline files", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(
                                        &tray.app,
                                        &format!("remote-action:remove-offline:{id}"),
                                    )
                                }
                            }),
                            item("Clear resolved cache", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(
                                        &tray.app,
                                        &format!("remote-action:clear-cache:{id}"),
                                    )
                                }
                            }),
                            MenuItem::Separator,
                            item("Config", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:config:{id}"))
                                }
                            }),
                            item("Reauthenticate", {
                                let id = id.clone();
                                move |tray| {
                                    emit_command(&tray.app, &format!("remote-action:reauth:{id}"))
                                }
                            }),
                        ],
                    )
                })
                .collect()
        };
        vec![
            item("Open Mountlet", |tray| {
                on_ui(tray.app.clone(), show_window_stack);
            }),
            submenu(
                "More",
                vec![
                    submenu(
                        "App",
                        vec![
                            inert(&status),
                            MenuItem::Separator,
                            item("Update status", |tray| emit_command(&tray.app, "refresh")),
                            item("Sync cached files now", |tray| {
                                emit_command(&tray.app, "sync-all")
                            }),
                            item("Remove all offline files", |tray| {
                                emit_command(&tray.app, "remove-all-offline")
                            }),
                            item("Clear all resolved cache", |tray| {
                                emit_command(&tray.app, "clear-all-cache")
                            }),
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
                            item("Keyboard shortcuts", |tray| {
                                emit_command(&tray.app, "shortcuts")
                            }),
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
                on_ui(tray.app.clone(), |app| {
                    hide_window_stack(app);
                    mark_clean_shutdown();
                    app.exit(0);
                });
            }),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_icon_uses_panel_sized_pixmaps() {
        let icons = tray_icon();
        assert!(!icons.is_empty());
        for icon in icons {
            assert!(icon.width >= 22 && icon.width <= 48);
            assert_eq!(icon.width, icon.height);
            assert_eq!(icon.data.len(), (icon.width * icon.height * 4) as usize);
            assert!(icon.data.iter().any(|value| *value > 0));
        }
    }
}
