import { fileIconDataUrl } from "./backend.ts";
import type { FileEntry, Provider } from "./model.ts";

const providerAssets: Partial<Record<Provider, string>> = {
  drive: "google-drive.png", gphotos: "google-photos.svg", dropbox: "dropbox.svg",
  onedrive: "onedrive.svg", box: "box.svg", pcloud: "pcloud.png", koofr: "koofr.png",
  protondrive: "proton-drive.svg", iclouddrive: "icloud.svg", mega: "mega.svg",
  s3: "amazon-s3.svg",
};

const actionAssets: Record<string, string> = {
  "App configuration": "ui-config.svg", "Push configuration": "ui-config-push.svg",
  "Pull configuration": "ui-config-pull.svg", Refresh: "ui-refresh.svg",
  "Refresh folder": "ui-refresh.svg", "Make all available offline": "ui-save-offline.svg",
  "Make available offline": "ui-save-offline.svg",
  "Remove offline copy": "ui-remove-offline.svg", "Remove all offline copies": "ui-remove-offline.svg",
  "Remove offline files for this remote": "ui-remove-offline.svg", "Clear resolved cache for this remote": "ui-clear-cache.svg",
  "Remove offline copies": "ui-remove-offline.svg", "Clear resolved cache": "ui-clear-cache.svg",
  "Clear all cached files": "ui-clear-cache.svg", "Sync all cached files": "ui-sync.svg", "Sort remotes": "ui-reorder.svg",
  "Sync cached files for this remote": "ui-sync.svg", "Show rclone output": "ui-rclone-output.svg",
  "Close file browser": "ui-window-close.svg",
  Notifications: "ui-bell.svg", "Keep window visible": "ui-pin.svg", "Add remote": "ui-add.svg",
  "Use multiple windows": "ui-layout-multiple.svg", "Use single window": "ui-layout-single.svg",
  "Parent folder": "ui-parent.svg", "Remote root": "ui-home.svg", "Open mounted folder": "ui-folder-open.svg",
  Copy: "ui-copy.svg", Cut: "ui-cut.svg", Paste: "ui-paste.svg", Delete: "ui-delete.svg",
  Download: "ui-entry-download.svg", "Save offline": "ui-save-offline.svg", Sync: "ui-sync.svg",
};

export function providerIcon(provider: Provider, providerLabel = ""): HTMLElement {
  const normalized = providerLabel.toLocaleLowerCase();
  const variant = provider === "s3"
    ? normalized.includes("cloudflare") ? "cloudflare-r2.svg"
      : normalized.includes("minio") ? "minio.svg"
      : normalized.includes("wasabi") ? "wasabi.svg"
      : "amazon-s3.svg"
    : provider === "webdav"
      ? normalized.includes("nextcloud") ? "nextcloud.svg"
        : normalized.includes("owncloud") ? "owncloud.png"
        : undefined
      : undefined;
  const asset = variant ?? providerAssets[provider];
  if (asset) {
    const image = document.createElement("img");
    image.className = "provider-icon";
    image.src = `/assets/providers/${asset}`;
    image.alt = provider;
    image.draggable = false;
    return image;
  }
  const icon = document.createElement("span");
  icon.className = `provider-icon provider-${provider}`;
  icon.textContent = provider.slice(0, 1).toUpperCase();
  icon.setAttribute("aria-label", provider);
  return icon;
}

export function actionIcon(symbol: string, label: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.className = "icon-button";
  button.type = "button";
  const asset = actionAssets[label];
  if (asset) {
    const icon = document.createElement("span");
    icon.className = "action-icon";
    const url = `url("/assets/${asset}")`;
    icon.style.webkitMaskImage = url;
    icon.style.maskImage = url;
    button.append(icon);
  } else {
    button.textContent = symbol;
  }
  button.title = label;
  button.setAttribute("aria-label", label);
  return button;
}

export function chromeIcon(asset: string, className: string): HTMLElement {
  const icon = document.createElement("span");
  icon.className = className;
  const url = `url("/assets/${asset}")`;
  icon.style.webkitMaskImage = url;
  icon.style.maskImage = url;
  icon.setAttribute("aria-hidden", "true");
  return icon;
}

const nativeIconCache = new Map<string, string | null>();

export function fileIcon(entry: FileEntry): HTMLElement {
  const extension = entry.name.toLocaleLowerCase().split(".").pop() || "";
  let asset = "file-document.svg";
  if (entry.isDir) asset = "file-folder.svg";
  else if (["jpg", "jpeg", "png", "gif", "webp", "svg", "heic"].includes(extension)) asset = "file-image.svg";
  else if (["mp3", "ogg", "wav", "flac", "m4a", "wma"].includes(extension)) asset = "file-audio.svg";
  else if (["zip", "7z", "rar", "tar", "gz", "bz2", "xz"].includes(extension)) asset = "file-archive.svg";
  else if (extension === "pdf") asset = "file-pdf.svg";
  const wrapper = document.createElement("span");
  wrapper.className = `file-icon ${entry.isDir ? "folder" : "document"}`;
  const image = document.createElement("img");
  image.className = "file-type-icon";
  image.src = `/assets/${asset}`;
  image.alt = "";
  image.draggable = false;
  wrapper.append(image);
  const cacheKey = `${entry.isDir ? "dir" : "file"}:${entry.name}`;
  const cached = nativeIconCache.get(cacheKey);
  if (cached) image.src = cached;
  else if (cached !== null) {
    void fileIconDataUrl(entry.name, entry.isDir).then(url => {
      nativeIconCache.set(cacheKey, url);
      if (url && image.isConnected) image.src = url;
    }).catch(() => { nativeIconCache.set(cacheKey, null); });
  }
  if (entry.cache.offline || entry.cache.cached) {
    const badge = document.createElement("img");
    badge.className = `cache-badge ${entry.cache.offline ? "offline" : "cached"}`;
    badge.src = `/assets/${entry.cache.offline ? "ui-save-offline.svg" : "ui-entry-download.svg"}`;
    badge.alt = entry.cache.offline ? "Available offline" : "Cached local copy";
    badge.title = badge.alt;
    wrapper.append(badge);
  }
  return wrapper;
}
