use super::{app_config_path, mountlet_config_dir, AppSettings};
use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes256Gcm, Nonce,
};
use pbkdf2::pbkdf2_hmac;
use rand::{rngs::OsRng, RngCore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Seek, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

const AAD: &[u8] = b"mountlet-config-bundle-v1";
const ITERATIONS: u32 = 390_000;
const MANIFEST: &str = "manifest.json";
const PAYLOAD: &str = "payload.bin";

fn sources(rclone: &Path) -> BTreeMap<String, PathBuf> {
    let mut files = BTreeMap::new();
    if rclone.is_file() {
        files.insert("rclone.conf".into(), rclone.to_path_buf());
    }
    if let Some(path) = app_config_path().filter(|path| path.is_file()) {
        files.insert("config.toml".into(), path);
    }
    if let Some(path) = mountlet_config_dir()
        .map(|path| path.join("mounts.toml"))
        .filter(|path| path.is_file())
    {
        files.insert("mounts.toml".into(), path);
    }
    if let Some(parent) = rclone.parent() {
        if let Ok(entries) = fs::read_dir(parent) {
            for entry in entries.flatten() {
                let path = entry.path();
                let name = path
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or("");
                if path.is_file() && name.starts_with("client_secret") && name.ends_with(".json") {
                    files.insert(format!("secrets/{name}"), path);
                }
            }
        }
    }
    files
}

fn config_hash(files: &BTreeMap<String, PathBuf>) -> Result<String, String> {
    let mut digest = Sha256::new();
    for (name, path) in files {
        digest.update(name.as_bytes());
        digest.update([0]);
        digest.update(fs::read(path).map_err(|error| error.to_string())?);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn archive_bytes(files: &BTreeMap<String, PathBuf>, manifest: &Value) -> Result<Vec<u8>, String> {
    let mut output = Cursor::new(Vec::new());
    {
        let mut archive = ZipWriter::new(&mut output);
        let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
        archive
            .start_file(MANIFEST, options)
            .map_err(|error| error.to_string())?;
        archive
            .write_all(&serde_json::to_vec_pretty(manifest).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
        for (name, path) in files {
            archive
                .start_file(name, options)
                .map_err(|error| error.to_string())?;
            archive
                .write_all(&fs::read(path).map_err(|error| error.to_string())?)
                .map_err(|error| error.to_string())?;
        }
        archive.finish().map_err(|error| error.to_string())?;
    }
    Ok(output.into_inner())
}

fn manifest(files: &BTreeMap<String, PathBuf>, backup: bool) -> Result<Value, String> {
    let created = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let mut value = json!({"format":"mountlet-config-bundle","version":1,"files":files.keys().collect::<Vec<_>>(),"created_at":created.to_string(),"device":std::env::var("HOSTNAME").unwrap_or_else(|_| "unknown device".into()),"system":std::env::consts::OS,"platform":format!("{}-{}",std::env::consts::OS,std::env::consts::ARCH),"config_hash":config_hash(files)?});
    if backup {
        value["backup"] = json!(true);
    }
    Ok(value)
}

pub fn export(
    destination: &Path,
    rclone: &Path,
    password: &str,
    backup: bool,
) -> Result<PathBuf, String> {
    let destination = if destination
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("mountlet"))
    {
        destination.to_path_buf()
    } else {
        destination.with_extension("mountlet")
    };
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let files = sources(rclone);
    if !files.contains_key("rclone.conf") {
        return Err(format!("Config file not found: {}", rclone.display()));
    }
    let inner_manifest = manifest(&files, backup)?;
    let bytes = if password.is_empty() {
        archive_bytes(&files, &inner_manifest)?
    } else {
        let payload = archive_bytes(&files, &inner_manifest)?;
        let mut salt = [0u8; 16];
        let mut nonce = [0u8; 12];
        OsRng.fill_bytes(&mut salt);
        OsRng.fill_bytes(&mut nonce);
        let mut key = [0u8; 32];
        pbkdf2_hmac::<Sha256>(password.as_bytes(), &salt, ITERATIONS, &mut key);
        let encrypted = Aes256Gcm::new_from_slice(&key)
            .map_err(|error| error.to_string())?
            .encrypt(
                Nonce::from_slice(&nonce),
                aes_gcm::aead::Payload {
                    msg: &payload,
                    aad: AAD,
                },
            )
            .map_err(|_| "Could not encrypt config bundle")?;
        let wrapper = json!({"format":"mountlet-config-bundle","version":1,"encrypted":true,"created_at":inner_manifest["created_at"],"device":inner_manifest["device"],"system":inner_manifest["system"],"platform":inner_manifest["platform"],"config_hash":inner_manifest["config_hash"],"cipher":"AES-256-GCM","kdf":"PBKDF2-HMAC-SHA256","iterations":ITERATIONS,"salt":hex::encode(salt),"nonce":hex::encode(nonce)});
        let mut map = BTreeMap::new();
        let temporary =
            std::env::temp_dir().join(format!("mountlet-payload-{}", std::process::id()));
        fs::write(&temporary, encrypted).map_err(|error| error.to_string())?;
        map.insert(PAYLOAD.into(), temporary.clone());
        let result = archive_bytes(&map, &wrapper);
        let _ = fs::remove_file(temporary);
        result?
    };
    let temporary = destination.with_extension("mountlet.tmp");
    fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
    fs::rename(&temporary, &destination).map_err(|error| error.to_string())?;
    Ok(destination)
}

fn read_entry<R: Read + Seek>(archive: &mut ZipArchive<R>, name: &str) -> Result<Vec<u8>, String> {
    let mut entry = archive
        .by_name(name)
        .map_err(|_| format!("This bundle does not contain {name}."))?;
    let mut value = Vec::new();
    entry
        .read_to_end(&mut value)
        .map_err(|error| error.to_string())?;
    Ok(value)
}
fn opened(source: &Path, password: &str) -> Result<ZipArchive<Cursor<Vec<u8>>>, String> {
    let bytes = fs::read(source).map_err(|error| error.to_string())?;
    let mut outer = ZipArchive::new(Cursor::new(bytes)).map_err(|error| error.to_string())?;
    let manifest: Value = serde_json::from_slice(&read_entry(&mut outer, MANIFEST)?)
        .map_err(|_| "This bundle has an invalid manifest.")?;
    if manifest.get("format").and_then(Value::as_str) != Some("mountlet-config-bundle") {
        return Err("This is not a Mountlet config bundle.".into());
    }
    if !manifest
        .get("encrypted")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(outer);
    }
    if password.is_empty() {
        return Err("This bundle is encrypted. Enter its password to import it.".into());
    }
    let salt = hex::decode(manifest.get("salt").and_then(Value::as_str).unwrap_or(""))
        .map_err(|_| "Invalid encryption metadata")?;
    let nonce = hex::decode(manifest.get("nonce").and_then(Value::as_str).unwrap_or(""))
        .map_err(|_| "Invalid encryption metadata")?;
    if nonce.len() != 12 {
        return Err("Invalid encryption metadata".into());
    }
    let mut key = [0u8; 32];
    pbkdf2_hmac::<Sha256>(
        password.as_bytes(),
        &salt,
        manifest
            .get("iterations")
            .and_then(Value::as_u64)
            .unwrap_or(ITERATIONS as u64) as u32,
        &mut key,
    );
    let encrypted = read_entry(&mut outer, PAYLOAD)?;
    let decrypted = Aes256Gcm::new_from_slice(&key)
        .map_err(|error| error.to_string())?
        .decrypt(
            Nonce::from_slice(&nonce),
            aes_gcm::aead::Payload {
                msg: &encrypted,
                aad: AAD,
            },
        )
        .map_err(|_| "The bundle password is incorrect.")?;
    ZipArchive::new(Cursor::new(decrypted)).map_err(|error| error.to_string())
}

fn merged_app_config(incoming: &[u8], local: &AppSettings) -> Vec<u8> {
    let text = String::from_utf8_lossy(incoming);
    let mut section = String::new();
    let mut lines = Vec::new();
    for raw in text.lines() {
        let trimmed = raw.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            section = trimmed[1..trimmed.len() - 1].to_string();
            lines.push(raw.to_string());
            continue;
        }
        let key = trimmed
            .split_once('=')
            .map(|value| value.0.trim())
            .unwrap_or("");
        let replacement = match (section.as_str(), key) {
            ("app", "mount_base") => Some(format!(
                "mount_base = {}",
                super::toml_string(&local.mount_base)
            )),
            ("app", "start_at_login") => Some(format!("start_at_login = {}", local.start_at_login)),
            ("tray", "file_manager") => Some(format!(
                "file_manager = {}",
                super::toml_string(&local.file_manager)
            )),
            ("tray", "open_folder_behavior") => Some(format!(
                "open_folder_behavior = {}",
                super::toml_string(&local.open_folder_behavior)
            )),
            ("tray", "focus_file_manager") => {
                Some(format!("focus_file_manager = {}", local.focus_file_manager))
            }
            _ => None,
        };
        lines.push(replacement.unwrap_or_else(|| raw.to_string()));
    }
    lines.join("\n").into_bytes()
}

pub fn import(source: &Path, rclone: &Path, password: &str) -> Result<Option<PathBuf>, String> {
    let mut archive = opened(source, password)?;
    let current = sources(rclone);
    let backup = if current.contains_key("rclone.conf") {
        let dir = mountlet_config_dir()
            .ok_or("Mountlet config directory unavailable")?
            .join("backups");
        fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
        Some(export(
            &dir.join(format!(
                "mountlet-config-backup-{}",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs()
            )),
            rclone,
            "",
            true,
        )?)
    } else {
        None
    };
    let local = super::app_settings();
    let rclone_bytes = read_entry(&mut archive, "rclone.conf")?;
    let mut writes = vec![(rclone.to_path_buf(), rclone_bytes)];
    if let Ok(bytes) = read_entry(&mut archive, "config.toml") {
        if let Some(path) = app_config_path() {
            writes.push((path, merged_app_config(&bytes, &local)));
        }
    }
    if let Ok(bytes) = read_entry(&mut archive, "mounts.toml") {
        if let Some(path) = mountlet_config_dir().map(|path| path.join("mounts.toml")) {
            writes.push((path, bytes));
        }
    }
    let names = archive
        .file_names()
        .filter(|name| name.starts_with("secrets/") && !name.ends_with('/'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    for name in names {
        let bytes = read_entry(&mut archive, &name)?;
        if let (Some(parent), Some(file)) = (rclone.parent(), Path::new(&name).file_name()) {
            writes.push((parent.join(file), bytes));
        }
    }
    for (path, bytes) in writes {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = path.with_extension("tmp");
        fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
        fs::rename(temporary, path).map_err(|error| error.to_string())?;
    }
    Ok(backup)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encrypted_bundle_is_python_format_compatible() {
        let directory =
            std::env::temp_dir().join(format!("mountlet-bundle-test-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let config = directory.join("rclone.conf");
        fs::write(&config, "[test]\ntype = drive\n").unwrap();
        let bundle = export(
            &directory.join("config.mountlet"),
            &config,
            "correct horse",
            false,
        )
        .unwrap();
        let mut archive = opened(&bundle, "correct horse").unwrap();
        assert_eq!(
            String::from_utf8(read_entry(&mut archive, "rclone.conf").unwrap()).unwrap(),
            "[test]\ntype = drive\n"
        );
        assert!(opened(&bundle, "wrong password").is_err());
        let _ = fs::remove_dir_all(directory);
    }
}
