import { openAddRemoteDialog } from "./add_remote_dialog.ts";
import { openGoogleAccountDialog } from "./google_account_dialog.ts";
import { bindScaledSelect } from "./dialogs.ts";
import type { RemoteConfigData, SaveRemoteConfigRequest } from "./backend.ts";

/** Shared form regression probe. Callbacks never mutate actual remote configs. */
export async function checkRemoteSettingsForm(): Promise<void> {
  const fixtures: Record<string, Record<string, string>> = {
    drive: { client_id: "fixture-id", client_secret: "••••••", scope: "drive.readonly", team_drive: "", shared_with_me: "", mountlet_google_account: "fixture@gmail.com", config_is_local: "true" },
    gphotos: { client_id: "", client_secret: "", read_only: "false", read_size: "", mountlet_google_account: "", config_is_local: "true" },
    dropbox: { client_id: "fixture-id", client_secret: "••••••", config_is_local: "false" },
    onedrive: { client_id: "", client_secret: "", region: "global", drive_type: "personal", drive_id: "fixture", config_is_local: "true" },
    box: { client_id: "", client_secret: "", config_is_local: "true" },
    pcloud: { client_id: "", client_secret: "", config_is_local: "true" },
    s3: { provider: "Other", endpoint: "https://fixture.invalid", region: "", access_key_id: "fixture-id", secret_access_key: "••••••", env_auth: "", acl: "private" },
    webdav: { vendor: "nextcloud", url: "https://fixture.invalid/remote.php/dav/files/fixture", user: "fixture", pass: "••••••" },
    iclouddrive: { service: "photos", apple_id: "fixture@example.com", password: "••••••", trust_token: "fixture" },
    mega: { user: "fixture@example.com", pass: "••••••" },
    protondrive: { username: "fixture@example.com", password: "••••••", mailbox_password: "••••••", enable_caching: "" },
    koofr: { provider: "koofr", user: "fixture@example.com", password: "••••••", mountid: "fixture" },
  };
  const ensure = (condition: boolean, message: string) => { if (!condition) throw new Error(message); };
  for (const [provider, fields] of Object.entries(fixtures)) {
    const config: RemoteConfigData = {
      id: `fixture-${provider}`, alias: "Fixture", provider, providerLabel: provider,
      mountPath: "fixture", remotePath: "root", autoMount: null,
      mountFlags: "--read-only --allow-other --vfs-cache-mode writes", fields,
      oauthClient: "client_id" in fields ? (fields.client_id ? "custom" : "rclone") : null,
      secretFields: Object.entries(fields).filter(([, value]) => value === "••••••").map(([key]) => key),
    };
    let captured: SaveRemoteConfigRequest | undefined;
    const closed = openAddRemoteDialog({ edit: { config, save: async request => { captured = request; return config.id; }, remove: async () => false } });
    let dialog: HTMLElement | null = null;
    for (let attempt = 0; attempt < 200 && !dialog; attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 10));
      dialog = document.querySelector<HTMLElement>(".remote-config-dialog");
    }
    ensure(Boolean(dialog), `${provider}: settings did not open`);
    const form = dialog!.querySelector(".add-remote-form");
    ensure(Boolean(form), `${provider}: settings bypassed the shared form`);
    const actions = [...dialog!.querySelectorAll<HTMLButtonElement>(".dialog-actions button")];
    ensure(actions.length === 3 && actions.every(button => !button.textContent?.trim() && button.querySelector("img") !== null), `${provider}: actions must be Delete/Cancel/Save icons`);
    const save = dialog!.querySelector<HTMLButtonElement>('button[aria-label="Save"]')!;
    ensure(save.disabled, `${provider}: unchanged settings enabled Save`);
    const label = [...form!.querySelectorAll<HTMLLabelElement>("label")].find(row => row.querySelector(".settings-label")?.textContent === "Remote name");
    const name = label!.querySelector<HTMLInputElement>("input")!;
    name.value = "Renamed"; name.dispatchEvent(new Event("input", { bubbles: true }));
    ensure(!save.disabled, `${provider}: changed settings did not enable Save`);
    save.click();
    await closed;
    ensure(captured?.alias === "Renamed", `${provider}: name edit was not saved`);
    ensure(Object.keys(captured!.fields).length === Object.keys(fields).length
      && Object.entries(fields).every(([key, value]) => captured!.fields[key] === value), `${provider}: unrelated edit changed saved provider fields`);
    ensure(captured!.mountFlags.split(/\s+/).sort().join(" ") === config.mountFlags.split(/\s+/).sort().join(" "), `${provider}: saved mount flags changed`);
    ensure(captured!.remotePath === config.remotePath && captured!.mountPath === config.mountPath && captured!.autoMount === null, `${provider}: saved mount options changed`);
  }
  const configs: RemoteConfigData[] = ["drive", "gphotos"].map(provider => ({
    id: `fixture-${provider}`, alias: "Google", provider, providerLabel: provider,
    mountPath: provider, remotePath: `${provider}-folder`, mountFlags: "--read-only", autoMount: null,
    fields: { client_id: `${provider}-id`, client_secret: "••••••", config_is_local: "false", mountlet_google_account: "fixture@gmail.com", ...(provider === "drive" ? { scope: "drive.readonly" } : { read_only: "false" }) },
    oauthClient: "custom", secretFields: ["client_secret"],
  }));
  let captured: SaveRemoteConfigRequest[] = [];
  const closed = openGoogleAccountDialog(configs, { save: async requests => { captured = requests; return configs[0].id; }, remove: async () => false });
  let dialog: HTMLElement | null = null;
  for (let attempt = 0; attempt < 200 && !dialog; attempt += 1) { await new Promise(resolve => window.setTimeout(resolve, 10)); dialog = document.querySelector<HTMLElement>(".google-account-dialog"); }
  ensure(Boolean(dialog), "Google account form did not open");
  ensure(dialog!.querySelector(".google-service-toggle") === null, "Google settings must not use service pages");
  ensure(!dialog!.querySelector<HTMLDetailsElement>("details")!.open, "Service overrides must be advanced");
  ensure(dialog!.querySelectorAll("[data-google-service-client]").length === 2, "Both service overrides must be available");
  ensure(dialog!.querySelectorAll("[data-google-auto-mount]").length === 1, "Auto-mount must be shared by the Google card");
  ensure(dialog!.querySelectorAll("fieldset legend .provider-icon").length === 4, "Service option groups must use provider icons");
  ensure(dialog!.querySelectorAll("fieldset:not(details fieldset)").length === 2, "Normal service settings must remain visible outside Advanced");
  ensure([...dialog!.querySelector<HTMLSelectElement>("[data-google-shared-client]")!.options].map(option => option.value).join(",") === "mountlet,rclone,custom", "Primary client choices must be categories only");
  const alias = dialog!.querySelector<HTMLInputElement>("input")!;
  const auto = dialog!.querySelector<HTMLSelectElement>("[data-google-auto-mount]")!;
  auto.value = "true"; auto.dispatchEvent(new Event("change", { bubbles: true }));
  alias.value = "Renamed Google"; alias.dispatchEvent(new Event("input", { bubbles: true }));
  dialog!.querySelector<HTMLButtonElement>('button[aria-label="Save"]')!.click(); await closed;
  ensure(captured.length === 2 && captured.every(request => request.alias === "Renamed Google"), "Shared alias did not reach both services");
  ensure(captured.every(request => request.autoMount === true), "Shared auto-mount must persist to both services");
  for (const request of captured) {
    const original = configs.find(config => config.id === request.remoteId)!;
    ensure(Object.entries(original.fields).every(([key, value]) => request.fields[key] === value), "Google rename changed authentication or service fields");
    ensure(request.remotePath === original.remotePath && request.mountPath === original.mountPath, "Google rename changed independent paths");
  }
  const second = openGoogleAccountDialog(configs, { save: async requests => { captured = requests; return configs[0].id; }, remove: async () => false });
  dialog = null;
  for (let attempt = 0; attempt < 200 && !dialog; attempt += 1) { await new Promise(resolve => window.setTimeout(resolve, 10)); dialog = document.querySelector<HTMLElement>(".google-account-dialog"); }
  const shared = dialog!.querySelector<HTMLSelectElement>("[data-google-shared-client]")!;
  shared.value = "custom"; shared.dispatchEvent(new Event("change", { bubbles: true }));
  const reuse = dialog!.querySelector<HTMLSelectElement>("[data-google-custom-source]")!;
  reuse.add(new Option("Other Google account", "reuse:fixture-other"));
  reuse.value = "reuse:fixture-other"; reuse.dispatchEvent(new Event("change", { bubbles: true }));
  ensure(!dialog!.querySelector<HTMLDetailsElement>("details")!.open, "Selecting a shared client exposed advanced overrides");
  const photos = dialog!.querySelector<HTMLSelectElement>('[data-google-service-client="gphotos"]')!;
  photos.value = "custom"; photos.dispatchEvent(new Event("change", { bubbles: true }));
  dialog!.querySelector<HTMLButtonElement>('button[aria-label="Save"]')!.click(); await second;
  ensure(captured.find(request => request.remoteId === "fixture-drive")!.oauthClient === "reuse:fixture-other", "Drive did not inherit the shared Google account");
  ensure(captured.find(request => request.remoteId === "fixture-gphotos")!.oauthClient === "custom", "Photos override did not remain independent");
  ensure(captured.every(request => request.googleSharedClient === "reuse:fixture-other"), "The shared category was not persisted separately from overrides");
  const reloaded = configs.map(config => { const request = captured.find(request => request.remoteId === config.id)!; return { ...config, oauthClient: request.oauthClient!, googleSharedClient: request.googleSharedClient, fields: request.fields }; });
  const third = openGoogleAccountDialog(reloaded, { save: async () => null, remove: async () => false });
  dialog = null;
  for (let attempt = 0; attempt < 200 && !dialog; attempt += 1) { await new Promise(resolve => window.setTimeout(resolve, 10)); dialog = document.querySelector<HTMLElement>(".google-account-dialog"); }
  ensure(dialog!.querySelector<HTMLSelectElement>("[data-google-shared-client]")!.value === "custom", "Reload lost the shared category");
  ensure(dialog!.querySelector<HTMLSelectElement>("[data-google-custom-source]")!.value === "reuse:fixture-other", "Reload lost the custom registration source");
  ensure(dialog!.querySelector<HTMLSelectElement>('[data-google-service-client="gphotos"]')!.value === "custom", "Reload lost the Photos override");
  dialog!.querySelector<HTMLButtonElement>('button[aria-label="Cancel"]')!.click(); await third;
  const defaults = configs.map(config => ({ ...config, oauthClient: "rclone", fields: { ...config.fields, client_id: "", client_secret: "" } }));
  const fourth = openGoogleAccountDialog(defaults, { save: async () => null, remove: async () => false });
  dialog = null;
  for (let attempt = 0; attempt < 200 && !dialog; attempt += 1) { await new Promise(resolve => window.setTimeout(resolve, 10)); dialog = document.querySelector<HTMLElement>(".google-account-dialog"); }
  const category = dialog!.querySelector<HTMLSelectElement>("[data-google-shared-client]")!;
  category.value = "custom"; category.dispatchEvent(new Event("change", { bubbles: true }));
  const guidance = dialog!.querySelector<HTMLButtonElement>("button.add-remote-help")!;
  ensure(!guidance.hidden && guidance.textContent!.includes("Drive and Photos"), "Missing custom registrations must point to Advanced options");
  ensure(!dialog!.querySelector<HTMLDetailsElement>("details")!.open, "Custom must not automatically expand Advanced");
  guidance.click();
  ensure(dialog!.querySelector<HTMLDetailsElement>("details")!.open, "Missing-client link must reveal Advanced");
  dialog!.querySelector<HTMLButtonElement>('button[aria-label="Cancel"]')!.click(); await fourth;
  const dropdown = bindScaledSelect(document.createElement("select"));
  dropdown.style.cssText = "position:fixed;bottom:8px;right:8px;width:140px";
  for (let index = 0; index < 40; index += 1) dropdown.add(new Option(`Option ${index}`, String(index)));
  document.body.append(dropdown);
  try {
    dropdown.dispatchEvent(new MouseEvent("mousedown", { button: 0, bubbles: true }));
    const menu = document.querySelector<HTMLElement>(".select-menu")!;
    const bounds = menu.getBoundingClientRect(), anchor = dropdown.getBoundingClientRect();
    ensure(bounds.top >= 0 && bounds.bottom <= window.innerHeight, "Dropdown must fit vertically in the viewport");
    ensure(bounds.left >= 0 && bounds.right <= window.innerWidth, "Dropdown must fit horizontally in the viewport");
    ensure(bounds.bottom <= anchor.top + 1, "Dropdown near the bottom must open upwards");
    ensure(menu.scrollHeight > menu.clientHeight, "Long dropdown must scroll rather than hide options");
    menu.querySelectorAll<HTMLButtonElement>("button")[39].click();
    ensure(dropdown.value === "39", "Last dropdown option must remain selectable");
  } finally { dropdown.remove(); document.querySelector(".select-menu")?.remove(); }
}
