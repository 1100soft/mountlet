import type { AppSettings } from "./model.ts";
import { trapModalFocus } from "./dialogs.ts";
import { normalizeShortcut, portableShortcut, resolveShortcutSettings, SHORTCUT_DEFAULTS, SHORTCUTS } from "./shortcuts.ts";

function make<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}

export function openShortcutDialog(settings: AppSettings, saveSettings: (settings: AppSettings) => Promise<void>): void {
  document.querySelector(".modal-layer")?.remove();
  const layer = make("div", "modal-layer");
  const dialog = make("section", "modal-dialog shortcuts-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  dialog.append(make("h2", "", "Keyboard shortcuts"));
  const scroll = make("div", "shortcuts-scroll");
  const fixed = make("fieldset", "shortcut-group"); fixed.append(make("legend", "", "Fixed inputs"));
  const fixedRows = SHORTCUTS.filter(item => item.fixed?.length).map(item => [item.fixed!.join(" / "), item.label]);
  for (const [keys, description] of fixedRows) { const row = make("div", "fixed-shortcut-row"); row.append(make("strong", "", keys), make("span", "", description)); fixed.append(row); }
  scroll.append(fixed);
  const fields = new Map<string, HTMLInputElement[]>();
  const alternatives = make("fieldset", "shortcut-group"); alternatives.append(make("legend", "", "Alternative inputs"));
  const groups = [...new Set(SHORTCUTS.filter(item => item.customizable).map(item => item.group))];
  const configured = resolveShortcutSettings(settings.shortcuts);
  for (const title of groups) {
    const definitions = SHORTCUTS.filter(item => item.customizable && item.group === title);
    const group = make("fieldset", "shortcut-subgroup"); group.append(make("legend", "", title));
    for (const definition of definitions) {
      const { key, label } = definition;
      const row = make("label", "shortcut-row"); row.append(make("span", "", label));
      const values = [...(configured[key] ?? [])]; while (values.length < 3) values.push("");
      const inputs = values.slice(0, 3).map((value, index) => {
        const input = make("input", "shortcut-input") as HTMLInputElement; input.readOnly = true; input.value = value; input.title = `Alternative shortcut ${index + 1}`;
        input.addEventListener("keydown", event => { event.preventDefault(); event.stopPropagation(); if (event.key === "Backspace" || event.key === "Delete") input.value = ""; else { const value = portableShortcut(event); if (value) input.value = value; } update(); });
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
    for (const [title, scope] of [["Remote list", "remote"], ["File browser", "browser"]] as const) {
      const definitions = SHORTCUTS.filter(item => item.customizable && (item.scope === "common" || item.scope === scope));
      const seen = new Map<string, string>();
      for (const { key, label } of definitions) for (const value of current()[key] ?? []) { const id = normalizeShortcut(value); if (seen.has(id)) { messages.add(`${title}: ${value} is assigned to ${seen.get(id)} and ${label}.`); conflictValues.add(id); } else seen.set(id, label); }
    }
    return { messages: [...messages], values: conflictValues };
  };
  function update() {
    const result = conflicts();
    conflict.textContent = result.messages.slice(0, 2).join("\n");
    save.disabled = result.messages.length > 0 || JSON.stringify(current()) === baseline;
    for (const inputs of fields.values()) for (const input of inputs) input.classList.toggle("conflict", result.values.has(normalizeShortcut(input.value)));
  }
  restore.addEventListener("click", () => { for (const [key, inputs] of fields) { const values = SHORTCUT_DEFAULTS[key] ?? []; inputs.forEach((input, index) => { input.value = values[index] ?? ""; }); } update(); });
  cancel.addEventListener("click", () => layer.remove()); layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  save.addEventListener("click", async () => { update(); if (save.disabled) return; save.disabled = true; await saveSettings({ ...settings, shortcuts: current() }); layer.remove(); });
  update();
  trapModalFocus(layer, dialog, cancel);
}
