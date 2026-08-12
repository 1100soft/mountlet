"""Shared semantic and brand colors for UI elements with fixed meaning.

Ordinary widget foregrounds, backgrounds, borders, and selections must use Qt
palette roles.  These constants are reserved for semantic state and provider
branding, where the color itself carries meaning across themes.
"""

SUCCESS = "#16a34a"
DANGER = "#dc2626"
ALERT_BADGE = "#ef4444"
WARNING = "#f59e0b"
INFO = "#3b82f6"
DOWNLOAD = "#38bdf8"
CACHE_TEMPORARY = "#ff0000"
CACHE_PROTECTED = "#00ff00"
MUTED = "#6b7280"
MUTED_CHECKING = "#9ca3af"
ON_ACCENT = "#ffffff"

PROVIDER_COLORS = {
    "drive": "#34a853",
    "gphotos": "#ea4335",
    "dropbox": "#0061ff",
    "onedrive": "#0078d4",
    "box": "#0057c2",
    "pcloud": "#17a2d4",
    "iclouddrive": "#0a84ff",
    "koofr": WARNING,
    "protondrive": "#6d4aff",
    "mega": "#d9272e",
    "nextcloud": "#0082c9",
    "s3": "#ff9900",
    "webdav": "#64748b",
}

PROVIDER_FALLBACK = PROVIDER_COLORS["webdav"]

SEARCH_QUALITY_COLORS = {
    "exact": SUCCESS,
    "phrase": SUCCESS,
    "filename": INFO,
    "mixed": WARNING,
}


def search_quality_color(quality: str) -> str:
    return SEARCH_QUALITY_COLORS.get(quality, MUTED)

NEUTRAL_ICON_SOURCE = "#334155"

DARK_THEME_COLORS = {
    "Window": "#31363b",
    "WindowText": "#eff0f1",
    "Base": "#232629",
    "AlternateBase": "#31363b",
    "ToolTipBase": "#31363b",
    "ToolTipText": "#eff0f1",
    "Text": "#eff0f1",
    "Button": "#31363b",
    "ButtonText": "#eff0f1",
    "BrightText": ON_ACCENT,
    "Highlight": "#3daee9",
    "HighlightedText": ON_ACCENT,
    "PlaceholderText": "#a1a9b1",
}

LIGHT_THEME_COLORS = {
    "Window": "#eff0f1",
    "WindowText": "#232629",
    "Base": ON_ACCENT,
    "AlternateBase": "#f7f7f7",
    "ToolTipBase": ON_ACCENT,
    "ToolTipText": "#232629",
    "Text": "#232629",
    "Button": "#eff0f1",
    "ButtonText": "#232629",
    "BrightText": ON_ACCENT,
    "Highlight": "#3daee9",
    "HighlightedText": ON_ACCENT,
    "PlaceholderText": "#707880",
}
