fn main() {
    if std::env::args().any(|argument| argument == "--version") {
        println!("Mountlet {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    mountlet::run();
}
