function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  result.className = className;
  result.textContent = text;
  return result;
}

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function bindScaledSelect(select: HTMLSelectElement): HTMLSelectElement {
  if (select.dataset.scaledSelect === "true") return select;
  select.dataset.scaledSelect = "true";
  const open = () => {
    if (select.disabled) return;
    document.querySelector(".select-menu")?.remove();
    const menu = node("div", "select-menu context-menu");
    const rect = select.getBoundingClientRect();
    menu.style.left = `${rect.left}px`;
    menu.style.top = `${rect.bottom}px`;
    menu.style.minWidth = `${Math.max(rect.width, 120)}px`;
    let active = select.selectedIndex;
    const items: HTMLButtonElement[] = [];
    const close = () => {
      menu.remove();
      window.removeEventListener("mousedown", onPointer, true);
      window.removeEventListener("keydown", onKey, true);
    };
    const choose = (index: number) => {
      select.selectedIndex = index;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      close();
      select.focus();
    };
    const paint = () => {
      items.forEach((item, index) => item.classList.toggle("active", index === active));
      items[active]?.scrollIntoView({ block: "nearest" });
    };
    const onPointer = (event: MouseEvent) => {
      if (event.target instanceof Node && menu.contains(event.target)) return;
      close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        select.focus();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        active = Math.min(items.length - 1, active + 1);
        paint();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        active = Math.max(0, active - 1);
        paint();
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        choose(active);
      }
    };
    for (const [index, option] of [...select.options].entries()) {
      const item = node("button", `context-action${index === select.selectedIndex ? " active" : ""}`, option.text);
      item.type = "button";
      item.addEventListener("click", () => choose(index));
      menu.append(item);
      items.push(item);
    }
    document.body.append(menu);
    window.setTimeout(() => {
      window.addEventListener("mousedown", onPointer, true);
      window.addEventListener("keydown", onKey, true);
    }, 0);
    paint();
  };
  select.addEventListener("mousedown", event => {
    if (event.button !== 0) return;
    event.preventDefault();
    open();
  });
  select.addEventListener("keydown", event => {
    if (event.key === " " || event.key === "Enter" || event.key === "ArrowDown") {
      event.preventDefault();
      open();
    }
  });
  return select;
}

export function trapModalFocus(layer: HTMLElement, dialog: HTMLElement, defaultFocus?: HTMLElement | null): void {
  const app = document.querySelector<HTMLElement>("#app");
  app?.setAttribute("inert", "");
  const release = () => {
    if (!document.querySelector(".modal-layer")) app?.removeAttribute("inert");
  };
  const observer = new MutationObserver(() => {
    if (!layer.isConnected) {
      release();
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true });
  const focusable = () => [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(item => !item.closest("[hidden], [inert]"));
  layer.addEventListener("keydown", event => {
    event.stopPropagation();
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      event.preventDefault();
      const cancel = dialog.querySelector<HTMLButtonElement>("[data-dialog-cancel]")
        ?? [...dialog.querySelectorAll<HTMLButtonElement>(".dialog-actions button")].find(button => /^(cancel|close|no|later)$/i.test(button.textContent?.trim() ?? ""));
      if (cancel && !cancel.disabled) cancel.click(); else layer.remove();
      return;
    }
    if (event.key === "Enter" && !(event.target instanceof HTMLTextAreaElement) && !(event.target instanceof HTMLButtonElement)) {
      const confirm = dialog.querySelector<HTMLButtonElement>("[data-dialog-confirm], .dialog-actions .primary");
      if (confirm && !confirm.disabled) { event.preventDefault(); confirm.click(); }
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  window.focus();
  queueMicrotask(() => (defaultFocus && dialog.contains(defaultFocus) ? defaultFocus : focusable()[0])?.focus());
}

interface DialogOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string | null;
  input?: { value: string; selectBaseName?: boolean; password?: boolean; choices?: Array<{ value: string; label: string }> };
}

export function ownedDialog(options: DialogOptions): Promise<string | boolean> {
  document.querySelector(".owned-dialog-layer")?.remove();
  return new Promise(resolve => {
    const layer = node("div", "modal-layer owned-dialog-layer");
    const dialog = node("section", "modal-dialog owned-dialog");
    dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
    dialog.append(node("h2", "", options.title), node("p", "dialog-message", options.message));
    let input: HTMLInputElement | HTMLSelectElement | null = null;
    const inputOptions = options.input;
    if (inputOptions) {
        if (inputOptions.choices?.length) {
        input = bindScaledSelect(document.createElement("select"));
        for (const choice of inputOptions.choices) input.add(new Option(choice.label, choice.value));
        if (inputOptions.value && !inputOptions.choices.some(choice => choice.value === inputOptions.value)) input.add(new Option(inputOptions.value, inputOptions.value));
      } else {
        input = document.createElement("input"); input.type = inputOptions.password ? "password" : "text";
      }
      input.value = inputOptions.value;
      dialog.append(input);
    }
    const actions = node("div", "dialog-actions");
    const confirm = node("button", "primary", options.confirmLabel ?? "OK");
    confirm.dataset.dialogConfirm = "true";
    const finish = (value: string | boolean) => { layer.remove(); resolve(value); };
    confirm.addEventListener("click", () => finish(input ? input.value : true));
    if (options.cancelLabel !== null) {
      const cancel = node("button", "", options.cancelLabel ?? "Cancel");
      cancel.dataset.dialogCancel = "true";
      cancel.addEventListener("click", () => finish(false)); actions.append(cancel);
    }
    actions.append(confirm); dialog.append(actions); layer.append(dialog); document.body.append(layer);
    layer.addEventListener("mousedown", event => { if (event.target === layer) finish(false); });
    const initial = input ?? (options.cancelLabel !== null ? actions.querySelector("button:not(.primary)") : confirm);
    trapModalFocus(layer, dialog, initial as HTMLElement | null);
    if (input && inputOptions) {
      if (input instanceof HTMLInputElement && inputOptions.selectBaseName) {
        const dot = input.value.lastIndexOf("."); input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
      } else if (input instanceof HTMLInputElement) input.select();
    }
  });
}

export async function showError(title: string, error: unknown): Promise<void> {
  await ownedDialog({ title, message: String(error), confirmLabel: "Close", cancelLabel: null });
}

export async function confirmOwned(title: string, message: string, confirmLabel = "Yes"): Promise<boolean> {
  return await ownedDialog({ title, message, confirmLabel, cancelLabel: "No" }) === true;
}

export async function promptOwned(title: string, message: string, value: string, selectBaseName = false): Promise<string | null> {
  const result = await ownedDialog({ title, message, input: { value, selectBaseName } });
  return typeof result === "string" ? result : null;
}

export async function promptWizardOption(title: string, message: string, value: string, password: boolean, choices: Array<{ value: string; label: string }>): Promise<string | null> {
  const result = await ownedDialog({ title, message, input: { value, password, choices: choices.length ? choices : undefined }, confirmLabel: "Continue" });
  return typeof result === "string" ? result : null;
}
