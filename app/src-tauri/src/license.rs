use super::{mountlet_config_dir, mountlet_state_dir};
use base64::{
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
    Engine,
};
use hmac::{Hmac, Mac};
use p256::{
    ecdsa::{signature::Verifier, Signature, VerifyingKey},
    pkcs8::DecodePublicKey,
};
use rand::{rngs::OsRng, RngCore};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    env, fs,
    path::{Path, PathBuf},
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const API: &str = "https://mountlet.app/api/license";
const TRIAL_SECONDS: f64 = 7.0 * 24.0 * 60.0 * 60.0;
const TRIAL_SALT: &[u8] = b"mountlet trial state v1";
// Public verification material is safe to distribute. Bundling it keeps local
// license checks independent of DNS, TLS and the availability of the website.
// The endpoint is still used as a rotation fallback when a signature does not
// match this key or a previously cached replacement.
const BUNDLED_PUBLIC_KEY: &str = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAENrcIcgvV+4hGhJI9dmZO3vEMA4Rz\
     e8kfNS97OhxF7xXuCeKjnV+ERmtJF+3Dhqw9NysrFiXEUgws/nd5e7Y3Cg==";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub state: String,
    pub summary: String,
    pub trial_days_remaining: i64,
    pub license_key: String,
    pub licensed_email: String,
    pub plan: String,
    pub license_kind: String,
    pub max_devices: i64,
    pub device_label: String,
    pub expires_at: String,
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
fn system_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "Darwin"
    } else if cfg!(target_os = "windows") {
        "Windows"
    } else {
        "Linux"
    }
}
fn machine_name() -> &'static str {
    match (env::consts::OS, env::consts::ARCH) {
        ("windows", "x86_64") => "AMD64",
        ("windows", "x86") => "x86",
        ("macos", "aarch64") => "arm64",
        (_, value) => value,
    }
}
fn stable_machine_identifier() -> String {
    #[cfg(target_os = "linux")]
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"] {
        if let Ok(value) = fs::read_to_string(path) {
            if !value.trim().is_empty() {
                return format!("machine-id:{}", value.trim());
            }
        }
    }
    #[cfg(target_os = "windows")]
    {
        use winreg::{enums::HKEY_LOCAL_MACHINE, RegKey};
        if let Ok(value) = RegKey::predef(HKEY_LOCAL_MACHINE)
            .open_subkey(r"SOFTWARE\Microsoft\Cryptography")
            .and_then(|key| key.get_value::<String, _>("MachineGuid"))
        {
            if !value.trim().is_empty() {
                return format!("machine-guid:{}", value.trim());
            }
        }
    }
    #[cfg(target_os = "macos")]
    {
        if let Ok(output) = crate::child_process::Command::new("ioreg")
            .args(["-rd1", "-c", "IOPlatformExpertDevice"])
            .output()
        {
            let text = String::from_utf8_lossy(&output.stdout);
            if let Some(value) = text
                .split("\"IOPlatformUUID\"")
                .nth(1)
                .and_then(|part| part.split('"').nth(2))
            {
                return format!("platform-uuid:{value}");
            }
        }
    }
    env::var("HOSTNAME").unwrap_or_else(|_| "unknown".into())
}
fn machine_hint() -> String {
    #[cfg(target_os = "windows")]
    let home = env::var("USERPROFILE")
        .or_else(|_| env::var("HOME"))
        .unwrap_or_default();
    #[cfg(not(target_os = "windows"))]
    let home = env::var("HOME")
        .or_else(|_| env::var("USERPROFILE"))
        .unwrap_or_default();
    hex::encode(Sha256::digest(
        format!(
            "{}|{}|{}|{}",
            system_name(),
            machine_name(),
            stable_machine_identifier(),
            home
        )
        .as_bytes(),
    ))
}
fn state_license(name: &str) -> Option<PathBuf> {
    Some(mountlet_state_dir()?.join("license").join(name))
}
fn config_license(name: &str) -> Option<PathBuf> {
    Some(mountlet_config_dir()?.join(name))
}
fn paths(name: &str) -> Vec<PathBuf> {
    [state_license(name), config_license(name)]
        .into_iter()
        .flatten()
        .collect()
}
fn home() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let value = env::var_os("USERPROFILE").or_else(|| env::var_os("HOME"));
    #[cfg(not(target_os = "windows"))]
    let value = env::var_os("HOME").or_else(|| env::var_os("USERPROFILE"));
    value.map(PathBuf::from)
}
fn trial_paths() -> Vec<PathBuf> {
    let mut result = Vec::new();
    if let Some(path) = state_license("trial.dat") {
        result.push(path);
    }
    if let Some(path) = config_license(".license-trial") {
        result.push(path);
    }
    #[cfg(target_os = "windows")]
    if let Some(path) = env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        result.push(path.join("Mountlet/.license-trial"));
        result.push(path.join("Mountlet/State/license/trial.dat"));
        result.push(path.join("Mountlet/Cache/.license-trial"));
        result.push(path.join("Microsoft/MountletTrial.dat"));
    }
    #[cfg(target_os = "windows")]
    if let Some(path) = env::var_os("APPDATA").map(PathBuf::from) {
        result.push(path.join("Microsoft/MountletTrial.dat"));
    }
    #[cfg(target_os = "macos")]
    if let Some(path) = home() {
        result.push(path.join("Library/Caches/Mountlet/.license-trial"));
        result.push(path.join("Library/Preferences/.mountlet-license-trial"));
        result.push(path.join("Library/Application Support/.mountlet-license-trial"));
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    if let Some(path) = home() {
        result.push(path.join(".cache/mountlet/.license-trial"));
        result.push(path.join(".local/state/.mountlet-license-trial"));
        result.push(path.join(".config/.mountlet-license-trial"));
    }
    if let Some(path) = home() {
        result.push(path.join(".mountlet-license-trial"));
    }
    result.sort();
    result.dedup();
    result
}
fn read_first(name: &str) -> String {
    paths(name)
        .into_iter()
        .find_map(|path| {
            fs::read_to_string(path)
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_default()
}
fn write_replicated(path: &Path, contents: &str) -> bool {
    if let Some(parent) = path.parent() {
        if fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    fs::write(path, contents).is_ok()
}

fn store(name: &str, value: &str) -> Result<(), String> {
    let contents = format!("{}\n", value.trim());
    let mut wrote = false;
    for path in paths(name) {
        wrote |= write_replicated(&path, &contents);
    }
    if wrote {
        Ok(())
    } else {
        Err("Could not store license state.".into())
    }
}
fn clear(name: &str) {
    for path in paths(name) {
        let _ = fs::remove_file(path);
    }
}
fn trial_signature(payload: &str, hint: &str) -> String {
    let key = Sha256::digest([TRIAL_SALT, hint.as_bytes()].concat());
    let mut mac = Hmac::<Sha256>::new_from_slice(&key).expect("HMAC key");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}
fn trial() -> Result<Value, String> {
    let hint = machine_hint();
    let paths = trial_paths();
    let mut valid = Vec::new();
    let mut encoded_records = std::collections::HashMap::<String, usize>::new();
    for path in &paths {
        let Ok(encoded) = fs::read_to_string(path) else {
            continue;
        };
        let encoded = encoded.trim().to_string();
        if encoded.is_empty() {
            continue;
        }
        *encoded_records.entry(encoded.clone()).or_default() += 1;
        let Ok(envelope_bytes) = URL_SAFE_NO_PAD.decode(encoded.trim()) else {
            continue;
        };
        let Ok(envelope) = serde_json::from_slice::<Value>(&envelope_bytes) else {
            continue;
        };
        let payload = envelope["payload"].as_str().unwrap_or("");
        if envelope["signature"].as_str() != Some(&trial_signature(payload, &hint)) {
            continue;
        }
        let Ok(bytes) = URL_SAFE_NO_PAD.decode(payload) else {
            continue;
        };
        let Ok(record) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        if record["version"].as_i64() != Some(2) || record["machine_hint"].as_str() != Some(&hint) {
            continue;
        }
        valid.push(record);
    }
    let current = now();
    let mut selected = oldest_trial(valid, current);
    if selected.is_none() {
        selected = recover_legacy_trial(&encoded_records, current, &hint);
    }
    if let Some(mut record) = selected {
        record["version"] = json!(2);
        record["machine_hint"] = json!(hint);
        record["last_seen_at"] = json!(record["last_seen_at"]
            .as_f64()
            .unwrap_or(current)
            .max(current));
        write_trial(&record, &hint)?;
        return Ok(record);
    }
    let mut random = [0u8; 24];
    OsRng.fill_bytes(&mut random);
    let record = json!({"version":2,"install_id":URL_SAFE_NO_PAD.encode(random),"machine_hint":hint,"started_at":now(),"last_seen_at":now()});
    write_trial(&record, &hint)?;
    Ok(record)
}

fn oldest_trial(valid: Vec<Value>, current: f64) -> Option<Value> {
    valid.into_iter().min_by(|left, right| {
        left["started_at"]
            .as_f64()
            .unwrap_or(current)
            .total_cmp(&right["started_at"].as_f64().unwrap_or(current))
    })
}

fn recover_legacy_trial(
    encoded_records: &std::collections::HashMap<String, usize>,
    current: f64,
    current_hint: &str,
) -> Option<Value> {
    let mut candidates = encoded_records.iter().collect::<Vec<_>>();
    candidates.sort_by_key(|(_, count)| std::cmp::Reverse(**count));
    for (encoded, count) in candidates {
        if *count < 2 {
            continue;
        }
        let Some(envelope) = URL_SAFE_NO_PAD
            .decode(encoded)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        else {
            continue;
        };
        let Some(payload) = envelope["payload"].as_str() else {
            continue;
        };
        let Some(record) = URL_SAFE_NO_PAD
            .decode(payload)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        else {
            continue;
        };
        let (Some(legacy_hint), Some(started), Some(last_seen)) = (
            record["machine_hint"].as_str(),
            record["started_at"].as_f64(),
            record["last_seen_at"].as_f64(),
        ) else {
            continue;
        };
        let hint_valid =
            legacy_hint.len() == 64 && legacy_hint.bytes().all(|value| value.is_ascii_hexdigit());
        if record["version"].as_i64() == Some(1)
            && hint_valid
            && envelope["signature"].as_str() == Some(&trial_signature(payload, legacy_hint))
            && started <= current + 86_400.0
            && last_seen >= started
        {
            let mut migrated = record;
            migrated["version"] = json!(2);
            migrated["machine_hint"] = json!(current_hint);
            migrated["last_seen_at"] = json!(last_seen.max(current));
            return Some(migrated);
        }
    }
    None
}
fn write_trial(record: &Value, hint: &str) -> Result<(), String> {
    let payload =
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(record).map_err(|error| error.to_string())?);
    let envelope = json!({"payload":payload,"signature":trial_signature(&payload,hint)});
    let encoded =
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&envelope).map_err(|error| error.to_string())?);
    let contents = format!("{encoded}\n");
    for path in trial_paths() {
        // Match Python: a permission, OneDrive, or antivirus failure on one
        // replica must not abort status evaluation or block startup.
        let _ = write_replicated(&path, &contents);
    }
    Ok(())
}
fn reset_trial() -> Result<Value, String> {
    let hint = machine_hint();
    let mut random = [0u8; 24];
    OsRng.fill_bytes(&mut random);
    let record = json!({"version":2,"install_id":URL_SAFE_NO_PAD.encode(random),"machine_hint":hint,"started_at":now(),"last_seen_at":now()});
    write_trial(&record, &hint)?;
    Ok(record)
}
fn unverified_payload(token: &str) -> Value {
    token
        .split('.')
        .nth(1)
        .and_then(|part| decode_part(part).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_else(|| json!({}))
}
fn decode_part(value: &str) -> Result<Vec<u8>, String> {
    URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| "Invalid license token data.".into())
}
fn parse_public_key(pem: &str) -> Result<VerifyingKey, String> {
    let normalized = pem.replace("\\n", "\n");
    if normalized.starts_with("-----BEGIN") {
        VerifyingKey::from_public_key_pem(&normalized)
            .map_err(|_| "License public key is invalid.".into())
    } else {
        let der = STANDARD
            .decode(normalized.split_whitespace().collect::<String>())
            .map_err(|_| "License public key is invalid.")?;
        VerifyingKey::from_public_key_der(&der).map_err(|_| "License public key is invalid.".into())
    }
}
fn fetch_public_key() -> Result<VerifyingKey, String> {
    let response: get_reqwest::Response = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(12))
        .build()
        .map_err(|error| error.to_string())?
        .get(format!("{}/public-key", api()))
        .send()
        .map_err(|error| error.to_string())?;
    let value: Value = response
        .error_for_status()
        .map_err(|error| error.to_string())?
        .json()
        .map_err(|error| error.to_string())?;
    let pem = value["publicKey"]
        .as_str()
        .ok_or("The license server did not return its public key.")?;
    let key = parse_public_key(pem)?;
    store("license-public.pem", pem)?;
    Ok(key)
}
fn public_key() -> Result<VerifyingKey, String> {
    let configured = env::var("MOUNTLET_LICENSE_PUBLIC_KEY").unwrap_or_default();
    let pem = if !configured.trim().is_empty() {
        configured
    } else {
        BUNDLED_PUBLIC_KEY.into()
    };
    parse_public_key(&pem)
}

fn verify_with_rotated_key(signed: &[u8], signature: &Signature) -> Result<bool, String> {
    let cached = read_first("license-public.pem");
    if !cached.is_empty()
        && parse_public_key(&cached).is_ok_and(|key| key.verify(signed, signature).is_ok())
    {
        return Ok(true);
    }
    Ok(fetch_public_key()?.verify(signed, signature).is_ok())
}
// Alias keeps the long reqwest response type out of diagnostics and generated command metadata.
mod get_reqwest {
    pub type Response = reqwest::blocking::Response;
}
fn verify_token(token: &str) -> Result<Value, String> {
    verify_token_with(token, true)
}

fn verify_token_local(token: &str) -> Result<Value, String> {
    verify_token_with(token, false)
}

fn verify_token_with(token: &str, allow_network: bool) -> Result<Value, String> {
    let payload = verify_token_payload(token)?;
    let parts = token.split('.').collect::<Vec<_>>();
    let bytes = decode_part(parts[2])?;
    let signature = signature_from_bytes(&bytes)?;
    let signed = format!("{}.{}", parts[0], parts[1]);
    verify_signature(signed.as_bytes(), &signature, allow_network)?;
    Ok(payload)
}

fn verify_token_payload(token: &str) -> Result<Value, String> {
    let parts = token.split('.').collect::<Vec<_>>();
    if parts.len() != 3 {
        return Err("Invalid license token format.".into());
    }
    let header: Value = serde_json::from_slice(&decode_part(parts[0])?)
        .map_err(|_| "Invalid license token data.")?;
    if !matches!(header["alg"].as_str(), Some("ES256" | "ES256-DER")) {
        return Err("Unsupported license token signature.".into());
    }
    let payload: Value = serde_json::from_slice(&decode_part(parts[1])?)
        .map_err(|_| "Invalid license token data.")?;
    if let Some(expires) = payload["expiresAt"]
        .as_str()
        .filter(|value| !value.is_empty())
    {
        if let Ok(expiry) = OffsetDateTime::parse(expires, &Rfc3339) {
            if expiry < OffsetDateTime::now_utc() {
                return Err("License token has expired.".into());
            }
        }
    }
    Ok(payload)
}

fn signature_from_bytes(bytes: &[u8]) -> Result<Signature, String> {
    if bytes.len() == 64 {
        Signature::from_slice(bytes)
    } else {
        Signature::from_der(bytes)
    }
    .map_err(|_| "License token signature is not valid.".into())
}

fn verify_signature(
    signed: &[u8],
    signature: &Signature,
    allow_network: bool,
) -> Result<(), String> {
    if public_key()?.verify(signed, signature).is_ok() {
        return Ok(());
    }
    let configured = !env::var("MOUNTLET_LICENSE_PUBLIC_KEY")
        .unwrap_or_default()
        .trim()
        .is_empty();
    if allow_network && !configured && verify_with_rotated_key(signed, signature)? {
        return Ok(());
    }
    Err("License token signature is not valid.".into())
}
fn api() -> String {
    env::var("MOUNTLET_LICENSE_API_URL")
        .unwrap_or_else(|_| API.into())
        .trim_end_matches('/')
        .to_string()
}
fn post(action: &str, body: Value) -> Result<Value, String> {
    let response = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(12))
        .build()
        .map_err(|error| error.to_string())?
        .post(format!("{}/{action}", api()))
        .header(
            "User-Agent",
            format!("Mountlet/{}", env!("CARGO_PKG_VERSION")),
        )
        .json(&body)
        .send()
        .map_err(|error| format!("Could not reach the license server: {error}"))?;
    let status = response.status();
    let value: Value = response
        .json()
        .map_err(|_| "The license server returned invalid data.")?;
    if !status.is_success() || value.get("error").is_some() {
        return Err(value["error"]
            .as_str()
            .unwrap_or("License request failed")
            .into());
    }
    Ok(value)
}
fn host_name() -> String {
    env::var("HOSTNAME")
        .or_else(|_| env::var("COMPUTERNAME"))
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            fs::read_to_string("/etc/hostname")
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "This device".into())
}
pub fn default_device_label() -> String {
    format!("{} ({})", host_name(), system_name())
}
fn fingerprint() -> Result<String, String> {
    let record = trial()?;
    Ok(hex::encode(Sha256::digest(
        format!(
            "{}|{}",
            machine_hint(),
            record["install_id"].as_str().unwrap_or("")
        )
        .as_bytes(),
    )))
}
fn from_payload(payload: &Value, key: String) -> Status {
    let email = payload["email"].as_str().unwrap_or("");
    let plan = payload["plan"].as_str().unwrap_or("");
    let kind = payload["licenseKind"].as_str().unwrap_or("paid");
    Status {
        state: "licensed".into(),
        summary: [
            if kind == "beta" {
                "Beta license"
            } else {
                "Licensed"
            },
            plan,
            email,
        ]
        .into_iter()
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>()
        .join(" - "),
        trial_days_remaining: 0,
        license_key: key,
        licensed_email: email.into(),
        plan: plan.into(),
        license_kind: kind.into(),
        max_devices: payload["maxDevices"].as_i64().unwrap_or(0),
        device_label: payload["deviceLabel"].as_str().unwrap_or("").into(),
        expires_at: payload["expiresAt"].as_str().unwrap_or("").into(),
    }
}
pub fn status() -> Result<Status, String> {
    let token = read_first("license-token.jwt");
    if !token.is_empty() {
        match verify_token_local(&token) {
            Ok(payload) => return Ok(from_payload(&payload, read_first("license-key.txt"))),
            Err(error) => {
                if error.to_ascii_lowercase().contains("expired") {
                    let payload = unverified_payload(&token);
                    let key = read_first("license-key.txt");
                    if payload["licenseKind"].as_str() == Some("beta")
                        || key.to_ascii_uppercase().starts_with("MTB-")
                    {
                        // Status is a local query and must never make startup
                        // wait for the network. A user can explicitly activate
                        // the stored key again from the License dialog.
                        clear("license-token.jwt");
                        clear("license-key.txt");
                        return Ok(Status {
                            state: "expired".into(),
                            summary: "Public beta access has ended. Buy a license to continue."
                                .into(),
                            trial_days_remaining: 0,
                            license_key: String::new(),
                            licensed_email: String::new(),
                            plan: String::new(),
                            license_kind: "beta".into(),
                            max_devices: 0,
                            device_label: String::new(),
                            expires_at: String::new(),
                        });
                    }
                    clear("license-token.jwt");
                    clear("license-key.txt");
                    let _ = reset_trial()?;
                } else {
                    return Ok(Status {
                        state: "expired".into(),
                        summary: format!("License cannot be verified: {error}"),
                        trial_days_remaining: 0,
                        license_key: read_first("license-key.txt"),
                        licensed_email: String::new(),
                        plan: String::new(),
                        license_kind: String::new(),
                        max_devices: 0,
                        device_label: String::new(),
                        expires_at: String::new(),
                    });
                }
            }
        }
    }
    let record = trial()?;
    let last_seen = record["last_seen_at"].as_f64().unwrap_or(0.0);
    if now() + 86_400.0 < last_seen {
        return Ok(Status {
            state: "expired".into(),
            summary: "Trial needs activation".into(),
            trial_days_remaining: 0,
            license_key: String::new(),
            licensed_email: String::new(),
            plan: String::new(),
            license_kind: String::new(),
            max_devices: 0,
            device_label: String::new(),
            expires_at: String::new(),
        });
    }
    let trial_ends = record["started_at"].as_f64().unwrap_or(now()) + TRIAL_SECONDS;
    let remaining = (trial_ends - now()).max(0.0);
    let days = (remaining / 86400.0).floor() as i64;
    let expires_at = OffsetDateTime::from_unix_timestamp(trial_ends as i64)
        .map(|value| value.format(&Rfc3339).unwrap_or_default())
        .unwrap_or_default();
    if remaining > 0.0 {
        Ok(Status {
            state: "trial".into(),
            summary: format!(
                "{} left in trial",
                if days == 0 {
                    "less than 1 day".into()
                } else {
                    format!("{days} day{}", if days == 1 { "" } else { "s" })
                }
            ),
            trial_days_remaining: days,
            license_key: String::new(),
            licensed_email: String::new(),
            plan: String::new(),
            license_kind: String::new(),
            max_devices: 0,
            device_label: String::new(),
            expires_at,
        })
    } else {
        Ok(Status {
            state: "expired".into(),
            summary: "Trial expired".into(),
            trial_days_remaining: 0,
            license_key: String::new(),
            licensed_email: String::new(),
            plan: String::new(),
            license_kind: String::new(),
            max_devices: 0,
            device_label: String::new(),
            expires_at,
        })
    }
}
pub fn activate(key: &str, label: &str) -> Result<Status, String> {
    if key.trim().is_empty() {
        return Err("Enter a license key.".into());
    }
    let label = if label.trim().is_empty() {
        default_device_label()
    } else {
        label.trim().into()
    };
    let value = post(
        "activate",
        json!({"licenseKey":key.trim(),"deviceLabel":label,"deviceFingerprint":fingerprint()?,"platform":format!("{} {}",system_name(),env::consts::ARCH),"appVersion":env!("CARGO_PKG_VERSION")}),
    )?;
    let token = value["token"]
        .as_str()
        .ok_or("The license server did not return a license token.")?;
    let payload = verify_token(token)?;
    store("license-token.jwt", token)?;
    store("license-key.txt", key.trim())?;
    Ok(from_payload(&payload, key.trim().into()))
}
pub fn devices() -> Result<Value, String> {
    let token = read_first("license-token.jwt");
    if token.is_empty() {
        return Err("Activate Mountlet before listing devices.".into());
    }
    let value = post("devices", json!({"token":token}))?;
    if let Some(token) = value["token"].as_str().filter(|value| !value.is_empty()) {
        verify_token(token)?;
        store("license-token.jwt", token)?;
    }
    Ok(value)
}
pub fn deactivate(device_id: &str) -> Result<(), String> {
    let token = read_first("license-token.jwt");
    if token.is_empty() {
        return Ok(());
    }
    let current_device = verify_token(&token)
        .ok()
        .and_then(|payload| payload["deviceId"].as_str().map(str::to_string));
    let mut body = json!({"token":token});
    if !device_id.is_empty() {
        body["deviceId"] = json!(device_id);
    }
    post("deactivate", body)?;
    if device_id.is_empty() || current_device.as_deref() == Some(device_id) {
        clear("license-token.jwt");
        clear("license-key.txt");
    }
    Ok(())
}

pub fn write_diagnostics(path: &Path) -> Result<(), String> {
    let mut lines = vec![
        format!("Mountlet {}", env!("CARGO_PKG_VERSION")),
        format!(
            "config: {}",
            mountlet_config_dir()
                .map(|value| value.display().to_string())
                .unwrap_or_else(|| "unavailable".into())
        ),
        format!(
            "state: {}",
            mountlet_state_dir()
                .map(|value| value.display().to_string())
                .unwrap_or_else(|| "unavailable".into())
        ),
    ];
    let started = Instant::now();
    let hint_at = Instant::now();
    let _ = machine_hint();
    lines.push(format!("machine_hint: {}ms", hint_at.elapsed().as_millis()));
    let token_at = Instant::now();
    let token = read_first("license-token.jwt");
    lines.push(format!(
        "token_present: {} ({}ms)",
        !token.is_empty(),
        token_at.elapsed().as_millis()
    ));
    if !token.is_empty() {
        let verify_at = Instant::now();
        let verified = verify_token_local(&token);
        lines.push(format!(
            "local_verify: {} ({}ms)",
            verified
                .as_ref()
                .err()
                .cloned()
                .unwrap_or_else(|| "ok".into()),
            verify_at.elapsed().as_millis()
        ));
    }
    lines.push("trial_paths:".into());
    for trial_path in trial_paths() {
        lines.push(format!("  {}", trial_path.display()));
    }
    let status_at = Instant::now();
    match status() {
        Ok(value) => lines.push(format!(
            "status: {} ({}) ({}ms)",
            value.state,
            value.summary,
            status_at.elapsed().as_millis()
        )),
        Err(error) => lines.push(format!(
            "status_error: {error} ({}ms)",
            status_at.elapsed().as_millis()
        )),
    }
    lines.push(format!("total: {}ms", started.elapsed().as_millis()));
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    fs::write(path, lines.join("\n") + "\n").map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trial_migration_keeps_the_oldest_valid_clock() {
        let selected = oldest_trial(
            vec![
                json!({"install_id":"tauri","started_at":200.0,"last_seen_at":210.0}),
                json!({"install_id":"python","started_at":100.0,"last_seen_at":190.0}),
            ],
            300.0,
        )
        .unwrap();
        assert_eq!(selected["install_id"], "python");
        assert_eq!(selected["started_at"], 100.0);
    }

    #[test]
    fn bundled_license_public_key_is_valid() {
        parse_public_key(BUNDLED_PUBLIC_KEY).unwrap();
    }

    fn unsigned_token() -> String {
        let header = URL_SAFE_NO_PAD.encode(br#"{"alg":"ES256"}"#);
        let payload = URL_SAFE_NO_PAD
            .encode(br#"{"email":"user@example.com","expiresAt":"2099-01-01T00:00:00Z"}"#);
        let signature = URL_SAFE_NO_PAD.encode([0u8; 64]);
        format!("{header}.{payload}.{signature}")
    }

    #[test]
    fn local_status_rejects_unknown_signatures_without_waiting_on_the_network() {
        let token = unsigned_token();
        let started = Instant::now();
        let error = verify_token_local(&token).expect_err("zero signatures must not verify");
        assert!(error.to_ascii_lowercase().contains("signature"));
        assert!(
            started.elapsed() < std::time::Duration::from_secs(2),
            "local verification waited on the network: {:?}",
            started.elapsed()
        );
    }
}
