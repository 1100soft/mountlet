# Python → Tauri parity ledger

The Python application remains the behavioral specification. A checked item
has a real Rust/Tauri implementation, not a static mock. Platform checks still
need to be exercised on their native operating systems before replacement.

## Application and platform lifecycle

- [x] Single-instance tray process, hidden startup, tray-owned show/hide/quit
- [x] One main taskbar identity with a transient detached file browser
- [x] Cached tray anchor and deterministic main/browser placement
- [x] Left-click tray toggle and right-click tray menu, with mount-status tooltip
- [x] Work-area helper separated by Linux, Windows, and macOS implementation
- [x] Startup stale-mount cleanup, lazy FUSE unmount, and tracked mount-process shutdown
- [x] Start-at-login integration on Linux, Windows, and macOS
- [x] Frozen-build rclone discovery and Windows upgrade-safe versioned runtime
- [x] Graphical rclone/FUSE prerequisite wizard
- [x] macOS accessory activation policy (no Dock icon)
- [ ] Native KWin/GNOME, Windows, and macOS multi-monitor acceptance matrix

## Settings and remote configuration

- [x] App configuration editor with dirty-only Save
- [x] Shortcut editor, three alternatives, defaults, and contextual conflicts
- [x] Remote mount/rclone editor with no-op detection and remount comparison
- [x] Add Remote flow with provider-specific fields and generic noninteractive rclone question wizard
- [x] Google account hint, Gmail-prefix entry, Drive client reuse, and no forced reauthentication
- [x] Browser OAuth plus interactive iCloud/other 2FA questions, with port 53682 conflict handling
- [x] File-manager discovery and desktop-aware folder open
- [x] Wayland single-window mode and GNOME Wayland pin disable
- [x] Persistent remote rename/delete migration for caches, paths, and metadata
- [x] Python-compatible encrypted `.mountlet` export/import and remote push/pull
- [x] Native config-bundle file dialogs
- [x] License/trial verification, activation, devices, and deactivation

## Main remote UI

- [x] Production assets, compact cards, usage cache, theme colors, and tooltips
- [x] Immediate hover/keyboard selection and gapless drag reordering
- [x] Sorting, reverse order, persistent order, context menu, and provider links
- [x] Real mount/unmount, bulk operations, auto-mount, errors, and auth retry
- [x] Filename-only indexed global search, ranked/capped results, no verification
- [x] Persistent notification history, unread badge, links, and deletion policy
- [x] Zoom controls, focus retention, stable scrollbars, and discrete metrics
- [x] Notice-server polling, persisted policy, dialogs, and native tray notifications
- [x] Dynamic native tray actions for selection, mount, folders, web, cache, and config

## File browser

- [x] Cached instant snapshots and cancellable background rclone listings
- [x] Persistent paths/selections with rapid stale-result suppression
- [x] Virtualized list, multi-selection, context menus, and fixed scrollbars
- [x] Integer row geometry and exact Size/Modified/Name allocation
- [x] Navigation, user shortcuts, search, inline rename, and file mutations
- [x] Local/native drop upload and internal cross-remote copy/move drag
- [x] Google Photos date projection, album-only mutations, and provider-specific failure guidance
- [x] Offline/cache manifest writes and immediate badge refresh
- [x] Scoped clear/remove operations that preserve protected/temporary descendants
- [x] Current-folder background polling and idle usage refresh
- [x] Native export drag after the pointer leaves the WebView, preserving internal drag
- [x] Local/cloud hash conflict detection, resolution UI, and idle change polling

## Support and quality

- [x] About/runtime/rclone/FUSE/work-area diagnostics and copyable rclone output
- [x] Locally generated redacted diagnostic report
- [x] Theme-variable-only UI colors and scalable production SVG assets
- [x] Browser build, Rust tests, native hidden-startup smoke test
- [x] Report preview/upload, panic log, clean shutdown, and reported-crash state
- [ ] Complete accessibility/native assistive-technology audit
- [ ] Cross-platform resource/latency benchmark and installer test matrix
