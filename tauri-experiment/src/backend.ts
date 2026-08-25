import type { AppSettings, FileEntry, FolderSnapshot, Notice, Preferences, Remote } from "./model.ts";
import { invoke as tauriInvoke } from "@tauri-apps/api/core";

const inTauri = "__TAURI_INTERNALS__" in window;

const providers: Array<[Remote["provider"], string, string]> = [
  ["drive", "EHol", "Drive"], ["gphotos", "EHol", "Google Photos"],
  ["drive", "EHil", "Drive"], ["dropbox", "EHol", "Dropbox"],
  ["onedrive", "EHil", "OneDrive"], ["mega", "MEGA Personal", "MEGA"],
  ["protondrive", "EH Proton", "Proton Drive"], ["pcloud", "Mountlet", "pCloud"],
  ["iclouddrive", "Personal", "iCloud"], ["iclouddrive", "Photos", "iCloud"],
];

const browserSelections = new Map<string, number>();

function mockRemotes(): Remote[] {
  return providers.map(([provider, name, providerLabel], index) => ({
    id: `${provider}-${index}`,
    name,
    provider,
    providerLabel,
    mounted: index % 3 !== 1,
    usedBytes: provider === "gphotos" || provider === "iclouddrive" ? null : (index + 1) * 370_000_000,
    totalBytes: provider === "gphotos" || provider === "iclouddrive" ? null : 15_000_000_000,
  }));
}

function mockEntries(remoteId: string, path: string): FileEntry[] {
  const count = path === "Projects" ? 2500 : 14;
  return Array.from({ length: count }, (_, index) => {
    const isDir = path === "" && index < 5;
    const name = isDir ? ["Archive", "Books", "Medical records", "Projects", "Records"][index] : `Document ${String(index + 1).padStart(4, "0")}.${index % 4 === 0 ? "pdf" : "docx"}`;
    const entryPath = [path, name].filter(Boolean).join("/");
    return {
      id: `${remoteId}:${entryPath}`,
      remoteId,
      path: entryPath,
      name,
      isDir,
      size: isDir ? 0 : 4096 + index * 1731,
      modified: `2026-08-${String(16 - (index % 8)).padStart(2, "0")} ${String(8 + index % 12).padStart(2, "0")}:30`,
      cache: { cached: index === 6, offline: index === 8, partial: false },
    };
  });
}

async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  return tauriInvoke<T>(command, args);
}

export async function listRemotes(): Promise<Remote[]> {
  return inTauri ? invoke<Remote[]>("list_remotes") : mockRemotes();
}

export async function refreshRemoteUsage(remoteId: string): Promise<Remote> {
  return inTauri ? invoke<Remote>("refresh_remote_usage", { remoteId }) : (await listRemotes()).find(remote => remote.id === remoteId)!;
}

export async function reorderRemotes(order: readonly string[]): Promise<void> {
  if (inTauri) await invoke("reorder_remotes", { order });
}

export async function refreshNativeTrayMenu(): Promise<void> {
  if (inTauri) await invoke("refresh_tray_menu");
}

export interface RemoteConfigData {
  id: string;
  alias: string;
  provider: string;
  providerLabel: string;
  mountPath: string;
  remotePath: string;
  mountFlags: string;
  autoMount: boolean | null;
  fields: Record<string, string>;
  secretFields: string[];
}

export interface SaveRemoteConfigRequest {
  remoteId: string;
  alias: string;
  mountPath: string;
  remotePath: string;
  mountFlags: string;
  autoMount: boolean | null;
  fields: Record<string, string>;
}

export async function loadRemoteConfig(remoteId: string): Promise<RemoteConfigData> {
  return invoke<RemoteConfigData>("load_remote_config", { remoteId });
}

export async function saveRemoteConfig(request: SaveRemoteConfigRequest): Promise<string> {
  return invoke<string>("save_remote_config", { request });
}

export async function createRemote(request: {
  alias: string;
  provider: string;
  providerLabel: string;
  fields: Record<string, string>;
  googleAccount: string;
  mountAfter?: boolean;
  remotePath?: string;
}): Promise<string> {
  return invoke<string>("create_remote", { request });
}

export async function deleteRemote(remoteId: string): Promise<void> {
  await invoke("delete_remote", { remoteId });
}

export async function remoteRegistrationOrder(): Promise<string[]> {
  return inTauri ? invoke<string[]>("remote_registration_order") : (await listRemotes()).map(remote => remote.id);
}

export async function autoMountRemoteIds(): Promise<string[]> {
  return inTauri ? invoke<string[]>("auto_mount_remote_ids") : [];
}

export async function loadPreferences(): Promise<Preferences | null> {
  return inTauri ? invoke<Preferences>("load_preferences") : null;
}

export async function loadAppSettings(): Promise<AppSettings> {
  if (inTauri) return invoke<AppSettings>("load_app_settings");
  return {
    mountBase: "", autoMount: false, autoMountDelay: 2, startAtLogin: false,
    integratedFileEdits: false, fileManager: "", openFolderBehavior: "current_desktop",
    focusFileManager: true, windowMode: "multiple", theme: "system", zoomSteps: 0,
    fileListMaxItems: 0, remoteCheckInterval: 30, noticeInfoDisplay: "tray",
    noticeImportantDisplay: "dialog", noticeCheckInterval: 14400,
    configSyncRemote: "", configSyncPath: "Mountlet/config.mountlet", shortcuts: {},
  };
}

export async function saveAppSettings(settings: AppSettings): Promise<AppSettings> {
  return inTauri ? invoke<AppSettings>("save_app_settings", { settings }) : settings;
}

export async function loadShortcuts(): Promise<Record<string, string[]>> {
  return inTauri ? invoke<Record<string, string[]>>("load_shortcuts") : {};
}

export async function openConfigFile(kind: "rclone" | "app" | "mounts"): Promise<void> {
  if (inTauri) await invoke("open_config_file", { kind });
}

export async function openConfigBackupFolder(): Promise<void> {
  if (inTauri) await invoke("open_config_backup_folder");
}

export async function exportConfigBundle(destination: string, password: string): Promise<string> {
  return invoke<string>("export_config_bundle", { destination, password });
}

export async function importConfigBundle(source: string, password: string): Promise<string> {
  return invoke<string>("import_config_bundle", { source, password });
}

export async function pushConfigSync(password: string): Promise<void> {
  await invoke("push_config_sync", { password });
}

export async function pullConfigSync(password: string): Promise<string> {
  return invoke<string>("pull_config_sync", { password });
}
export async function configSyncDirty(): Promise<boolean> { return inTauri ? invoke<boolean>("config_sync_dirty") : false; }

export async function openExternal(url: string): Promise<void> {
  if (inTauri) await invoke("open_external", { url });
}

export async function quitApp(): Promise<void> {
  if (inTauri) await invoke("quit_app");
}

export interface BrowserMemory {
  paths?: Record<string, string>;
  selections?: Record<string, Record<string, { index?: number; path?: string }>>;
}

export interface SearchEntry {
  remoteId: string;
  remoteDisplay: string;
  provider: string;
  name: string;
  path: string;
  parentPath: string;
  isDir: boolean;
  size: number;
  modified: string;
  quality: "exact" | "phrase" | "filename";
}

export async function loadBrowserMemory(): Promise<BrowserMemory> {
  return inTauri ? invoke<BrowserMemory>("load_browser_memory") : {};
}

export async function persistBrowserMemory(memory: BrowserMemory): Promise<void> {
  if (inTauri) await invoke("persist_browser_memory", { memory });
}

export async function searchIndex(query: string, remoteId: string | null, limit: number): Promise<SearchEntry[]> {
  if (!inTauri) return [];
  return invoke<SearchEntry[]>("search_index", { request: { query, remoteId, limit } });
}

export async function listFolder(remoteId: string, path: string): Promise<FolderSnapshot> {
  if (inTauri) return invoke<FolderSnapshot>("list_folder", { request: { remoteId, path } });
  await Promise.resolve();
  const key = `${remoteId}:${path}`;
  return { remoteId, path, revision: 1, selectedIndex: browserSelections.get(key) ?? 0, entries: mockEntries(remoteId, path) };
}

export async function invalidateFolder(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("invalidate_folder", { request: { remoteId, path } });
}

export async function renameRemoteEntry(remoteId: string, path: string, newName: string): Promise<void> {
  if (inTauri) await invoke("rename_entry", { request: { remoteId, path, newName } });
}

export async function createRemoteFolder(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("create_folder", { request: { remoteId, path } });
}

export async function deleteRemoteEntry(remoteId: string, path: string, isDir: boolean): Promise<void> {
  if (inTauri) await invoke("delete_entry", { request: { remoteId, path }, isDir });
}

export async function transferRemoteEntry(sourceRemoteId: string, sourcePath: string, destinationRemoteId: string, destinationPath: string, moveEntry: boolean): Promise<void> {
  if (inTauri) await invoke("transfer_entry", { request: { sourceRemoteId, sourcePath, destinationRemoteId, destinationPath, moveEntry } });
}

export async function uploadLocalPaths(remoteId: string, destinationPath: string, localPaths: readonly string[]): Promise<void> {
  if (inTauri) await invoke("upload_local_paths", { request: { remoteId, destinationPath, localPaths } });
}

export async function listenNativeFileDrop(handler: (paths: string[]) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { getCurrentWebviewWindow } = await import("@tauri-apps/api/webviewWindow");
  return getCurrentWebviewWindow().onDragDropEvent(event => {
    if (event.payload.type === "drop") handler(event.payload.paths);
  });
}

export async function makeRemoteEntryOffline(remoteId: string, path: string, isDir: boolean): Promise<void> {
  if (inTauri) await invoke("make_offline", { request: { remoteId, path }, isDir });
}

export async function removeRemoteEntryOffline(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("remove_offline", { request: { remoteId, path } });
}

export async function removeOfflineCopies(remoteId: string | null = null): Promise<number> {
  return inTauri ? invoke<number>("remove_all_offline", { remoteId }) : 0;
}

export async function clearResolvedCache(remoteId: string | null = null, path: string | null = null): Promise<number> {
  return inTauri ? invoke<number>("clear_cache", { remoteId, path }) : 0;
}

export async function openRemoteEntry(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("open_entry", { request: { remoteId, path } });
}

export async function openMountedFolder(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("open_mounted_folder", { request: { remoteId, path } });
}

export async function openRemoteWeb(remoteId: string): Promise<void> {
  if (inTauri) await invoke("open_remote_web", { remoteId });
}

export async function reauthenticateRemote(remoteId: string): Promise<void> {
  if (inTauri) await invoke("reauthenticate_remote", { remoteId });
}

export interface ConfigWizardStep {
  state: string;
  option: Record<string, unknown>;
  error: string;
  result: string;
}

export async function configWizardStep(remoteId: string, state = "", result = ""): Promise<ConfigWizardStep> {
  return invoke<ConfigWizardStep>("config_wizard_step", { request: { remoteId, state, result } });
}

export async function rcloneOutput(): Promise<string> {
  return inTauri ? invoke<string>("rclone_output") : "";
}
export async function cacheSyncDiagnostics(): Promise<string> { return inTauri ? invoke<string>("cache_sync_diagnostics") : ""; }

export async function appDiagnostics(): Promise<string> {
  return inTauri ? invoke<string>("app_diagnostics") : "Mountlet Tauri browser preview";
}

export async function createBugReport(): Promise<string> {
  return inTauri ? invoke<string>("create_bug_report") : "Bug reports require the desktop app.";
}

export async function unreportedCrash(): Promise<string> { return inTauri ? invoke<string>("unreported_crash") : ""; }
export async function markCrashReported(crash: string): Promise<void> { if (inTauri) await invoke("mark_crash_reported", { crash }); }
export async function submitBugReport(kind: "bug" | "crash", message: string, contact: string, includeLogs: boolean, crash = ""): Promise<string> {
  return invoke<string>("submit_bug_report", { kind, message, contact, includeLogs, crash });
}

export interface LicenseStatus {
  state: "licensed" | "trial" | "expired";
  summary: string; trialDaysRemaining: number; licenseKey: string; licensedEmail: string;
  plan: string; licenseKind: string; maxDevices: number; deviceLabel: string; expiresAt: string;
}

export async function licenseStatus(): Promise<LicenseStatus> { return invoke<LicenseStatus>("license_status"); }
export async function activateLicense(key: string, deviceLabel: string): Promise<LicenseStatus> { return invoke<LicenseStatus>("activate_license", { key, deviceLabel }); }
export async function licenseDevices(): Promise<Record<string, unknown>> { return invoke<Record<string, unknown>>("license_devices"); }
export async function deactivateLicenseDevice(deviceId = ""): Promise<void> { await invoke("deactivate_license_device", { deviceId }); }

export async function notificationHistory(): Promise<Notice[]> {
  return inTauri ? invoke<Notice[]>("notification_history") : [];
}

export async function pollNotifications(): Promise<Notice[]> {
  return inTauri ? invoke<Notice[]>("poll_notifications") : [];
}

export async function markNotificationSeen(key: string): Promise<void> {
  if (inTauri) await invoke("mark_notification_seen", { key });
}

export async function deleteNotification(key: string): Promise<boolean> {
  return inTauri ? invoke<boolean>("delete_notification", { key }) : true;
}

export interface OfflineConflict {
  remoteId: string;
  path: string;
  localModified: number;
  cloudModified: number;
}

export async function syncOffline(remoteId: string): Promise<OfflineConflict[]> {
  return inTauri ? invoke<OfflineConflict[]>("sync_offline", { remoteId }) : [];
}

export async function changedOfflineRemotes(): Promise<string[]> {
  return inTauri ? invoke<string[]>("changed_offline_remotes") : [];
}

export async function detectRemoteCacheChanges(remoteId: string, entries: readonly Pick<FileEntry, "path" | "size" | "modified">[], accept = false): Promise<string[]> {
  return inTauri ? invoke<string[]>("detect_remote_cache_changes", { remoteId, entries, accept }) : [];
}

export async function resolveOfflineConflict(conflict: OfflineConflict, choice: "newer" | "older" | "keep_both"): Promise<void> {
  if (inTauri) await invoke("resolve_offline_conflict", { conflict, choice });
}

export async function materializeEntriesForDrag(entries: readonly Pick<FileEntry, "remoteId" | "path" | "isDir">[]): Promise<string[]> {
  return inTauri ? invoke<string[]>("materialize_entries_for_drag", { requests: entries }) : [];
}

export async function dragPreviewIcon(): Promise<string> {
  return inTauri ? invoke<string>("drag_preview_icon") : "";
}


export async function toggleRemoteMount(remoteId: string): Promise<boolean> {
  if (!inTauri) return true;
  return invoke<boolean>("toggle_mount", { remoteId });
}

export interface WindowLayoutRequest {
  mode: Preferences["mode"];
  selectedIndex: number;
  remoteCount: number;
  browserItems: number;
  remoteCardTop: number;
  globalSearchHeight: number;
  browserSearchHeight: number;
  remoteChromeHeight: number;
  remoteRowHeight: number;
  remotePaneWidth: number;
  singleWindowWidth: number;
  browserChromeHeight: number;
  browserRowHeight: number;
  browserWidth: number;
  browserMinHeight: number;
  availableX: number;
  availableY: number;
  availableWidth: number;
  availableHeight: number;
}

export async function applyWindowLayout(request: WindowLayoutRequest): Promise<void> {
  if (inTauri) await invoke("apply_window_layout", { request });
}

export function rememberSelection(remoteId: string, path: string, index: number, itemPath = ""): void {
  const key = `${remoteId}:${path}`;
  browserSelections.set(key, index);
  if (inTauri) void invoke("remember_selection", { request: { remoteId, path }, index, itemPath });
}

export async function setDetachedBrowser(enabled: boolean): Promise<void> {
  if (!inTauri) return;
  await invoke("set_browser_window", { enabled });
}

export async function emitBrowserState(remoteId: string, path: string): Promise<void> {
  if (!inTauri) return;
  await invoke("set_browser_window", { enabled: true });
  await invoke("set_browser_state", { remoteId, path });
  const { emitTo } = await import("@tauri-apps/api/event");
  await emitTo("browser", "browser-state", { remoteId, path });
}

export async function rememberBrowserState(remoteId: string, path: string): Promise<void> {
  if (inTauri) await invoke("set_browser_state", { remoteId, path });
}

export async function getBrowserState(): Promise<{ remoteId: string; path: string } | null> {
  if (!inTauri) return null;
  const [remoteId, path] = await invoke<[string, string]>("get_browser_state");
  return remoteId ? { remoteId, path } : null;
}

export async function focusNativeWindow(label: "main" | "browser"): Promise<void> {
  if (inTauri) await invoke("focus_window", { label });
}

export async function setWindowPinned(pinned: boolean): Promise<void> {
  if (inTauri) await invoke("set_window_pinned", { pinned });
}

export async function browserWindowSide(): Promise<"left" | "right"> {
  if (!inTauri) return "right";
  return invoke<"left" | "right">("browser_window_side");
}

export async function listenBrowserState(handler: (value: { remoteId: string; path: string }) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<{ remoteId: string; path: string }>("browser-state", event => handler(event.payload));
}

export async function emitUiPreferences(preferences: Preferences): Promise<void> {
  if (!inTauri) return;
  const { emitTo } = await import("@tauri-apps/api/event");
  await emitTo("browser", "ui-preferences", preferences);
}

export async function listenUiPreferences(handler: (value: Preferences) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<Preferences>("ui-preferences", event => handler(event.payload));
}

export async function listenNativeLayout(handler: (value: { browserSide: string; browserInnerHeight: number }) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<{ browserSide: string; browserInnerHeight: number }>("native-layout", event => handler(event.payload));
}

export async function listenTrayAnchor(handler: () => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen("tray-anchor-changed", handler);
}

export async function listenTrayCommand(handler: (command: string) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("tray-command", event => handler(event.payload));
}

export async function listenFolderUpdated(handler: (remoteId: string, path: string) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<{ remoteId: string; path: string }>("folder-updated", event => handler(event.payload.remoteId, event.payload.path));
}

export async function listenRemoteUsageDirty(handler: (remoteId: string) => void): Promise<() => void> {
  if (!inTauri) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("remote-usage-dirty", event => handler(event.payload));
}

export interface Prerequisite {
  key: string;
  label: string;
  ready: boolean;
  detail: string;
  helpUrl: string;
}

export interface DesktopHints {
  wayland: boolean;
  gnome: boolean;
  pinSupported: boolean;
  systemName: string;
}

export interface OauthPortStatus {
  available: boolean;
  owner: string;
  rclonePid: number | null;
}

export interface DriveOauthSource {
  remoteId: string;
  label: string;
}

export interface FileManagerOption {
  identifier: string;
  label: string;
  isSystemDefault: boolean;
  supportsNewWindow: boolean;
}

export async function checkPrerequisites(): Promise<Prerequisite[]> {
  if (!inTauri) return [];
  return invoke<Prerequisite[]>("check_prerequisites");
}

export async function desktopHints(): Promise<DesktopHints> {
  if (!inTauri) {
    return { wayland: false, gnome: false, pinSupported: true, systemName: "Linux" };
  }
  return invoke<DesktopHints>("desktop_hints");
}

export async function oauthPortStatus(): Promise<OauthPortStatus> {
  if (!inTauri) return { available: true, owner: "", rclonePid: null };
  return invoke<OauthPortStatus>("oauth_port_status");
}

export async function terminateOauthRclone(): Promise<boolean> {
  return inTauri ? invoke<boolean>("terminate_oauth_rclone") : false;
}

export async function driveOauthSources(): Promise<DriveOauthSource[]> {
  return inTauri ? invoke<DriveOauthSource[]>("drive_oauth_sources") : [];
}

export async function listFileManagers(): Promise<FileManagerOption[]> {
  return inTauri ? invoke<FileManagerOption[]>("list_file_managers") : [];
}

export async function openRcloneConfigTerminal(): Promise<string> {
  return invoke<string>("open_rclone_config_terminal");
}

export async function pickConfigBundlePath(save: boolean, suggested: string): Promise<string | null> {
  if (!inTauri) return null;
  return invoke<string | null>("pick_config_bundle_path", { save, suggested });
}

export async function showDesktopNotification(title: string, message: string): Promise<void> {
  if (inTauri) await invoke("show_desktop_notification", { title, message });
}

export async function fileIconDataUrl(name: string, isDir: boolean): Promise<string | null> {
  if (!inTauri) return null;
  return invoke<string | null>("file_icon_data_url", { name, isDir });
}
