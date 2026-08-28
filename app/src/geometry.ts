import { MAX_ZOOM_STEP, MIN_ZOOM_STEP, zoomFactor } from "./model.ts";

/** Python/Qt uses round-to-even. Keep every discrete pixel metric identical. */
function roundToEven(value: number): number {
  const floor = Math.floor(value);
  const fraction = value - floor;
  if (fraction < 0.5) return floor;
  if (fraction > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

export function scaledMetric(reference: number, step: number): number {
  if (reference <= 0) return reference;
  return Math.max(1, roundToEven(reference * zoomFactor(Math.min(MAX_ZOOM_STEP, Math.max(MIN_ZOOM_STEP, step)))));
}

export interface UiMetrics {
  remoteRow: number;
  searchRow: number;
  searchHeader: number;
  fileRow: number;
  fileHeader: number;
  fileIcon: number;
  fileSizeColumn: number;
  fileModifiedColumn: number;
  chromeRow: number;
  menuRow: number;
  toolbarRow: number;
  layoutMargin: number;
  layoutSpacing: number;
  remoteChrome: number;
  remoteCardTop: number;
  browserChrome: number;
  remotePaneWidth: number;
  singleWindowWidth: number;
  browserWidth: number;
  browserMinHeight: number;
  purchaseRow: number;
}

const METRICS_CACHE = new Map<number, UiMetrics>();

export function metricsAt(step: number): UiMetrics {
  const normalized = Math.min(MAX_ZOOM_STEP, Math.max(MIN_ZOOM_STEP, step));
  const cached = METRICS_CACHE.get(normalized);
  if (cached) return cached;
  const metrics = {
    remoteRow: scaledMetric(40, normalized),
    searchRow: scaledMetric(22, normalized),
    searchHeader: scaledMetric(28, normalized),
    fileRow: scaledMetric(36, normalized),
    fileHeader: scaledMetric(28, normalized),
    fileIcon: scaledMetric(30, normalized),
    fileSizeColumn: scaledMetric(76, normalized),
    fileModifiedColumn: scaledMetric(124, normalized),
    chromeRow: scaledMetric(28, normalized),
    menuRow: scaledMetric(18, normalized),
    toolbarRow: scaledMetric(30, normalized),
    layoutMargin: scaledMetric(8, normalized),
    layoutSpacing: scaledMetric(5, normalized),
    remoteChrome: 0, remoteCardTop: 0, browserChrome: 0,
    remotePaneWidth: scaledMetric(540, normalized), singleWindowWidth: scaledMetric(1080, normalized),
    browserWidth: scaledMetric(540, normalized), browserMinHeight: scaledMetric(240, normalized),
    purchaseRow: scaledMetric(36, normalized),
  };
  const remoteGap = scaledMetric(4, normalized);
  metrics.remoteCardTop = metrics.layoutMargin + metrics.menuRow + remoteGap + metrics.toolbarRow + remoteGap + scaledMetric(28, normalized) + remoteGap;
  metrics.remoteChrome = metrics.remoteCardTop + remoteGap + metrics.toolbarRow + metrics.layoutMargin;
  metrics.browserChrome = 2 * metrics.layoutMargin + 2 * metrics.toolbarRow + scaledMetric(28, normalized) + metrics.toolbarRow
    + metrics.fileHeader + scaledMetric(26, normalized) + 6 * metrics.layoutSpacing;
  METRICS_CACHE.set(normalized, metrics);
  return metrics;
}

export function applyMetricVariables(step: number): void {
  const metrics = metricsAt(step);
  const root = document.documentElement.style;
  for (const [name, value] of Object.entries(metrics)) {
    root.setProperty(`--${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`, `${value}px`);
  }
}
