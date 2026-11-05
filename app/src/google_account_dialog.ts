import { driveOauthSources, oauthClientAvailability, type RemoteConfigData, type SaveRemoteConfigRequest } from "./backend.ts";
import { actionIcon, providerIcon } from "./icons.ts";
import { bindScaledSelect, showError, trapModalFocus } from "./dialogs.ts";
import { googleAccountValue } from "./remote_auth.ts";

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = ""): HTMLElementTagNameMap[K] => {
  const element = document.createElement(tag); element.textContent = text; return element;
};
const input = (value: string, secret = false) => { const field = node("input"); field.className = "settings-input"; field.value = value; field.type = secret ? "password" : "text"; return field; };
const select = (choices: readonly (readonly [string, string])[], value: string) => {
  const field = bindScaledSelect(node("select")); field.className = "settings-input"; for (const [key, label] of choices) field.add(new Option(label, key)); field.value = value; return field;
};
const row = (label: string, field: HTMLElement) => { const element = node("label"); element.className = "settings-row"; const title = node("span", label); title.className = "settings-label"; element.append(title, field); return element; };

/** One account form. Service-specific credentials and paths stay independent. */
export async function openGoogleAccountDialog(configs: readonly RemoteConfigData[], handlers: {
  save: (requests: SaveRemoteConfigRequest[]) => Promise<string | null>;
  remove: () => Promise<boolean>;
}): Promise<string | null> {
  document.querySelector(".modal-layer")?.remove();
  const [availability, sources] = await Promise.all([oauthClientAvailability(), driveOauthSources()]);
  const primary = configs.find(config => config.provider === "drive") ?? configs[0];
  const choices: Array<readonly [string, string]> = [["mountlet", "Mountlet"], ["rclone", "rclone"], ["custom", "Custom"]];
  const initial = primary.googleSharedClient ?? (configs.every(config => config.oauthClient === primary.oauthClient) ? primary.oauthClient ?? "rclone" : "saved");
  const customSources = sources.filter(source => !configs.some(config => config.id === source.remoteId) && (source.driveCustom || source.photosCustom));
  const layer = node("div"); layer.className = "modal-layer";
  const dialog = node("section"); dialog.className = "modal-dialog remote-config-dialog google-account-dialog";
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  dialog.append(node("h2", `${primary.alias} settings`));
  const form = node("form"); form.className = "add-remote-form";
  const alias = input(primary.alias);
  const account = input(primary.fields.mountlet_google_account ?? "");
  const source = select(choices, initial === "saved" || initial.startsWith("reuse:") ? "custom" : initial); source.dataset.googleSharedClient = "true";
  const reuse = select([["custom", "This account"], ...customSources.map(item => [`reuse:${item.remoteId}`, item.label] as const)], initial.startsWith("reuse:") ? initial : "custom");
  reuse.dataset.googleCustomSource = "true";
  if (initial.startsWith("reuse:") && ![...reuse.options].some(option => option.value === initial)) { reuse.add(new Option("Saved custom clients", initial)); reuse.value = initial; }
  const reuseRow = row("Custom clients", reuse);
  const sharedChoice = () => source.value === "custom" ? reuse.value : source.value;
  let clientChanged = false;
  const mountlet = [...source.options].find(option => option.value === "mountlet")!;
  mountlet.disabled = !configs.every(config => availability[config.provider]);
  if (mountlet.disabled) mountlet.text = "Mountlet (unavailable)";
  const authInitial = configs.every(config => (config.fields.config_is_local ?? "true") === (primary.fields.config_is_local ?? "true")) ? primary.fields.config_is_local ?? "true" : "saved";
  const auth = select([["saved", "Saved settings"], ["true", "Browser"], ["false", "Paste token"]], authInitial);
  const auto = select([["", "App default"], ["true", "Enabled"], ["false", "Disabled"]], primary.autoMount === null ? "" : String(primary.autoMount));
  auto.dataset.googleAutoMount = "true";
  form.append(row("Remote name", alias), row("Google account", account), row("OAuth client", source), reuseRow, row("Sign in", auth), row("Auto-mount", auto));
  const notice = node("p", "Google Photos can download only media uploaded through the app's rclone client. Album writes stay inside the album folder."); notice.className = "add-remote-help"; form.append(notice);
  const advanced = node("details"); advanced.className = "remote-advanced"; advanced.append(node("summary", "Advanced options"));
  const guidance = node("button"); guidance.type = "button"; guidance.className = "add-remote-help";
  guidance.addEventListener("click", () => { advanced.open = true; advanced.scrollIntoView({ block: "nearest" }); });
  form.append(guidance);
  const controls = configs.map(config => {
    const serviceGroup = () => {
      const group = node("fieldset"), legend = node("legend");
      const label = config.provider === "drive" ? "Drive" : "Photos";
      legend.setAttribute("aria-label", label); legend.title = label;
      legend.append(providerIcon(config.provider === "drive" ? "drive" : "gphotos")); group.append(legend); return group;
    };
    const section = serviceGroup(), credentials = serviceGroup();
    const client = select([["", "Shared choice"], ...choices.filter(([value]) => value !== "saved")], "");
    if (config.oauthClient && ![...client.options].some(option => option.value === config.oauthClient)) client.add(new Option("Saved client", config.oauthClient));
    if (initial === "saved" || config.oauthClient !== initial) client.value = config.oauthClient ?? "rclone";
    client.dataset.googleServiceClient = config.provider;
    const embedded = [...client.options].find(option => option.value === "mountlet")!; embedded.disabled = !availability[config.provider];
    const hasCustom = config.oauthClient === "custom" || config.oauthClient?.startsWith("reuse:");
    const id = input(hasCustom ? config.fields.client_id ?? "" : ""); const secret = input(hasCustom ? config.fields.client_secret ?? "" : "", true);
    const idRow = row("Client ID", id); const secretRow = row("Client secret", secret);
    const path = input(config.mountPath), remotePath = input(config.remotePath), flags = input(config.mountFlags);
    credentials.append(row("OAuth client", client), idRow, secretRow);
    section.append(row("Local folder", path), row("Remote path", remotePath), row("Mount flags", flags));
    const fields = new Map<string, HTMLInputElement>();
    for (const [key, value] of Object.entries(config.fields)) {
      if (["client_id", "client_secret", "mountlet_google_account", "config_is_local"].includes(key)) continue;
      const field = input(value, config.secretFields.includes(key));
      if (["read_only", "read_size", "include_archived", "shared_with_me"].includes(key)) { field.type = "checkbox"; field.checked = ["true", "yes", "1", "on"].includes(value.toLowerCase()); }
      fields.set(key, field); section.append(row(key.replaceAll("_", " "), field));
    }
    const initialFields = new Map([...fields].map(([key, field]) => [key, field.type === "checkbox" ? String(field.checked) : field.value]));
    const effective = () => {
      if (client.value) return client.value;
      const shared = sharedChoice();
      const selected = customSources.find(item => `reuse:${item.remoteId}` === shared);
      if (selected && !(config.provider === "drive" ? selected.driveCustom : selected.photosCustom)) return "custom";
      return shared;
    };
    const update = () => { idRow.hidden = secretRow.hidden = effective() !== "custom"; };
    client.addEventListener("change", update);
    const changeShared = () => { clientChanged = true; client.value = ""; update(); };
    source.addEventListener("change", changeShared); reuse.addEventListener("change", changeShared); update();
    form.append(section); advanced.append(credentials);
    return { config, client, id, secret, path, remotePath, flags, auto, fields, initialFields, effective };
  });
  const updateCustom = () => {
    reuseRow.hidden = source.value !== "custom";
    const missing = controls.filter(control => control.effective() === "custom" && !control.id.value.trim()).map(control => control.config.provider === "drive" ? "Drive" : "Photos");
    guidance.hidden = source.value !== "custom" || missing.length === 0;
    guidance.textContent = `Configure ${missing.join(" and ")} custom client in Advanced options`;
  };
  source.addEventListener("change", updateCustom); reuse.addEventListener("change", updateCustom); form.addEventListener("input", updateCustom); form.addEventListener("change", updateCustom); updateCustom();
  form.append(advanced);
  const actions = node("div"); actions.className = "dialog-actions";
  const remove = actionIcon("", "Delete"), cancel = actionIcon("", "Cancel"), save = actionIcon("", "Save");
  remove.classList.add("danger"); save.classList.add("primary"); save.dataset.dialogConfirm = "true";
  actions.append(remove, cancel, save); dialog.append(form, actions); layer.append(dialog);
  let finish: (id: string | null) => void = () => undefined; const closed = new Promise<string | null>(resolve => { finish = resolve; });
  let busy = false;
  const close = (id: string | null) => { if (!busy) { layer.remove(); finish(id); } };
  const state = () => JSON.stringify([...form.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select")].map(field => field instanceof HTMLInputElement && field.type === "checkbox" ? field.checked : field.value));
  const baseline = state(); save.disabled = true;
  const dirty = () => { save.disabled = busy || state() === baseline; };
  form.addEventListener("input", dirty); form.addEventListener("change", dirty);
  cancel.addEventListener("click", () => close(null));
  layer.addEventListener("mousedown", event => { if (event.target === layer) close(null); });
  layer.addEventListener("keydown", event => { if (event.key === "Escape") close(null); });
  const submit = async () => {
    if (busy || save.disabled) return;
    busy = true; save.disabled = true; remove.disabled = true;
    try {
      const requests = controls.map(control => {
        const { config } = control; const fields = { ...config.fields };
        for (const [key, field] of control.fields) { const value = field.type === "checkbox" ? String(field.checked) : field.value; if (value !== control.initialFields.get(key)) fields[key] = value; }
        if (account.value !== (primary.fields.mountlet_google_account ?? "")) fields.mountlet_google_account = googleAccountValue(account.value);
        if (auth.value !== "saved" && auth.value !== authInitial) fields.config_is_local = auth.value;
        const oauthClient = control.effective();
        if (oauthClient === "custom") { fields.client_id = control.id.value.trim(); fields.client_secret = control.secret.value; if (!fields.client_id) throw Error(`Enter the ${config.provider === "drive" ? "Drive" : "Photos"} client ID in Advanced options.`); }
        return { remoteId: config.id, alias: alias.value === primary.alias ? config.alias : alias.value.trim(), mountPath: control.path.value, remotePath: control.remotePath.value, mountFlags: control.flags.value, autoMount: control.auto.value === "" ? null : control.auto.value === "true", oauthClient, googleSharedClient: !clientChanged && initial === "saved" ? undefined : sharedChoice(), fields } satisfies SaveRemoteConfigRequest;
      });
      const id = await handlers.save(requests); busy = false; if (id) close(id);
    } catch (error) { advanced.open = true; await showError("Google settings", error); }
    finally { busy = false; remove.disabled = false; dirty(); }
  };
  save.addEventListener("click", () => void submit()); form.addEventListener("submit", event => { event.preventDefault(); void submit(); });
  remove.addEventListener("click", async () => { if (busy) return; busy = true; try { const removed = await handlers.remove(); busy = false; if (removed) close(null); } catch (error) { await showError("Delete remote", error); } finally { busy = false; dirty(); } });
  document.body.append(layer); trapModalFocus(layer, dialog, cancel);
  return closed;
}
