import type { AppSettings } from "./model.ts";
import { trapModalFocus } from "./dialogs.ts";

const defaults: Record<string, string[]> = {
  common_previous: [], common_next: [], common_search: ["F"], remote_enter_browser: ["Space"],
  remote_move_up: ["Shift+Up"], remote_move_down: ["Shift+Down"], remote_toggle_mount: [],
  remote_config: [], remote_open_browser: [], browser_open: [], browser_parent: ["Backspace"],
  browser_root: ["Alt+Home"], browser_refresh: ["F5"], browser_zoom_in: ["Ctrl++"],
  browser_zoom_out: ["Ctrl+-"], browser_open_folder: ["Ctrl+Return"], browser_copy: [],
  browser_cut: [], browser_paste: [], browser_delete: [], browser_new_folder: [],
};

const groups: Array<[string, Array<[string, string]>]> = [
  ["Common alternatives", [["common_previous", "Previous item"], ["common_next", "Next item"], ["common_search", "Search"]]],
  ["Remote list", [["remote_enter_browser", "Enter file browser"], ["remote_move_up", "Move remote up"], ["remote_move_down", "Move remote down"], ["remote_toggle_mount", "Mount or unmount remote"], ["remote_config", "Open remote settings"], ["remote_open_browser", "Open provider website"]]],
  ["File browser", [["browser_open", "Open selected item"], ["browser_parent", "Parent folder"], ["browser_root", "Remote root"], ["browser_refresh", "Refresh folder"], ["browser_zoom_in", "Application zoom in"], ["browser_zoom_out", "Application zoom out"], ["browser_open_folder", "Open folder in file manager"], ["browser_copy", "Copy selected items"], ["browser_cut", "Cut selected items"], ["browser_paste", "Paste into current folder"], ["browser_delete", "Delete selected items"], ["browser_new_folder", "Create new folder"]]],
];

const remoteContext = [...groups[0][1], ...groups[1][1]];
const browserContext = [...groups[0][1], ...groups[2][1]];

function make<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}

function portableKey(event: KeyboardEvent): string {
  const modifiers: string[] = [];
  if (event.ctrlKey) modifiers.push("Ctrl");
  if (event.altKey) modifiers.push("Alt");
  if (event.shiftKey) modifiers.push("Shift");
  if (event.metaKey) modifiers.push("Meta");
  if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) return "";
  const key = ({ " ": "Space", Enter: "Return", Escape: "Esc", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right" } as Record<string, string>)[event.key]
    ?? (event.key.length === 1 ? event.key.toLocaleUpperCase() : event.key);
  return [...modifiers, key].join("+");
}

function normalized(value: string): string { return value.replaceAll(" ", "").replaceAll("Enter", "Return").toLocaleLowerCase(); }

export function openShortcutDialog(settings: AppSettings, saveSettings: (settings: AppSettings) => Promise<void>): void {
  document.querySelector(".modal-layer")?.remove();
  const layer = make("div", "modal-layer");
  const dialog = make("section", "modal-dialog shortcuts-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  dialog.append(make("h2", "", "Keyboard shortcuts"));
  const scroll = make("div", "shortcuts-scroll");
  const fixed = make("fieldset", "shortcut-group"); fixed.append(make("legend", "", "Fixed inputs"));
  const fixedRows = [
    ["Up / Down", "Move selection in the focused list"], ["Left / Right", "Move between the remote list and file browser when the key points toward the other pane"],
    ["Enter", "Select the focused remote or open the focused file item"], ["Esc", "Return from the file browser to the remote list"],
    ["Ctrl++ / Ctrl+-", "Zoom the complete Mountlet interface"], ["Ctrl+0", "Reset application zoom to the system-derived size"],
    ["Ctrl+C", "Copy selected file-browser items"], ["Ctrl+X", "Cut selected file-browser items"], ["Ctrl+V", "Paste into the current file-browser folder"], ["Delete", "Delete selected file-browser items"],
  ];
  for (const [keys, description] of fixedRows) { const row = make("div", "fixed-shortcut-row"); row.append(make("strong", "", keys), make("span", "", description)); fixed.append(row); }
  scroll.append(fixed);
  const fields = new Map<string, HTMLInputElement[]>();
  const alternatives = make("fieldset", "shortcut-group"); alternatives.append(make("legend", "", "Alternative inputs"));
  for (const [title, definitions] of groups) {
    const group = make("fieldset", "shortcut-subgroup"); group.append(make("legend", "", title));
    for (const [key, label] of definitions) {
      const row = make("label", "shortcut-row"); row.append(make("span", "", label));
      const values = [...(settings.shortcuts[key] ?? defaults[key] ?? [])]; while (values.length < 3) values.push("");
      const inputs = values.slice(0, 3).map((value, index) => {
        const input = make("input", "shortcut-input") as HTMLInputElement; input.readOnly = true; input.value = value; input.title = `Alternative shortcut ${index + 1}`;
        input.addEventListener("keydown", event => { event.preventDefault(); event.stopPropagation(); if (event.key === "Backspace" || event.key === "Delete") input.value = ""; else { const value = portableKey(event); if (value) input.value = value; } update(); });
        return input;
      });
      fields.set(key, inputs); row.append(...inputs); group.append(row);
    }
    alternatives.append(group);
  }
  scroll.append(alternatives); dialog.append(scroll);
  const conflict = make("div", "shortcut-conflict"); dialog.append(conflict);
  const actions = make("div", "dialog-actions");
  const restore = make("button", "", "Restore defaults"); const cancel = make("button", "", "Cancel"); const save = make("button", "primary", "Save");
  actions.append(restore, cancel, save); dialog.append(actions); layer.append(dialog); document.body.append(layer);
  const current = () => Object.fromEntries([...fields].map(([key, inputs]) => [key, inputs.map(input => input.value.trim()).filter(Boolean).slice(0, 3)]));
  const baseline = JSON.stringify(current());
  const conflicts = () => {
    const messages = new Set<string>(); const conflictValues = new Set<string>();
    for (const [title, definitions] of [["Remote list", remoteContext], ["File browser", browserContext]] as Array<[string, Array<[string, string]>]>) {
      const seen = new Map<string, string>();
      for (const [key, label] of definitions) for (const value of current()[key] ?? []) { const id = normalized(value); if (seen.has(id)) { messages.add(`${title}: ${value} is assigned to ${seen.get(id)} and ${label}.`); conflictValues.add(id); } else seen.set(id, label); }
    }
    return { messages: [...messages], values: conflictValues };
  };
  function update() {
    const result = conflicts();
    conflict.textContent = result.messages.slice(0, 2).join("\n");
    save.disabled = result.messages.length > 0 || JSON.stringify(current()) === baseline;
    for (const inputs of fields.values()) for (const input of inputs) input.classList.toggle("conflict", result.values.has(normalized(input.value)));
  }
  restore.addEventListener("click", () => { for (const [key, inputs] of fields) { const values = defaults[key] ?? []; inputs.forEach((input, index) => { input.value = values[index] ?? ""; }); } update(); });
  cancel.addEventListener("click", () => layer.remove()); layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  save.addEventListener("click", async () => { update(); if (save.disabled) return; save.disabled = true; await saveSettings({ ...settings, shortcuts: current() }); layer.remove(); });
  update();
  trapModalFocus(layer, dialog, cancel);
}
