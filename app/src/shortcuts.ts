export type ShortcutScope = "common" | "remote" | "browser";

export interface ShortcutDefinition {
  key: string;
  label: string;
  group: string;
  scope: ShortcutScope;
  fixed?: string[];
  defaults?: string[];
  customizable?: boolean;
}

export const SHORTCUTS: readonly ShortcutDefinition[] = [
  { key: "common_previous", label: "Previous item", group: "Common alternatives", scope: "common", customizable: true },
  { key: "common_next", label: "Next item", group: "Common alternatives", scope: "common", customizable: true },
  { key: "common_list_navigation", label: "Move selection in the focused list", group: "Common", scope: "common", fixed: ["Up", "Down"] },
  { key: "common_search", label: "Search", group: "Common alternatives", scope: "common", defaults: ["F"], customizable: true },
  { key: "common_context_menu", label: "Open context menu", group: "Common alternatives", scope: "common", defaults: ["Shift+F10", "Menu"], customizable: true },
  { key: "common_escape", label: "Return to the owning list or cancel a dialog", group: "Common", scope: "common", fixed: ["Esc"] },
  { key: "common_zoom_in", label: "Zoom the complete Mountlet interface in", group: "Common", scope: "common", fixed: ["Ctrl++"] },
  { key: "common_zoom_out", label: "Zoom the complete Mountlet interface out", group: "Common", scope: "common", fixed: ["Ctrl+-"] },
  { key: "common_zoom_reset", label: "Reset application zoom", group: "Common", scope: "common", fixed: ["Ctrl+0"] },
  { key: "common_cycle_theme", label: "Cycle application theme", group: "Common", scope: "common", fixed: ["Ctrl+T"] },
  { key: "remote_enter_browser", label: "Enter file browser", group: "Remote list", scope: "remote", fixed: ["Return"], defaults: ["Space"], customizable: true },
  { key: "remote_switch_pane", label: "Move to the file browser when the arrow points toward it", group: "Remote list", scope: "remote", fixed: ["Left", "Right"] },
  { key: "remote_move_up", label: "Move remote up", group: "Remote list", scope: "remote", defaults: ["Shift+Up"], customizable: true },
  { key: "remote_move_down", label: "Move remote down", group: "Remote list", scope: "remote", defaults: ["Shift+Down"], customizable: true },
  { key: "remote_toggle_mount", label: "Mount or unmount remote", group: "Remote list", scope: "remote", customizable: true },
  { key: "remote_config", label: "Open remote settings", group: "Remote list", scope: "remote", customizable: true },
  { key: "remote_open_browser", label: "Open provider website", group: "Remote list", scope: "remote", customizable: true },
  { key: "browser_open", label: "Open selected item", group: "File browser", scope: "browser", fixed: ["Return"], customizable: true },
  { key: "browser_switch_pane", label: "Move to the remote list when the arrow points toward it", group: "File browser", scope: "browser", fixed: ["Left", "Right"] },
  { key: "browser_first_last", label: "Select the first or last item", group: "File browser", scope: "browser", fixed: ["Home", "End"] },
  { key: "browser_page", label: "Move selection by one page", group: "File browser", scope: "browser", fixed: ["PageUp", "PageDown"] },
  { key: "browser_rename", label: "Rename selected item", group: "File browser", scope: "browser", fixed: ["F2"] },
  { key: "browser_select_all", label: "Select all items", group: "File browser", scope: "browser", fixed: ["Ctrl+A"] },
  { key: "browser_parent", label: "Parent folder", group: "File browser", scope: "browser", defaults: ["Backspace"], customizable: true },
  { key: "browser_root", label: "Remote root", group: "File browser", scope: "browser", defaults: ["Alt+Home"], customizable: true },
  { key: "browser_refresh", label: "Refresh folder", group: "File browser", scope: "browser", defaults: ["F5"], customizable: true },
  { key: "browser_zoom_in", label: "Application zoom in", group: "File browser", scope: "browser", defaults: ["Ctrl++"], customizable: true },
  { key: "browser_zoom_out", label: "Application zoom out", group: "File browser", scope: "browser", defaults: ["Ctrl+-"], customizable: true },
  { key: "browser_open_folder", label: "Open folder in file manager", group: "File browser", scope: "browser", defaults: ["Ctrl+Return"], customizable: true },
  { key: "browser_copy", label: "Copy selected items", group: "File browser", scope: "browser", fixed: ["Ctrl+C"], customizable: true },
  { key: "browser_cut", label: "Cut selected items", group: "File browser", scope: "browser", fixed: ["Ctrl+X"], customizable: true },
  { key: "browser_paste", label: "Paste into current folder", group: "File browser", scope: "browser", fixed: ["Ctrl+V"], customizable: true },
  { key: "browser_delete", label: "Delete selected items", group: "File browser", scope: "browser", fixed: ["Delete"], customizable: true },
  { key: "browser_new_folder", label: "Create new folder", group: "File browser", scope: "browser", customizable: true },
];

export const SHORTCUT_DEFAULTS: Record<string, string[]> = Object.fromEntries(
  SHORTCUTS.filter(item => item.customizable).map(item => [item.key, [...(item.defaults ?? [])]]),
);

export function resolveShortcutSettings(values: Record<string, string[]>): Record<string, string[]> {
  return Object.fromEntries(Object.entries(SHORTCUT_DEFAULTS).map(([key, defaults]) => [key, [...(values[key] ?? defaults)]]));
}

export function portableShortcut(event: KeyboardEvent): string {
  const modifiers: string[] = [];
  if (event.ctrlKey) modifiers.push("Ctrl");
  if (event.altKey) modifiers.push("Alt");
  if (event.shiftKey) modifiers.push("Shift");
  if (event.metaKey) modifiers.push("Meta");
  if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) return "";
  const key = (event.ctrlKey && event.key === "=" ? "+" : ({ " ": "Space", Enter: "Return", Escape: "Esc", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", ContextMenu: "Menu" } as Record<string, string>)[event.key])
    ?? (event.key.length === 1 ? event.key.toLocaleUpperCase() : event.key);
  return [...modifiers, key].join("+");
}

export function normalizeShortcut(value: string): string {
  return value.replaceAll(" ", "").replaceAll("Enter", "Return").replaceAll("Arrow", "").toLocaleLowerCase();
}

export function matchesShortcut(event: KeyboardEvent, action: string, configured: Record<string, string[]>): boolean {
  const definition = SHORTCUTS.find(item => item.key === action);
  const expected = [...(definition?.fixed ?? []), ...(configured[action] ?? definition?.defaults ?? [])];
  const actual = normalizeShortcut(portableShortcut(event));
  return Boolean(actual) && expected.some(value => !value.includes(" / ") && normalizeShortcut(value) === actual);
}
