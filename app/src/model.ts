export type Provider =
  | "drive" | "gphotos" | "dropbox" | "onedrive" | "mega"
  | "protondrive" | "iclouddrive" | "pcloud" | "box" | "koofr" | "s3" | "webdav";

export interface Remote {
  id: string;
  name: string;
  provider: Provider;
  providerLabel: string;
  mounted: boolean;
  usedBytes: number | null;
  totalBytes: number | null;
}

export interface CacheState {
  cached: boolean;
  offline: boolean;
  partial: boolean;
}

export interface FileEntry {
  id: string;
  remoteId: string;
  path: string;
  name: string;
  isDir: boolean;
  size: number;
  modified: string;
  cache: CacheState;
}

export interface FolderSnapshot {
  remoteId: string;
  path: string;
  revision: number;
  selectedIndex: number;
  entries: readonly FileEntry[];
}

export interface Notice {
  key: string;
  title: string;
  message: string;
  level: "info" | "important" | "critical";
  url: string;
  seen: boolean;
  deletable: boolean;
  receivedAt: number;
  updatedAt: string;
  critical: boolean;
  archived: boolean;
}

export type WindowMode = "single" | "multiple";
export type ThemeMode = "system" | "light" | "dark";

export interface Preferences {
  mode: WindowMode;
  theme: ThemeMode;
  zoomStep: number;
  integratedFileEdits: boolean;
  fileListMaxItems: number;
}

export interface AppSettings {
  mountBase: string;
  autoMount: boolean;
  autoMountDelay: number;
  startAtLogin: boolean;
  integratedFileEdits: boolean;
  fileManager: string;
  openFolderBehavior: string;
  focusFileManager: boolean;
  windowMode: WindowMode;
  theme: ThemeMode;
  zoomSteps: number;
  fileListMaxItems: number;
  remoteCheckInterval: number;
  noticeInfoDisplay: "off" | "tray" | "dialog";
  noticeImportantDisplay: "off" | "tray" | "dialog";
  noticeCheckInterval: number;
  configSyncRemote: string;
  configSyncPath: string;
  shortcuts: Record<string, string[]>;
}

export const MIN_ZOOM_STEP = -4;
export const MAX_ZOOM_STEP = 6;
export const zoomFactor = (step: number): number => 1 + Math.min(MAX_ZOOM_STEP, Math.max(MIN_ZOOM_STEP, step)) * 0.1;

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let current = value;
  for (const unit of units) {
    current /= 1024;
    if (current < 1024 || unit === "TB") return `${current.toFixed(1)} ${unit}`;
  }
  return `${current.toFixed(1)} TB`;
}

export function parentPath(path: string): string {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}
