/** Clipboard access must remain available without an active license. */
export function bindLicenseKeyPaste(
  key: HTMLInputElement,
  paste: HTMLButtonElement,
  readText: () => Promise<string>,
  showError: (error: unknown) => Promise<void>,
): void {
  paste.addEventListener("click", async () => {
    if (key.readOnly || paste.disabled) return;
    paste.disabled = true;
    try {
      const text = await readText();
      // A license refresh or dialog close can replace these controls while
      // the native clipboard request is pending.
      if (!key.isConnected || key.readOnly) return;
      key.value = text.trim();
      key.dispatchEvent(new Event("input", { bubbles: true }));
      key.focus();
    } catch (error) {
      if (key.isConnected) await showError(error);
    } finally {
      paste.disabled = key.readOnly;
    }
  });
}
