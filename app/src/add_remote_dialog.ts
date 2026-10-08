import {
  createRemote,
  driveOauthSources,
  oauthPortStatus,
  openExternal,
  openRcloneConfigTerminal,
  terminateOauthRclone,
  type DriveOauthSource,
  type OauthPortStatus,
} from "./backend.ts";
import { bindScaledSelect, showError, trapModalFocus } from "./dialogs.ts";

const OAUTH_TYPES = new Set(["drive", "gphotos", "dropbox", "onedrive", "box", "pcloud"]);
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
}

export interface AddRemoteDialogOptions {
  tutorial?: boolean;
  onTutorialSkip?: () => void;
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

function googleAccountValue(value: string): string {
  const account = value.trim();
  return account && !account.includes("@") ? `${account}@gmail.com` : account;
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
  const sources = await driveOauthSources().catch(() => [] as DriveOauthSource[]);
  const layer = node("div", "modal-layer");
  const dialog = node("section", "modal-dialog remote-config-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.append(node("h2", "", "Add remote"));
  const form = node("form", "add-remote-form");

  const provider = select(PROVIDERS.map(([label, type]) => [type, label] as const));
  const name = input("Personal Drive");
  const credentialSource = select([
    ...sources.map(source => [source.remoteId, `Use ${source.label} credentials`] as const),
    ["builtin", "Built-in rclone client (retires in 2026)"] as const,
    ["custom", "Enter client ID and secret"] as const,
  ]);
  if (sources.length) credentialSource.value = sources[0].remoteId;
  else credentialSource.value = "builtin";
  const clientId = input("Optional", "text", "Google OAuth client ID. Leave blank to let rclone use its built-in client.");
  const clientSecret = input("Optional", "password", "Google OAuth client secret. Use the secret that matches the client ID.");
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
  const driveClientHelp = node("p", "add-remote-help drive-client-help", "For reliable long-term access, create your own Google OAuth client. ");
  const rcloneClientGuide = node("button", "link-button", "Setup guide");
  const googleOauthGuide = node("button", "link-button", "Google OAuth help");
  rcloneClientGuide.type = googleOauthGuide.type = "button";
  rcloneClientGuide.addEventListener("click", () => void openExternal("https://rclone.org/drive/#making-your-own-client-id"));
  googleOauthGuide.addEventListener("click", () => void openExternal("https://developers.google.com/workspace/guides/configure-oauth-consent"));
  driveClientHelp.append(rcloneClientGuide, document.createTextNode(" · "), googleOauthGuide);

  const rows = {
    name: labeled("Remote name", name),
    credentials: labeled("Google client", credentialSource),
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
    const drive = provider.value === "drive";
    const gphotos = provider.value === "gphotos";
    const showClient = gphotos || (drive && custom);
    setVisible([rows.clientId, rows.clientSecret], showClient);
    clientId.disabled = !showClient;
    clientSecret.disabled = !showClient;
    if (drive && !custom) {
      clientId.value = "";
      clientSecret.value = "";
    }
  };
  const applyProvider = () => {
    const type = provider.value;
    name.placeholder = NAME_PLACEHOLDERS[type] || "Cloud storage";
    const isDrive = type === "drive";
    const isGphotos = type === "gphotos";
    const isS3 = type === "s3";
    const isWebdav = type === "webdav" || type === "nextcloud";
    const isExternal = type === "__external__";
    setVisible([rows.name, rows.mount], !isExternal);
    setVisible([rows.credentials, rows.driveClientHelp], isDrive);
    setVisible([rows.account], isDrive || isGphotos);
    setVisible([rows.gphotosReadOnly, rows.gphotosHelp], isGphotos);
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
  const cancel = node("button", "", "Cancel");
  const create = node("button", "primary", "Create remote");
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
      const fields: Record<string, string> = {};
      let suffix = PROVIDERS.find(item => item[1] === type)?.[2] ?? type;
      const backend = type === "nextcloud" ? "webdav" : type;
      if (type === "drive" || type === "gphotos") {
        if (clientId.value.trim()) fields.client_id = clientId.value.trim();
        if (clientSecret.value.trim()) fields.client_secret = clientSecret.value.trim();
        if (type === "drive" && credentialSource.value !== "builtin" && credentialSource.value !== "custom") {
          fields.reuse_client_from = credentialSource.value;
        }
        if (type === "drive") {
          fields.scope = "drive";
          fields.config_team_drive = sharedTeam.checked ? "true" : "false";
          if (sharedTeam.checked && sharedDriveId.value.trim()) fields.team_drive = sharedDriveId.value.trim();
        }
        if (type === "gphotos") {
          fields.read_only = gphotosReadOnly.checked ? "true" : "false";
          fields.read_size = "true";
          fields.config_edit_advanced = "false";
        }
      }
      if (OAUTH_TYPES.has(type)) fields.config_is_local = localAuth.checked ? "true" : "false";
      let remotePath = "";
      if (type === "s3") {
        const option = S3_OPTIONS.find(item => item.provider === s3Provider.value) ?? S3_OPTIONS[0];
        suffix = option.suffix;
        fields.provider = option.provider;
        fields.access_key_id = s3Access.value.trim();
        fields.secret_access_key = s3Secret.value;
        if (s3Region.value.trim()) fields.region = s3Region.value.trim();
        if (s3Endpoint.value.trim()) fields.endpoint = s3Endpoint.value.trim();
        Object.assign(fields, option.extra);
        remotePath = s3Path.value.trim().replace(/^\/+|\/+$/g, "");
      }
      if (type === "webdav" || type === "nextcloud") {
        const option = WEBDAV_VENDORS.find(item => item.vendor === webdavVendor.value) ?? WEBDAV_VENDORS[0];
        const vendor = type === "nextcloud" ? "nextcloud" : option.vendor;
        suffix = type === "nextcloud" ? "Nextcloud" : option.suffix;
        const url = type === "nextcloud"
          ? nextcloudWebdavUrl(webdavUrl.value, webdavUser.value)
          : webdavUrl.value.trim();
        fields.url = url;
        fields.vendor = vendor;
        if (webdavUser.value.trim()) fields.user = webdavUser.value.trim();
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
        fields.enable_caching = "false";
        if (proton2fa.value.trim()) fields["2fa"] = proton2fa.value.trim();
        if (protonMailbox.value) fields.mailbox_password = protonMailbox.value;
      }
      if (type === "koofr") {
        fields.provider = "koofr";
        fields.user = koofrUser.value.trim();
        fields.password = koofrPass.value;
      }
      const remoteId = await createRemote({
        alias,
        provider: backend,
        providerLabel: suffix,
        fields,
        googleAccount: googleAccountValue(account.value),
        mountAfter: mountAfter.checked,
        remotePath,
      });
      close({ remoteId, provider: backend, mountAfter: mountAfter.checked });
    } catch (error) {
      await showError("Add remote", error);
      create.disabled = false;
    }
  });
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
        gphotos: [rows.clientId, rows.clientSecret, rows.auth], dropbox: [rows.auth], onedrive: [rows.auth], box: [rows.auth], pcloud: [rows.auth],
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
          ? "Choose your own client for reliable long-term access. Use Setup guide below."
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
