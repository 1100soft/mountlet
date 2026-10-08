# Resolved handoff: launch and tray-reopen keyboard focus

Date: 2026-08-28. Branch: `wip`.

## Resolution confirmed by user

The user confirmed the focus behavior works after these changes:

- Linux activation calls `present()` and grabs WebKit focus again from GTK's
  next idle turn, after the first window map has been processed.
- The 400 ms blanket suppression of native focus events was removed because it
  discarded legitimate user focus changes after reopening.
- Hiding snapshots the currently focused native window when available; ordinary
  `Focused(true)` events remain the fallback when clicking the tray has already
  removed application focus.

Treat these as regression-sensitive invariants. In particular, do not replace
the GTK idle callback with an arbitrary delay or reintroduce timed suppression.

## Root causes

Previous attempts only set DOM `activeElement` and called Tauri
`WebviewWindow::set_focus()`. That maps to tao `WindowRequest::Focus`, which
**does nothing unless `gtk_window.get_visible()` is already true**. The first
tray show queues `show()` and then `set_focus()` on the same turn, so focus is
dropped. The WebKitGTK widget is never `grab_focus()`'d, so Left/Right never
reach JS until a click inside the view.

On later reopen, a 400 ms blanket suppression of `WindowEvent::Focused`
discarded genuine focus changes. The remembered value therefore remained
`main`, even after the user worked in the detached browser.

### What changed

- `src-tauri/src/lib.rs`
  - `activate_linux_keyboard`: synchronous `show_all` + `present` +
    `grab_focus`, repeated on GTK's next idle turn after mapping.
  - `activate_window_keyboard` used from `show_window_stack` and `focus_window`.
  - Native focus events are never suppressed by an arbitrary time window.
  - `hide_window_stack` snapshots the actual native focus owner when possible.
- `src/main.ts`
  - `parkedFocusOwner` is saved on `window.blur` (hide / OS unfocus).
  - Restore always reapplies the parked owner; it no longer trusts whatever
    WebKit focused first.
  - Removed the 80 ms / microtask retry.

## Manual regression matrix

1. First tray open after a cold start: Left/Right between remote list and
   file list without clicking the window.
2. Hide and reopen with the remote list focused: remotes should still own
   keyboard focus.
3. Hide and reopen with the file list focused: file list should remain.
4. Multiple-window mode (X11): reopen should restore the last native window.

Do not move GTK work back onto the ksni callback thread.

## User-visible behavior (original)

- On the first tray opening after process launch, Left/Right pane navigation
  does nothing. Clicking inside the main window immediately makes it work.
- After hiding and reopening, Left/Right navigation works, but focus has been
  observed returning to the file browser even when the main window owned focus
  before hiding.

## Regression-sensitive constraints

- The app starts hidden and must remain tray-owned.
- Linux tray callbacks must schedule UI work with `run_on_main_thread`.
- Menu clicks must not activate a remote through pointer hover.
- Search fields and focused lists must retain their owner through ordinary
  refocusing.
- In multiple-window mode reopening should restore the last actually focused
  native window; in single-window mode it should restore the last DOM pane.

## Checks

```bash
cd app
npm run build
cargo test --offline -j 2 --manifest-path src-tauri/Cargo.toml
cargo clippy --offline -j 2 --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

Rust/native focus changes require restarting `npm run tauri:dev`; hot reload is
not sufficient.
