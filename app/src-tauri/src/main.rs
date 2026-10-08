#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::Path;

fn main() {
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == "--version" {
            println!("Mountlet {}", env!("CARGO_PKG_VERSION"));
            return;
        }
        if argument == "--license-diagnostics" {
            let Some(path) = arguments.next() else {
                eprintln!("usage: mountlet --license-diagnostics <path>");
                std::process::exit(2);
            };
            if let Err(error) = mountlet::write_license_diagnostics(Path::new(&path)) {
                eprintln!("{error}");
                std::process::exit(1);
            }
            return;
        }
        if argument == "--startup-smoke" {
            let Some(path) = arguments.next() else {
                eprintln!("usage: mountlet --startup-smoke <marker-path>");
                std::process::exit(2);
            };
            // Package activation does not inherit ad-hoc environment changes
            // from a CI shell because Explorer is already running. Normalize
            // the explicit test-only argument to the existing smoke contract.
            std::env::set_var("MOUNTLET_STARTUP_SMOKE", path);
        }
    }
    mountlet::run();
}
