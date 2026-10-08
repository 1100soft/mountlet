fn main() {
    for name in [
        "MOUNTLET_DRIVE_CLIENT_ID",
        "MOUNTLET_DRIVE_CLIENT_SECRET",
        "MOUNTLET_GPHOTOS_CLIENT_ID",
        "MOUNTLET_GPHOTOS_CLIENT_SECRET",
        "MOUNTLET_DROPBOX_CLIENT_ID",
        "MOUNTLET_DROPBOX_CLIENT_SECRET",
        "MOUNTLET_ONEDRIVE_CLIENT_ID",
        "MOUNTLET_ONEDRIVE_CLIENT_SECRET",
        "MOUNTLET_BOX_CLIENT_ID",
        "MOUNTLET_BOX_CLIENT_SECRET",
        "MOUNTLET_PCLOUD_CLIENT_ID",
        "MOUNTLET_PCLOUD_CLIENT_SECRET",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    tauri_build::build()
}
