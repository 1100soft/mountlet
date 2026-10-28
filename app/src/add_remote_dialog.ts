import {
  createRemote,
  driveOauthSources,
  oauthClientAvailability,
  oauthPortStatus,
  openExternal,
  openRcloneConfigTerminal,
  terminateOauthRclone,
  type DriveOauthSource,
  type OauthPortStatus,
  type RemoteConfigData,
  type SaveRemoteConfigRequest,
} from "./backend.ts";
import { actionIcon } from "./icons.ts";
import { bindScaledSelect, showError, trapModalFocus } from "./dialogs.ts";
import { defaultOAuthClient, googleAccountValue, OAUTH_CLIENT_OPTIONS, OAUTH_REMOTE_TYPES as OAUTH_TYPES, updateOAuthClientOptions } from "./remote_auth.ts";

const S3_OPTIONS = [
  { label: "Cloudflare R2", provider: "Cloudflare", suffix: "Cloudflare R2", endpoint: "https://<ACCOUNT_ID>.r2.cloudflarestorage.com", region: "auto", hideEndpoint: false, extra: { acl: "private", no_check_bucket: "true" } },
  { label: "MinIO / S3-compatible", provider: "Minio", suffix: "MinIO", endpoint: "http://127.0.0.1:9000", region: "us-east-1", hideEndpoint: false, extra: {} },
  { label: "Amazon S3", provider: "AWS", suffix: "Amazon S3", endpoint: "", region: "us-east-1", hideEndpoint: true, extra: {} },
  { label: "Wasabi", provider: "Wasabi", suffix: "Wasabi", endpoint: "https://s3.wasabisys.com", region: "us-east-1", hideEndpoint: false, extra: {} },
  { label: "Other S3-compatible", provider: "Other", suffix: "S3", endpoint: "https://s3.example.com", region: "us-east-1", hideEndpoint: false, extra: {} },
] as const;
const WEBDAV_VENDORS = [
  { label: "Nextcloud", vendor: "nextcloud", suffix: "Nextcloud", url: "https://cloud.example.com/remote.php/dav/files/user" },
  { label: "ownCloud", vendor: "owncloud", suffix: "ownCloud", url: "https://cloud.example.com/remote.php/webdav/" },
  { label: "SharePoint Online", vendor: "sharepoint", suffix: "SharePoint WebDAV", url: "https://tenant.sharepoint.com/sites/site/Shared%20Documents" },
  { label: "SharePoint NTLM", vendor: "sharepoint-ntlm", suffix: "SharePoint NTLM", url: "https://sharepoint.example.com/sites/site/Documents" },
  { label: "Fastmail Files", vendor: "fastmail", suffix: "Fastmail Files", url: "https://webdav.fastmail.com/" },
  { label: "rclone WebDAV server", vendor: "rclone", suffix: "rclone WebDAV", url: "http://127.0.0.1:8080/" },
  { label: "Other WebDAV", vendor: "other", suffix: "WebDAV", url: "https://cloud.example.com/webdav" },
] as const;
const PROVIDERS = [
  ["Google Drive", "drive", "Drive"],
  ["Google Photos (limited)", "gphotos", "Google Photos"],
  ["Dropbox", "dropbox", "Dropbox"],
  ["Microsoft OneDrive", "onedrive", "OneDrive"],
  ["Box", "box", "Box"],
  ["pCloud", "pcloud", "pCloud"],
  ["iCloud Drive / Photos", "iclouddrive", "iCloud"],
  ["Koofr", "koofr", "Koofr"],
  ["Proton Drive", "protondrive", "Proton Drive"],
  ["MEGA", "mega", "MEGA"],
  ["Nextcloud", "nextcloud", "Nextcloud"],
  ["S3-compatible storage", "s3", "S3"],
  ["WebDAV", "webdav", "WebDAV"],
  ["Other provider (rclone config)", "__external__", "Remote"],
] as const;
const NAME_PLACEHOLDERS: Record<string, string> = {
  drive: "Personal Drive", gphotos: "Personal Photos", dropbox: "Personal Dropbox",
  onedrive: "Personal OneDrive", box: "Work Box", pcloud: "Personal pCloud",
  iclouddrive: "Personal iCloud", koofr: "Personal Koofr", protondrive: "Personal Proton Drive",
  mega: "Personal MEGA", nextcloud: "Personal Nextcloud", s3: "Archive S3", webdav: "Nextcloud",
};

export interface AddRemoteResult {
  remoteId: string;
  provider: string;
  mountAfter: boolean;
  localAuth?: boolean;
}

export interface AddRemoteDialogOptions {
  tutorial?: boolean;
  onTutorialSkip?: () => void;
  edit?: {
    config: RemoteConfigData;
    save: (request: SaveRemoteConfigRequest) => Promise<string | null>;
    remove: () => Promise<boolean>;
  };
}

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const value = document.createElement(tag);
  value.className = className;
  value.textContent = text;
  return value;
}

function labeled(label: string, control: HTMLElement, extraClass = ""): HTMLLabelElement {
  const row = node("label", `settings-row add-remote-row ${extraClass}`);
  row.append(node("span", "settings-label", label), control);
  return row;
}

function input(placeholder: string, type = "text", title = ""): HTMLInputElement {
  const field = node("input", "settings-input");
  field.type = type;
  field.placeholder = placeholder;
  if (title) field.title = title;
  return field;
}

function select(options: ReadonlyArray<readonly [string, string]>): HTMLSelectElement {
  const field = bindScaledSelect(node("select", "settings-input"));
  for (const [value, label] of options) field.append(new Option(label, value));
  return field;
}

function nextcloudWebdavUrl(server: string, username: string): string {
  const trimmed = server.trim().replace(/\/+$/, "");
  if (trimmed.toLowerCase().includes("/remote.php/")) return `${trimmed}/`;
  return `${trimmed}/remote.php/dav/files/${encodeURIComponent(username.trim())}/`;
}

function setVisible(rows: HTMLElement[], visible: boolean): void {
  for (const row of rows) row.hidden = !visible;
}

export async function openAddRemoteDialog(options: AddRemoteDialogOptions = {}): Promise<AddRemoteResult | "external" | null> {
  document.querySelector(".modal-layer")?.remove();
  const [sources, availability] = await Promise.all([driveOauthSources().catch(() => [] as DriveOauthSource[]), oauthClientAvailability()]);
  const editing = options.edit;
  const saved = editing?.config;
  const layer = node("div", "modal-layer");
  const dialog = node("section", "modal-dialog remote-config-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.append(node("h2", "", saved ? `${saved.alias} settings` : "Add remote"));
  const form = node("form", "add-remote-form");

  const provider = select(PROVIDERS.map(([label, type]) => [type, label] as const));
  if (saved) {
    provider.value = saved.provider === "webdav" && saved.fields.vendor === "nextcloud" ? "nextcloud" : saved.provider;
    if (!provider.value) provider.add(new Option(saved.providerLabel, saved.provider, true, true));
    provider.disabled = true;
  }
  const name = input("Personal Drive");
  const credentialSource = select([
    ...OAUTH_CLIENT_OPTIONS,
    ...sources.filter(source => source.remoteId !== saved?.id).map(source => [`reuse:${source.remoteId}`, `Use ${source.label} credentials`] as const),
  ]);
  credentialSource.value = defaultOAuthClient(provider.value, availability);
  const clientId = input("Client ID", "text", "OAuth client ID. This overrides the default client.");
  const clientSecret = input("Client secret", "password", "OAuth client secret matching the client ID.");
  const account = input("Optional, for example name@gmail.com", "text", "Used to suggest the right Google account during sign-in.");
  const gphotosReadOnly = node("input");
  gphotosReadOnly.type = "checkbox";
  const gphotosReadOnlyRow = node("label", "settings-check");
  gphotosReadOnlyRow.append(gphotosReadOnly, node("span", "", "Read only"));
  gphotosReadOnlyRow.title = "Request read-only Google Photos access. Leave off if you want Mountlet/rclone to upload media.";
  const sharedMine = node("input"); sharedMine.type = "radio"; sharedMine.name = "drive-kind"; sharedMine.checked = true;
  const sharedTeam = node("input"); sharedTeam.type = "radio"; sharedTeam.name = "drive-kind";
  const driveKind = node("div", "window-mode-choices");
  const mineChoice = node("label", "window-mode-choice"); mineChoice.append(sharedMine, node("strong", "", "My Drive"));
  const teamChoice = node("label", "window-mode-choice"); teamChoice.append(sharedTeam, node("strong", "", "Shared drive"));
  driveKind.append(mineChoice, teamChoice);
  const sharedDriveId = input("Shared drive ID");
  sharedDriveId.disabled = true;
  sharedTeam.addEventListener("change", () => { sharedDriveId.disabled = !sharedTeam.checked; });
  sharedMine.addEventListener("change", () => { sharedDriveId.disabled = !sharedTeam.checked; });
  const localAuth = node("input"); localAuth.type = "radio"; localAuth.name = "auth-kind"; localAuth.checked = true;
  const pasteAuth = node("input"); pasteAuth.type = "radio"; pasteAuth.name = "auth-kind";
  const authKind = node("div", "window-mode-choices");
  const localChoice = node("label", "window-mode-choice"); localChoice.append(localAuth, node("strong", "", "Open browser"), node("small", "", "rclone listens on localhost:53682"));
  const pasteChoice = node("label", "window-mode-choice"); pasteChoice.append(pasteAuth, node("strong", "", "Paste token"), node("small", "", "Finish authentication in the wizard"));
  authKind.append(localChoice, pasteChoice);
  const s3Provider = select(S3_OPTIONS.map(option => [option.provider, option.label] as const));
  const s3Endpoint = input("Endpoint");
  const s3Region = input("us-east-1");
  const s3Access = input("Access key");
  const s3Secret = input("Secret key", "password");
  const s3Path = input("Bucket name or bucket/folder");
  const webdavVendor = select(WEBDAV_VENDORS.map(option => [option.vendor, option.label] as const));
  const webdavUrl = input("https://cloud.example.com");
  const webdavUser = input("Username");
  const webdavPass = input("Password", "password");
  const icloudService = select([["drive", "iCloud Drive"], ["photos", "iCloud Photos"]]);
  const icloudUser = input("Apple ID");
  const icloudPass = input("Password", "password");
  const megaUser = input("Email");
  const megaPass = input("Password", "password");
  const mega2fa = input("Optional 2FA code");
  const protonUser = input("Email");
  const protonPass = input("Password", "password");
  const proton2fa = input("Optional 2FA code");
  const protonMailbox = input("Mailbox password", "password");
  const koofrUser = input("Email");
  const koofrPass = input("Password", "password");
  const mountAfter = node("input"); mountAfter.type = "checkbox"; mountAfter.checked = true;
  const mountRow = node("label", "settings-check");
  mountRow.append(mountAfter, node("span", "", "Mount after creating"));
  mountRow.title = "Mount the new remote as an operating-system folder after setup succeeds.";
  const portStatus = node("p", "add-remote-status");
  const killRclone = node("button", "", "Stop leftover rclone");
  killRclone.type = "button";
  const gphotosHelp = node("p", "add-remote-help", "Google Photos is limited by Google's API. Recent rclone versions can only download media uploaded by rclone, and album writes stay inside the album folder.");
  const gphotosLink = node("button", "link-button", "rclone Google Photos limits");
  gphotosLink.type = "button";
  gphotosLink.addEventListener("click", () => void openExternal("https://rclone.org/googlephotos/"));
  gphotosHelp.append(document.createTextNode(" "), gphotosLink);
  const driveClientHelp = node("p", "add-remote-help drive-client-help");
  driveClientHelp.title = "Mountlet uses the credentials included in this build. Custom credentials must belong to the same registration.";
  const rcloneClientGuide = node("button", "link-button", "Setup guide");
  const googleOauthGuide = node("button", "link-button", "Google OAuth help");
  rcloneClientGuide.type = googleOauthGuide.type = "button";
  rcloneClientGuide.addEventListener("click", () => void openExternal("https://rclone.org/drive/#making-your-own-client-id"));
  googleOauthGuide.addEventListener("click", () => void openExternal("https://developers.google.com/workspace/guides/configure-oauth-consent"));
  driveClientHelp.append(rcloneClientGuide, document.createTextNode(" · "), googleOauthGuide);

  const rows = {
    name: labeled("Remote name", name),
    credentials: labeled("OAuth client", credentialSource),
    driveClientHelp,
    clientId: labeled("Client ID", clientId),
    clientSecret: labeled("Client secret", clientSecret),
    account: labeled("Google account", account),
    gphotosReadOnly: labeled("Google Photos access", gphotosReadOnlyRow),
    gphotosHelp,
    driveKind: labeled("Drive", driveKind),
    sharedDriveId: labeled("Shared drive ID", sharedDriveId),
    auth: labeled("Sign-in", authKind),
    port: node("div", "add-remote-port"),
    s3Provider: labeled("S3 provider", s3Provider),
    s3Endpoint: labeled("Endpoint", s3Endpoint),
    s3Region: labeled("Region", s3Region),
    s3Access: labeled("Access key", s3Access),
    s3Secret: labeled("Secret key", s3Secret),
    s3Path: labeled("Bucket", s3Path),
    webdavVendor: labeled("WebDAV vendor", webdavVendor),
    webdavUrl: labeled("Server address", webdavUrl),
    webdavUser: labeled("Username", webdavUser),
    webdavPass: labeled("Password", webdavPass),
    icloudService: labeled("iCloud service", icloudService),
    icloudUser: labeled("Apple ID", icloudUser),
    icloudPass: labeled("Password", icloudPass),
    megaUser: labeled("MEGA user", megaUser),
    megaPass: labeled("Password", megaPass),
    mega2fa: labeled("2FA code", mega2fa),
    protonUser: labeled("Proton user", protonUser),
    protonPass: labeled("Password", protonPass),
    proton2fa: labeled("2FA code", proton2fa),
    protonMailbox: labeled("Mailbox password", protonMailbox),
    koofrUser: labeled("Koofr user", koofrUser),
    koofrPass: labeled("Password", koofrPass),
    mount: mountRow,
  };
  const mountPath = input("Default folder");
  const remotePath = input("Optional remote folder");
  const autoMount = select([["", "App default"], ["true", "Enabled"], ["false", "Disabled"]]);
  const mountFlags = input("Additional rclone flags");
  const readOnlyMount = node("input"); readOnlyMount.type = "checkbox";
  const allowOther = node("input"); allowOther.type = "checkbox";
  const advanced = node("details", "remote-advanced");
  advanced.append(node("summary", "", "Options"));
  const advancedFields = new Map<string, HTMLInputElement | HTMLSelectElement>();
  if (saved) {
    name.value = saved.alias;
    credentialSource.value = saved.oauthClient ?? "custom";
    clientId.value = saved.oauthClient === "custom" ? saved.fields.client_id ?? "" : "";
    clientSecret.value = saved.oauthClient === "custom" ? saved.fields.client_secret ?? "" : "";
    account.value = saved.fields.mountlet_google_account ?? "";
    gphotosReadOnly.checked = saved.fields.read_only === "true";
    sharedTeam.checked = Boolean(saved.fields.team_drive); sharedMine.checked = !sharedTeam.checked;
    sharedDriveId.value = saved.fields.team_drive ?? ""; sharedDriveId.disabled = !sharedTeam.checked;
    s3Provider.value = saved.fields.provider ?? S3_OPTIONS[0].provider;
    if (!s3Provider.value && saved.fields.provider) s3Provider.add(new Option(saved.fields.provider, saved.fields.provider, true, true));
    s3Endpoint.value = saved.fields.endpoint ?? ""; s3Region.value = saved.fields.region ?? "";
    s3Access.value = saved.fields.access_key_id ?? ""; s3Secret.value = saved.fields.secret_access_key ?? "";
    s3Path.value = saved.remotePath;
    webdavVendor.value = saved.fields.vendor ?? "other";
    if (!webdavVendor.value && saved.fields.vendor) webdavVendor.add(new Option(saved.fields.vendor, saved.fields.vendor, true, true));
    webdavUrl.value = saved.fields.url ?? "";
    if (provider.value === "nextcloud") webdavUrl.value = webdavUrl.value.replace(/\/remote\.php\/(?:dav\/files\/[^/]+|webdav)\/?$/i, "");
    webdavUser.value = saved.fields.user ?? ""; webdavPass.value = saved.fields.pass ?? "";
    icloudService.value = saved.fields.service ?? "drive"; icloudUser.value = saved.fields.apple_id ?? ""; icloudPass.value = saved.fields.password ?? "";
    megaUser.value = saved.fields.user ?? ""; megaPass.value = saved.fields.pass ?? "";
    protonUser.value = saved.fields.username ?? ""; protonPass.value = saved.fields.password ?? "";
    protonMailbox.value = saved.fields.mailbox_password ?? "";
    koofrUser.value = saved.fields.user ?? ""; koofrPass.value = saved.fields.password ?? "";
    mountPath.value = saved.mountPath; remotePath.value = saved.remotePath;
    autoMount.value = saved.autoMount === null ? "" : String(saved.autoMount);
    const flags = saved.mountFlags.split(/\s+/).filter(Boolean);
    readOnlyMount.checked = flags.includes("--read-only"); allowOther.checked = flags.includes("--allow-other");
    mountFlags.value = flags.filter(flag => !["--read-only", "--allow-other"].includes(flag)).join(" ");
    advanced.append(labeled("Local folder", mountPath));
    if (saved.provider !== "s3") advanced.append(labeled("Remote path", remotePath));
    advanced.append(labeled("Auto-mount", autoMount), labeled("Read-only mount", readOnlyMount), labeled("Allow other users", allowOther), labeled("Mount flags", mountFlags));
    localAuth.checked = saved.fields.config_is_local !== "false"; pasteAuth.checked = !localAuth.checked;
    const primary = new Set<string>();
    const primaryByProvider: Record<string, readonly string[]> = {
      drive: ["client_id", "client_secret", "mountlet_google_account", "config_is_local", "team_drive"],
      gphotos: ["client_id", "client_secret", "mountlet_google_account", "config_is_local", "read_only"],
      dropbox: ["client_id", "client_secret", "config_is_local"], onedrive: ["client_id", "client_secret", "config_is_local"],
      box: ["client_id", "client_secret", "config_is_local"], pcloud: ["client_id", "client_secret", "config_is_local"],
      s3: ["provider", "endpoint", "region", "access_key_id", "secret_access_key"], webdav: ["vendor", "url", "user", "pass"],
      iclouddrive: ["service", "apple_id", "password"], mega: ["user", "pass"], protondrive: ["username", "password", "mailbox_password"],
      koofr: ["user", "password"],
    };
    for (const key of primaryByProvider[saved.provider] ?? []) primary.add(key);
    for (const [key, value] of Object.entries(saved.fields)) {
      if (primary.has(key)) continue;
      const choices: Record<string, ReadonlyArray<readonly [string, string]>> = {
        scope: [["", "Default"], ["drive", "Full Drive access"], ["drive.readonly", "Read only"], ["drive.file", "App files"], ["drive.appfolder", "App folder"], ["drive.metadata.readonly", "Names and metadata"]],
        drive_type: [["", "Default"], ["personal", "Personal"], ["business", "Business"], ["documentLibrary", "SharePoint library"]],
      };
      const field = choices[key] ? select(choices[key]) : input("", saved.secretFields.includes(key) ? "password" : "text");
      if (field instanceof HTMLSelectElement && ![...field.options].some(option => option.value === value)) field.add(new Option(value, value));
      field.value = value;
      if (field instanceof HTMLInputElement && ["shared_with_me", "env_auth", "enable_caching", "read_size", "include_archived"].includes(key)) {
        field.type = "checkbox"; field.checked = ["true", "1", "yes", "on"].includes(value.toLowerCase());
      }
      advancedFields.set(key, field);
      advanced.append(labeled(key.replaceAll("_", " "), field));
    }
    for (const field of [clientSecret, s3Secret, webdavPass, icloudPass, megaPass, protonPass, protonMailbox, koofrPass]) {
      field.placeholder = "Leave blank to keep saved value";
    }
    rows.mount.hidden = true;
  }
  rows.port.append(portStatus, killRclone);
  form.append(
    labeled("Provider", provider), rows.name, rows.credentials, rows.driveClientHelp, rows.clientId, rows.clientSecret, rows.account,
    rows.gphotosReadOnly, rows.gphotosHelp, rows.driveKind, rows.sharedDriveId, rows.auth, rows.port,
    rows.s3Provider, rows.s3Endpoint, rows.s3Region, rows.s3Access, rows.s3Secret, rows.s3Path,
    rows.webdavVendor, rows.webdavUrl, rows.webdavUser, rows.webdavPass,
    rows.icloudService, rows.icloudUser, rows.icloudPass,
    rows.megaUser, rows.megaPass, rows.mega2fa,
    rows.protonUser, rows.protonPass, rows.proton2fa, rows.protonMailbox,
    rows.koofrUser, rows.koofrPass, rows.mount,
  );
  if (saved) form.append(advanced);

  const applyS3 = () => {
    const option = S3_OPTIONS.find(item => item.provider === s3Provider.value) ?? S3_OPTIONS[0];
    s3Endpoint.placeholder = option.endpoint || "Optional";
    if (!s3Endpoint.value || S3_OPTIONS.some(item => item.endpoint === s3Endpoint.value)) {
      s3Endpoint.value = option.hideEndpoint ? "" : option.endpoint;
    }
    s3Region.placeholder = option.region;
    if (!s3Region.value || S3_OPTIONS.some(item => item.region === s3Region.value)) s3Region.value = option.region;
    rows.s3Endpoint.hidden = provider.value !== "s3" || option.hideEndpoint;
  };
  const applyWebdav = () => {
    const option = WEBDAV_VENDORS.find(item => item.vendor === webdavVendor.value) ?? WEBDAV_VENDORS[0];
    const nextcloud = provider.value === "nextcloud";
    (rows.webdavUrl.querySelector(".settings-label") as HTMLElement).textContent = nextcloud ? "Server address" : "WebDAV URL";
    webdavUrl.placeholder = nextcloud ? "https://cloud.example.com" : option.url;
  };
  const applyCredentials = () => {
    const custom = credentialSource.value === "custom";
    const oauth = OAUTH_TYPES.has(provider.value);
    const showClient = oauth && custom;
    if (custom && saved?.oauthClient === "custom" && !clientId.value && !clientSecret.value) {
      clientId.value = saved.fields.client_id ?? ""; clientSecret.value = saved.fields.client_secret ?? "";
    }
    setVisible([rows.clientId, rows.clientSecret], showClient);
    clientId.disabled = !showClient;
    clientSecret.disabled = !showClient;
    if (oauth && !custom) {
      clientId.value = "";
      clientSecret.value = "";
    }
  };
  let credentialProvider = provider.value;
  const applyProvider = () => {
    const type = provider.value;
    if (credentialProvider !== type) {
      credentialSource.value = defaultOAuthClient(type, availability);
      credentialProvider = type;
    }
    name.placeholder = NAME_PLACEHOLDERS[type] || "Cloud storage";
    const isDrive = type === "drive";
    updateOAuthClientOptions(credentialSource, type, availability);
    if (!saved && credentialSource.selectedOptions[0]?.disabled) credentialSource.value = defaultOAuthClient(type, availability);
    const isGphotos = type === "gphotos";
    const isS3 = type === "s3";
    const isWebdav = type === "webdav" || type === "nextcloud";
    const isExternal = type === "__external__";
    setVisible([rows.name, rows.mount], !isExternal);
    if (saved) rows.mount.hidden = true;
    setVisible([rows.credentials], OAUTH_TYPES.has(type));
    setVisible([rows.driveClientHelp], isDrive || isGphotos);
    setVisible([rows.account], isDrive || isGphotos);
    setVisible([rows.gphotosReadOnly, rows.gphotosHelp], isGphotos);
    if (saved) { rows.driveClientHelp.hidden = true; rows.gphotosHelp.hidden = true; }
    setVisible([rows.driveKind, rows.sharedDriveId], isDrive);
    setVisible([rows.auth, rows.port], OAUTH_TYPES.has(type));
    setVisible([rows.s3Provider, rows.s3Region, rows.s3Access, rows.s3Secret, rows.s3Path], isS3);
    setVisible([rows.webdavUrl, rows.webdavUser, rows.webdavPass], isWebdav);
    rows.webdavVendor.hidden = type !== "webdav";
    setVisible([rows.icloudService, rows.icloudUser, rows.icloudPass], type === "iclouddrive");
    setVisible([rows.megaUser, rows.megaPass, rows.mega2fa], type === "mega");
    setVisible([rows.protonUser, rows.protonPass, rows.proton2fa, rows.protonMailbox], type === "protondrive");
    setVisible([rows.koofrUser, rows.koofrPass], type === "koofr");
    applyS3();
    applyWebdav();
    applyCredentials();
    void refreshPort();
  };

  let portTimer = 0;
  const refreshPort = async () => {
    if (!OAUTH_TYPES.has(provider.value)) { rows.port.hidden = true; return; }
    const status: OauthPortStatus = await oauthPortStatus();
    if (status.available) {
      portStatus.textContent = "OAuth port 53682 is available.";
      killRclone.hidden = true;
    } else if (status.rclonePid) {
      portStatus.textContent = "A leftover rclone process is using OAuth port 53682.";
      killRclone.hidden = false;
    } else {
      portStatus.textContent = status.owner
        ? `OAuth port 53682 is in use: ${status.owner}`
        : "OAuth port 53682 is in use by another program.";
      killRclone.hidden = true;
    }
  };
  killRclone.addEventListener("click", async () => {
    await terminateOauthRclone();
    await refreshPort();
  });
  provider.addEventListener("change", applyProvider);
  s3Provider.addEventListener("change", applyS3);
  webdavVendor.addEventListener("change", applyWebdav);
  credentialSource.addEventListener("change", applyCredentials);
  applyProvider();
  portTimer = window.setInterval(() => void refreshPort(), 1500);

  const actions = node("div", "dialog-actions");
  const cancel = actionIcon("", "Cancel");
  const create = actionIcon("", saved ? "Save" : "Add remote");
  create.classList.add("primary");
  create.dataset.dialogConfirm = "true";
  form.addEventListener("submit", event => { event.preventDefault(); if (!create.disabled) create.click(); });
  if (saved) {
    const state = () => JSON.stringify([...form.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select")].map(field => field instanceof HTMLInputElement && ["checkbox", "radio"].includes(field.type) ? field.checked : field.value));
    const baseline = state();
    create.disabled = true;
    const updateDirty = () => { create.disabled = state() === baseline; };
    form.addEventListener("input", updateDirty); form.addEventListener("change", updateDirty);
  }
  cancel.type = "button"; create.type = "button";
  let finish: (result: AddRemoteResult | "external" | null) => void = () => undefined;
  const closed = new Promise<AddRemoteResult | "external" | null>(resolve => { finish = resolve; });
  const close = (result: AddRemoteResult | "external" | null) => {
    window.clearInterval(portTimer);
    layer.remove();
    finish(result);
  };
  cancel.addEventListener("click", () => close(null));
  layer.addEventListener("mousedown", event => { if (event.target === layer) close(null); });
  layer.addEventListener("keydown", event => { if (event.key === "Escape") close(null); });
  const collectFields = () => {
    const type = provider.value;
    const fields: Record<string, string> = saved ? { ...saved.fields } : {};
    for (const [key, field] of advancedFields) fields[key] = field instanceof HTMLInputElement && field.type === "checkbox" ? String(field.checked) : field.value;
    let suffix = PROVIDERS.find(item => item[1] === type)?.[2] ?? type;
    const backend = type === "nextcloud" ? "webdav" : type;
    if (OAUTH_TYPES.has(type)) {
      fields.mountlet_oauth_client = credentialSource.value;
      if (clientId.value.trim()) fields.client_id = clientId.value.trim();
      if (clientSecret.value.trim()) fields.client_secret = clientSecret.value.trim();
    }
    if (type === "drive" || type === "gphotos") {
      if (type === "drive") {
        fields.scope = fields.scope || "drive";
        fields.config_team_drive = sharedTeam.checked ? "true" : "false";
        fields.team_drive = sharedTeam.checked ? sharedDriveId.value.trim() : "";
      }
      if (type === "gphotos") {
        fields.read_only = gphotosReadOnly.checked ? "true" : "false";
        fields.read_size = fields.read_size || "true";
        fields.config_edit_advanced = "false";
      }
    }
    if (OAUTH_TYPES.has(type)) fields.config_is_local = localAuth.checked ? "true" : "false";
    let selectedPath = saved ? remotePath.value.trim() : "";
    if (type === "s3") {
      const option = S3_OPTIONS.find(item => item.provider === s3Provider.value) ?? S3_OPTIONS[0];
      suffix = option.suffix;
      fields.provider = s3Provider.value;
      fields.access_key_id = s3Access.value.trim();
      fields.secret_access_key = s3Secret.value;
      fields.region = s3Region.value.trim();
      fields.endpoint = s3Endpoint.value.trim();
      if (!saved) Object.assign(fields, option.extra);
      selectedPath = s3Path.value.trim().replace(/^\/+|\/+$/g, "");
    }
    if (type === "webdav" || type === "nextcloud") {
      const option = WEBDAV_VENDORS.find(item => item.vendor === webdavVendor.value) ?? WEBDAV_VENDORS[0];
      const vendor = type === "nextcloud" ? "nextcloud" : webdavVendor.value;
      suffix = type === "nextcloud" ? "Nextcloud" : option.suffix;
      const url = type === "nextcloud"
        ? nextcloudWebdavUrl(webdavUrl.value, webdavUser.value)
        : webdavUrl.value.trim();
      fields.url = url;
      fields.vendor = vendor;
      fields.user = webdavUser.value.trim();
      if (webdavPass.value) fields.pass = webdavPass.value;
    }
    if (type === "iclouddrive") {
      fields.service = icloudService.value;
      fields.apple_id = icloudUser.value.trim();
      fields.password = icloudPass.value;
    }
    if (type === "mega") {
      fields.user = megaUser.value.trim();
      fields.pass = megaPass.value;
      if (mega2fa.value.trim()) fields["2fa"] = mega2fa.value.trim();
    }
    if (type === "protondrive") {
      fields.username = protonUser.value.trim();
      fields.password = protonPass.value;
      fields.enable_caching = fields.enable_caching || "false";
      if (proton2fa.value.trim()) fields["2fa"] = proton2fa.value.trim();
      if (protonMailbox.value) fields.mailbox_password = protonMailbox.value;
    }
    if (type === "koofr") {
      fields.provider = fields.provider || "koofr";
      fields.user = koofrUser.value.trim();
      fields.password = koofrPass.value;
    }
    return { fields, suffix, backend, selectedPath };
  };
  const baselineFields = saved ? collectFields().fields : undefined;
  create.addEventListener("click", async event => {
    event.preventDefault();
    const type = provider.value;
    if (type === "__external__") {
      try { await openRcloneConfigTerminal(); close("external"); }
      catch (error) { await showError("rclone config", error); }
      return;
    }
    const alias = name.value.trim();
    if (!alias) { name.focus(); return; }
    create.disabled = true;
    try {
      if (/[:@/\\\r\n]/.test(alias)) throw new Error("Use a name without ':', '@', line breaks, or path separators.");
      const { fields, suffix, backend, selectedPath } = collectFields();
      if (editing && saved) {
        delete fields.mountlet_oauth_client;
        if (OAUTH_TYPES.has(backend)) { fields.client_id = clientId.value.trim(); fields.client_secret = clientSecret.value; }
        if (["drive", "gphotos"].includes(backend)) fields.mountlet_google_account = googleAccountValue(account.value);
        // Preserve saved representations/defaults for controls the user did not
        // change; a rename must not silently reconfigure provider credentials.
        for (const [key, value] of Object.entries(fields)) {
          if (["client_id", "client_secret"].includes(key) && credentialSource.value !== saved.oauthClient) continue;
          if (baselineFields && value === baselineFields[key]) {
            if (Object.hasOwn(saved.fields, key)) fields[key] = saved.fields[key];
            else delete fields[key];
          }
        }
        const remoteId = await editing.save({ remoteId: saved.id, alias, mountPath: mountPath.value, remotePath: selectedPath,
          mountFlags: [mountFlags.value.trim(), readOnlyMount.checked ? "--read-only" : "", allowOther.checked ? "--allow-other" : ""].filter(Boolean).join(" "), autoMount: autoMount.value === "" ? null : autoMount.value === "true", fields,
          oauthClient: OAUTH_TYPES.has(backend) ? credentialSource.value : undefined });
        if (remoteId) close({ remoteId, provider: backend, mountAfter: false });
        else create.disabled = false;
        return;
      }
      const remoteId = await createRemote({
        alias,
        provider: backend,
        providerLabel: suffix,
        fields,
        googleAccount: googleAccountValue(account.value),
        mountAfter: mountAfter.checked,
        remotePath: selectedPath,
      });
      close({ remoteId, provider: backend, mountAfter: mountAfter.checked, localAuth: localAuth.checked });
    } catch (error) {
      await showError(saved ? "Remote settings" : "Add remote", error);
      create.disabled = false;
    }
  });
  if (editing) {
    const remove = actionIcon("", "Delete"); remove.classList.add("danger");
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try { if (await editing.remove()) close(null); }
      catch (error) { await showError("Delete remote", error); }
      finally { remove.disabled = false; }
    });
    actions.append(remove);
  }
  actions.append(cancel, create);
  dialog.append(form, actions);
  layer.append(dialog);
  document.body.append(layer);

  if (options.tutorial) {
    const coach = node("aside", "tutorial-coach");
    coach.setAttribute("role", "status");
    const progress = node("span", "tutorial-progress");
    const message = node("strong", "tutorial-message");
    const controls = node("div", "tutorial-controls");
    const skip = node("button", "tutorial-skip", "Skip tutorial");
    const next = node("button", "primary tutorial-next", "Next");
    skip.type = next.type = "button";
    controls.append(skip, next);
    coach.append(progress, message, controls);
    dialog.prepend(coach);

    const providerRow = provider.closest<HTMLElement>(".settings-row")!;
    type TutorialStep = { target: HTMLElement; text: string };
    const detailRows = (): HTMLElement[] => {
      const byProvider: Record<string, HTMLElement[]> = {
        drive: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        gphotos: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        dropbox: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        onedrive: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        box: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        pcloud: [rows.credentials, ...(credentialSource.value === "custom" ? [rows.clientId, rows.clientSecret] : []), rows.auth],
        s3: [rows.s3Provider, rows.s3Access, rows.s3Secret, rows.s3Path],
        nextcloud: [rows.webdavUrl, rows.webdavUser, rows.webdavPass],
        webdav: [rows.webdavVendor, rows.webdavUrl, rows.webdavUser, rows.webdavPass],
        iclouddrive: [rows.icloudService, rows.icloudUser, rows.icloudPass],
        mega: [rows.megaUser, rows.megaPass], protondrive: [rows.protonUser, rows.protonPass], koofr: [rows.koofrUser, rows.koofrPass],
      };
      return (byProvider[provider.value] ?? []).filter(item => !item.hidden);
    };
    const steps = (): TutorialStep[] => [
      { target: providerRow, text: "Choose your storage provider." },
      { target: rows.name, text: "Give this remote a recognizable name." },
      ...detailRows().map(target => ({
        target,
        text: target === rows.credentials
          ? "Choose the default client or enter your own OAuth credentials."
          : `Set ${target.querySelector(".settings-label")?.textContent?.toLowerCase() || "the connection details"}.`,
      })),
      { target: rows.mount, text: "Choose whether to mount it now." },
      { target: create, text: "Create the remote and finish setup." },
    ];
    let step = 0;
    const paintStep = () => {
      dialog.querySelectorAll(".tutorial-highlight").forEach(item => item.classList.remove("tutorial-highlight"));
      const currentSteps = steps();
      step = Math.min(step, currentSteps.length - 1);
      const current = currentSteps[step];
      current.target.classList.add("tutorial-highlight");
      progress.textContent = `${step + 1} of ${currentSteps.length}`;
      message.textContent = current.text;
      next.textContent = step === currentSteps.length - 1 ? "Got it" : "Next";
      current.target.scrollIntoView({ block: "nearest" });
    };
    const stopTutorial = (skipped: boolean) => {
      dialog.querySelectorAll(".tutorial-highlight").forEach(item => item.classList.remove("tutorial-highlight"));
      coach.remove();
      if (skipped) options.onTutorialSkip?.();
    };
    skip.addEventListener("click", () => stopTutorial(true));
    next.addEventListener("click", () => {
      if (step === steps().length - 1) { stopTutorial(false); create.focus(); return; }
      step += 1;
      paintStep();
      if (step === 1) name.focus();
    });
    provider.addEventListener("change", () => { if (step === 0) { step = 1; paintStep(); name.focus(); } else paintStep(); });
    credentialSource.addEventListener("change", paintStep);
    name.addEventListener("input", () => { if (step === 1 && name.value.trim()) { step = 2; paintStep(); } }, { once: true });
    queueMicrotask(paintStep);
  }
  trapModalFocus(layer, dialog, name);
  return closed;
}
