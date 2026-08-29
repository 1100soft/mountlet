#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args().any(|argument| argument == "--version") {
        println!("Mountlet {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    mountlet::run();
}
