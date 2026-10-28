import { openAddRemoteDialog } from "./add_remote_dialog.ts";
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
}
