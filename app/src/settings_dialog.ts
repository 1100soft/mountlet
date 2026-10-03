import type { AppSettings, Remote } from "./model.ts";
import type { FileManagerOption } from "./backend.ts";
import { trapModalFocus, bindScaledSelect } from "./dialogs.ts";

type SaveHandler = (settings: AppSettings) => Promise<void>;

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const value = document.createElement(tag);
  value.className = className;
  value.textContent = text;
  return value;
}

function option(value: string, label: string): HTMLOptionElement {
  const item = node("option", "", label);
  item.value = value;
  return item;
}

function fieldRow(label: string, control: HTMLElement): HTMLElement {
  const row = node("label", "settings-row");
  row.append(node("span", "settings-label", label), control);
  return row;
}

function textField(value: string, type = "text"): HTMLInputElement {
  const input = node("input", "settings-input") as HTMLInputElement;
  input.type = type;
  input.value = value;
  return input;
}

function checkField(label: string, checked: boolean, tooltip: string): HTMLLabelElement {
  const wrapper = node("label", "settings-check") as HTMLLabelElement;
  const input = node("input") as HTMLInputElement;
  input.type = "checkbox";
  input.checked = checked;
  wrapper.title = tooltip;
  wrapper.append(input, node("span", "", label));
  return wrapper;
}

function selectField(value: string, values: Array<[string, string]>): HTMLSelectElement {
  const select = bindScaledSelect(node("select", "settings-input") as HTMLSelectElement);
  for (const [candidate, label] of values) select.append(option(candidate, label));
  select.value = value;
  return select;
}

export function openAppSettingsDialog(
  initial: AppSettings,
  remotes: readonly Remote[],
  onSave: SaveHandler,
  extras: { fileManagers?: readonly FileManagerOption[]; wayland?: boolean; initialPage?: string } = {},
): void {
  document.querySelector(".modal-layer")?.remove();
  const layer = node("div", "modal-layer");
  const dialog = node("section", "modal-dialog app-settings-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "app-settings-title");
  const title = node("h2", "", "App settings");
  title.id = "app-settings-title";

  const tabs = node("div", "settings-tabs");
  const pages = node("div", "settings-pages");
  const controls: Record<string, HTMLInputElement | HTMLSelectElement> = {};
  const addPage = (name: string) => {
    const button = node("button", "settings-tab", name);
    button.type = "button";
    const page = node("div", "settings-page");
    page.dataset.page = name;
    const activate = () => {
      tabs.querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
      pages.querySelectorAll<HTMLElement>(".settings-page").forEach(item => {
        const selected = item === page;
        item.hidden = !selected;
        item.inert = !selected;
      });
    };
    button.addEventListener("click", activate);
    tabs.append(button);
    pages.append(page);
    if (tabs.children.length === 1) activate(); else { page.hidden = true; page.inert = true; }
    return page;
  };
  const bind = <T extends HTMLInputElement | HTMLSelectElement>(key: string, control: T): T => { controls[key] = control; return control; };

  const general = addPage("General");
  const startAtLogin = checkField("Start Mountlet when I log in", initial.startAtLogin, "Start Mountlet automatically after signing in.");
  bind("startAtLogin", startAtLogin.querySelector("input")!);
  general.append(startAtLogin);
  general.append(fieldRow("App folder", bind("mountBase", textField(initial.mountBase))));
  const mode = node("div", "window-mode-choices");
  for (const [value, label, description] of [
    ["single", "Single window", "Dock files beside the remote list."],
    ["multiple", "Multiple windows", "Open Mountlet Files as a separate window."],
  ]) {
    const choice = node("label", "window-mode-choice");
    const radio = node("input") as HTMLInputElement;
    radio.type = "radio"; radio.name = "window-mode"; radio.value = value; radio.checked = initial.windowMode === value;
    choice.title = description;
    choice.append(radio, node("strong", "", label), node("small", "", description));
    mode.append(choice);
  }
  general.append(fieldRow("Window mode", mode));
  if (extras.wayland) {
    for (const radio of mode.querySelectorAll<HTMLInputElement>('input[name="window-mode"]')) {
      if (radio.value === "multiple") {
        radio.disabled = true;
        radio.checked = false;
      } else {
        radio.checked = true;
      }
    }
    general.append(node("p", "settings-warning", "Wayland requires a single window."));
  }
  general.append(fieldRow("Theme", bind("theme", selectField(initial.theme, [["system", "System"], ["light", "Light"], ["dark", "Dark"]]))));

  const mounting = addPage("Mounting");
  const autoMount = checkField("Auto-mount by default", initial.autoMount, "Mount remotes automatically unless a remote overrides it.");
  bind("autoMount", autoMount.querySelector("input")!); mounting.append(autoMount);
  mounting.append(fieldRow("Auto-mount delay", bind("autoMountDelay", textField(String(initial.autoMountDelay), "number"))));
  const managers = extras.fileManagers ?? [];
  const managerChoices: Array<[string, string]> = managers.length
    ? managers.map(manager => [manager.identifier, manager.label])
    : [["", "System default"]];
  if (initial.fileManager && !managerChoices.some(([value]) => value === initial.fileManager)) {
    managerChoices.push([initial.fileManager, initial.fileManager]);
  }
  mounting.append(fieldRow("File manager", bind("fileManager", selectField(initial.fileManager || (managers.find(manager => manager.isSystemDefault)?.identifier ?? ""), managerChoices))));
  mounting.append(fieldRow("Open folders", bind("openFolderBehavior", selectField(initial.openFolderBehavior, [
    ["current_desktop", "Current desktop"], ["existing_window", "Any existing file manager window"],
    ["new_window", "New window"], ["file-manager-service", "File manager service"], ["default", "System default"],
  ]))));
  const focusManager = checkField("Focus file manager", initial.focusFileManager, "Bring the file manager forward after opening a mount folder.");
  bind("focusFileManager", focusManager.querySelector("input")!); mounting.append(focusManager);

  const files = addPage("Files");
  const edits = checkField("Allow edits in Mountlet Files", initial.integratedFileEdits, "Allow direct copy, move, delete, drag-and-drop, and folder creation in Mountlet Files.");
  bind("integratedFileEdits", edits.querySelector("input")!); files.append(edits);
  const maximum = bind("fileListMaxItems", textField(initial.fileListMaxItems > 0 ? String(initial.fileListMaxItems) : "", "number"));
  maximum.placeholder = "No limit";
  maximum.title = "Maximum file items visible at once. Leave blank or use 0 to fill the available height.";
  files.append(fieldRow("Maximum visible items", maximum));
  const checkInterval = bind("remoteCheckInterval", textField(String(initial.remoteCheckInterval), "number"));
  checkInterval.title = "Seconds between background checks for cloud-side changes in cached and offline files. Use 0 for manual sync only.";
  files.append(fieldRow("Cloud check interval", checkInterval), node("p", "settings-warning", "Mountlet file edits are direct, permanent, and not undoable."));

  const sync = addPage("Config Sync");
  const syncRemote = bind("configSyncRemote", selectField(initial.configSyncRemote, [["", "Not set"], ...remotes.map(remote => [remote.id, `${remote.name} (${remote.providerLabel})`] as [string, string]) ]));
  sync.append(fieldRow("Remote", syncRemote), fieldRow("Path", bind("configSyncPath", textField(initial.configSyncPath))));

  const notices = addPage("Notifications");
  const displays: Array<[string, string]> = [["dialog", "Dialog"], ["tray", "Tray notification"], ["off", "Off"]];
  notices.append(
    fieldRow("Info", bind("noticeInfoDisplay", selectField(initial.noticeInfoDisplay, [["tray", "Tray notification"], ["dialog", "Dialog"], ["off", "Off"]]))),
    fieldRow("Important", bind("noticeImportantDisplay", selectField(initial.noticeImportantDisplay, displays))),
    fieldRow("Check interval", bind("noticeCheckInterval", textField(String(initial.noticeCheckInterval), "number"))),
    node("p", "settings-warning", "Critical notices are always shown."),
  );

  const actions = node("div", "dialog-actions");
  const cancel = node("button", "", "Cancel"); cancel.type = "button";
  const save = node("button", "primary", "Save"); save.type = "button"; save.disabled = true;
  actions.append(cancel, save);
  cancel.dataset.dialogCancel = "true";
  save.dataset.dialogConfirm = "true";
  dialog.append(title, tabs, pages, actions); layer.append(dialog); document.body.append(layer);

  const read = (): AppSettings => ({
    ...initial,
    mountBase: controls.mountBase.value.trim(), autoMount: (controls.autoMount as HTMLInputElement).checked,
    autoMountDelay: Math.max(0, Number(controls.autoMountDelay.value) || 0),
    startAtLogin: (controls.startAtLogin as HTMLInputElement).checked,
    integratedFileEdits: (controls.integratedFileEdits as HTMLInputElement).checked,
    fileManager: controls.fileManager.value.trim(), openFolderBehavior: controls.openFolderBehavior.value,
    focusFileManager: (controls.focusFileManager as HTMLInputElement).checked,
    windowMode: (dialog.querySelector<HTMLInputElement>('input[name="window-mode"]:checked')?.value || "multiple") as AppSettings["windowMode"],
    theme: controls.theme.value as AppSettings["theme"], zoomSteps: initial.zoomSteps,
    fileListMaxItems: Math.max(0, Number.parseInt(controls.fileListMaxItems.value || "0", 10) || 0),
    remoteCheckInterval: Math.max(0, Number(controls.remoteCheckInterval.value) || 0),
    noticeInfoDisplay: controls.noticeInfoDisplay.value as AppSettings["noticeInfoDisplay"],
    noticeImportantDisplay: controls.noticeImportantDisplay.value as AppSettings["noticeImportantDisplay"],
    noticeCheckInterval: Math.max(0, Number(controls.noticeCheckInterval.value) || 0),
    configSyncRemote: controls.configSyncRemote.value, configSyncPath: controls.configSyncPath.value.trim() || "Mountlet/config.mountlet",
  });
  const baseline = JSON.stringify(read());
  const updateDirty = () => { save.disabled = JSON.stringify(read()) === baseline; };
  dialog.addEventListener("input", updateDirty); dialog.addEventListener("change", updateDirty);
  dialog.addEventListener("click", updateDirty);
  const close = () => layer.remove();
  cancel.addEventListener("click", close);
  layer.addEventListener("mousedown", event => { if (event.target === layer) close(); });
  layer.addEventListener("keydown", event => { if (event.key === "Escape") close(); });
  save.addEventListener("click", async () => {
    if (save.disabled) return;
    save.disabled = true;
    await onSave(read());
    close();
  });
  trapModalFocus(layer, dialog, cancel);
  if (extras.initialPage) {
    [...tabs.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent === extras.initialPage)?.click();
  }
  updateDirty();
}
