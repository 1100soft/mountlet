use std::process::Command;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WorkArea {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl WorkArea {
    pub(crate) fn overlaps(self, other: Self) -> bool {
        let x = self.x.max(other.x);
        let y = self.y.max(other.y);
        let right = (self.x + self.width).min(other.x + other.width);
        let bottom = (self.y + self.height).min(other.y + other.height);
        right > x && bottom > y
    }

    pub(crate) fn intersect(self, other: Self) -> Self {
        let x = self.x.max(other.x);
        let y = self.y.max(other.y);
        let right = (self.x + self.width).min(other.x + other.width);
        let bottom = (self.y + self.height).min(other.y + other.height);
        if right <= x || bottom <= y {
            other
        } else {
            Self {
                x,
                y,
                width: right - x,
                height: bottom - y,
            }
        }
    }
}

#[cfg(all(target_os = "linux", not(target_os = "android")))]
fn x11_work_area() -> Option<WorkArea> {
    std::env::var_os("DISPLAY")?;
    let output = Command::new("xprop")
        .args(["-root", "_NET_CURRENT_DESKTOP", "_NET_WORKAREA"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_x11_work_area(&String::from_utf8_lossy(&output.stdout))
}

#[cfg(not(all(target_os = "linux", not(target_os = "android"))))]
fn x11_work_area() -> Option<WorkArea> {
    None
}

pub fn resolve(fallback: WorkArea) -> WorkArea {
    platform_work_area(fallback)
        .map(|area| fallback.intersect(area))
        .unwrap_or(fallback)
}

#[cfg(target_os = "linux")]
fn platform_work_area(_fallback: WorkArea) -> Option<WorkArea> {
    x11_work_area()
}

#[cfg(target_os = "windows")]
fn platform_work_area(fallback: WorkArea) -> Option<WorkArea> {
    #[repr(C)]
    struct Point {
        x: i32,
        y: i32,
    }
    #[repr(C)]
    struct Rect {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }
    #[repr(C)]
    struct MonitorInfo {
        size: u32,
        monitor: Rect,
        work: Rect,
        flags: u32,
    }
    #[link(name = "user32")]
    extern "system" {
        fn MonitorFromPoint(point: Point, flags: u32) -> isize;
        fn GetMonitorInfoW(monitor: isize, info: *mut MonitorInfo) -> i32;
    }
    const DEFAULT_TO_NEAREST: u32 = 2;
    let center = Point {
        x: (fallback.x + fallback.width / 2.0).round() as i32,
        y: (fallback.y + fallback.height / 2.0).round() as i32,
    };
    let monitor = unsafe { MonitorFromPoint(center, DEFAULT_TO_NEAREST) };
    let mut info = MonitorInfo {
        size: std::mem::size_of::<MonitorInfo>() as u32,
        monitor: Rect {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        },
        work: Rect {
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
        },
        flags: 0,
    };
    if monitor == 0 || unsafe { GetMonitorInfoW(monitor, &mut info) } == 0 {
        return None;
    }
    let native_width = (info.monitor.right - info.monitor.left).max(1) as f64;
    let scale = native_width / fallback.width.max(1.0);
    Some(WorkArea {
        x: fallback.x + f64::from(info.work.left - info.monitor.left) / scale,
        y: fallback.y + f64::from(info.work.top - info.monitor.top) / scale,
        width: f64::from(info.work.right - info.work.left) / scale,
        height: f64::from(info.work.bottom - info.work.top) / scale,
    })
}

#[cfg(target_os = "macos")]
fn platform_work_area(_fallback: WorkArea) -> Option<WorkArea> {
    // Finder reports the visible desktop bounds after the menu bar and Dock,
    // in the same logical coordinate system used by AppKit/Tauri.
    let output = Command::new("osascript")
        .args([
            "-e",
            "tell application \"Finder\" to get bounds of window of desktop",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let values = String::from_utf8_lossy(&output.stdout)
        .split(|character: char| !(character == '-' || character.is_ascii_digit()))
        .filter(|value| !value.is_empty())
        .filter_map(|value| value.parse::<f64>().ok())
        .collect::<Vec<_>>();
    let [left, top, right, bottom, ..] = values.as_slice() else {
        return None;
    };
    Some(WorkArea {
        x: *left,
        y: *top,
        width: right - left,
        height: bottom - top,
    })
}

#[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "macos")))]
fn platform_work_area(_fallback: WorkArea) -> Option<WorkArea> {
    None
}

fn numbers_after(text: &str, marker: &str) -> Option<Vec<i64>> {
    let line = text.lines().find(|line| line.contains(marker))?;
    let values = line.split_once('=')?.1;
    Some(
        values
            .split(|character: char| !(character == '-' || character.is_ascii_digit()))
            .filter(|value| !value.is_empty())
            .filter_map(|value| value.parse().ok())
            .collect(),
    )
}

fn parse_x11_work_area(text: &str) -> Option<WorkArea> {
    let desktop = *numbers_after(text, "_NET_CURRENT_DESKTOP")?.first()? as usize;
    let values = numbers_after(text, "_NET_WORKAREA")?;
    let start = desktop.checked_mul(4)?;
    let slice = values.get(start..start + 4)?;
    if slice[2] <= 0 || slice[3] <= 0 {
        return None;
    }
    Some(WorkArea {
        x: slice[0] as f64,
        y: slice[1] as f64,
        width: slice[2] as f64,
        height: slice[3] as f64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_current_x11_desktop() {
        let source = "_NET_CURRENT_DESKTOP(CARDINAL) = 1\n_NET_WORKAREA(CARDINAL) = 0, 30, 1920, 1050, 1920, 0, 1880, 1080";
        assert_eq!(
            parse_x11_work_area(source),
            Some(WorkArea {
                x: 1920.0,
                y: 0.0,
                width: 1880.0,
                height: 1080.0
            })
        );
    }
}
