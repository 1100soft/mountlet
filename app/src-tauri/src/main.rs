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
    }
    mountlet::run();
}
