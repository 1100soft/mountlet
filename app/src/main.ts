import "./style.css";
import { completeStartupSmoke, exportConfigBundle, importConfigBundle, markCrashReported, pullConfigSync, pushConfigSync, startupSmokeEnabled, submitBugReport, unreportedCrash } from "./backend.ts";
import { changedOfflineRemotes } from "./backend.ts";
import { refreshNativeTrayMenu } from "./backend.ts";
import { detectRemoteCacheChanges } from "./backend.ts";
import { configSyncDirty } from "./backend.ts";
import { cacheSyncDiagnostics } from "./backend.ts";
import { dragPreviewIcon, materializeEntriesForDrag } from "./backend.ts";
import { startDrag } from "@crabnebula/tauri-plugin-drag";
import { activateLicense, clipboardText, deactivateLicenseDevice, licenseDefaultDeviceLabel, licenseDevices, licenseStatus, type LicenseStatus } from "./backend.ts";
import { openAddRemoteDialog } from "./add_remote_dialog.ts";
import { appVersion, applyWindowLayout, autoMountRemoteIds, bugReportPreview, checkPrerequisites, clearResolvedCache, configWizardStep, createRemoteFile, createRemoteFolder, deleteNotification, deleteRemote, deleteRemoteEntry, desktopHints, emitBrowserState, emitUiPreferences, focusNativeWindow, getBrowserState, invalidateFolder, listFileManagers, listFolder, listRemotes, listenBrowserState, listenFolderUpdated, listenNativeFileDrop, listenNativeLayout, listenRemoteUsageDirty, listenRestoreKeyboardFocus, listenTrayAnchor, listenTrayCommand, listenUiPreferences, loadAppSettings, loadBrowserMemory, loadPreferences, loadRemoteConfig, loadShortcuts, makeRemoteEntryOffline, markNotificationSeen, markNotificationsSeen, notificationHistory, openConfigBackupFolder, openConfigFile, openExternal, openMountedFolder, openRemoteEntry, openRemoteWeb, persistBrowserMemory, pickConfigBundlePath, pollNotifications, quitApp, refreshRemoteUsage, resolveOfflineConflict, rcloneOutput, rememberBrowserState, rememberSelection, remoteRegistrationOrder, removeOfflineCopies, removeRemoteEntryOffline, renameRemoteEntry, reorderRemotes, saveAppSettings, saveRemoteConfig, searchIndex, setDetachedBrowser, setWindowPinned, showDesktopNotification, syncOffline, toggleRemoteMount, transferRemoteEntry, uploadLocalPaths, type BrowserMemory, type DesktopHints, type OfflineConflict, type SearchEntry } from "./backend.ts";
import { actionIcon, chromeIcon, fileIcon, providerIcon, refreshTintedIcons } from "./icons.ts";
import { applyMetricVariables, metricsAt, scaledMetric } from "./geometry.ts";
import { MAX_ZOOM_STEP, MIN_ZOOM_STEP, formatBytes, parentPath, zoomFactor, type AppSettings, type FileEntry, type FolderSnapshot, type Notice, type Preferences, type Remote } from "./model.ts";
import { openAppSettingsDialog } from "./settings_dialog.ts";
import { openShortcutDialog } from "./shortcuts_dialog.ts";
import { matchesShortcut as shortcutMatches, resolveShortcutSettings } from "./shortcuts.ts";
import { confirmOwned, promptOwned, promptWizardOption, showError, trapModalFocus, bindScaledSelect } from "./dialogs.ts";

const isBrowserWindow = new URLSearchParams(location.search).has("browser");
const preferences: Preferences = {
  mode: (localStorage.getItem("mountlet-mode") as Preferences["mode"]) || "single",
  theme: (localStorage.getItem("mountlet-theme") as Preferences["theme"]) || "system",
  zoomStep: Number(localStorage.getItem("mountlet-zoom") || 0),
  integratedFileEdits: false,
  fileListMaxItems: 0,
};

let remotes: readonly Remote[] = [];
let selectedRemote = "";
let currentPath = "";
let snapshot: FolderSnapshot | null = null;
let remoteFilter = "";
let fileFilter = localStorage.getItem("mountlet-browser-query") || "";
let requestGeneration = 0;
let selectionGeneration = 0;
let browserLoading = false;
let outwardDragEntries: FileEntry[] = [];
let outwardDragStarted = false;
let browserError = "";
let nativeLayoutTimer = 0;
let focusRefreshTimer = 0;
let zoomRenderTimer = 0;
let persistUiTimer = 0;
let cachedBrowserSide: "left" | "right" = "right";
let nativeBrowserInnerHeight = 0;
let remoteHoverArmed = false;
let lastPointer = { x: Number.NaN, y: Number.NaN };
let lastFocusOwner: "main" | "browser" | "global-search" | "remote-search" = "main";
let parkedFocusOwner: "main" | "browser" | "global-search" | "remote-search" | null = null;
const searchTimers = { global: 0, remote: 0 };
const searchGenerations = { global: 0, remote: 0 };
let globalSearchResults: readonly SearchEntry[] = [];
let remoteSearchResults: readonly SearchEntry[] = [];
let globalSearchLoading = false;
let remoteSearchLoading = false;
let globalSearchSelected = 0;
let remoteSearchSelected = 0;
let globalPreviewGeneration = 0;
const folderSnapshots = new Map<string, FolderSnapshot>();
const pendingMounts = new Set<string>();
const pendingReauthentication = new Set<string>();
const remoteErrors = new Map<string, string>();
const usageRefreshQueue = new Set<string>();
let usageRefreshTimer = 0;
let folderPollTimer = 0;
let browserMemory: BrowserMemory = {};
let draggedRemote = "";
let sortMode = "manual";
let sortReverse = false;
let shortcuts: Record<string, string[]> = {};
let completeSettings: AppSettings | null = null;
let currentHints: DesktopHints | null = null;
let windowPinned = false;
let registrationOrder: readonly string[] = [];
let currentLicense: LicenseStatus | null = null;
type FileClipboard = { entries: Array<{ remoteId: string; path: string }>; move: boolean };

const app = document.querySelector<HTMLDivElement>("#app")!;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  return node;
}

function requireIntegratedEdits(): boolean {
  if (preferences.integratedFileEdits) return true;
  showToast("Integrated file edits are disabled. Enable them in App settings, or use the system file manager.");
  return false;
}
function licenseLocked(): boolean { return currentLicense?.state === "expired"; }

function bounded<T>(operation: Promise<T>, milliseconds: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), milliseconds);
    operation.then(value => { window.clearTimeout(timer); resolve(value); }, error => { window.clearTimeout(timer); reject(error); });
  });
}

function formatLocalDate(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

interface ContextAction { label?: string; enabled?: boolean; run?: () => void; separator?: boolean; children?: ContextAction[] }

function dismissContextMenu(): void {
  document.querySelectorAll(".context-menu").forEach(menu => menu.remove());
}

function showContextMenu(event: MouseEvent, actions: ContextAction[]): void {
  event.preventDefault();
  event.stopPropagation();
  dismissContextMenu();
  const menu = element("div", "context-menu");
  menu.setAttribute("role", "menu");
  const appendActions = (target: HTMLElement, items: ContextAction[]) => {
    for (const action of items) {
      if (action.separator) { target.append(element("hr")); continue; }
      const item = element("div", `context-item${action.children?.length ? " has-children" : ""}`);
      const button = element("button", "context-action", action.label || "");
      button.type = "button"; button.disabled = action.enabled === false; button.setAttribute("role", "menuitem");
      if (action.children?.length) {
        button.append(element("span", "context-cascade", "›"));
        const child = element("div", "context-submenu"); child.setAttribute("role", "menu"); appendActions(child, action.children); item.append(button, child);
      } else {
        button.addEventListener("click", () => { suppressRemoteHover(); dismissContextMenu(); action.run?.(); }); item.append(button);
      }
      target.append(item);
    }
  };
  appendActions(menu, actions);
  document.body.append(menu);
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  menu.style.left = `${Math.max(0, Math.min(event.clientX, innerWidth - width))}px`;
  menu.style.top = `${Math.max(0, Math.min(event.clientY, innerHeight - height))}px`;
  menu.classList.toggle("open-left", event.clientX + width * 2 > innerWidth);
  (menu.querySelector("button:not(:disabled)") as HTMLButtonElement | null)?.focus({ preventScroll: true });
  const dismiss = (dismissEvent: Event) => {
    if (!(dismissEvent.target as Element | null)?.closest?.(".context-menu")) dismissContextMenu();
  };
  queueMicrotask(() => document.addEventListener("mousedown", dismiss, { once: true }));
}

window.addEventListener("blur", dismissContextMenu);
document.addEventListener("visibilitychange", () => { if (document.hidden) dismissContextMenu(); });

function suppressRemoteHover(): void {
  remoteHoverArmed = false;
}

function fileListHasFocus(): boolean {
  return Boolean(document.activeElement?.closest("#browser-pane .file-viewport"));
}

function paneFocusOwner(): "main" | "browser" {
  if (isBrowserWindow || lastFocusOwner === "browser" || lastFocusOwner === "remote-search") return "browser";
  return "main";
}

function syncFocusChrome(): void {
  const owner = document.hasFocus() ? paneFocusOwner() : "none";
  document.documentElement.dataset.focusOwner = owner;
  document.querySelector(".remote-pane")?.classList.toggle("focus-active", owner === "main");
  document.querySelector(".browser-pane")?.classList.toggle("focus-active", owner === "browser");
}

function rememberFocusOwner(target: EventTarget | null = document.activeElement): void {
  const active = target instanceof HTMLElement ? target : null;
  if (active?.closest(".browser-pane .search-input")) lastFocusOwner = "remote-search";
  else if (active?.closest(".remote-pane .search-input")) lastFocusOwner = "global-search";
  else if (active?.closest("#browser-pane")) lastFocusOwner = "browser";
  else lastFocusOwner = "main";
  syncFocusChrome();
}

function globalSearchHasResults(): boolean {
  return Boolean(remoteFilter.trim()) && globalSearchResults.length > 0;
}

function remoteSearchHasResults(): boolean {
  return Boolean(fileFilter.trim()) && remoteSearchResults.length > 0;
}

function focusSearchField(scope: "global" | "remote"): boolean {
  const field = document.querySelector<HTMLInputElement>(scope === "remote" ? ".browser-pane .search-input" : ".remote-pane .search-input");
  if (!field) return false;
  if (document.activeElement === field) return false;
  field.focus();
  field.select();
  rememberFocusOwner(field);
  return true;
}

async function syncBrowserMemory(): Promise<void> {
  try { browserMemory = await loadBrowserMemory(); } catch { /* Keep the in-window copy if the store is unavailable. */ }
}

function directionPointsToBrowser(key: string): boolean {
  return cachedBrowserSide === "left" ? key === "ArrowLeft" : key === "ArrowRight";
}

function directionPointsToMain(key: string): boolean {
  return cachedBrowserSide === "left" ? key === "ArrowRight" : key === "ArrowLeft";
}

function persistBrowserFolder(remoteId: string, folderPath: string, itemPath = "", index = 0): Promise<void> {
  browserMemory = {
    ...browserMemory,
    paths: { ...browserMemory.paths, [remoteId]: folderPath },
    selections: {
      ...browserMemory.selections,
      [remoteId]: {
        ...browserMemory.selections?.[remoteId],
        [folderPath]: { path: itemPath, index },
      },
    },
  };
  return persistBrowserMemory(browserMemory).catch(() => undefined);
}

function persistBrowserPath(remoteId: string, folderPath: string): Promise<void> {
  browserMemory = {
    ...browserMemory,
    paths: { ...browserMemory.paths, [remoteId]: folderPath },
  };
  return persistBrowserMemory(browserMemory).catch(() => undefined);
}

function rememberedFolderPath(remoteId: string): string {
  return browserMemory.paths?.[remoteId] ?? localStorage.getItem(`mountlet-path:${remoteId}`) ?? "";
}

function rememberedFolderSelection(remoteId: string, folderPath: string): { path: string; index: number } {
  const stored = browserMemory.selections?.[remoteId]?.[folderPath];
  const path = stored?.path || localStorage.getItem(`mountlet-selection:${remoteId}:${folderPath}`) || "";
  const indexValue = stored?.index ?? Number.parseInt(localStorage.getItem(`mountlet-selection-index:${remoteId}:${folderPath}`) ?? "", 10);
  return { path, index: Number.isFinite(indexValue) ? Number(indexValue) : 0 };
}

function fileViewportBudget(): number {
  const metrics = metricsAt(preferences.zoomStep);
  const search = fileFilter.trim() ? metrics.searchHeader + 6 * metrics.searchRow + scaledMetric(5, preferences.zoomStep) : 0;
  const fromNative = nativeBrowserInnerHeight > 0 ? nativeBrowserInnerHeight - metrics.browserChrome - search : 0;
  return Math.max(window.innerHeight - metrics.browserChrome - search, fromNative, 0);
}

function matchesShortcut(event: KeyboardEvent, action: string): boolean {
  return shortcutMatches(event, action, shortcuts);
}

function applyPreferences(): void {
  document.documentElement.dataset.theme = preferences.theme;
  document.documentElement.style.colorScheme = preferences.theme === "system" ? "light dark" : preferences.theme;
  document.documentElement.style.setProperty("--zoom", String(zoomFactor(preferences.zoomStep)));
  applyMetricVariables(preferences.zoomStep);
  refreshTintedIcons();
  localStorage.setItem("mountlet-mode", preferences.mode);
  localStorage.setItem("mountlet-theme", preferences.theme);
  localStorage.setItem("mountlet-zoom", String(preferences.zoomStep));
}

function persistLiveUi(): void {
  if (isBrowserWindow) return;
  window.clearTimeout(persistUiTimer);
  persistUiTimer = window.setTimeout(() => {
    if (document.querySelector(".modal-layer")) {
      persistLiveUi();
      return;
    }
    void (async () => {
      try {
        const current = completeSettings ?? await loadAppSettings();
        completeSettings = await saveAppSettings({
          ...current,
          windowMode: preferences.mode,
          theme: preferences.theme,
          zoomSteps: preferences.zoomStep,
          integratedFileEdits: preferences.integratedFileEdits,
          fileListMaxItems: preferences.fileListMaxItems,
        });
      } catch { /* config.toml is rewritten on the next zoom, theme, or settings save. */ }
    })();
  }, 200);
}

function cycleTheme(): void {
  preferences.theme = preferences.theme === "system" ? "light" : preferences.theme === "light" ? "dark" : "system";
  applyPreferences();
  render();
  persistLiveUi();
  if (!isBrowserWindow) void emitUiPreferences(preferences);
}

function availableDesktop() {
  const desktop = window.screen as Screen & { availLeft?: number; availTop?: number };
  return {
    availableX: desktop.availLeft ?? 0,
    availableY: desktop.availTop ?? 0,
    availableWidth: desktop.availWidth,
    availableHeight: desktop.availHeight,
  };
}

async function layoutNativeWindows(): Promise<void> {
  const metrics = metricsAt(preferences.zoomStep);
  const purchaseHeight = currentLicense && currentLicense.state !== "licensed" ? metrics.purchaseRow : 0;
  const globalSearchHeight = remoteFilter.trim() ? metrics.searchHeader + 6 * metrics.searchRow + scaledMetric(4, preferences.zoomStep) : 0;
  const browserSearchHeight = fileFilter.trim() ? metrics.searchHeader + 6 * metrics.searchRow + scaledMetric(5, preferences.zoomStep) : 0;
  await applyWindowLayout({
    mode: licenseLocked() ? "single" : preferences.mode,
    selectedIndex: Math.max(0, visibleRemotes().findIndex(remote => remote.id === selectedRemote)),
    remoteCount: remotes.length,
    browserItems: preferences.fileListMaxItems > 0
      ? Math.min(snapshot?.entries.length ?? 0, preferences.fileListMaxItems)
      : snapshot?.entries.length ?? 0,
    remoteCardTop: metrics.remoteCardTop + purchaseHeight + globalSearchHeight,
    globalSearchHeight,
    browserSearchHeight,
    remoteChromeHeight: metrics.remoteChrome + purchaseHeight,
    remoteRowHeight: metrics.remoteRow,
    remotePaneWidth: metrics.remotePaneWidth,
    singleWindowWidth: metrics.singleWindowWidth,
    browserChromeHeight: metrics.browserChrome,
    browserRowHeight: metrics.fileRow,
    browserWidth: metrics.browserWidth,
    browserMinHeight: preferences.mode === "single" ? scaledMetric(340, preferences.zoomStep) : metrics.browserMinHeight,
    ...availableDesktop(),
  });
}

function scheduleNativeLayout(delay = 70): void {
  window.clearTimeout(nativeLayoutTimer);
  nativeLayoutTimer = window.setTimeout(() => void layoutNativeWindows(), delay);
}

function setZoom(delta: number): void {
  preferences.zoomStep = Math.min(MAX_ZOOM_STEP, Math.max(MIN_ZOOM_STEP, preferences.zoomStep + delta));
  renderFooter();
  window.clearTimeout(zoomRenderTimer);
  zoomRenderTimer = window.setTimeout(() => {
    applyPreferences(); document.dispatchEvent(new Event("mountlet-resize")); scheduleNativeLayout(0);
    persistLiveUi();
    if (!isBrowserWindow) void emitUiPreferences(preferences);
  }, 80);
}

function resetZoom(): void {
  preferences.zoomStep = 0; renderFooter(); window.clearTimeout(zoomRenderTimer);
  zoomRenderTimer = window.setTimeout(() => {
    applyPreferences(); document.dispatchEvent(new Event("mountlet-resize")); scheduleNativeLayout(0);
    persistLiveUi();
    if (!isBrowserWindow) void emitUiPreferences(preferences);
  }, 80);
}

function noticeDisplayTime(notice: Notice): string {
  if (!notice.updatedAt) return "Date unavailable";
  const date = new Date(notice.updatedAt);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  const parts = new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(date).reduce<Record<string, string>>((values, part) => { values[part.type] = part.value; return values; }, {});
  return `Sent ${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function showNoticeDetails(notice: Notice): Promise<void> {
  return new Promise(resolve => {
    const layer = element("div", "modal-layer");
    const dialog = element("section", `modal-dialog notice-detail level-${notice.level}${notice.critical ? " critical" : ""}`);
    dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
    const title = element("h2", "", notice.title);
    const sent = element("p", "notification-date", noticeDisplayTime(notice));
    const body = element("p", "notification-detail-message", notice.message);
    const actions = element("div", "dialog-actions");
    const close = element("button", "primary", "OK"); close.type = "button";
    const dismiss = async () => { layer.remove(); await markNotificationSeen(notice.key); void refreshNotificationIndicator(); resolve(); };
    if (notice.url) {
      const open = element("button", "", "Open link"); open.type = "button";
      open.addEventListener("click", () => void openExternal(notice.url)); actions.append(open);
    }
    close.addEventListener("click", () => void dismiss()); actions.append(close);
    layer.addEventListener("keydown", event => {
      if (event.key === "Escape" || (event.key === "Enter" && event.target === dialog)) { event.preventDefault(); void dismiss(); }
    });
    dialog.append(title, sent, body, actions); layer.append(dialog); document.body.append(layer);
    trapModalFocus(layer, dialog, close);
  });
}

async function showNotifications(): Promise<void> {
  try { await pollNotifications(); } catch { /* Keep locally stored history available offline. */ }
  const layer = element("div", "modal-layer");
  const dialog = element("section", "modal-dialog notification-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  const title = element("h2", "", "Notifications");
  const list = element("div", "notification-list");
  const close = element("button", "primary", "Close"); close.type = "button";
  const renderNotifications = async () => {
    const notices = await notificationHistory();
    if (!notices.length) { list.replaceChildren(element("p", "notification-empty", "No notifications")); return; }
    list.replaceChildren(...notices.map(notice => {
      const card = element("article", `notification-card level-${notice.level}${notice.critical ? " critical" : ""}${notice.seen ? " seen" : " unseen"}`);
      card.tabIndex = 0; card.setAttribute("role", "button");
      const heading = element("div", "notification-title", notice.title);
      const sent = element("div", "notification-date", noticeDisplayTime(notice));
      const body = element("p", "notification-message", notice.message);
      const open = () => void showNoticeDetails(notice);
      card.addEventListener("click", open);
      card.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
      const remove = actionIcon("", "Delete"); remove.classList.add("notification-delete"); remove.disabled = !notice.deletable;
      remove.title = notice.deletable ? "Delete notification" : "Critical notifications cannot be deleted.";
      remove.addEventListener("click", async event => { event.stopPropagation(); if (await deleteNotification(notice.key)) { await renderNotifications(); await refreshNotificationIndicator(); } });
      card.append(heading, sent, body, remove);
      return card;
    }));
    await markNotificationsSeen(notices.map(notice => notice.key));
    void refreshNotificationIndicator();
  };
  const dismiss = () => layer.remove();
  close.addEventListener("click", dismiss);
  layer.addEventListener("mousedown", event => { if (event.target === layer) dismiss(); });
  layer.addEventListener("keydown", event => { if (event.key === "Escape") dismiss(); });
  const actions = element("div", "dialog-actions");
  const viewAll = element("button", "", "View all"); viewAll.type = "button";
  viewAll.addEventListener("click", () => void openExternal("https://mountlet.app/#notifications"));
  actions.append(viewAll, close); dialog.append(title, list, actions);
  layer.append(dialog); document.body.append(layer);
  await renderNotifications();
  trapModalFocus(layer, dialog, close);
}

async function refreshNotificationIndicator(): Promise<void> {
  const button = document.querySelector<HTMLButtonElement>(".notification-button");
  if (!button) return;
  const unseen = (await notificationHistory()).filter(notice => !notice.seen).length;
  button.classList.toggle("has-notice", unseen > 0);
  button.title = unseen ? `Notifications (${unseen} unread)` : "Notifications";
  button.setAttribute("aria-label", button.title);
}

let noticePollTimer = 0;
let noticePollPending = false;
async function pollNoticeServer(): Promise<void> {
  if (isBrowserWindow || noticePollPending) return;
  noticePollPending = true;
  try {
    const notices = await pollNotifications();
    for (const notice of notices) {
      const display = notice.level === "important"
        ? completeSettings?.noticeImportantDisplay
        : completeSettings?.noticeInfoDisplay;
      if (notice.critical || display === "dialog") await showNoticeDetails(notice);
      else if (display === "off") await markNotificationSeen(notice.key);
      else if (display === "tray") {
        await showDesktopNotification(notice.title, notice.message);
        await markNotificationSeen(notice.key);
      }
    }
    await refreshNotificationIndicator();
  } catch { /* Notice availability must never delay or interrupt the application. */ }
  finally { noticePollPending = false; }
  const seconds = Math.max(60, completeSettings?.noticeCheckInterval ?? 14400);
  if ((completeSettings?.noticeCheckInterval ?? 14400) > 0) noticePollTimer = window.setTimeout(pollNoticeServer, seconds * 1000);
}

async function bundlePassword(title: string, confirm = false): Promise<string | null> {
  const first = await promptWizardOption(title, "Bundle password. Leave blank for an unencrypted bundle.", "", true, []);
  if (first === null || !confirm || !first) return first;
  const second = await promptWizardOption("Confirm bundle password", "Enter the same password again.", "", true, []);
  if (second === null) return null;
  if (first !== second) { await showError("Config bundle", "The passwords did not match."); return null; }
  return first;
}

async function exportBundle(): Promise<void> {
  const destination = await pickConfigBundlePath(true, "config.mountlet")
    || await promptOwned("Export Mountlet config bundle", "Destination path", "~/mountlet-config.mountlet");
  if (!destination) return;
  const password = await bundlePassword("Export bundle password", true); if (password === null) return;
  if (!password && !await confirmOwned("Export without password?", "This bundle contains cloud credentials and will not be encrypted. Continue?", "Export")) return;
  try { showToast(`Exported to ${await exportConfigBundle(destination, password)}.`); } catch (error) { await showError("Export config", error); }
}

async function importBundle(): Promise<void> {
  const source = await pickConfigBundlePath(false, "config.mountlet")
    || await promptOwned("Import Mountlet config bundle", "Bundle path", "~/mountlet-config.mountlet");
  if (!source || !await confirmOwned("Import Mountlet config?", "Replace this device's shared Mountlet and rclone settings? Mountlet will first create a restorable backup.", "Import")) return;
  const password = await bundlePassword("Import bundle password"); if (password === null) return;
  try { const backup = await importConfigBundle(source, password); await refreshRemoteStatus(); showToast(`Imported configuration.${backup ? ` Backup: ${backup}` : ""}`); }
  catch (error) { await showError("Import config", error); }
}

async function syncConfiguration(direction: "push" | "pull"): Promise<void> {
  completeSettings ??= await loadAppSettings();
  if (!completeSettings.configSyncRemote) { showToast("Set a config sync remote in App configuration first."); return; }
  if (direction === "pull" && !await confirmOwned("Pull synced config?", "Replace this device's shared Mountlet and rclone settings? A backup will be created first.", "Pull")) return;
  const password = await bundlePassword("Sync bundle password", direction === "push"); if (password === null) return;
  if (direction === "push" && !password && !await confirmOwned("Sync without password?", "The remote bundle contains cloud credentials and will not be encrypted. Continue?", "Push")) return;
  try {
    if (direction === "push") { await pushConfigSync(password); showToast("Configuration pushed to the sync location."); }
    else { const backup = await pullConfigSync(password); await refreshRemoteStatus(); showToast(`Configuration pulled.${backup ? ` Backup: ${backup}` : ""}`); }
    document.querySelectorAll(".sync-dirty").forEach(element => element.classList.remove("sync-dirty"));
  } catch (error) { await showError(direction === "push" ? "Push config" : "Pull config", error); }
}

async function showLicense(): Promise<void> {
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer"); const dialog = element("section", "modal-dialog license-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  const title = element("h2", "", "License"); const content = element("div", "license-content"); content.append(element("p", "dialog-status", "Checking license…")); const actions = element("div", "dialog-actions");
  const close = element("button", "primary", "Close"); close.addEventListener("click", () => layer.remove()); actions.append(close); dialog.append(title, content, actions); layer.append(dialog); document.body.append(layer);
  const defaultDeviceLabel = await bounded(licenseDefaultDeviceLabel(), 5000, "Could not identify this device.").catch(() => "This device");
  const renderLicense = async () => {
    const status = await bounded(licenseStatus(), 15000, "License status timed out. Please try again."); currentLicense = status; content.replaceChildren();
    content.append(element("p", `license-summary${status.state === "expired" ? " error" : ""}`, status.summary));
    const expiry = status.licenseKind === "beta" && status.state === "licensed"
      ? "Public beta access renews daily and can end when the beta closes."
      : status.expiresAt ? `${status.state === "trial" ? "Ends" : status.state === "expired" ? "Expired" : "Renews"}: ${formatLocalDate(status.expiresAt)}` : "";
    if (expiry) content.append(element("p", "license-expiry", expiry));
    const form = element("div", "license-form");
    const keyRow = element("label", "dialog-field"); keyRow.append(element("span", "", "License key"));
    const keyControls = element("span", "license-key-controls"); const key = element("input", "") as HTMLInputElement; key.value = status.licenseKey; key.placeholder = "MNT-…"; key.readOnly = status.state === "licensed";
    const copy = actionIcon("", "Copy"); copy.classList.add("license-key-button"); copy.disabled = !key.value;
    let copyCheckGeneration = 0;
    const updateCopyState = async () => {
      const generation = ++copyCheckGeneration;
      let copied = false;
      try { copied = Boolean(key.value.trim()) && (await clipboardText()).trim() === key.value.trim(); } catch { copied = false; }
      if (generation !== copyCheckGeneration) return;
      copy.classList.toggle("copied", copied);
      copy.title = copied ? "Copied to clipboard." : "Copy the license key.";
    };
    copy.addEventListener("click", async () => { await navigator.clipboard.writeText(key.value.trim()); await updateCopyState(); });
    copy.addEventListener("focus", () => void updateCopyState());
    const onWindowFocus = () => void updateCopyState(); window.addEventListener("focus", onWindowFocus);
    const onCopy = () => window.setTimeout(() => void updateCopyState(), 50); document.addEventListener("copy", onCopy);
    const clipboardTimer = window.setInterval(() => { if (document.hasFocus()) void updateCopyState(); }, 400);
    const copyObserver = new MutationObserver(() => { if (!copy.isConnected) { window.removeEventListener("focus", onWindowFocus); document.removeEventListener("copy", onCopy); window.clearInterval(clipboardTimer); copyObserver.disconnect(); } }); copyObserver.observe(content, { childList: true });
    const paste = actionIcon("", "Paste"); paste.classList.add("license-key-button"); paste.disabled = key.readOnly; paste.title = key.readOnly ? "Deactivate this device before changing the license key." : "Paste a license key from the clipboard."; paste.addEventListener("click", async () => { key.value = (await navigator.clipboard.readText()).trim(); activate.disabled = !key.value; await updateCopyState(); });
    keyControls.append(key, copy, paste); keyRow.append(keyControls);
    const deviceRow = element("label", "dialog-field"); deviceRow.append(element("span", "", "Device name")); const device = element("input", "") as HTMLInputElement; device.value = status.deviceLabel || defaultDeviceLabel; device.placeholder = defaultDeviceLabel; device.disabled = status.state === "licensed"; deviceRow.append(device);
    const controls = element("div", "license-controls");
    const activate = element("button", "primary", "Activate"); activate.disabled = status.state === "licensed" || !key.value;
    activate.dataset.dialogConfirm = "true";
    key.addEventListener("input", () => { copy.disabled = !key.value; activate.disabled = !key.value || status.state === "licensed"; void updateCopyState(); });
    void updateCopyState();
    activate.addEventListener("click", async () => { activate.disabled = true; try { currentLicense = await activateLicense(key.value, device.value); render(); if (preferences.mode === "multiple") { await setDetachedBrowser(true); await emitBrowserState(selectedRemote, currentPath); } scheduleNativeLayout(0); await renderLicense(); showToast("Mountlet is activated on this device."); } catch (error) { await showError("License activation", error); } finally { activate.disabled = false; } });
    const buy = element("button", "", "Buy license"); buy.addEventListener("click", () => void openExternal("https://mountlet.app/#pricing"));
    controls.append(activate); if (status.state !== "licensed") controls.append(buy);
    form.append(keyRow, deviceRow, controls); content.append(form);
    if (status.state !== "licensed") return;
    const deviceSection = element("section", "license-device-section"); const heading = element("div", "license-device-heading"); const headingText = element("strong", "", "Activated devices");
    const addDevices = element("button", "", "+ Add devices"); addDevices.hidden = status.licenseKind === "beta"; addDevices.addEventListener("click", () => void openExternal(`https://mountlet.app/?license_action=add_devices&license_key=${encodeURIComponent(status.licenseKey)}#pricing`)); heading.append(headingText, addDevices); deviceSection.append(heading);
    try {
      const result = await licenseDevices(); const devices = Array.isArray(result.devices) ? result.devices as Array<Record<string, unknown>> : []; const used = Number(result.usedDevices ?? devices.length); const maximum = Number(result.maxDevices ?? status.maxDevices);
      headingText.textContent = status.licenseKind === "beta" ? "Public beta" : `Activated devices (${used}${maximum ? `/${maximum}` : ""})`;
      const list = element("div", "license-devices");
      if (!devices.length) list.append(element("p", "license-devices-empty", "No activated devices were returned."));
      for (const item of devices) {
        const row = element("div", "license-device"); const label = String(item.deviceLabel ?? item.device_label ?? item.label ?? "Unnamed device"); const meta = [item.platform, formatLocalDate(String(item.activatedAt ?? item.activated_at ?? "")), item.current ? "current" : ""].filter(Boolean).join(" · ");
        const text = element("span"); text.append(element("strong", "", label)); if (meta) text.append(element("small", "", meta));
        const remove = element("button", "", item.current ? "Deactivate this device" : "Deactivate"); remove.addEventListener("click", async () => { const id = String(item.deviceId ?? item.device_id ?? item.id ?? ""); if (!await confirmOwned("Deactivate device?", `Deactivate ${label} and free one device slot?`, "Deactivate")) return; await deactivateLicenseDevice(id); if (item.current) { currentLicense = await licenseStatus(); await setDetachedBrowser(false); render(); scheduleNativeLayout(0); } await renderLicense(); }); row.append(text, remove); list.append(row);
      }
      deviceSection.append(list);
    } catch (error) { deviceSection.append(element("p", "dialog-status error", String(error))); }
    const deactivate = element("button", "", "Deactivate this device"); deactivate.addEventListener("click", async () => { if (!await confirmOwned("Deactivate this device?", "Deactivate this Mountlet installation and free one device slot?", "Deactivate")) return; await deactivateLicenseDevice(); currentLicense = await licenseStatus(); await setDetachedBrowser(false); render(); scheduleNativeLayout(0); await renderLicense(); });
    deviceSection.append(deactivate); content.append(deviceSection);
  };
  try { await renderLicense(); }
  catch (error) {
    content.replaceChildren(element("p", "dialog-status error", String(error)));
    const buy = element("button", "", "Buy license"); buy.addEventListener("click", () => void openExternal("https://mountlet.app/#pricing")); content.append(buy);
  }
  trapModalFocus(layer, dialog, close);
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
}

function renderToolbar(): HTMLElement {
  const header = element("header", "app-header");
  const menu = element("nav", "menu-bar");
  ["App", "Mount", "Config"].forEach(label => {
    const button = element("button", "menu-button", label);
    button.type = "button";
    button.addEventListener("click", event => showApplicationMenu(label, event));
    menu.append(button);
  });
  header.append(menu);

  const toolbar = element("div", "toolbar");
  const dragHandle = element("span", "window-drag-handle", "✥");
  dragHandle.setAttribute("data-tauri-drag-region", "");
  dragHandle.title = "Drag to move Mountlet.";
  dragHandle.setAttribute("aria-label", dragHandle.title);
  const settingsButton = actionIcon("⚙", "App configuration");
  settingsButton.addEventListener("click", () => void showAppSettings());
  const pushConfig = actionIcon("⇧", "Push configuration");
  pushConfig.addEventListener("click", () => void syncConfiguration("push"));
  const pullConfig = actionIcon("⇩", "Pull configuration");
  pullConfig.addEventListener("click", () => void syncConfiguration("pull"));
  if (completeSettings?.configSyncRemote) void configSyncDirty().then(dirty => {
    pushConfig.classList.toggle("sync-dirty", dirty);
    pushConfig.title = dirty ? "Push configuration (local config changed)" : "Push configuration";
  });
  const syncAll = actionIcon("↻", "Sync all cached files");
  syncAll.addEventListener("click", () => void synchronizeAllOffline());
  const removeOffline = actionIcon("⇊", "Remove all offline copies");
  removeOffline.addEventListener("click", () => void removeAllOffline());
  const clearCache = actionIcon("⌫", "Clear all cached files");
  clearCache.addEventListener("click", () => void clearAllCache());
  toolbar.append(
    dragHandle,
    settingsButton,
    pushConfig,
    pullConfig,
    syncAll,
    removeOffline,
    clearCache,
  );
  const sort = actionIcon("⇅", "Sort remotes");
  sort.addEventListener("click", event => {
    event.stopPropagation();
    document.querySelector(".sort-popup")?.remove();
    const popup = element("div", "sort-popup");
    [["registration", "Registration time"], ["name", "Name"], ["provider", "Provider"], ["size", "Total size, largest first"], ["used", "Used space, largest first"], ["remaining", "Remaining space, lowest first"]].forEach(([value, label]) => {
      const option = element("button", "", label);
      option.type = "button";
      option.addEventListener("click", () => {
        if (["size", "used", "remaining"].includes(value) && remotes.some(remote => remote.usedBytes == null || remote.totalBytes == null)) {
          showToast("Storage usage is loading. Try again when the values appear.");
          popup.remove();
          return;
        }
        const sorted = [...remotes];
        if (value === "registration") {
          const ranks = new Map(registrationOrder.map((id, index) => [id, index]));
          sorted.sort((left, right) => (ranks.get(left.id) ?? Number.MAX_SAFE_INTEGER) - (ranks.get(right.id) ?? Number.MAX_SAFE_INTEGER));
        } else if (value === "name") sorted.sort((left, right) => left.name.localeCompare(right.name));
        else if (value === "provider") sorted.sort((left, right) => left.providerLabel.localeCompare(right.providerLabel) || left.name.localeCompare(right.name));
        else if (value === "size") sorted.sort((left, right) => (right.totalBytes ?? 0) - (left.totalBytes ?? 0));
        else if (value === "used") sorted.sort((left, right) => (right.usedBytes ?? 0) - (left.usedBytes ?? 0));
        else if (value === "remaining") sorted.sort((left, right) => ((left.totalBytes ?? 0) - (left.usedBytes ?? 0)) - ((right.totalBytes ?? 0) - (right.usedBytes ?? 0)));
        remotes = sorted;
        sortMode = "manual";
        sortReverse = false;
        persistRemoteOrder();
        popup.remove();
        renderRemoteList();
      });
      popup.append(option);
    });
    const reverse = element("button", "", "Reverse order");
    reverse.type = "button";
    reverse.addEventListener("click", () => {
      remotes = [...remotes].reverse();
      sortMode = "manual";
      sortReverse = false;
      persistRemoteOrder();
      popup.remove();
      renderRemoteList();
    });
    popup.append(reverse);
    header.append(popup);
  });
  const notifications = actionIcon("♧", "Notifications");
  notifications.classList.add("notification-button");
  notifications.addEventListener("click", () => void showNotifications());
  const pin = actionIcon("⌖", "Keep window visible");
  pin.classList.toggle("active", windowPinned);
  if (currentHints && !currentHints.pinSupported) {
    pin.disabled = true;
    pin.title = "GNOME on Wayland does not allow apps to pin their own windows.";
  } else {
    pin.addEventListener("click", () => { windowPinned = !windowPinned; pin.classList.toggle("active", windowPinned); void setWindowPinned(windowPinned); });
  }
  toolbar.append(sort, notifications, pin);
  header.append(toolbar);
  queueMicrotask(() => void refreshNotificationIndicator());
  return header;
}

function showApplicationMenu(label: string, event: MouseEvent): void {
  const separators = { separator: true } as ContextAction;
  if (label === "App") showContextMenu(event, [
    { label: "Update status", run: () => void refreshRemoteStatus() }, separators,
    { label: "Sync cached files now", run: () => void synchronizeAllOffline() },
    { label: "Remove all offline files", run: () => void removeAllOffline() },
    { label: "Clear all resolved cache", run: () => void clearAllCache() },
    { label: "Debug cache sync", run: () => void showCacheSyncDiagnostics() },
    { label: "Report bug", run: () => void reportBug() }, separators,
    { label: "License", run: () => void showLicense() }, { label: "About Mountlet", run: () => void showAbout() }, separators,
    { label: "Quit", run: () => void quitApp() },
  ]);
  else if (label === "Mount") showContextMenu(event, [
    { label: "Mount all", enabled: remotes.length > 0, run: () => void setAllMounted(true) },
    { label: "Unmount all", enabled: remotes.length > 0, run: () => void setAllMounted(false) }, separators,
    { label: "Add remote", run: () => void showAddRemote() },
  ]);
  else showContextMenu(event, [
    { label: "Keyboard shortcuts", run: () => void showShortcuts() }, separators,
    { label: "Export config bundle", run: () => void exportBundle() }, { label: "Import config bundle", run: () => void importBundle() },
    { label: "Open config backup folder", run: () => void openConfigBackupFolder() }, separators,
    { label: "Set config sync location", run: () => void showAppSettings("Config Sync") }, { label: "Push config to sync location", run: () => void syncConfiguration("push") },
    { label: "Pull config from sync location", run: () => void syncConfiguration("pull") }, separators,
    { label: "Open rclone config file", run: () => void openConfigFile("rclone") },
    { label: "Open App config file", run: () => void openConfigFile("app") },
    { label: "Open Mounts config file", run: () => void openConfigFile("mounts") },
  ]);
}

async function showShortcuts(): Promise<void> {
  const settings = await loadAppSettings();
  openShortcutDialog(settings, async next => {
    completeSettings = await saveAppSettings(next);
    shortcuts = resolveShortcutSettings(completeSettings.shortcuts);
  });
}

async function refreshRemoteStatus(): Promise<void> {
  remotes = await listRemotes();
  renderRemoteList();
  if (!isBrowserWindow) void refreshNativeTrayMenu();
}

async function setAllMounted(mounted: boolean): Promise<void> {
  for (const remote of remotes) {
    if (remote.mounted !== mounted) await changeRemoteMount(remote.id);
  }
}

async function removeAllOffline(): Promise<void> {
  if (!await confirmOwned("Remove offline files?", "Remove all files explicitly saved for offline use?", "Remove")) return;
  await removeOfflineCopies();
  await refreshFolder();
}

async function clearAllCache(): Promise<void> {
  if (!await confirmOwned("Clear cache?", "Remove all temporary resolved cache while preserving files saved for offline use?", "Clear")) return;
  await clearResolvedCache();
  await refreshFolder();
}

function showToast(message: string): void {
  document.querySelector(".toast")?.remove();
  const toast = element("div", "toast", message);
  toast.setAttribute("role", "status");
  document.body.append(toast);
  window.setTimeout(() => toast.remove(), 5000);
}

function queueUsageRefresh(remoteId: string): void {
  usageRefreshQueue.add(remoteId);
  if (usageRefreshTimer) return;
  usageRefreshTimer = window.setTimeout(async function drain() {
    usageRefreshTimer = 0;
    if (pendingMounts.size || browserLoading) { usageRefreshTimer = window.setTimeout(drain, 500); return; }
    const next = usageRefreshQueue.values().next().value as string | undefined;
    if (!next) return;
    usageRefreshQueue.delete(next);
    try {
      const updated = await refreshRemoteUsage(next);
      remotes = remotes.map(remote => remote.id === next ? updated : remote);
      renderRemoteList();
    } catch { /* Usage is optional and stale cached data remains preferable. */ }
    if (usageRefreshQueue.size) usageRefreshTimer = window.setTimeout(drain, 300);
  }, 1200);
}

function scheduleFolderPoll(): void {
  window.clearTimeout(folderPollTimer);
  if (!isBrowserWindow && preferences.mode === "multiple") return;
  const seconds = completeSettings?.remoteCheckInterval ?? 30;
  if (seconds <= 0 || !selectedRemote) return;
  const remoteId = selectedRemote; const path = currentPath;
  folderPollTimer = window.setTimeout(async () => {
    if (selectedRemote !== remoteId || currentPath !== path || browserLoading) { scheduleFolderPoll(); return; }
    await invalidateFolder(remoteId, path);
    void listFolder(remoteId, path).finally(() => { if (selectedRemote === remoteId && currentPath === path) scheduleFolderPoll(); });
  }, Math.max(1, seconds) * 1000);
}

async function runRemoteConfigWizard(remoteId: string): Promise<boolean> {
  let state = "";
  let result = "";
  for (let count = 0; count < 64; count += 1) {
    const step = await configWizardStep(remoteId, state, result);
    if (step.error) throw new Error(step.error);
    if (!step.state) return true;
    state = step.state;
    const option = step.option;
    const name = String(option.Name ?? "");
    const type = String(option.Type ?? "").toLocaleLowerCase();
    const help = String(option.Help ?? "").trim();
    const defaultValue = String(option.DefaultStr ?? option.Default ?? step.result ?? "");
    const searchText = `${name} ${help}`.toLocaleLowerCase();
    if (name === "config_is_local") result = "true";
    else if (["config_edit_advanced", "edit_advanced"].includes(name)) result = "false";
    else if (type === "bool" && (name.toLocaleLowerCase().includes("warning") || searchText.startsWith("warning") || searchText.includes(" important "))) result = "true";
    else {
      const examples = Array.isArray(option.Examples) ? option.Examples as Array<Record<string, unknown>> : [];
      const choices = type === "bool" ? [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] : option.Exclusive
        ? examples.map(example => ({ value: String(example.Value ?? ""), label: String(example.Help ?? example.Value ?? "").trim().split("\n")[0] })) : [];
      const answer = await promptWizardOption(name.replace(/^config_/, "").replaceAll("_", " ") || "Remote configuration", help || "Enter the value requested by rclone.", defaultValue, Boolean(option.IsPassword), choices);
      if (answer === null) return false;
      result = answer;
    }
  }
  throw new Error("rclone returned too many configuration questions");
}

async function showRcloneOutput(): Promise<void> {
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer"); const dialog = element("section", "modal-dialog output-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.append(element("h2", "", "rclone output"));
  const output = element("pre", "rclone-output", await rcloneOutput()); output.tabIndex = 0; dialog.append(output);
  const actions = element("div", "dialog-actions"); const copy = element("button", "", "Copy"); const refresh = element("button", "", "Refresh"); const close = element("button", "primary", "Close");
  copy.addEventListener("click", () => void navigator.clipboard.writeText(output.textContent ?? ""));
  refresh.addEventListener("click", async () => { output.textContent = await rcloneOutput(); output.scrollTop = output.scrollHeight; });
  close.addEventListener("click", () => layer.remove()); actions.append(copy, refresh, close); dialog.append(actions); layer.append(dialog); document.body.append(layer);
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  trapModalFocus(layer, dialog, close);
}

async function showCacheSyncDiagnostics(): Promise<void> {
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer"); const dialog = element("section", "modal-dialog output-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.append(element("h2", "", "Cache sync diagnostics"));
  const output = element("pre", "rclone-output", "Collecting diagnostics…"); output.tabIndex = 0; dialog.append(output);
  const actions = element("div", "dialog-actions"); const copy = element("button", "", "Copy"); const close = element("button", "primary", "Close");
  copy.addEventListener("click", () => void navigator.clipboard.writeText(output.textContent ?? "")); close.addEventListener("click", () => layer.remove());
  actions.append(copy, close); dialog.append(actions); layer.append(dialog); document.body.append(layer);
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  trapModalFocus(layer, dialog, close);
  try { output.textContent = await cacheSyncDiagnostics(); } catch (error) { output.textContent = String(error); }
}

async function showAbout(): Promise<void> {
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer"); const dialog = element("section", "modal-dialog about-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.append(element("h2", "", "About Mountlet"));
  const version = await appVersion(); dialog.append(element("p", "about-product", "Mountlet"), element("p", "about-version", `Version ${version}`), element("p", "", "Cloud storage access from your desktop."));
  const actions = element("div", "dialog-actions");
  const website = element("button", "", "Website"); website.addEventListener("click", () => void openExternal("https://mountlet.app"));
  const close = element("button", "primary", "Close"); close.addEventListener("click", () => layer.remove());
  actions.append(website, close); dialog.append(actions); layer.append(dialog); document.body.append(layer);
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  trapModalFocus(layer, dialog, close);
}

async function reportBug(crash = ""): Promise<void> {
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer"); const dialog = element("section", "modal-dialog report-dialog");
  dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
  const kind = crash ? "crash" : "bug"; dialog.append(element("h2", "", crash ? "Crash report" : "Report bug"));
  dialog.append(element("p", "", "Review the report before sending. Redacted diagnostics can still include file paths, remote names, and filenames."));
  const fields = element("div", "report-fields");
  const contactLabel = element("label", "dialog-field"); contactLabel.append(element("span", "", "Contact email (optional)")); const contact = element("input", "") as HTMLInputElement; contact.type = "email"; contact.autocomplete = "email"; contactLabel.append(contact);
  const messageLabel = element("label", "dialog-field"); messageLabel.append(element("span", "", "What happened?")); const message = element("textarea", "report-message") as HTMLTextAreaElement; message.placeholder = "Describe what you expected, what happened, and how to reproduce it."; messageLabel.append(message);
  if (crash) message.value = "Mountlet closed unexpectedly. Please describe what you were doing.";
  const logsLabel = element("label", "report-logs"); const logs = element("input", "") as HTMLInputElement; logs.type = "checkbox"; logs.checked = true; logsLabel.append(logs, document.createTextNode(" Include recent diagnostics and rclone output"));
  fields.append(contactLabel, messageLabel, logsLabel);
  const previewLabel = element("label", "dialog-field report-preview-field"); previewLabel.append(element("span", "", "Report preview")); const preview = element("pre", "rclone-output", "Preparing report…"); preview.tabIndex = 0; previewLabel.append(preview);
  const status = element("p", "dialog-status"); const actions = element("div", "dialog-actions");
  const send = element("button", "primary", "Send"); send.addEventListener("click", async () => { send.disabled = true; status.textContent = "Sending report…"; try { const url = await bounded(submitBugReport(kind, message.value, contact.value, logs.checked, crash), 20000, "Sending the report timed out. Please try again."); status.textContent = url ? `Report sent: ${url}` : "Report sent. Thank you."; if (url) { const open = element("button", "", "Open report"); open.addEventListener("click", () => void openExternal(url)); actions.prepend(open); } } catch (error) { status.textContent = String(error); send.disabled = false; } });
  const close = element("button", "", "Close"); close.addEventListener("click", () => layer.remove());
  actions.append(send, close); dialog.append(fields, previewLabel, status, actions); layer.append(dialog); document.body.append(layer);
  let previewGeneration = 0; let previewTimer = 0;
  const updatePreview = () => { const generation = ++previewGeneration; window.clearTimeout(previewTimer); previewTimer = window.setTimeout(async () => { try { const text = await bounded(bugReportPreview(kind, message.value, contact.value, logs.checked, crash), 8000, "Report preview timed out."); if (generation === previewGeneration) preview.textContent = text; } catch (error) { if (generation === previewGeneration) preview.textContent = `Could not prepare report preview: ${String(error)}`; } }, 100); };
  message.addEventListener("input", updatePreview); contact.addEventListener("input", updatePreview); logs.addEventListener("change", updatePreview); updatePreview();
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  trapModalFocus(layer, dialog, close);
}

async function maybePromptCrashReport(): Promise<void> {
  const crash = await unreportedCrash().catch(() => "");
  if (!crash || licenseLocked()) return;
  const choice = await promptWizardOption(
    "Mountlet closed unexpectedly",
    "Mountlet found a crash log from the previous run. You can keep using the app or review and send it.",
    "later", false,
    [{ value: "review", label: "Review report" }, { value: "ignore", label: "Ignore this crash" }, { value: "later", label: "Later" }],
  );
  if (choice === "review") await reportBug(crash);
  else if (choice === "ignore") await markCrashReported(crash);
}

function persistRemoteOrder(): void {
  const order = remotes.map(remote => remote.id);
  localStorage.setItem("mountlet-remote-order", JSON.stringify(order));
  void reorderRemotes(order);
}

async function showAppSettings(initialPage?: string): Promise<void> {
  completeSettings = await loadAppSettings();
  const managers = await listFileManagers().catch(() => []);
  openAppSettingsDialog({ ...completeSettings, zoomSteps: preferences.zoomStep }, remotes, async next => {
    const previousMode = preferences.mode;
    completeSettings = await saveAppSettings(next);
    preferences.mode = completeSettings.windowMode;
    preferences.theme = completeSettings.theme;
    preferences.zoomStep = completeSettings.zoomSteps;
    preferences.integratedFileEdits = completeSettings.integratedFileEdits;
    preferences.fileListMaxItems = completeSettings.fileListMaxItems;
    shortcuts = resolveShortcutSettings(completeSettings.shortcuts);
    applyPreferences();
    if (previousMode !== preferences.mode) await setDetachedBrowser(preferences.mode === "multiple");
    render();
    if (preferences.mode === "multiple" && selectedRemote) await emitBrowserState(selectedRemote, currentPath);
    await emitUiPreferences(preferences);
    scheduleNativeLayout(0);
  }, { fileManagers: managers, wayland: Boolean(currentHints?.wayland), initialPage });
}

function driveUsageNote(remote: Remote): HTMLElement | null {
  if (remote.provider !== "drive") return null;
  const note = element("span", "usage-note", "ⓘ");
  note.title = "Google Drive usage excludes Photos and other Google account data.";
  note.setAttribute("aria-label", note.title);
  note.classList.add("row-control");
  note.addEventListener("click", event => event.stopPropagation());
  return note;
}

function usage(remote: Remote): HTMLElement {
  const box = element("div", "usage");
  const note = driveUsageNote(remote);
  if (remote.usedBytes == null || remote.totalBytes == null) {
    const info = element("span", "usage-info", "ⓘ");
    info.title = "This provider does not expose storage usage information.";
    const line = element("span", "usage-line");
    line.append(info);
    if (note) line.append(note);
    box.append(line);
    return box;
  }
  const ratio = Math.min(1, remote.usedBytes / remote.totalBytes);
  const meter = element("div", "usage-meter");
  const fill = element("span", "usage-fill");
  fill.style.width = `${ratio * 100}%`;
  meter.append(fill);
  const line = element("span", "usage-line");
  line.append(element("span", "usage-text", `${formatBytes(remote.usedBytes)}/${formatBytes(remote.totalBytes)}`));
  if (note) line.append(note);
  box.append(meter, line);
  return box;
}

function remoteCard(remote: Remote): HTMLElement {
  const card = element("button", `remote-card${remote.id === selectedRemote ? " selected" : ""}`);
  card.type = "button";
  card.disabled = licenseLocked();
  card.dataset.remoteId = remote.id;
  card.setAttribute("aria-current", remote.id === selectedRemote ? "true" : "false");
  card.draggable = sortMode === "manual" && !sortReverse;
  const state = element("img", `mount-state${remote.mounted ? " mounted" : ""}${pendingMounts.has(remote.id) ? " working" : ""}${remoteErrors.has(remote.id) ? " error" : ""}`) as HTMLImageElement;
  state.src = remote.mounted ? "/assets/status-mounted.svg" : "/assets/status-cloud.svg";
  state.alt = pendingMounts.has(remote.id) ? "Working" : remoteErrors.has(remote.id) ? "Error" : remote.mounted ? "Mounted" : "Cloud only";
  state.classList.add("row-control");
  state.title = remoteErrors.get(remote.id) ?? (pendingMounts.has(remote.id) ? `${remote.name} (${remote.providerLabel}) is working…` : remote.mounted
    ? `${remote.name} (${remote.providerLabel}) is mounted. Click to open in the system file manager.`
    : `${remote.name} (${remote.providerLabel}) is available in the cloud.`);
  if (remote.mounted) state.addEventListener("click", event => { event.stopPropagation(); void openMountedFolder(remote.id, ""); });
  const provider = providerIcon(remote.provider, remote.providerLabel);
  provider.classList.add("row-control");
  provider.title = `Open ${remote.providerLabel} in web`;
  provider.addEventListener("click", event => { event.stopPropagation(); void openRemoteWeb(remote.id); });
  const titleGroup = element("span", "remote-title-group");
  titleGroup.append(provider, element("span", "remote-name", remote.name));
  card.append(state, titleGroup, usage(remote));
  card.title = `Double-click to configure ${remote.name} (${remote.providerLabel}). Drag to move this remote.`;
  card.addEventListener("dblclick", event => {
    if ((event.target as HTMLElement).closest(".row-control")) return;
    void showRemoteConfig(remote);
  });
  card.addEventListener("contextmenu", event => {
    selectedRemote = remote.id;
    renderRemoteList();
    const index = visibleRemotes().findIndex(candidate => candidate.id === remote.id);
    showContextMenu(event, [
      { label: remote.mounted ? "Unmount" : "Mount", run: () => void changeRemoteMount(remote.id) },
      { label: "Open in file manager", enabled: remote.mounted, run: () => void openMountedFolder(remote.id, "") },
      { label: "Open in web", run: () => void openRemoteWeb(remote.id) },
      { separator: true },
      { label: "Config", run: () => void showRemoteConfig(remote) },
      { label: "Move up", enabled: sortMode === "manual" && !sortReverse && index > 0, run: () => moveSelectedRemote(-1) },
      { label: "Move down", enabled: sortMode === "manual" && !sortReverse && index >= 0 && index < visibleRemotes().length - 1, run: () => moveSelectedRemote(1) },
      { separator: true },
      { label: "Reauthenticate", run: () => void runRemoteConfigWizard(remote.id).catch(error => showError("Reauthenticate remote", error)) },
    ]);
  });
  card.addEventListener("pointerenter", () => {
    if (!remoteHoverArmed || remote.id === selectedRemote) return;
    void selectRemote(remote.id);
  });
  card.addEventListener("focus", () => void selectRemote(remote.id));
  card.addEventListener("dragstart", event => {
    draggedRemote = remote.id;
    event.dataTransfer?.setData("text/plain", remote.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  });
  card.addEventListener("dragover", event => {
    if (!draggedRemote || draggedRemote === remote.id) return;
    event.preventDefault();
    card.classList.add("drop-target");
    card.title = `Move ${remotes.find(candidate => candidate.id === draggedRemote)?.name ?? "remote"} here`;
  });
  card.addEventListener("dragleave", () => card.classList.remove("drop-target"));
  card.addEventListener("drop", event => {
    event.preventDefault();
    card.classList.remove("drop-target");
    const source = draggedRemote || event.dataTransfer?.getData("text/plain") || "";
    const from = remotes.findIndex(candidate => candidate.id === source);
    const to = remotes.findIndex(candidate => candidate.id === remote.id);
    if (from >= 0 && to >= 0 && from !== to) {
      const reordered = [...remotes];
      const [moved] = reordered.splice(from, 1);
      reordered.splice(to, 0, moved);
      remotes = reordered;
      persistRemoteOrder();
      renderRemoteList();
      scheduleNativeLayout(0);
    }
    draggedRemote = "";
  });
  card.addEventListener("dragend", () => { draggedRemote = ""; card.classList.remove("drop-target"); });
  return card;
}

async function changeRemoteMount(remoteId: string): Promise<void> {
  if (licenseLocked()) { void showLicense(); return; }
  if (pendingMounts.has(remoteId)) return;
  pendingMounts.add(remoteId);
  remoteErrors.delete(remoteId);
  renderRemoteList();
  let retryAfterAuthentication = false;
  try {
    const mounted = await toggleRemoteMount(remoteId);
    remotes = remotes.map(remote => remote.id === remoteId ? { ...remote, mounted } : remote);
    renderRemoteList();
    void refreshNativeTrayMenu();
    if (selectedRemote === remoteId) renderBrowserOnly();
  } catch (error) {
    const detail = String(error);
    remoteErrors.set(remoteId, detail);
    const authenticationFailure = /invalid global session|missing x-apple-webauth-token|unauthori[sz]ed|invalid.?grant|token.*(expired|invalid)|authentication|login required|2fa|verification code/i.test(detail);
    const remote = remotes.find(candidate => candidate.id === remoteId);
    if (authenticationFailure && remote && !pendingReauthentication.has(remoteId)
      && await confirmOwned("Reauthentication required", `${remote.name} (${remote.providerLabel}) needs to be authenticated again. Start authentication now?`, "Authenticate")) {
      pendingReauthentication.add(remoteId);
      try { retryAfterAuthentication = await runRemoteConfigWizard(remoteId); }
      catch (authError) { await showError("Reauthenticate remote", authError); }
      finally { pendingReauthentication.delete(remoteId); }
    } else if (!authenticationFailure) await showError("Mount remote", error);
  } finally {
    pendingMounts.delete(remoteId);
    renderRemoteList();
  }
  if (retryAfterAuthentication) void changeRemoteMount(remoteId);
}

const REMOTE_FIELD_LABELS: Record<string, string> = {
  description: "Description", mountlet_google_account: "Google account", client_id: "Client ID",
  client_secret: "Client secret", shared_with_me: "Shared with me", root_folder_id: "Root folder ID",
  team_drive: "Shared drive ID", scope: "Access scope", read_only: "Read only", read_size: "Read sizes",
  include_archived: "Include archived", start_year: "Start year", drive_type: "Drive type", region: "Region",
  drive_id: "Drive ID", url: "URL", vendor: "Vendor", user: "Username", pass: "Password",
  bearer_token: "Bearer token", provider: "Provider", endpoint: "Endpoint", env_auth: "Use environment credentials",
  access_key_id: "Access key ID", secret_access_key: "Secret access key", session_token: "Session token",
  storage_class: "Storage class", acl: "ACL", password: "Password", mountid: "Mount ID", username: "Username",
  "2fa": "2FA code", otp_secret_key: "OTP secret", mailbox_password: "Mailbox password",
  enable_caching: "Enable backend cache", service: "Service", apple_id: "Apple ID", trust_token: "Trust token",
  cookies: "Cookies",
};
const REMOTE_AUTH_FIELDS: Record<string, readonly string[]> = {
  drive: ["client_id", "client_secret", "scope"], gphotos: ["client_id", "client_secret", "read_only"],
  onedrive: ["region", "drive_id", "drive_type"], s3: ["provider", "region", "env_auth", "endpoint", "access_key_id", "secret_access_key", "session_token"],
  webdav: ["url", "vendor", "user", "pass", "bearer_token"], koofr: ["provider", "user", "password"],
  protondrive: ["username", "password", "2fa", "otp_secret_key", "mailbox_password"], iclouddrive: ["service", "apple_id", "password", "trust_token", "cookies"],
  mega: ["user", "pass", "2fa"],
};

function googleAccountValue(value: string): string {
  const account = value.trim();
  return account && !account.includes("@") ? `${account}@gmail.com` : account;
}

async function showRemoteConfig(remote: Remote): Promise<void> {
  let config;
  try { config = await loadRemoteConfig(remote.id); } catch (error) { await showError("Remote settings", error); return; }
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer");
  const dialog = element("section", "modal-dialog remote-config-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.append(element("h2", "", `${remote.name} (${remote.providerLabel}) settings`));
  const form = element("form", "remote-config-form");
  const controls = new Map<string, HTMLInputElement | HTMLSelectElement>();
  const addText = (key: string, label: string, value: string, password = false, tooltip = "") => {
    const input = document.createElement("input"); input.type = password ? "password" : "text"; input.value = value;
    if (password) input.placeholder = "Leave blank to keep the saved value";
    if (tooltip) { input.title = tooltip; }
    controls.set(key, input); form.append(element("label", "", label), input);
  };
  const addBoolean = (key: string, label: string, value: string) => {
    const input = document.createElement("input"); input.type = "checkbox";
    input.checked = ["1", "true", "yes", "on"].includes(value.trim().toLocaleLowerCase());
    controls.set(key, input); form.append(element("label", "", label), input);
  };
  const addSelect = (key: string, label: string, value: string, options: readonly (readonly [string, string])[]) => {
    const select = bindScaledSelect(document.createElement("select"));
    for (const [candidate, text] of options) select.add(new Option(text, candidate));
    if (value && !options.some(([candidate]) => candidate === value)) select.add(new Option(value, value));
    select.value = value; controls.set(key, select); form.append(element("label", "", label), select);
  };
  addText("alias", "Remote name", config.alias);
  addText("mountPath", "Local folder name", config.mountPath, false, "Folder under Mountlet's mounted folder. An absolute path is also accepted.");
  addText("remotePath", "Remote path", config.remotePath, false, "Optional folder within this remote to expose as its root.");
  const auto = bindScaledSelect(document.createElement("select"));
  [["", "Use app default"], ["true", "Enabled"], ["false", "Disabled"]].forEach(([value, label]) => auto.add(new Option(label, value)));
  auto.value = config.autoMount === null ? "" : String(config.autoMount); controls.set("autoMount", auto);
  form.append(element("label", "", "Auto-mount"), auto);
  const savedFlags = config.mountFlags.split(/\s+/).filter(Boolean);
  const readOnly = document.createElement("input"); readOnly.type = "checkbox"; readOnly.checked = savedFlags.includes("--read-only"); controls.set("readOnly", readOnly);
  const allowOther = document.createElement("input"); allowOther.type = "checkbox"; allowOther.checked = savedFlags.includes("--allow-other"); controls.set("allowOther", allowOther);
  form.append(element("label", "", "Read-only mount"), readOnly, element("label", "", "Allow other users"), allowOther);
  addText("mountFlags", "Custom mount flags", savedFlags.filter(flag => !["--read-only", "--allow-other"].includes(flag)).join(" "));
  Object.entries(config.fields).forEach(([key, value]) => {
    if (["shared_with_me", "env_auth", "enable_caching"].includes(key)) addBoolean(`field:${key}`, REMOTE_FIELD_LABELS[key] ?? key, value);
    else if (key === "scope") addSelect(`field:${key}`, "Access scope", value, [["drive", "Full Drive access"], ["drive.readonly", "Read-only Drive access"], ["drive.file", "Files created or opened by rclone"], ["drive.appfolder", "Rclone application data folder"], ["drive.metadata.readonly", "Read-only names and metadata"]]);
    else if (key === "drive_type") addSelect(`field:${key}`, "Drive type", value, [["personal", "Personal"], ["business", "Business"], ["documentLibrary", "SharePoint document library"]]);
    else addText(`field:${key}`, REMOTE_FIELD_LABELS[key] ?? key.replaceAll("_", " "), key === "mountlet_google_account" && value.toLocaleLowerCase().endsWith("@gmail.com") ? value.slice(0, -10) : value, config.secretFields.includes(key),
      key === "mountlet_google_account" ? "Used only to open Google Drive or Photos with the corresponding browser account. Enter the full address for Google Workspace accounts." : "");
  });
  dialog.append(form);
  const actions = element("div", "dialog-actions");
  const web = element("button", "", "Open in web");
  web.addEventListener("click", () => void openRemoteWeb(remote.id));
  const folder = element("button", "", "Open mounted folder");
  folder.disabled = !remote.mounted;
  folder.addEventListener("click", () => void openMountedFolder(remote.id, ""));
  const cancel = element("button", "", "Cancel");
  cancel.addEventListener("click", () => layer.remove());
  const remove = element("button", "danger", "Delete remote");
  remove.addEventListener("click", async () => {
    if (!await confirmOwned("Delete remote?", `Delete ${remote.name} (${remote.providerLabel}) from rclone and Mountlet?`, "Delete")) return;
    try {
      await deleteRemote(remote.id); layer.remove(); remotes = await listRemotes();
      selectedRemote = remotes[0]?.id ?? ""; currentPath = ""; snapshot = null; render();
      if (selectedRemote) await selectRemote(selectedRemote);
    } catch (error) { await showError("Delete remote", error); }
  });
  const close = element("button", "primary", "Save");
  close.disabled = true;
  const snapshotValues = () => JSON.stringify([...controls].map(([key, input]) => [key, input instanceof HTMLInputElement && input.type === "checkbox" ? input.checked : input.value]));
  const baseline = snapshotValues();
  const updateDirty = () => { close.disabled = snapshotValues() === baseline; };
  form.addEventListener("input", updateDirty);
  form.addEventListener("change", updateDirty);
  close.addEventListener("click", async event => {
    event.preventDefault(); close.disabled = true;
    const fields = Object.fromEntries([...controls].filter(([key]) => key.startsWith("field:")).map(([key, input]) => {
      const field = key.slice(6); const value = input instanceof HTMLInputElement && input.type === "checkbox" ? (input.checked ? "true" : "") : input.value;
      return [field, field === "mountlet_google_account" ? googleAccountValue(value) : value];
    }));
    try {
      const effectiveFlags = [controls.get("mountFlags")!.value.trim(), (controls.get("readOnly") as HTMLInputElement).checked ? "--read-only" : "", (controls.get("allowOther") as HTMLInputElement).checked ? "--allow-other" : ""].filter(Boolean).join(" ");
      const mountChanged = controls.get("alias")!.value.trim() !== config.alias || controls.get("mountPath")!.value.trim() !== config.mountPath
        || controls.get("remotePath")!.value.trim().replace(/^\/+|\/+$/g, "") !== config.remotePath || effectiveFlags.split(/\s+/).sort().join(" ") !== config.mountFlags.split(/\s+/).filter(Boolean).sort().join(" ");
      const authFields = new Set(REMOTE_AUTH_FIELDS[config.provider] ?? []);
      const authChanged = Object.entries(fields).some(([key, value]) => authFields.has(key)
        && (config.secretFields.includes(key) ? Boolean(value && value !== "••••••") : ["", "false"].includes(value.trim().toLocaleLowerCase()) && ["", "false"].includes((config.fields[key] ?? "").trim().toLocaleLowerCase()) ? false : value.trim() !== (config.fields[key] ?? "").trim()));
      if (authChanged && !await confirmOwned("Authentication changed", "The authentication settings changed. Reauthenticate this remote after saving?", "Save and reauthenticate")) {
        close.disabled = false; return;
      }
      const remount = remote.mounted && (mountChanged || authChanged);
      if (remount && !await confirmOwned("Remount remote?", "Mount settings changed. Unmount this remote, save the settings, and mount it again?", "Remount")) {
        close.disabled = false; return;
      }
      if (remount) await toggleRemoteMount(remote.id);
      const newId = await saveRemoteConfig({ remoteId: config.id, alias: controls.get("alias")!.value, mountPath: controls.get("mountPath")!.value,
        remotePath: controls.get("remotePath")!.value, mountFlags: effectiveFlags,
        autoMount: controls.get("autoMount")!.value === "" ? null : controls.get("autoMount")!.value === "true", fields });
      if (authChanged) await runRemoteConfigWizard(newId);
      if (remount) await toggleRemoteMount(newId);
      remotes = await listRemotes(); selectedRemote = newId; layer.remove(); renderRemoteList(); await selectRemote(newId);
    } catch (error) { await showError("Remote settings", error); close.disabled = false; }
  });
  actions.append(remove, web, folder, cancel, close);
  dialog.append(actions);
  layer.append(dialog);
  layer.addEventListener("mousedown", event => { if (event.target === layer) layer.remove(); });
  layer.addEventListener("keydown", event => { if (event.key === "Escape") layer.remove(); });
  document.body.append(layer);
  trapModalFocus(layer, dialog, cancel);
}

const OAUTH_REMOTE_TYPES = new Set(["drive", "gphotos", "dropbox", "onedrive", "box", "pcloud"]);

async function showAddRemote(): Promise<void> {
  const result = await openAddRemoteDialog();
  if (!result) return;
  remotes = await listRemotes();
  if (result === "external") {
    render();
    return;
  }
  selectedRemote = result.remoteId;
  render();
  const created = remotes.find(remote => remote.id === result.remoteId);
  if (OAUTH_REMOTE_TYPES.has(result.provider)) {
    await runRemoteConfigWizard(result.remoteId).catch(error => showError("Authenticate remote", error));
  } else if (created) {
    await showRemoteConfig(created);
  }
  remotes = await listRemotes();
  render();
  if (remotes.some(remote => remote.id === result.remoteId)) await selectRemote(result.remoteId);
  if (result.mountAfter) await changeRemoteMount(result.remoteId);
}

function selectAdjacentRemote(delta: number): void {
  suppressRemoteHover();
  const visible = visibleRemotes();
  if (!visible.length) return;
  const current = visible.findIndex(remote => remote.id === selectedRemote);
  const index = Math.min(visible.length - 1, Math.max(0, (current < 0 ? 0 : current) + delta));
  const card = document.querySelector<HTMLElement>(`.remote-card[data-remote-id="${CSS.escape(visible[index].id)}"]`);
  card?.focus({ preventScroll: true });
  card?.scrollIntoView({ block: "nearest" });
}

function moveSelectedRemote(delta: number): void {
  if (sortMode !== "manual" || sortReverse) return;
  const from = remotes.findIndex(remote => remote.id === selectedRemote);
  const to = Math.min(remotes.length - 1, Math.max(0, from + delta));
  if (from < 0 || from === to) return;
  const reordered = [...remotes];
  const [moved] = reordered.splice(from, 1);
  reordered.splice(to, 0, moved);
  remotes = reordered;
  persistRemoteOrder();
  renderGlobalResults();
  document.querySelector<HTMLElement>(`.remote-card[data-remote-id="${CSS.escape(selectedRemote)}"]`)?.focus({ preventScroll: true });
  scheduleNativeLayout(0);
}

function focusRemoteList(): void {
  if (isBrowserWindow) {
    void focusNativeWindow("main");
    return;
  }
  suppressRemoteHover();
  document.querySelector<HTMLElement>(`.remote-card[data-remote-id="${CSS.escape(selectedRemote)}"]`)?.focus({ preventScroll: true });
}

function focusBrowserList(): void {
  suppressRemoteHover();
  if (preferences.mode === "multiple" && !isBrowserWindow) {
    void focusNativeWindow("browser");
    return;
  }
  fileList?.focus();
}

function restoreFocusOwner(): void {
  if (document.querySelector(".modal-layer")) return;
  if (parkedFocusOwner) {
    lastFocusOwner = parkedFocusOwner;
    parkedFocusOwner = null;
  }
  if (lastFocusOwner === "remote-search") { focusSearchField("remote"); return; }
  if (lastFocusOwner === "global-search") { focusSearchField("global"); return; }
  if (isBrowserWindow || lastFocusOwner === "browser") fileList?.focus();
  else focusRemoteList();
}

function scheduleFocusRestore(forceMain = false): void {
  if (forceMain && !isBrowserWindow) {
    lastFocusOwner = "main";
    parkedFocusOwner = "main";
  }
  restoreFocusOwner();
  syncFocusChrome();
}

function renderRemotePane(): HTMLElement {
  const pane = element("section", "remote-pane");
  if (currentLicense && currentLicense.state !== "licensed") {
    const purchase = element("div", "purchase-row"); const button = element("button", "primary purchase-button", "Buy license");
    button.title = "Open the Mountlet license purchase page."; button.addEventListener("click", () => void openExternal("https://mountlet.app/#pricing")); purchase.append(button); pane.append(purchase);
  }
  pane.append(renderToolbar());
  const searchRow = element("div", "search-row");
  const search = element("input", "search-input") as HTMLInputElement;
  search.type = "search";
  search.placeholder = "Search all remotes";
  search.value = remoteFilter;
  search.addEventListener("input", () => {
    const wasActive = Boolean(remoteFilter.trim());
    remoteFilter = search.value;
    globalSearchLoading = Boolean(remoteFilter.trim());
    renderRemoteList();
    renderGlobalResults();
    scheduleSearch("global");
    if (wasActive !== Boolean(remoteFilter.trim())) scheduleNativeLayout(0);
  });
  search.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") {
      if (globalSearchHasResults()) moveGlobalSearchSelection(1);
      else selectAdjacentRemote(1);
      event.preventDefault();
    }
    if (event.key === "ArrowUp") {
      if (globalSearchHasResults()) moveGlobalSearchSelection(-1);
      else selectAdjacentRemote(-1);
      event.preventDefault();
    }
    if (event.key === "Enter") {
      const result = globalSearchHasResults() ? globalSearchResults[Math.min(globalSearchSelected, 79)] : undefined;
      if (result) void openSearchResult(result);
      event.preventDefault();
    }
    if (event.key === "Escape") {
      focusRemoteList();
      event.preventDefault();
    }
  });
  const searchIcon = chromeIcon("ui-search.svg", "search-symbol");
  const searchStatus = element("span", "global-search-status");
  searchRow.append(searchIcon, search, searchStatus);
  pane.append(searchRow);
  if (licenseLocked()) pane.append(element("div", "license-lock-banner", currentLicense?.summary || "A license is required to continue."));
  const results = element("div", "global-results");
  results.id = "global-results";
  pane.append(results);
  const list = element("div", "remote-list");
  list.id = "remote-list";
  list.setAttribute("aria-label", "Configured remotes");
  pane.append(list);
  const footer = element("footer", "main-footer");
  footer.id = "main-footer";
  pane.append(footer);
  queueMicrotask(() => { renderRemoteList(); renderGlobalResults(); renderFooter(); });
  return pane;
}

function renderRemoteList(): void {
  const list = document.querySelector<HTMLDivElement>("#remote-list");
  if (!list) return;
  list.replaceChildren(...visibleRemotes().map(remoteCard));
}

function renderGlobalResults(): void {
  const list = document.querySelector<HTMLDivElement>("#global-results");
  const status = document.querySelector<HTMLElement>(".global-search-status");
  if (!list || !status) return;
  const query = remoteFilter.trim();
  list.classList.toggle("active", Boolean(query));
  if (!query) { list.replaceChildren(); status.textContent = ""; return; }
  const header = element("div", "search-result-header");
  header.append(element("span", "", "Name"), element("span", "", "Remote"));
  if (globalSearchLoading) {
    list.replaceChildren(header);
    status.textContent = "Searching…";
    return;
  }
  const capped = globalSearchResults.length > 80;
  const rows: HTMLElement[] = globalSearchResults.slice(0, 80).map((result, index) => {
    const remote = remotes.find(candidate => candidate.id === result.remoteId);
    const row = element("button", `search-result quality-${result.quality}${index === globalSearchSelected ? " selected" : ""}`);
    row.type = "button";
    row.tabIndex = -1;
    row.dataset.resultIndex = String(index);
    const text = element("span", "search-result-text");
    text.append(element("span", "", result.name));
    const remoteCell = element("span", "search-result-remote");
    if (remote) remoteCell.append(providerIcon(remote.provider, remote.providerLabel));
    remoteCell.append(element("span", "", remote?.name || result.remoteDisplay));
    row.append(text, remoteCell);
    row.addEventListener("pointerenter", () => {
      if (!remoteHoverArmed) return;
      setGlobalSearchSelection(index);
      void previewGlobalSearchResult(result);
    });
    row.addEventListener("focus", () => {
      setGlobalSearchSelection(index);
      void previewGlobalSearchResult(result);
    });
    row.addEventListener("click", () => void openSearchResult(result));
    return row;
  });
  list.replaceChildren(header, ...rows);
  const count = Math.min(80, globalSearchResults.length);
  status.textContent = `${count}${capped ? "+" : ""} result${count === 1 ? "" : "s"}`;
}

function visibleRemotes(): readonly Remote[] {
  return remotes;
}

function moveGlobalSearchSelection(delta: number): void {
  const count = Math.min(80, globalSearchResults.length);
  if (!count) return;
  setGlobalSearchSelection(Math.min(count - 1, Math.max(0, globalSearchSelected + delta)));
  const result = globalSearchResults[globalSearchSelected];
  if (result) void previewGlobalSearchResult(result);
}

function setGlobalSearchSelection(index: number): void {
  globalSearchSelected = index;
  document.querySelectorAll<HTMLElement>(".search-result").forEach(row => {
    row.classList.toggle("selected", Number(row.dataset.resultIndex) === index);
  });
  document.querySelector<HTMLElement>(`.search-result[data-result-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
}

async function revealSearchResult(result: SearchEntry, focusBrowser: boolean): Promise<void> {
  const generation = ++globalPreviewGeneration;
  selectedRemote = result.remoteId;
  currentPath = result.parentPath;
  localStorage.setItem(`mountlet-path:${result.remoteId}`, result.parentPath);
  localStorage.setItem(`mountlet-selection:${result.remoteId}:${result.parentPath}`, result.path);
  localStorage.removeItem(`mountlet-selection-index:${result.remoteId}:${result.parentPath}`);
  await persistBrowserFolder(result.remoteId, result.parentPath, result.path, 0);
  snapshot = folderSnapshots.get(`${result.remoteId}:${result.parentPath}`) ?? null;
  browserLoading = !snapshot;
  browserError = "";
  renderRemoteList();
  if (!snapshot) await loadSnapshot();
  if (generation !== globalPreviewGeneration) return;
  applyBrowserFolderView({ keepFocus: focusBrowser, reveal: true });
  if (focusBrowser) suppressRemoteHover();
  if (isBrowserWindow) {
    await rememberBrowserState(selectedRemote, currentPath);
    if (focusBrowser) fileList?.focus();
  } else if (preferences.mode === "multiple") {
    await emitBrowserState(selectedRemote, currentPath, focusBrowser);
    if (focusBrowser) await focusNativeWindow("browser");
  } else if (focusBrowser) {
    fileList?.focus();
  }
  scheduleNativeLayout(0);
}

async function previewGlobalSearchResult(result: SearchEntry): Promise<void> {
  await revealSearchResult(result, false);
}

function scheduleSearch(scope: "global" | "remote"): void {
  window.clearTimeout(searchTimers[scope]);
  const query = scope === "global" ? remoteFilter.trim() : fileFilter.trim();
  if (!query) {
    searchGenerations[scope] += 1;
    if (scope === "global") { globalSearchResults = []; globalSearchLoading = false; renderGlobalResults(); }
    else { remoteSearchResults = []; remoteSearchLoading = false; renderRemoteSearchResults(); }
    return;
  }
  const generation = ++searchGenerations[scope];
  searchTimers[scope] = window.setTimeout(async () => {
    try {
      const results = await searchIndex(query, scope === "remote" ? selectedRemote : null, scope === "global" ? 80 : 50);
      if (generation !== searchGenerations[scope]) return;
      if (scope === "global") { globalSearchResults = results; globalSearchSelected = 0; globalSearchLoading = false; renderGlobalResults(); }
      else { remoteSearchResults = results; remoteSearchSelected = 0; remoteSearchLoading = false; renderRemoteSearchResults(); }
    } catch {
      if (generation !== searchGenerations[scope]) return;
      if (scope === "global") { globalSearchResults = []; globalSearchLoading = false; renderGlobalResults(); }
      else { remoteSearchResults = []; remoteSearchLoading = false; renderRemoteSearchResults(); }
    }
  }, 120);
}

function renderFooter(): void {
  const footer = document.querySelector<HTMLElement>("#main-footer");
  if (!footer) return;
  const add = actionIcon("＋", "Add remote");
  add.addEventListener("click", () => void showAddRemote());
  const zoom = element("div", "zoom-controls");
  const minus = actionIcon("−", "Zoom out");
  minus.addEventListener("click", () => setZoom(-1));
  const label = element("span", "zoom-label", `${Math.round(zoomFactor(preferences.zoomStep) * 100)}%`);
  const plus = actionIcon("＋", "Zoom in");
  plus.addEventListener("click", () => setZoom(1));
  const reset = element("button", "zoom-reset", "Reset");
  reset.addEventListener("click", resetZoom);
  zoom.append(minus, label, plus, reset);
  footer.replaceChildren(add, element("span", "build-label", "Local source"), zoom);
}

class VirtualFileList {
  private readonly viewport: HTMLElement;
  private readonly canvas: HTMLElement;
  private entries: readonly FileEntry[] = [];
  private selected = 0;
  private selectedIndexes = new Set<number>();
  private anchor = 0;
  private rowHeight = 36;
  private renamingPath = "";

  constructor(viewport: HTMLElement) {
    this.viewport = viewport;
    this.canvas = element("div", "file-canvas");
    viewport.append(this.canvas);
    viewport.addEventListener("scroll", () => this.paint());
    new ResizeObserver(() => this.paint()).observe(viewport);
  }

  setEntries(entries: readonly FileEntry[], selectedIndex = 0, selectedPath = "", reveal = false): void {
    this.entries = entries;
    let selected = Math.min(Math.max(selectedIndex, 0), Math.max(0, entries.length - 1));
    if (selectedPath && entries[selected]?.path !== selectedPath) selected = entries.findIndex(entry => entry.path === selectedPath);
    this.selected = Math.min(Math.max(selected, 0), Math.max(0, entries.length - 1));
    this.selectedIndexes = entries.length ? new Set([this.selected]) : new Set();
    this.anchor = this.selected;
    if (reveal) this.revealSelection();
    this.paint();
  }

  select(index: number, extend = false, toggle = false): void {
    if (!snapshot || !this.entries.length) return;
    this.selected = Math.min(Math.max(index, 0), this.entries.length - 1);
    index = this.selected;
    if (extend) {
      this.selectedIndexes.clear();
      for (let value = Math.min(this.anchor, index); value <= Math.max(this.anchor, index); value += 1) this.selectedIndexes.add(value);
    } else if (toggle) {
      if (this.selectedIndexes.has(index) && this.selectedIndexes.size > 1) this.selectedIndexes.delete(index);
      else this.selectedIndexes.add(index);
      this.anchor = index;
    } else {
      this.selectedIndexes = new Set([index]); this.anchor = index;
    }
    const entry = this.entries[index];
    const sourceIndex = index;
    snapshot = { ...snapshot, selectedIndex: Math.max(0, sourceIndex) };
    localStorage.setItem(`mountlet-selection:${snapshot.remoteId}:${snapshot.path}`, entry.path);
    localStorage.setItem(`mountlet-selection-index:${snapshot.remoteId}:${snapshot.path}`, String(sourceIndex));
    rememberSelection(snapshot.remoteId, snapshot.path, Math.max(0, sourceIndex), entry.path);
    persistBrowserFolder(snapshot.remoteId, snapshot.path, entry.path, Math.max(0, sourceIndex));
    this.revealSelection();
    this.paint();
    updateFileActionButtons();
  }

  private revealSelection(): void {
    const index = this.selected;
    const top = index * this.rowHeight;
    if (top < this.viewport.scrollTop) this.viewport.scrollTop = top;
    if (top + this.rowHeight > this.viewport.scrollTop + this.viewport.clientHeight) this.viewport.scrollTop = top + this.rowHeight - this.viewport.clientHeight;
  }

  current(): FileEntry | undefined { return this.entries[this.selected]; }
  selectedEntries(): FileEntry[] { return [...this.selectedIndexes].sort((a, b) => a - b).map(index => this.entries[index]).filter(Boolean); }
  selectAll(): void {
    this.selectedIndexes = new Set(this.entries.map((_, index) => index));
    this.paint();
  }
  currentIndex(): number { return this.selected; }
  pageSize(): number { return Math.max(1, Math.floor(this.viewport.clientHeight / this.rowHeight)); }
  focus(): void { this.viewport.focus({ preventScroll: true }); }
  renameCurrent(): void {
    const current = this.current();
    if (!current) return;
    this.renamingPath = current.path;
    this.paint();
    queueMicrotask(() => {
      const input = this.canvas.querySelector<HTMLInputElement>(".file-rename-input");
      if (!input) return;
      input.focus();
      const dot = current.isDir ? -1 : current.name.lastIndexOf(".");
      input.setSelectionRange(0, dot > 0 ? dot : current.name.length);
    });
  }

  paint(): void {
    this.rowHeight = metricsAt(preferences.zoomStep).fileRow;
    const measured = Math.max(this.viewport.clientHeight, this.viewport.getBoundingClientRect().height);
    const height = measured > 1 ? measured : Math.max(fileViewportBudget(), 1);
    this.viewport.style.minHeight = "";
    const start = Math.max(0, Math.floor(this.viewport.scrollTop / this.rowHeight) - 2);
    const count = Math.ceil(height / this.rowHeight) + 4;
    const end = Math.min(this.entries.length, start + count);
    this.canvas.style.height = `${this.entries.length * this.rowHeight}px`;
    const fragment = document.createDocumentFragment();
    for (let index = start; index < end; index += 1) {
      const entry = this.entries[index];
      const row = element("div", `file-row${this.selectedIndexes.has(index) ? " selected" : ""}`);
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", this.selectedIndexes.has(index) ? "true" : "false");
      row.draggable = preferences.integratedFileEdits;
      row.style.height = `${this.rowHeight}px`;
      row.style.transform = `translateY(${index * this.rowHeight}px)`;
      row.dataset.index = String(index);
      row.dataset.entryPath = entry.path;
      row.dataset.entryDirectory = entry.isDir ? "true" : "false";
      const icon = fileIcon(entry);
      const name = element("span", "file-name");
      if (entry.path === this.renamingPath) {
        const input = element("input", "file-rename-input") as HTMLInputElement;
        input.value = entry.name;
        let committed = false;
        const finish = async (save: boolean) => {
          if (committed) return;
          committed = true;
          this.renamingPath = "";
          const next = input.value.trim();
          if (save && next && next !== entry.name) await mutateFolder(() => renameRemoteEntry(entry.remoteId, entry.path, next));
          else this.paint();
        };
        input.addEventListener("keydown", event => {
          event.stopPropagation();
          if (event.key === "Enter") { void finish(true); event.preventDefault(); }
          if (event.key === "Escape") { void finish(false); event.preventDefault(); }
        });
        input.addEventListener("blur", () => void finish(true));
        name.append(icon, input);
      } else name.append(icon, element("span", "file-name-text", entry.name));
      row.append(name, element("span", "file-size", entry.isDir ? "" : formatBytes(entry.size)), element("span", "file-modified", entry.modified));
      row.addEventListener("click", event => this.select(index, event.shiftKey, event.ctrlKey || event.metaKey));
      row.addEventListener("dblclick", () => void openEntry(entry));
      row.addEventListener("dragstart", event => {
        if (!preferences.integratedFileEdits || !event.dataTransfer) { event.preventDefault(); return; }
        if (!this.selectedIndexes.has(index)) this.select(index);
        event.dataTransfer.effectAllowed = "copyMove";
        const selected = this.selectedEntries();
        outwardDragEntries = selected;
        outwardDragStarted = false;
        event.dataTransfer.setData("application/x-mountlet-entries", JSON.stringify(selected.map(item => ({ remoteId: item.remoteId, path: item.path }))));
      });
      if (entry.isDir) {
        row.addEventListener("dragover", event => {
          if (!event.dataTransfer?.types.includes("application/x-mountlet-entries")) return;
          event.preventDefault(); event.dataTransfer.dropEffect = event.ctrlKey ? "copy" : "move"; row.classList.add("drop-target");
        });
        row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
        row.addEventListener("drop", event => {
          const payload = event.dataTransfer?.getData("application/x-mountlet-entries") || "";
          if (!payload) return;
          event.preventDefault(); event.stopPropagation(); row.classList.remove("drop-target");
          void dropRemoteEntries(payload, entry.path, !event.ctrlKey);
        });
      }
      row.addEventListener("dragend", () => {
        if (!outwardDragStarted) outwardDragEntries = [];
      });
      row.addEventListener("contextmenu", event => {
        if (!this.selectedIndexes.has(index)) this.select(index);
        showContextMenu(event, [
          newFileActions(),
          { separator: true },
          { label: entry.isDir ? "Open" : "Open / download", run: () => void openEntry(entry) },
          { label: "Open mounted folder", enabled: Boolean(remotes.find(remote => remote.id === entry.remoteId)?.mounted), run: () => void openMountedFolder(entry.remoteId, entry.isDir ? entry.path : parentPath(entry.path)) },
          { separator: true },
          { label: "Copy", enabled: preferences.integratedFileEdits, run: () => rememberFileClipboard(false) },
          { label: "Cut", enabled: preferences.integratedFileEdits, run: () => rememberFileClipboard(true) },
          { label: "Rename", enabled: preferences.integratedFileEdits && this.selectedIndexes.size === 1, run: () => this.renameCurrent() },
          { label: "Delete", enabled: preferences.integratedFileEdits, run: () => void deleteCurrentEntry() },
          { separator: true },
          { label: "Save for offline use", run: () => void mutateFolder(async () => { for (const item of this.selectedEntries()) await makeRemoteEntryOffline(item.remoteId, item.path, item.isDir); }) },
          { label: "Remove offline copies", enabled: this.selectedEntries().some(item => item.cache.offline), run: () => void mutateFolder(async () => { for (const item of this.selectedEntries().filter(item => item.cache.offline)) await removeRemoteEntryOffline(item.remoteId, item.path); }) },
          { label: "Clear resolved cache", enabled: this.selectedEntries().some(item => item.cache.cached && !item.cache.offline), run: () => void mutateFolder(async () => { for (const item of this.selectedEntries().filter(item => item.cache.cached && !item.cache.offline)) await clearResolvedCache(item.remoteId, item.path); }) },
        ]);
      });
      fragment.append(row);
    }
    this.canvas.replaceChildren(fragment);
    updateFileActionButtons();
  }
}

let fileList: VirtualFileList | null = null;
document.addEventListener("mountlet-resize", () => fileList?.paint());

function fileClipboard(): FileClipboard | null {
  try { return JSON.parse(localStorage.getItem("mountlet-file-clipboard") || "null") as FileClipboard | null; }
  catch { return null; }
}

function canMutateEntries(entries: readonly FileEntry[]): boolean {
  const remote = remotes.find(candidate => candidate.id === selectedRemote);
  if (!remote || !entries.length) return false;
  if (remote.provider !== "gphotos") return true;
  return entries.every(entry => {
    const parts = entry.path.split("/").filter(Boolean);
    return !entry.isDir && parts.length >= 3 && parts[0].toLocaleLowerCase() === "album";
  });
}

function updateFileActionButtons(): void {
  const actions = document.querySelector(".file-actions");
  if (!actions) return;
  const selected = fileList?.selectedEntries() ?? [];
  const hasSelection = selected.length > 0;
  const edits = preferences.integratedFileEdits;
  const editReason = !hasSelection
    ? "Select files or folders first"
    : edits ? "" : "Enable integrated file edits in App settings first";
  const destructive = hasSelection && edits && canMutateEntries(selected);
  const clipboard = fileClipboard();
  const set = (label: string, enabled: boolean, title: string) => {
    const button = actions.querySelector<HTMLButtonElement>(`button[data-action="${CSS.escape(label)}"]`);
    if (!button) return;
    button.disabled = !enabled;
    button.title = title;
  };
  set("Copy", hasSelection && edits, editReason || "Copy selected items");
  set("Cut", destructive, editReason || (destructive ? "Cut selected items" : "Google Photos media cannot be moved from this view"));
  set("Delete", destructive, editReason || (destructive ? "Delete selected items" : "Google Photos can remove media only from albums created through rclone"));
  set("Paste", Boolean(edits && clipboard), !edits ? "Enable integrated file edits in App settings first" : clipboard ? "Paste into this folder" : "Copy or cut files first");
  const allOffline = hasSelection && selected.every(entry => entry.cache.offline);
  set("Save offline", hasSelection && !allOffline, !hasSelection ? "Select files or folders to make them available offline" : allOffline ? "Already available offline" : "Save a local snapshot");
  const hasOffline = selected.some(entry => entry.cache.offline);
  set("Remove offline copies", hasSelection && hasOffline, hasOffline ? "Remove offline copies for selected items" : "Select offline files or folders first");
  const hasCache = selected.some(entry => entry.cache.cached && !entry.cache.offline);
  set("Clear resolved cache", hasSelection && hasCache, hasCache ? "Clear resolved cache for selected items" : "Select temporarily cached items first");
}

async function dropRemoteEntries(payload: string, destinationPath: string, move: boolean): Promise<void> {
  if (!preferences.integratedFileEdits || !selectedRemote) return;
  let entries: Array<{ remoteId: string; path: string }>;
  try { entries = JSON.parse(payload); } catch { return; }
  const usable = entries.filter(entry => entry.path && !(entry.remoteId === selectedRemote && (destinationPath === entry.path || destinationPath.startsWith(`${entry.path}/`))));
  if (!usable.length) return;
  try {
    for (const entry of usable) {
      const name = entry.path.split("/").pop()!;
      await transferRemoteEntry(entry.remoteId, entry.path, selectedRemote, [destinationPath, name].filter(Boolean).join("/"), move);
    }
    await refreshFolder();
  } catch (error) { await showError(move ? "Move files" : "Copy files", error); }
}

async function synchronizeOffline(remoteId = selectedRemote): Promise<void> {
  if (!remoteId) return;
  try {
    const conflicts = await syncOffline(remoteId);
    await resolveOfflineConflicts(remoteId, conflicts);
    await refreshFolder();
  } catch (error) { await showError("Sync cached files", error); }
}

async function resolveOfflineConflicts(remoteId: string, conflicts: OfflineConflict[]): Promise<void> {
  const remote = remotes.find(candidate => candidate.id === remoteId);
  for (const conflict of conflicts) {
      const choice = await promptWizardOption(
        "Offline file conflict",
        `${remote?.name ?? remoteId}/${conflict.path} changed both locally and in the cloud.`,
        "newer",
        false,
        [{ value: "newer", label: "Keep newer" }, { value: "older", label: "Keep older" }, { value: "keep_both", label: "Keep both" }],
      ) as "newer" | "older" | "keep_both" | null;
      if (!choice) continue;
      await resolveOfflineConflict(conflict as OfflineConflict, choice);
  }
}

async function synchronizeAllOffline(): Promise<void> {
  // Deliberately sequential: rclone work stays out of the UI path and does not
  // create a process burst on machines with many configured remotes.
  for (const remote of remotes) await synchronizeOffline(remote.id);
}

let offlineReconcileTimer = 0;
let offlineReconcileRunning = false;
async function scanOfflineChanges(): Promise<void> {
  if (isBrowserWindow || offlineReconcileRunning || pendingMounts.size || browserLoading) {
    offlineReconcileTimer = window.setTimeout(scanOfflineChanges, 2000); return;
  }
  offlineReconcileRunning = true;
  try {
    for (const remoteId of await changedOfflineRemotes()) {
      const generation = selectionGeneration;
      const conflicts = await syncOffline(remoteId);
      if (conflicts.length) await resolveOfflineConflicts(remoteId, conflicts);
      if (generation === selectionGeneration && remoteId === selectedRemote && !browserLoading) {
        await refreshFolder();
      }
    }
  } catch { /* Background reconciliation retries after the next local scan. */ }
  finally { offlineReconcileRunning = false; offlineReconcileTimer = window.setTimeout(scanOfflineChanges, 2000); }
}

function renderBrowserPane(): HTMLElement {
  const pane = element("section", "browser-pane");
  pane.id = "browser-pane";
  const remote = remotes.find(candidate => candidate.id === selectedRemote);
  const title = element("div", "browser-title");
  title.append(element("strong", "", remote ? `${remote.name} (${remote.providerLabel})` : "File browser"));
  const mount = element("span", `mount-toggle ${remote?.mounted ? "on" : ""}`);
  mount.append(element("span", "mount-toggle-knob"));
  if (remote) mount.addEventListener("click", () => void changeRemoteMount(remote.id));
  const syncRemote = actionIcon("⇄", "Sync cached files for this remote");
  syncRemote.addEventListener("click", () => void synchronizeOffline(remote?.id));
  const removeRemoteOffline = actionIcon("⌫", "Remove offline files for this remote");
  removeRemoteOffline.addEventListener("click", async () => {
    if (!remote || !await confirmOwned("Remove offline files?", `Remove files saved for offline use from ${remote.name}?`, "Remove")) return;
    await removeOfflineCopies(remote.id); await refreshFolder();
  });
  const clearRemoteCache = actionIcon("⌫", "Clear resolved cache for this remote");
  clearRemoteCache.addEventListener("click", async () => {
    if (!remote || !await confirmOwned("Clear cache?", `Remove temporary resolved cache from ${remote.name}?`, "Clear")) return;
    await clearResolvedCache(remote.id); await refreshFolder();
  });
  const rcloneOutput = actionIcon("▤", "Show rclone output");
  rcloneOutput.addEventListener("click", () => void showRcloneOutput());
  const closeBrowser = actionIcon("×", "Close file browser");
  closeBrowser.hidden = preferences.mode !== "multiple";
  closeBrowser.addEventListener("click", () => void setDetachedBrowser(false));
  title.append(mount, syncRemote, removeRemoteOffline, clearRemoteCache, rcloneOutput, closeBrowser);
  pane.append(title);
  const pathbar = element("div", "path-bar");
  const up = actionIcon("↑", "Parent folder");
  up.dataset.browserNav = "up";
  up.disabled = !currentPath;
  up.addEventListener("click", () => void navigate(parentPath(currentPath)));
  const home = actionIcon("⌂", "Remote root");
  home.dataset.browserNav = "root";
  home.disabled = !currentPath;
  home.addEventListener("click", () => void navigate(""));
  const refresh = actionIcon("↻", "Refresh folder");
  refresh.addEventListener("click", () => void refreshFolder());
  const openMounted = actionIcon("▰", "Open mounted folder");
  openMounted.addEventListener("click", async () => {
    try { await openMountedFolder(selectedRemote, currentPath); }
    catch (error) { browserError = String(error); renderBrowserOnly(); }
  });
  pathbar.append(up, home, element("span", "path-text", currentPath || "Remote root"), refresh, openMounted);
  pane.append(pathbar);
  const searchRow = element("div", "search-row browser-search");
  const search = element("input", "search-input") as HTMLInputElement;
  search.type = "search";
  search.placeholder = "Search this remote";
  search.value = fileFilter;
  search.addEventListener("input", () => {
    const wasActive = Boolean(fileFilter.trim());
    fileFilter = search.value;
    localStorage.setItem("mountlet-browser-query", fileFilter);
    remoteSearchLoading = Boolean(fileFilter.trim());
    renderRemoteSearchResults();
    scheduleSearch("remote");
    if (wasActive !== Boolean(fileFilter.trim())) scheduleNativeLayout(0);
  });
  search.addEventListener("keydown", event => {
    if (event.key === "ArrowDown") {
      if (remoteSearchHasResults()) moveRemoteSearchSelection(1);
      event.preventDefault();
    }
    if (event.key === "ArrowUp") {
      if (remoteSearchHasResults()) moveRemoteSearchSelection(-1);
      event.preventDefault();
    }
    if (event.key === "Enter") {
      const result = remoteSearchHasResults() ? remoteSearchResults[Math.min(remoteSearchSelected, 49)] : undefined;
      if (result) void previewRemoteSearchResult(result, true);
      event.preventDefault();
    }
    if (event.key === "Escape") {
      fileList?.focus();
      event.preventDefault();
    }
  });
  const searchIcon = chromeIcon("ui-search.svg", "search-symbol");
  const searchStatus = element("span", "remote-search-status");
  searchRow.append(searchIcon, search, searchStatus);
  pane.append(searchRow);
  const searchResults = element("div", "remote-search-results");
  searchResults.id = "remote-search-results";
  pane.append(searchResults);
  const actions = element("div", "file-actions");
  const actionHandlers: Record<string, () => void> = {
    Copy: () => { if (requireIntegratedEdits()) rememberFileClipboard(false); },
    Cut: () => { if (requireIntegratedEdits()) rememberFileClipboard(true); },
    Paste: () => { if (requireIntegratedEdits()) void pasteFileClipboard(); },
    Delete: () => { if (requireIntegratedEdits()) void deleteCurrentEntry(); },
    "Save offline": () => { const entries = fileList?.selectedEntries() ?? []; if (entries.length) void mutateFolder(async () => { for (const entry of entries) await makeRemoteEntryOffline(entry.remoteId, entry.path, entry.isDir); }); },
    "Remove offline copies": () => { const entries = (fileList?.selectedEntries() ?? []).filter(entry => entry.cache.offline); if (entries.length) void mutateFolder(async () => { for (const entry of entries) await removeRemoteEntryOffline(entry.remoteId, entry.path); }); },
    "Clear resolved cache": () => { const entries = (fileList?.selectedEntries() ?? []).filter(entry => entry.cache.cached && !entry.cache.offline); if (entries.length) void mutateFolder(async () => { for (const entry of entries) await clearResolvedCache(entry.remoteId, entry.path); }); },
    Sync: () => void synchronizeOffline(),
  };
  [["▣", "Copy"], ["✂", "Cut"], ["▤", "Paste"], ["⌫", "Delete"], ["⇊", "Save offline"], ["⌫", "Remove offline copies"], ["⌫", "Clear resolved cache"], ["↻", "Sync"]].forEach(([symbol, label]) => {
    const button = actionIcon(symbol, label);
    button.dataset.action = label;
    const handler = actionHandlers[label];
    if (handler) button.addEventListener("click", handler);
    actions.append(button);
  });
  pane.append(actions);
  const header = element("div", "file-header");
  header.append(element("span", "", "Name"), element("span", "", "Size"), element("span", "", "Modified"));
  pane.append(header);
  const viewport = element("div", "file-viewport");
  viewport.tabIndex = 0;
  viewport.setAttribute("role", "listbox");
  viewport.setAttribute("aria-label", remote ? `Files in ${remote.name}` : "Files");
  viewport.setAttribute("aria-multiselectable", "true");
  viewport.addEventListener("contextmenu", event => showContextMenu(event, [newFileActions()]));
  viewport.addEventListener("dragover", event => {
    if (!event.dataTransfer?.types.includes("application/x-mountlet-entries")) return;
    event.preventDefault(); event.dataTransfer.dropEffect = event.ctrlKey ? "copy" : "move";
  });
  viewport.addEventListener("drop", event => {
    const payload = event.dataTransfer?.getData("application/x-mountlet-entries") || "";
    if (!payload) return;
    event.preventDefault(); void dropRemoteEntries(payload, currentPath, !event.ctrlKey);
  });
  const statusText = browserError || (browserLoading ? "Checking cloud changes…" : snapshot ? `${snapshot.entries.length} items` : "Select a remote");
  pane.append(viewport, element("div", `browser-status${browserError ? " error" : ""}`, statusText));
  queueMicrotask(() => {
    fileList = new VirtualFileList(viewport);
    updateVisibleEntries(true);
    renderRemoteSearchResults();
    viewport.addEventListener("keydown", event => {
      if (!fileList) return;
      if (matchesShortcut(event, "browser_switch_pane")) {
        event.preventDefault();
        event.stopPropagation();
        if (directionPointsToMain(event.key)) focusRemoteList();
        return;
      }
      if (event.key === "ArrowDown") { fileList.select(fileList.currentIndex() + 1, event.shiftKey); event.preventDefault(); }
      if (event.key === "ArrowUp") { fileList.select(fileList.currentIndex() - 1, event.shiftKey); event.preventDefault(); }
      if (matchesShortcut(event, "common_next")) { fileList.select(fileList.currentIndex() + 1); event.preventDefault(); }
      if (matchesShortcut(event, "common_previous")) { fileList.select(fileList.currentIndex() - 1); event.preventDefault(); }
      if (matchesShortcut(event, "browser_first_last")) { fileList.select(event.key === "Home" ? 0 : Number.MAX_SAFE_INTEGER); event.preventDefault(); }
      if (matchesShortcut(event, "browser_page")) { fileList.select(fileList.currentIndex() + (event.key === "PageDown" ? 1 : -1) * fileList.pageSize()); event.preventDefault(); }
      if (matchesShortcut(event, "browser_parent")) { void navigate(parentPath(currentPath)); event.preventDefault(); }
      if (matchesShortcut(event, "browser_root")) { void navigate(""); event.preventDefault(); }
      if (matchesShortcut(event, "browser_refresh")) { void refreshFolder(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_rename")) { if (requireIntegratedEdits()) fileList.renameCurrent(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_copy")) { if (requireIntegratedEdits()) rememberFileClipboard(false); event.preventDefault(); }
      if (matchesShortcut(event, "browser_select_all")) { fileList.selectAll(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_cut")) { if (requireIntegratedEdits()) rememberFileClipboard(true); event.preventDefault(); }
      if (matchesShortcut(event, "browser_paste")) { if (requireIntegratedEdits()) void pasteFileClipboard(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_delete")) { if (requireIntegratedEdits()) void deleteCurrentEntry(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_new_folder")) { if (requireIntegratedEdits()) void makeFolder(); event.preventDefault(); }
      if (matchesShortcut(event, "browser_open_folder")) { void openMountedFolder(selectedRemote, currentPath); event.preventDefault(); }
      else if (matchesShortcut(event, "browser_open")) { const current = fileList.current(); if (current) void openEntry(current); event.preventDefault(); }
      if (matchesShortcut(event, "common_escape")) { focusRemoteList(); event.preventDefault(); }
      if (matchesShortcut(event, "common_search")) {
        if (focusSearchField("remote")) event.preventDefault();
      }
      event.stopPropagation();
    });
  });
  return pane;
}

function updateVisibleEntries(reveal = false): void {
  if (!fileList || !snapshot) { fileList?.setEntries([]); updateFileActionButtons(); return; }
  const remembered = rememberedFolderSelection(snapshot.remoteId, snapshot.path);
  const path = remembered.path || snapshot.entries[snapshot.selectedIndex]?.path || "";
  fileList.setEntries(snapshot.entries, remembered.index, path, reveal);
}

function renderRemoteSearchResults(): void {
  const list = document.querySelector<HTMLElement>("#remote-search-results");
  const status = document.querySelector<HTMLElement>(".remote-search-status");
  if (!list || !status) return;
  const query = fileFilter.trim();
  list.classList.toggle("active", Boolean(query));
  if (!query) { list.replaceChildren(); status.textContent = ""; return; }
  const header = element("div", "remote-search-header");
  header.append(element("span", "", "Name"), element("span", "", "Path"), element("span", "", "Modified"));
  if (remoteSearchLoading) { list.replaceChildren(header); status.textContent = "Searching…"; return; }
  const rows = remoteSearchResults.slice(0, 50).map((result, index) => {
    const row = element("button", `remote-search-result quality-${result.quality}${index === remoteSearchSelected ? " selected" : ""}`);
    row.type = "button";
    row.tabIndex = -1;
    row.dataset.resultIndex = String(index);
    row.append(element("span", "", result.name), element("span", "", result.parentPath || "Remote root"), element("span", "", result.modified));
    row.addEventListener("pointerenter", () => { setRemoteSearchSelection(index); void previewRemoteSearchResult(result, false); });
    row.addEventListener("focus", () => { setRemoteSearchSelection(index); void previewRemoteSearchResult(result, false); });
    row.addEventListener("click", () => void previewRemoteSearchResult(result, true));
    return row;
  });
  list.replaceChildren(header, ...rows);
  const count = Math.min(50, remoteSearchResults.length);
  status.textContent = `${count}${remoteSearchResults.length > 50 ? "+" : ""} indexed result${count === 1 ? "" : "s"}`;
}

function setRemoteSearchSelection(index: number): void {
  remoteSearchSelected = index;
  document.querySelectorAll<HTMLElement>(".remote-search-result").forEach(row => row.classList.toggle("selected", Number(row.dataset.resultIndex) === index));
  document.querySelector<HTMLElement>(`.remote-search-result[data-result-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
}

function moveRemoteSearchSelection(delta: number): void {
  const count = Math.min(50, remoteSearchResults.length);
  if (!count) return;
  setRemoteSearchSelection(Math.min(count - 1, Math.max(0, remoteSearchSelected + delta)));
  const result = remoteSearchResults[remoteSearchSelected];
  if (result) void previewRemoteSearchResult(result, false);
}

async function previewRemoteSearchResult(result: SearchEntry, focusBrowser: boolean): Promise<void> {
  await revealSearchResult(result, focusBrowser);
}

async function openEntry(entry: FileEntry): Promise<void> {
  if (entry.isDir) { await navigate(entry.path); return; }
  if (fileFilter.trim()) {
    localStorage.setItem(`mountlet-selection:${entry.remoteId}:${parentPath(entry.path)}`, entry.path);
    fileFilter = "";
    localStorage.removeItem("mountlet-browser-query");
    remoteSearchResults = [];
    await navigate(parentPath(entry.path));
    return;
  }
  browserLoading = true;
  browserError = "";
  renderBrowserOnly();
  try {
    await openRemoteEntry(entry.remoteId, entry.path);
    if (snapshot?.remoteId === entry.remoteId) {
      snapshot = { ...snapshot, entries: snapshot.entries.map(item => item.path === entry.path ? { ...item, cache: { ...item.cache, cached: true } } : item) };
      folderSnapshots.set(`${snapshot.remoteId}:${snapshot.path}`, snapshot);
    }
  }
  catch (error) { browserError = String(error); }
  finally { browserLoading = false; renderBrowserOnly(); }
}

async function mutateFolder(operation: () => Promise<void>): Promise<void> {
  browserError = "";
  try {
    await operation();
    await invalidateFolder(selectedRemote, currentPath);
    folderSnapshots.delete(`${selectedRemote}:${currentPath}`);
    await loadSnapshot();
    renderBrowserOnly();
  } catch (error) {
    browserError = String(error);
    renderBrowserOnly();
  }
}

function rememberFileClipboard(move: boolean): void {
  const entries = fileList?.selectedEntries() ?? [];
  if (!entries.length) return;
  const value: FileClipboard = { entries: entries.map(({ remoteId, path }) => ({ remoteId, path })), move };
  localStorage.setItem("mountlet-file-clipboard", JSON.stringify(value));
  updateFileActionButtons();
}

async function pasteFileClipboard(): Promise<void> {
  let clipboard: FileClipboard | null = null;
  try { clipboard = JSON.parse(localStorage.getItem("mountlet-file-clipboard") || "null") as FileClipboard | null; }
  catch { /* Ignore invalid old clipboard state. */ }
  if (!clipboard) return;
  await mutateFolder(async () => {
    for (const entry of clipboard!.entries) {
      const name = entry.path.split("/").filter(Boolean).pop();
      if (!name) continue;
      const destination = [currentPath, name].filter(Boolean).join("/");
      await transferRemoteEntry(entry.remoteId, entry.path, selectedRemote, destination, clipboard!.move);
    }
  });
  if (clipboard.move) localStorage.removeItem("mountlet-file-clipboard");
}

async function deleteCurrentEntry(): Promise<void> {
  const entries = fileList?.selectedEntries() ?? [];
  if (!entries.length) return;
  const description = entries.length === 1 ? entries[0].name : `${entries.length} selected items`;
  const remote = remotes.find(candidate => candidate.id === selectedRemote);
  const albumPath = currentPath.trim().replace(/^\/+|\/+$/g, "");
  const albumWritable = remote?.provider === "gphotos"
    && albumPath.toLowerCase().startsWith("album/")
    && albumPath.split("/").filter(Boolean).length >= 2;
  const message = albumWritable
    ? `Remove ${description} from this album?\n\nThe media remains in your Google Photos library.`
    : `Delete ${description}?`;
  if (!await confirmOwned("Delete item?", message, "Delete")) return;
  await mutateFolder(async () => { for (const entry of entries) await deleteRemoteEntry(entry.remoteId, entry.path, entry.isDir); });
}

async function makeFolder(): Promise<void> {
  const name = (await promptOwned("New folder", "Folder name", "New folder"))?.trim();
  if (!name || name.includes("/") || name === "." || name === "..") return;
  await mutateFolder(() => createRemoteFolder(selectedRemote, [currentPath, name].filter(Boolean).join("/")));
}

const NEW_FILE_TYPES = [
  { label: "Text file", name: "New file.txt", contents: "" },
  { label: "Markdown file", name: "New file.md", contents: "# New document\n" },
  { label: "JSON file", name: "New file.json", contents: "{}\n" },
  { label: "CSV file", name: "New file.csv", contents: "" },
  { label: "HTML file", name: "New file.html", contents: "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <title>New document</title>\n</head>\n<body>\n</body>\n</html>\n" },
];

function newFileActions(): ContextAction {
  const enabled = preferences.integratedFileEdits && !browserLoading && Boolean(selectedRemote);
  return {
    label: "New",
    enabled,
    children: [
      { label: "Folder", enabled, run: () => void makeFolder() },
      { separator: true },
      ...NEW_FILE_TYPES.map(type => ({ label: type.label, enabled, run: () => void makeFile(type.name, type.contents) })),
    ],
  };
}

async function makeFile(suggestedName: string, contents: string): Promise<void> {
  const name = (await promptOwned("New file", "File name", suggestedName))?.trim();
  if (!name || name.includes("/") || name === "." || name === "..") return;
  if (snapshot?.entries.some(entry => entry.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
    await showError("New file", `An item named ${name} already exists in this folder.`); return;
  }
  await mutateFolder(() => createRemoteFile(selectedRemote, [currentPath, name].filter(Boolean).join("/"), contents));
}

async function openSearchResult(result: SearchEntry): Promise<void> {
  await revealSearchResult(result, true);
}

async function navigate(path: string): Promise<void> {
  if (!selectedRemote) return;
  const keepFocus = fileListHasFocus();
  const previousPath = currentPath;
  currentPath = path;
  localStorage.setItem(`mountlet-path:${selectedRemote}`, currentPath);
  if (path === parentPath(previousPath) && previousPath) await persistBrowserFolder(selectedRemote, path, previousPath, 0);
  else await persistBrowserPath(selectedRemote, currentPath);
  snapshot = folderSnapshots.get(`${selectedRemote}:${currentPath}`) ?? null;
  browserError = "";
  browserLoading = !snapshot;
  applyBrowserFolderView({ keepFocus, reveal: true });
  await loadSnapshot();
  applyBrowserFolderView({ keepFocus, reveal: true });
  if (isBrowserWindow) await rememberBrowserState(selectedRemote, currentPath);
  else if (preferences.mode === "multiple") await emitBrowserState(selectedRemote, currentPath);
  scheduleNativeLayout(0);
  scheduleFolderPoll();
}

async function refreshFolder(): Promise<void> {
  if (!selectedRemote) return;
  const keepFocus = fileListHasFocus();
  folderSnapshots.delete(`${selectedRemote}:${currentPath}`);
  await invalidateFolder(selectedRemote, currentPath);
  browserLoading = true;
  browserError = "";
  applyBrowserFolderView({ keepFocus, reveal: false });
  await loadSnapshot();
  applyBrowserFolderView({ keepFocus, reveal: true });
  if (isBrowserWindow) await rememberBrowserState(selectedRemote, currentPath);
  else if (preferences.mode === "multiple") await emitBrowserState(selectedRemote, currentPath);
  scheduleNativeLayout(0);
}

async function loadSnapshot(): Promise<void> {
  const generation = ++requestGeneration;
  browserLoading = true;
  browserError = "";
  try {
    const result = await listFolder(selectedRemote, currentPath);
    if (generation !== requestGeneration || result.remoteId !== selectedRemote || result.path !== currentPath) return;
    snapshot = result;
    folderSnapshots.set(`${result.remoteId}:${result.path}`, result);
    if (!isBrowserWindow) queueRemoteMetadataReconcile(result, generation);
  } catch (error) {
    if (generation === requestGeneration) browserError = String(error);
  } finally {
    if (generation === requestGeneration) browserLoading = false;
  }
}

let remoteMetadataTimer = 0;
function queueRemoteMetadataReconcile(result: FolderSnapshot, generation: number): void {
  window.clearTimeout(remoteMetadataTimer);
  remoteMetadataTimer = window.setTimeout(async () => {
    if (generation !== requestGeneration || browserLoading || result.remoteId !== selectedRemote || result.path !== currentPath) return;
    try {
      const changed = await detectRemoteCacheChanges(result.remoteId, result.entries);
      if (!changed.length || generation !== requestGeneration) return;
      const conflicts = await syncOffline(result.remoteId);
      if (conflicts.length && generation === requestGeneration) await resolveOfflineConflicts(result.remoteId, conflicts);
      await detectRemoteCacheChanges(result.remoteId, result.entries, true);
    } catch { /* Remote cache verification is retried by the next folder poll. */ }
  }, 500);
}

async function selectRemote(remoteId: string): Promise<void> {
  if (remoteId === selectedRemote) return;
  const generation = ++selectionGeneration;
  await syncBrowserMemory();
  selectedRemote = remoteId;
  currentPath = rememberedFolderPath(remoteId);
  const selectedPath = currentPath;
  snapshot = folderSnapshots.get(`${remoteId}:${currentPath}`) ?? null;
  browserLoading = !snapshot;
  browserError = "";
  if (fileFilter.trim()) {
    remoteSearchLoading = true;
    remoteSearchResults = [];
    scheduleSearch("remote");
  }
  renderRemoteList();
  if (preferences.mode === "single") applyBrowserFolderView({ reveal: true });
  else await emitBrowserState(selectedRemote, currentPath);
  scheduleNativeLayout(0);
  scheduleFolderPoll();
  await loadSnapshot();
  if (generation !== selectionGeneration || selectedRemote !== remoteId || currentPath !== selectedPath) return;
  localStorage.setItem(`mountlet-path:${remoteId}`, selectedPath);
  await persistBrowserPath(remoteId, selectedPath);
  if (preferences.mode === "multiple") await emitBrowserState(selectedRemote, currentPath);
  else applyBrowserFolderView({ reveal: true });
  scheduleNativeLayout(0);
}

function applyBrowserFolderView(options: { keepFocus?: boolean; reveal?: boolean } = {}): void {
  const pane = document.querySelector("#browser-pane");
  if (!pane) {
    if (isBrowserWindow) render();
    else if (preferences.mode === "single") renderBrowserOnly();
    if (options.keepFocus) queueMicrotask(() => fileList?.focus());
    return;
  }
  const remote = remotes.find(candidate => candidate.id === selectedRemote);
  const title = pane.querySelector(".browser-title strong");
  if (title) title.textContent = remote ? `${remote.name} (${remote.providerLabel})` : "File browser";
  const pathText = pane.querySelector(".path-text");
  if (pathText) pathText.textContent = currentPath || "Remote root";
  pane.querySelectorAll<HTMLButtonElement>("[data-browser-nav]").forEach(button => {
    if (button.dataset.browserNav === "up" || button.dataset.browserNav === "root") button.disabled = !currentPath;
  });
  const status = pane.querySelector(".browser-status");
  if (status) {
    status.textContent = browserError || (browserLoading ? "Checking cloud changes…" : snapshot ? `${snapshot.entries.length} items` : "Select a remote");
    status.classList.toggle("error", Boolean(browserError));
  }
  if (fileList) {
    updateVisibleEntries(options.reveal ?? true);
    renderRemoteSearchResults();
    if (options.keepFocus) fileList.focus();
  } else if (options.keepFocus) {
    queueMicrotask(() => fileList?.focus());
  }
}

function renderBrowserOnly(): void {
  const existing = document.querySelector("#browser-pane");
  if (existing) existing.replaceWith(renderBrowserPane());
  syncFocusChrome();
}

function render(): void {
  if (isBrowserWindow) {
    app.replaceChildren(renderBrowserPane());
    app.className = "browser-only";
    syncFocusChrome();
    return;
  }
  const shell = element("main", `app-shell mode-${preferences.mode}`);
  shell.append(renderRemotePane());
  if (preferences.mode === "single" && !licenseLocked()) shell.append(renderBrowserPane());
  app.className = "";
  app.replaceChildren(shell);
  syncFocusChrome();
}

async function beginOutwardFileDrag(): Promise<void> {
  if (outwardDragStarted || !outwardDragEntries.length) return;
  outwardDragStarted = true;
  const entries = [...outwardDragEntries];
  try {
    const [paths, icon] = await Promise.all([
      materializeEntriesForDrag(entries),
      dragPreviewIcon(),
    ]);
    if (!paths.length) return;
    await startDrag({ item: paths, icon, mode: "copy" }, () => {
      outwardDragEntries = [];
      outwardDragStarted = false;
    });
    outwardDragEntries = [];
    outwardDragStarted = false;
  } catch (error) {
    outwardDragEntries = [];
    outwardDragStarted = false;
    showToast(`Could not export dragged items: ${String(error)}`);
  }
}

window.addEventListener("dragleave", event => {
  if (!outwardDragEntries.length || outwardDragStarted) return;
  const leftWindow = event.clientX <= 0 || event.clientY <= 0
    || event.clientX >= window.innerWidth - 1 || event.clientY >= window.innerHeight - 1;
  if (leftWindow) void beginOutwardFileDrag();
});

document.addEventListener("keydown", event => {
  if (event.defaultPrevented) return;
  if (document.querySelector(".modal-layer")) return;
  const target = event.target as HTMLElement | null;
  const editing = target?.matches("input, textarea, [contenteditable=true]");
  const inBrowser = Boolean(target?.closest("#browser-pane"));
  const inGlobalResults = Boolean(target?.closest("#global-results"));
  const inRemoteResults = Boolean(target?.closest("#remote-search-results"));
  if (!editing && matchesShortcut(event, "common_context_menu")) {
    const candidate = (isBrowserWindow || inBrowser)
      ? document.querySelector<HTMLElement>(`.file-row[data-index="${fileList?.currentIndex() ?? -1}"]`) ?? document.querySelector<HTMLElement>(".file-viewport")
      : document.querySelector<HTMLElement>(`.remote-card[data-remote-id="${CSS.escape(selectedRemote)}"]`);
    if (candidate) {
      const rect = candidate.getBoundingClientRect();
      candidate.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: rect.left + Math.min(24, rect.width / 2), clientY: rect.top + Math.min(24, rect.height / 2) }));
      event.preventDefault(); return;
    }
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "common_next")) {
    selectAdjacentRemote(1); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "common_previous")) {
    selectAdjacentRemote(-1); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && ((event.shiftKey && event.key === "ArrowDown") || matchesShortcut(event, "remote_move_down"))) {
    moveSelectedRemote(1); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && ((event.shiftKey && event.key === "ArrowUp") || matchesShortcut(event, "remote_move_up"))) {
    moveSelectedRemote(-1); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && event.key === "ArrowDown") {
    if (inGlobalResults && globalSearchHasResults()) moveGlobalSearchSelection(1);
    else selectAdjacentRemote(1);
    event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && event.key === "ArrowUp") {
    if (inGlobalResults && globalSearchHasResults()) moveGlobalSearchSelection(-1);
    else selectAdjacentRemote(-1);
    event.preventDefault(); return;
  }
  if ((isBrowserWindow || inBrowser) && !editing && inRemoteResults && event.key === "ArrowDown") {
    if (remoteSearchHasResults()) moveRemoteSearchSelection(1);
    event.preventDefault(); return;
  }
  if ((isBrowserWindow || inBrowser) && !editing && inRemoteResults && event.key === "ArrowUp") {
    if (remoteSearchHasResults()) moveRemoteSearchSelection(-1);
    event.preventDefault(); return;
  }
  if (!editing && matchesShortcut(event, "common_search")) {
    const wantRemote = isBrowserWindow || inBrowser;
    if (focusSearchField(wantRemote ? "remote" : "global")) { event.preventDefault(); return; }
  }
  if ((isBrowserWindow || inBrowser) && !editing && matchesShortcut(event, "browser_switch_pane")) {
    if (directionPointsToMain(event.key)) focusRemoteList();
    event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "remote_switch_pane")) {
    if (directionPointsToBrowser(event.key)) focusBrowserList();
    event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "remote_enter_browser")) {
    focusBrowserList(); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "remote_toggle_mount")) {
    if (selectedRemote) void changeRemoteMount(selectedRemote); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "remote_config")) {
    const remote = remotes.find(candidate => candidate.id === selectedRemote); if (remote) void showRemoteConfig(remote); event.preventDefault(); return;
  }
  if (!isBrowserWindow && !editing && !inBrowser && matchesShortcut(event, "remote_open_browser")) {
    if (selectedRemote) void openRemoteWeb(selectedRemote); event.preventDefault(); return;
  }
  if (matchesShortcut(event, "common_zoom_in")) { setZoom(1); event.preventDefault(); return; }
  if (matchesShortcut(event, "common_zoom_out")) { setZoom(-1); event.preventDefault(); return; }
  if (matchesShortcut(event, "common_zoom_reset")) { resetZoom(); event.preventDefault(); return; }
  if (matchesShortcut(event, "common_cycle_theme")) { cycleTheme(); event.preventDefault(); }
});

document.addEventListener("wheel", event => {
  if (!event.ctrlKey) return;
  setZoom(event.deltaY < 0 ? 1 : -1);
  event.preventDefault();
}, { passive: false });

document.addEventListener("mousedown", event => {
  if (isBrowserWindow) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest(".icon-button, .source-button, .menu-button, .zoom-label")) event.preventDefault();
});

document.addEventListener("pointermove", event => {
  if (event.pointerType && event.pointerType !== "mouse") {
    remoteHoverArmed = true;
    return;
  }
  const moved = Number.isFinite(lastPointer.x) && (event.clientX !== lastPointer.x || event.clientY !== lastPointer.y);
  lastPointer = { x: event.clientX, y: event.clientY };
  if (moved) remoteHoverArmed = true;
});

document.addEventListener("pointerdown", () => {
  remoteHoverArmed = true;
});

document.addEventListener("focusin", event => rememberFocusOwner(event.target));
window.addEventListener("blur", () => {
  parkedFocusOwner = lastFocusOwner;
  syncFocusChrome();
});

window.addEventListener("focus", () => {
  syncFocusChrome();
  if (document.querySelector(".modal-layer")) return;
  window.clearTimeout(focusRefreshTimer);
  focusRefreshTimer = window.setTimeout(() => {
    if (!selectedRemote || browserLoading || !document.querySelector("#browser-pane")) return;
    const remoteId = selectedRemote;
    const path = currentPath;
    void invalidateFolder(remoteId, path)
      .then(() => listFolder(remoteId, path))
      .catch(() => undefined);
  }, 250);
  restoreFocusOwner();
});

window.addEventListener("resize", () => fileList?.paint());

async function ensurePrerequisites(): Promise<boolean> {
  const items = await checkPrerequisites();
  if (items.find(item => item.key === "rclone")?.ready) return true;
  document.querySelector(".modal-layer")?.remove();
  const layer = element("div", "modal-layer");
  const dialog = element("section", "modal-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.append(element("h2", "", "Prepare Mountlet"));
  dialog.append(element("p", "add-remote-help", "Mountlet needs rclone to access cloud storage. Filesystem mount support is optional and only needed for native folders."));
  const rows = element("div", "prerequisite-rows");
  dialog.append(rows);
  const actions = element("div", "dialog-actions");
  const recheck = element("button", "", "Check again");
  const close = element("button", "primary", "Close");
  actions.append(recheck, close);
  dialog.append(actions);
  layer.append(dialog);
  document.body.append(layer);
  trapModalFocus(layer, dialog, close);
  let finished = false;
  const renderItems = (prereqs: Awaited<ReturnType<typeof checkPrerequisites>>) => {
    rows.replaceChildren();
    for (const item of prereqs) {
      const row = element("div", "settings-row");
      row.append(element("span", "settings-label", item.label));
      const status = element("span", "", item.ready ? "Ready" : (item.key === "rclone" ? item.detail : `Optional: ${item.detail}`));
      row.append(status);
      if (!item.ready) {
        const help = element("button", "", "Installation instructions");
        help.type = "button";
        help.addEventListener("click", () => void openExternal(item.helpUrl));
        row.append(help);
      }
      rows.append(row);
    }
  };
  const refresh = async () => {
    const prereqs = await checkPrerequisites();
    renderItems(prereqs);
    if (prereqs.find(item => item.key === "rclone")?.ready) {
      finished = true;
      layer.remove();
      return true;
    }
    return false;
  };
  await refresh();
  if (finished) return true;
  return await new Promise(resolve => {
    const timer = window.setInterval(() => { void refresh().then(ready => { if (ready) { window.clearInterval(timer); resolve(true); } }); }, 1000);
    recheck.addEventListener("click", () => void refresh().then(ready => { if (ready) { window.clearInterval(timer); resolve(true); } }));
    close.addEventListener("click", () => { window.clearInterval(timer); layer.remove(); resolve(false); });
  });
}

async function start(): Promise<void> {
  if (await startupSmokeEnabled()) {
    const checks: string[] = [];
    await appVersion(); checks.push("app-version");
    await listRemotes(); checks.push("remote-state");
    const smokePreferences = await loadPreferences(); checks.push("preferences");
    const smokeSettings = await loadAppSettings(); checks.push("settings");
    if (!smokePreferences || smokePreferences.mode !== smokeSettings.windowMode || smokePreferences.theme !== smokeSettings.theme) {
      throw new Error("UI preferences did not use the app settings source");
    }
    checks.push("settings-compatibility");
    await loadShortcuts(); checks.push("shortcuts");
    await bounded(licenseStatus(), 15000, "License status did not respond"); checks.push("license");
    await bounded(bugReportPreview("bug", "Startup behavior probe", "", true), 8000, "Report preview did not respond"); checks.push("report-preview");
    const isMac = navigator.userAgent.includes("Macintosh");
    if (!isMac) {
      await desktopHints(); checks.push("desktop-hints");
      const attempts = navigator.userAgent.includes("Windows") ? 3 : 1;
      for (let attempt = 0; attempt < attempts; attempt += 1) await checkPrerequisites();
      checks.push("prerequisites");
    }
    await refreshNativeTrayMenu(); checks.push("tray-menu");
    const addRemoteResult = openAddRemoteDialog();
    let addRemoteDialog: HTMLElement | null = null;
    for (let attempt = 0; attempt < 400 && !addRemoteDialog; attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 25));
      addRemoteDialog = document.querySelector<HTMLElement>(".remote-config-dialog");
    }
    if (!addRemoteDialog) throw new Error("Add Remote did not open");
    const row = (label: string): HTMLLabelElement => {
      const match = Array.from(addRemoteDialog!.querySelectorAll<HTMLLabelElement>("label.settings-row"))
        .find(candidate => candidate.querySelector(".settings-label")?.textContent === label);
      if (!match) throw new Error(`Add Remote is missing ${label}`);
      return match;
    };
    const providerField = row("Provider").querySelector("select") as HTMLSelectElement;
    const credentialField = row("Google client").querySelector("select") as HTMLSelectElement;
    const clientIdRow = row("Client ID");
    const clientSecretRow = row("Client secret");
    const s3ProviderRow = row("S3 provider");
    if (getComputedStyle(s3ProviderRow).display !== "none") throw new Error("Drive showed S3 fields");
    credentialField.value = "custom"; credentialField.dispatchEvent(new Event("change"));
    if (getComputedStyle(clientIdRow).display === "none" || getComputedStyle(clientSecretRow).display === "none") throw new Error("Custom Drive client fields stayed hidden");
    credentialField.value = "builtin"; credentialField.dispatchEvent(new Event("change"));
    if (getComputedStyle(clientIdRow).display !== "none" || getComputedStyle(clientSecretRow).display !== "none") throw new Error("Existing Drive client showed custom credential fields");
    providerField.value = "s3"; providerField.dispatchEvent(new Event("change"));
    if (getComputedStyle(s3ProviderRow).display === "none" || getComputedStyle(row("Google client")).display !== "none") throw new Error("S3 provider fields were not isolated");
    (addRemoteDialog.querySelector(".dialog-actions button") as HTMLButtonElement).click();
    await addRemoteResult;
    checks.push("add-remote-fields");
    app.replaceChildren(element("main", "startup-smoke", "Mountlet startup ready"));
    if (app.querySelector<HTMLElement>(".startup-smoke")?.textContent !== "Mountlet startup ready") {
      throw new Error("The production frontend did not render its startup probe");
    }
    checks.push("frontend-render");
    await completeStartupSmoke(checks);
    return;
  }
  await listenRestoreKeyboardFocus(firstShow => scheduleFocusRestore(firstShow));
  const [saved, savedSettings, savedBrowserMemory, savedShortcuts, savedRegistrationOrder] = await Promise.all([loadPreferences(), loadAppSettings(), loadBrowserMemory(), loadShortcuts(), remoteRegistrationOrder()]);
  const savedLicense = await bounded(licenseStatus(), 15000, "License status timed out.").catch(error => ({
    state: "expired" as const,
    summary: `License status could not be verified: ${String(error)}`,
    trialDaysRemaining: 0, licenseKey: "", licensedEmail: "", plan: "", licenseKind: "", maxDevices: 0, deviceLabel: "", expiresAt: "",
  }));
  registrationOrder = savedRegistrationOrder;
  completeSettings = savedSettings;
  browserMemory = savedBrowserMemory;
  shortcuts = resolveShortcutSettings(savedShortcuts);
  currentLicense = savedLicense;
  if (saved) Object.assign(preferences, saved);
  applyPreferences();
  await listenNativeLayout(event => {
    cachedBrowserSide = event.browserSide === "left" ? "left" : "right";
    nativeBrowserInnerHeight = event.browserInnerHeight;
    if (isBrowserWindow && nativeBrowserInnerHeight > 0) {
      const height = `${Math.round(nativeBrowserInnerHeight)}px`;
      document.documentElement.style.height = height;
      document.body.style.height = height;
      app.style.height = height;
    }
    fileList?.paint();
  });
  if (!isBrowserWindow) {
    currentHints = await desktopHints();
    if (currentHints.wayland) {
      preferences.mode = "single";
      if (completeSettings) completeSettings.windowMode = "single";
    }
    if (!await ensurePrerequisites()) {
      await quitApp();
      return;
    }
  }
  await listenFolderUpdated(async (remoteId, path) => {
    const refreshed = await listFolder(remoteId, path);
    folderSnapshots.set(`${remoteId}:${path}`, refreshed);
    if (remoteId === selectedRemote && path === currentPath) {
      snapshot = refreshed; browserLoading = false; applyBrowserFolderView({ keepFocus: fileListHasFocus(), reveal: false }); scheduleNativeLayout(0);
    }
  });
  if (!isBrowserWindow) await listenRemoteUsageDirty(queueUsageRefresh);
  await listenNativeFileDrop(drop => {
    const { paths, clientX, clientY } = drop;
    if (!paths.length || !selectedRemote || !preferences.integratedFileEdits) return;
    void (async () => {
      const target = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      const targetCard = target?.closest<HTMLElement>(".remote-card");
      const targetRow = target?.closest<HTMLElement>(".file-row[data-entry-directory=true]");
      const destinationRemote = targetCard?.dataset.remoteId || selectedRemote;
      const destinationPath = targetRow?.dataset.entryPath
        || (targetCard ? rememberedFolderPath(destinationRemote) : currentPath);
      const remote = remotes.find(candidate => candidate.id === destinationRemote);
      if (!remote || !await confirmOwned("Upload local items?", `Upload ${paths.length} local item${paths.length === 1 ? "" : "s"} to ${remote.name}${destinationPath ? `/${destinationPath}` : ""}?`, "Upload")) return;
      try {
        await uploadLocalPaths(destinationRemote, destinationPath, paths);
        if (destinationRemote === selectedRemote && destinationPath === currentPath) await refreshFolder();
        else await invalidateFolder(destinationRemote, destinationPath);
      } catch (error) { await showError("Upload files", error); }
    })();
  });
  if (!isBrowserWindow) {
    await listenTrayAnchor(() => scheduleNativeLayout(0));
    await listenTrayCommand(command => {
      if (command === "refresh") void refreshRemoteStatus();
      else if (command === "mount-all") void setAllMounted(true);
      else if (command === "unmount-all") void setAllMounted(false);
      else if (command === "add-remote") void showAddRemote();
      else if (command === "settings") void showAppSettings();
      else if (command === "license") void showLicense();
      else if (command === "about") void showAbout();
      else if (command === "report-bug") void reportBug();
      else if (command === "shortcuts") void showShortcuts();
      else if (command === "export-config") void exportBundle();
      else if (command === "import-config") void importBundle();
      else if (command === "sync-all") void synchronizeAllOffline();
      else if (command === "remove-all-offline") void removeAllOffline();
      else if (command === "clear-all-cache") void clearAllCache();
      else if (command === "cache-debug") void showCacheSyncDiagnostics();
      else if (command === "open-config-backup") void openConfigBackupFolder();
      else if (command.startsWith("select-remote:")) void selectRemote(command.slice("select-remote:".length));
      else if (command.startsWith("remote-action:")) {
        const payload = command.slice("remote-action:".length);
        const separator = payload.indexOf(":");
        const action = payload.slice(0, separator);
        const remote = remotes.find(candidate => candidate.id === payload.slice(separator + 1));
        if (!remote) return;
        if (action === "select") void selectRemote(remote.id);
        else if (action === "mount") void changeRemoteMount(remote.id);
        else if (action === "folder") void openMountedFolder(remote.id, "");
        else if (action === "web") void openRemoteWeb(remote.id);
        else if (action === "config") void showRemoteConfig(remote);
        else if (action === "reauth") void runRemoteConfigWizard(remote.id).catch(error => showError("Reauthenticate remote", error));
        else if (action === "sync") void synchronizeOffline(remote.id);
        else if (action === "remove-offline") void (async () => {
          if (!await confirmOwned("Remove offline files?", `Remove files saved for offline use from ${remote.name}?`, "Remove")) return;
          await removeOfflineCopies(remote.id);
          if (selectedRemote === remote.id) await refreshFolder();
        })();
        else if (action === "clear-cache") void (async () => {
          await clearResolvedCache(remote.id);
          if (selectedRemote === remote.id) await refreshFolder();
        })();
      }
    });
  }
  remotes = await listRemotes();
  selectedRemote = remotes[0]?.id ?? "";
  currentPath = rememberedFolderPath(selectedRemote);
  if (isBrowserWindow) {
    const initial = await getBrowserState();
    if (initial) {
      selectedRemote = initial.remoteId;
      currentPath = initial.path;
    }
    await listenBrowserState(async state => {
      const focus = Boolean(state.focus);
      await syncBrowserMemory();
      selectedRemote = state.remoteId;
      currentPath = state.path || rememberedFolderPath(state.remoteId);
      snapshot = folderSnapshots.get(`${selectedRemote}:${currentPath}`) ?? null;
      browserLoading = !snapshot;
      browserError = "";
      applyBrowserFolderView({ reveal: true, keepFocus: focus });
      if (fileFilter.trim()) {
        remoteSearchLoading = true;
        remoteSearchResults = [];
        scheduleSearch("remote");
      }
      scheduleNativeLayout(0);
      await loadSnapshot();
      applyBrowserFolderView({ reveal: true, keepFocus: focus });
      if (focus) fileList?.focus();
      scheduleNativeLayout(0);
    });
    await listenUiPreferences(state => {
      Object.assign(preferences, state);
      applyPreferences();
      render();
    });
  }
  render();
  if (!isBrowserWindow && preferences.mode === "multiple" && !licenseLocked()) {
    await setDetachedBrowser(true);
    await emitBrowserState(selectedRemote, currentPath);
  } else if (!isBrowserWindow && licenseLocked()) {
    await setDetachedBrowser(false);
    await layoutNativeWindows();
    window.setTimeout(() => void showLicense(), 0);
    queueMicrotask(restoreFocusOwner);
    return;
  }
  if (selectedRemote) {
    await loadSnapshot();
    if (preferences.mode === "single" || isBrowserWindow) renderBrowserOnly();
    else await emitBrowserState(selectedRemote, currentPath);
  }
  scheduleFolderPoll();
  if (!isBrowserWindow) await layoutNativeWindows();
  if ((isBrowserWindow || preferences.mode === "single") && fileFilter.trim()) {
    remoteSearchLoading = true;
    scheduleSearch("remote");
  }
  if (!isBrowserWindow) window.setTimeout(async () => {
    for (const remoteId of await autoMountRemoteIds()) await changeRemoteMount(remoteId);
  }, Math.max(0, (completeSettings?.autoMountDelay ?? 2) * 1000));
  if (!isBrowserWindow) {
    window.clearTimeout(noticePollTimer);
    noticePollTimer = window.setTimeout(pollNoticeServer, 2500);
  }
  if (!isBrowserWindow) {
    window.clearTimeout(offlineReconcileTimer);
    offlineReconcileTimer = window.setTimeout(scanOfflineChanges, 2000);
    window.setTimeout(() => void maybePromptCrashReport(), 1200);
  }
  // The first native focus event can arrive while startup is still awaiting
  // configuration and folder data, before the focused row exists. Restore the
  // DOM owner once initialization has produced the final panes as well.
  queueMicrotask(restoreFocusOwner);
}

void start();
