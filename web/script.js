let releaseFilesPromise = null;
let releaseCatalogPromise = null;
let notificationsPromise = null;
let loadedNotifications = [];

async function openConfiguredLink(group, key) {
  if (group === "downloads") {
    const catalog = await loadReleaseCatalog();
    const version = document.querySelector("#release-version")?.value || catalog?.latest || "";
    const release = catalog?.releases?.find((item) => item.version === version);
    if (catalog?.releases?.length) {
      if (!release?.files?.[key]) {
        window.alert(`That build is not available for Mountlet v${version}.`);
        return;
      }
      window.location.href = `/api/download/${encodeURIComponent(key)}?version=${encodeURIComponent(version)}`;
      return;
    }
    const fileName = await getReleaseDownloadFile(key);
    if (!fileName) {
      window.alert(`No release file is configured for "${key}".`);
      return;
    }
    window.location.href = `/api/download/${encodeURIComponent(fileName)}`;
    return;
  }
  const config = window.MOUNTLET_SITE_CONFIG || {};
  const url = config[group] && config[group][key];
  if (!url || url.includes("replace-")) {
    const label = group === "checkout" ? "Stripe Payment Link" : "release asset URL";
    window.alert(`Set the ${label} for "${key}" in web/config.js before launch.`);
    return;
  }
  window.location.href = url;
}

async function loadReleaseCatalog() {
  if (!releaseCatalogPromise) {
    releaseCatalogPromise = fetch("/api/releases", {headers: {accept: "application/json"}})
      .then(async (response) => response.ok ? response.json() : null)
      .catch(() => null);
  }
  return releaseCatalogPromise;
}

async function getReleaseDownloadFile(key) {
  const releases = await loadReleaseFiles();
  return releases.legacyDownloads && releases.legacyDownloads[key];
}

async function loadReleaseFiles() {
  if (!releaseFilesPromise) {
    releaseFilesPromise = fetch("release-files.json", {headers: {accept: "application/json"}})
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Release file list returned ${response.status}.`);
        }
        return response.json();
      });
  }
  return releaseFilesPromise;
}

function applyConfiguredLinks() {
  const config = window.MOUNTLET_SITE_CONFIG || {};
  document.querySelectorAll("[data-config-link]").forEach((link) => {
    const key = link.dataset.configLink || "";
    const url = config.source && config.source[key];
    if (url) {
      link.href = url;
    }
  });
}

async function startCheckout(button) {
  const kind = selectedLicenseAction();
  const plan = selectedLicensePlan();
  const deviceCount = Number(document.querySelector("#add-device-count")?.value || 0);
  const checkoutDeviceCount = kind === "add_devices"
    ? Number(document.querySelector("#add-device-count")?.value || 1)
    : undefined;
  const licenseKey = kind === "add_devices"
    ? String(document.querySelector("#existing-license-key")?.value || "").trim()
    : "";
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "Opening checkout...";
  try {
    const response = await fetch("/api/checkout", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({kind, plan, deviceCount: checkoutDeviceCount ?? deviceCount, licenseKey}),
    });
    const data = await readJsonResponse(response, "Checkout");
    if (response.ok && data.ok && !data.url) {
      const renewalText = data.billingModel && data.billingModel !== "lifetime"
        ? ` Renews ${formatLicenseDate(data.expiresAt)}.`
        : "";
      const baseMessage = data.message || `Device slots were added. This license now has ${Number(data.usedDevices || 0)}/${Number(data.devices || 0)} devices used.`;
      showLicenseResult({
        message: `${baseMessage}${renewalText}`,
        output: "Device slots added",
      });
      button.disabled = false;
      button.textContent = originalText;
      if (kind === "add_devices" && licenseKey) {
        validateLicenseKey();
      }
      return;
    }
    if (!response.ok || data.error || !data.url) {
      throw new Error(data.error || "Checkout is not configured yet.");
    }
    window.location.href = data.url;
  } catch (error) {
    window.alert(error.message || "Checkout is not configured yet.");
    button.disabled = false;
    button.textContent = originalText;
  }
}

const LICENSE_KEY_PATTERN = /^(MNT|MTB)-[A-Z2-9]{5}-[A-Z2-9]{5}-[A-Z2-9]{5}-[A-Z2-9]{5}$/;
const PUBLIC_BETA_KEY = String(window.MOUNTLET_SITE_CONFIG?.license?.publicBetaKey || "").trim();
const LICENSE_PLANS = {
  monthly: {
    label: "Monthly",
    base: 5,
    extra: 1,
    suffix: "/mo",
  },
  annual: {
    label: "Annual",
    base: 30,
    extra: 6,
    suffix: "/yr",
  },
  lifetime: {
    label: "Lifetime",
    base: 50,
    extra: 10,
    suffix: "",
  },
};
let validateTimer = 0;
let validatedLicenseDevices = 0;
let validatedUsedDevices = 0;
let validatedBillingModel = "";
let validatedExpiresAt = "";
let validatedLicenseKind = "";
let currentCheckoutSessionId = "";

function updateAddDevicePrice() {
  updateCart();
}

async function validateLicenseKey() {
  const input = document.querySelector("#existing-license-key");
  const status = document.querySelector("#license-key-status");
  if (!input || !status) {
    return;
  }
  const licenseKey = input.value.trim();
  setAddDeviceEnabled(false);
  if (!licenseKey) {
    setLicenseStatus("", "");
    return;
  }
  if (!LICENSE_KEY_PATTERN.test(licenseKey.toUpperCase())) {
    setLicenseStatus("Invalid key format.", "invalid");
    return;
  }
  setLicenseStatus("Checking key...", "");
  try {
    const response = await fetch("/api/license/validate", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({licenseKey}),
    });
    const data = await readJsonResponse(response, "License check");
    if (!response.ok || data.error || !data.ok) {
      throw new Error(data.error || "License key is not valid.");
    }
    validatedLicenseDevices = Number(data.maxDevices || 0);
    validatedUsedDevices = Number(data.usedDevices || 0);
    validatedBillingModel = String(data.billingModel || "lifetime");
    validatedExpiresAt = String(data.expiresAt || "");
    validatedLicenseKind = String(data.licenseKind || "paid");
    if (validatedLicenseKind === "beta") {
      setLicenseStatus(
        "Valid public beta key. It renews daily while the beta is open; add-device checkout is disabled.",
        "valid"
      );
      setAddDeviceEnabled(false);
      updateCart();
      return;
    }
    const billingText = validatedBillingModel === "lifetime" ? "key" : `${validatedBillingModel} key`;
    const renewalText = validatedBillingModel === "lifetime"
      ? ""
      : ` Renews ${formatLicenseDate(validatedExpiresAt)}.`;
    setLicenseStatus(
      `Valid ${billingText}; ${validatedUsedDevices}/${validatedLicenseDevices} devices used.${renewalText}`,
      "valid"
    );
    setAddDeviceEnabled(true);
    updateCart();
  } catch (error) {
    validatedLicenseDevices = 0;
    validatedUsedDevices = 0;
    validatedBillingModel = "";
    validatedExpiresAt = "";
    validatedLicenseKind = "";
    setLicenseStatus(error.message || "License key is not valid.", "invalid");
    updateCart();
  }
}

function usePublicBetaKey() {
  const betaOption = document.querySelector('input[name="license-action"][value="add_devices"]');
  const input = document.querySelector("#existing-license-key");
  if (betaOption) {
    betaOption.checked = true;
  }
  updatePricingMode();
  if (input) {
    input.value = PUBLIC_BETA_KEY;
  }
  validateLicenseKey();
}

function setLicenseStatus(message, state) {
  const status = document.querySelector("#license-key-status");
  if (!status) {
    return;
  }
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle("valid", state === "valid");
  status.classList.toggle("invalid", state === "invalid");
}

function updateValidateButtonState() {
  const button = document.querySelector("#validate-license-key");
  const input = document.querySelector("#existing-license-key");
  if (!button || !input) {
    return;
  }
  const canCheck = selectedLicenseAction() === "add_devices"
    && !input.disabled
    && LICENSE_KEY_PATTERN.test(input.value.trim().toUpperCase());
  button.disabled = !canCheck;
}

function setAddDeviceEnabled(enabled) {
  const card = document.querySelector("#add-device-card");
  const input = document.querySelector("#add-device-count");
  card?.classList.toggle("disabled", !enabled);
  if (input) {
    input.disabled = !enabled;
  }
  updateCart();
}

function selectedLicenseAction() {
  return document.querySelector('input[name="license-action"]:checked')?.value || "new_license";
}

function selectedLicensePlan() {
  return document.querySelector('input[name="license-plan"]:checked')?.value || "monthly";
}

function updatePricingMode() {
  const action = selectedLicenseAction();
  const keyField = document.querySelector("#existing-license-key");
  const validateButton = document.querySelector("#validate-license-key");
  const planChoice = document.querySelector("#license-plan-choice");
  if (action === "new_license") {
    validatedLicenseDevices = 0;
    validatedUsedDevices = 0;
    validatedBillingModel = "";
    validatedExpiresAt = "";
    validatedLicenseKind = "";
    if (keyField) {
      keyField.disabled = true;
    }
    if (planChoice) {
      planChoice.disabled = false;
      planChoice.classList.remove("disabled");
    }
    const addInput = document.querySelector("#add-device-count");
    if (addInput) {
      addInput.min = "0";
    }
    if (validateButton) {
      validateButton.disabled = true;
    }
    setLicenseStatus("", "");
    setAddDeviceEnabled(true);
  } else {
    const addInput = document.querySelector("#add-device-count");
    if (addInput) {
      addInput.min = "1";
      if (Number(addInput.value || 0) < 1) {
        addInput.value = "1";
      }
    }
    if (keyField) {
      keyField.disabled = false;
    }
    if (planChoice) {
      planChoice.disabled = true;
      planChoice.classList.add("disabled");
    }
    if (validateButton) {
      validateButton.disabled = true;
    }
    setLicenseStatus("", "");
    setAddDeviceEnabled(false);
    keyField?.focus();
  }
  updateValidateButtonState();
  updateCart();
}

function applyPricingUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const action = String(params.get("license_action") || params.get("licenseAction") || "").trim();
  const key = String(params.get("license_key") || params.get("licenseKey") || "").trim().toUpperCase();
  if (action === "add_devices" || key) {
    setActiveTab("pricing", {skipHash: Boolean(window.location.hash)});
    const addDevices = document.querySelector('input[name="license-action"][value="add_devices"]');
    if (addDevices) {
      addDevices.checked = true;
    }
  }
  if (key) {
    const keyField = document.querySelector("#existing-license-key");
    if (keyField) {
      keyField.value = key;
    }
  }
  return LICENSE_KEY_PATTERN.test(key);
}

function updateCart() {
  const lines = document.querySelector("#cart-lines");
  const total = document.querySelector("#cart-total");
  const devices = document.querySelector("#cart-devices");
  const checkoutButton = document.querySelector("#checkout-button");
  const planPrice = document.querySelector("#license-plan-price");
  const addDevicePrice = document.querySelector("#add-device-price");
  if (!lines || !total || !devices || !checkoutButton) {
    return;
  }
  const action = selectedLicenseAction();
  const plan = LICENSE_PLANS[selectedLicensePlan()] || LICENSE_PLANS.monthly;
  const addInput = document.querySelector("#add-device-count");
  const extraDevices = Math.max(0, Math.floor(Number(addInput?.value || 0)));
  let amount = 0;
  let totalDevices = 0;
  const parts = [];
  if (planPrice) {
    planPrice.textContent = `$${plan.base}${plan.suffix}`;
  }
  if (addDevicePrice) {
    const existingPlan = LICENSE_PLANS[validatedBillingModel] || LICENSE_PLANS.lifetime;
    addDevicePrice.textContent = `$${action === "new_license" ? plan.extra : existingPlan.extra}/device`;
  }
  if (action === "new_license") {
    amount += plan.base;
    totalDevices = 1 + extraDevices;
    parts.push([`${plan.label} license`, `$${plan.base}${plan.suffix}`]);
    if (extraDevices > 0) {
      amount += extraDevices * plan.extra;
      parts.push([`Extra devices x ${extraDevices}`, `$${extraDevices * plan.extra}${plan.suffix}`]);
    }
    checkoutButton.disabled = false;
  } else {
    if (validatedLicenseKind === "beta") {
      parts.push(["Public beta key", "Free"]);
      checkoutButton.disabled = true;
      lines.innerHTML = parts.map(([label, price]) => `<div><dt>${label}</dt><dd>${price}</dd></div>`).join("");
      devices.textContent = "Temporary beta access";
      total.textContent = "$0";
      return;
    }
    const enabled = !addInput?.disabled;
    const existingPlan = LICENSE_PLANS[validatedBillingModel] || LICENSE_PLANS.lifetime;
    amount = extraDevices * existingPlan.extra;
    totalDevices = validatedLicenseDevices + extraDevices;
    parts.push([
      `${existingPlan.label} extra devices x ${extraDevices}`,
      validatedBillingModel === "lifetime" ? `$${amount}` : `$${amount}${existingPlan.suffix}`,
    ]);
    checkoutButton.disabled = !enabled;
  }
  lines.innerHTML = parts.map(([label, price]) => `<div><dt>${label}</dt><dd>${price}</dd></div>`).join("");
  devices.textContent = totalDevices > 0
    ? `${totalDevices} device${totalDevices === 1 ? "" : "s"} total`
    : "Check a license key to calculate total devices";
  total.textContent = action === "add_devices" && validatedBillingModel && validatedBillingModel !== "lifetime"
    ? "Prorated by Stripe"
    : `$${amount}`;
}

async function loadCheckoutLicense() {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("checkout_session_id");
  if (!sessionId) {
    return;
  }
  currentCheckoutSessionId = sessionId;
  setActiveTab("license", {skipHash: true});
  const result = document.querySelector("#license-result");
  const output = document.querySelector("#license-key-output");
  const message = document.querySelector("#license-result-message");
  if (!result || !output) {
    return;
  }
  result.hidden = false;
  output.textContent = "Preparing your license key...";
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    try {
      const response = await fetch(`/api/license/checkout?session_id=${encodeURIComponent(sessionId)}`, {
        headers: {accept: "application/json"},
      });
      const data = await readJsonResponse(response, "License lookup");
      if (response.ok && data.licenseKey) {
        if (message) {
          const renewalText = data.billingModel && data.billingModel !== "lifetime"
            ? ` Renews ${formatLicenseDate(data.expiresAt)}.`
            : "";
          message.textContent = `Save this key now. It covers ${Number(data.usedDevices || 0)}/${Number(data.devices || 0)} activated devices.${renewalText}`;
        }
        output.textContent = data.licenseKey;
        const emailButton = document.querySelector("#email-license-key");
        if (emailButton) {
          emailButton.hidden = false;
          emailButton.disabled = false;
        }
        const input = document.querySelector("#existing-license-key");
        if (input) {
          input.value = data.licenseKey;
          validateLicenseKey();
        }
        return;
      }
      if (response.ok && data.kind === "add_devices") {
        if (message) {
          const renewalText = data.billingModel && data.billingModel !== "lifetime"
            ? ` Renews ${formatLicenseDate(data.expiresAt)}.`
            : "";
          message.textContent = `Device slots were added. This license now has ${Number(data.usedDevices || 0)}/${Number(data.devices || 0)} devices used.${renewalText}`;
        }
        output.textContent = "Device slots added";
        const emailButton = document.querySelector("#email-license-key");
        if (emailButton) {
          emailButton.hidden = true;
        }
        return;
      }
      output.textContent = data.error || "The license key is not ready yet.";
    } catch (error) {
      output.textContent = error.message || "Could not load the license key.";
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function showLicenseResult({message, output}) {
  setActiveTab("license", {skipHash: true});
  const result = document.querySelector("#license-result");
  const outputNode = document.querySelector("#license-key-output");
  const messageNode = document.querySelector("#license-result-message");
  if (result) {
    result.hidden = false;
  }
  if (messageNode) {
    messageNode.textContent = message;
  }
  if (outputNode) {
    outputNode.textContent = output;
  }
}

function formatLicenseDate(value) {
  if (!value) {
    return "at the end of the current paid period";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function readJsonResponse(response, label) {
  const text = await response.text();
  if (!text.trim()) {
    throw new Error(`${label} returned an empty response (${response.status}). Is Wrangler Pages running with Functions enabled?`);
  }
  try {
    return JSON.parse(text);
  } catch (_error) {
    const detail = text.length > 240 ? `${text.slice(0, 240)}...` : text;
    throw new Error(`${label} returned non-JSON response (${response.status}): ${detail}`);
  }
}

function normalizeTabName(value) {
  return String(value || "").replace(/^#/, "") || "home";
}

function resetTabScroll() {
  const pageFrame = document.querySelector(".page-frame");
  if (pageFrame) {
    pageFrame.scrollTop = 0;
    pageFrame.scrollLeft = 0;
  }
  window.scrollTo(0, 0);
}

function setActiveTab(nextTab, options = {}) {
  const tabName = normalizeTabName(nextTab);
  const panel = document.querySelector(`[data-panel="${tabName}"]`);
  if (!panel) {
    setActiveTab("home", options);
    return;
  }

  const activePanel = document.querySelector("[data-panel].active");
  const tabChanged = activePanel !== panel;

  document.querySelectorAll("[data-panel]").forEach((candidate) => {
    const isActive = candidate === panel;
    candidate.classList.toggle("active", isActive);
    candidate.hidden = !isActive;
  });

  document.querySelectorAll(".tab-nav .tab-link").forEach((button) => {
    const isActive = button.dataset.tab === tabName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  });

  if (!options.skipHash) {
    const nextHash = `#${tabName}`;
    if (window.location.hash !== nextHash) {
      window.history.pushState(null, "", nextHash);
    }
  }

  if (tabChanged && options.resetScroll !== false) {
    resetTabScroll();
  }

  if (tabName === "notifications") {
    loadNotifications().catch(() => {});
  }
}

function openLinkedFaq() {
  const faqId = new URLSearchParams(window.location.search).get("faq");
  if (!faqId) {
    return;
  }
  const entry = document.getElementById(faqId);
  if (!(entry instanceof HTMLDetailsElement) || !entry.closest("#panel-faq")) {
    return;
  }
  entry.hidden = false;
  entry.open = true;
  requestAnimationFrame(() => entry.scrollIntoView({block: "start"}));
}

async function loadNotifications({force = false} = {}) {
  const status = document.querySelector("#notifications-status");
  const refreshButton = document.querySelector("#refresh-notifications");
  if (force) {
    notificationsPromise = null;
  }
  if (!notificationsPromise) {
    notificationsPromise = fetch("/api/notices", {headers: {accept: "application/json"}})
      .then(async (response) => {
        const data = await readJsonResponse(response, "Notifications");
        if (!response.ok || data.error || !data.ok) {
          throw new Error(data.error || "Could not load notifications.");
        }
        return Array.isArray(data.notices) ? data.notices : [];
      })
      .catch((error) => {
        notificationsPromise = null;
        throw error;
      });
  }
  if (status) {
    status.hidden = false;
    status.textContent = "Loading notifications...";
    status.classList.remove("error");
  }
  if (refreshButton) {
    refreshButton.disabled = true;
  }
  try {
    const notices = await notificationsPromise;
    loadedNotifications = notices;
    renderNotifications(notices, document.querySelector("#notification-search")?.value || "");
    if (status) {
      status.textContent = notices.length ? "" : "There are no current notifications.";
      status.hidden = Boolean(notices.length);
    }
  } catch (error) {
    if (status) {
      status.hidden = false;
      status.textContent = error.message || "Could not load notifications.";
      status.classList.add("error");
    }
  } finally {
    if (refreshButton) {
      refreshButton.disabled = false;
    }
  }
}

function renderNotifications(notices, query = "") {
  const list = document.querySelector("#notifications-list");
  if (!list) {
    return;
  }
  list.replaceChildren();
  const searchText = String(query || "").trim().toLocaleLowerCase();
  const ordered = notices.filter((notice) => {
    if (!searchText) {
      return true;
    }
    return [notice.title, notice.message, notice.type, formatNoticeDate(notice.updatedAt)]
      .some((value) => String(value || "").toLocaleLowerCase().includes(searchText));
  }).sort((left, right) => {
    const lifecycle = Number(Boolean(left.archived)) - Number(Boolean(right.archived));
    return lifecycle || String(right.updatedAt || "").localeCompare(String(left.updatedAt || ""));
  });
  const active = ordered.filter((notice) => !notice.archived);
  const archived = ordered.filter((notice) => notice.archived);
  for (const notice of active) {
    list.append(createNotificationCard(notice));
  }
  if (archived.length) {
    const archive = document.createElement("details");
    archive.className = "notification-archive";
    const summary = document.createElement("summary");
    summary.textContent = `Archive (${archived.length})`;
    const archiveList = document.createElement("div");
    archiveList.className = "notification-archive-list";
    for (const notice of archived) {
      archiveList.append(createNotificationCard(notice));
    }
    archive.append(summary, archiveList);
    archive.open = Boolean(searchText);
    list.append(archive);
  }
  if (!ordered.length) {
    const empty = document.createElement("p");
    empty.className = "filter-status";
    empty.textContent = searchText ? "No matching notifications." : "There are no notifications.";
    list.append(empty);
  }
}

function createNotificationCard(notice) {
  const article = document.createElement("article");
  const level = ["critical", "important"].includes(String(notice.level || "").toLowerCase())
    ? String(notice.level).toLowerCase()
    : "info";
  article.className = `website-notice notice-${level}${notice.archived ? " archived" : " active"}`;
  article.tabIndex = 0;
  article.setAttribute("role", "button");
  article.setAttribute("aria-expanded", "false");

  const accent = document.createElement("span");
  accent.className = "website-notice-accent";
  accent.setAttribute("aria-hidden", "true");

  const header = document.createElement("div");
  header.className = "website-notice-header";
  const title = document.createElement("h3");
  title.textContent = String(notice.title || "Notification");
  const date = document.createElement("time");
  date.dateTime = String(notice.updatedAt || "");
  date.textContent = formatNoticeDate(notice.updatedAt);
  header.append(title, date);

  const message = document.createElement("p");
  message.className = "website-notice-message";
  message.textContent = String(notice.message || "");
  const footer = document.createElement("div");
  footer.className = "website-notice-footer";
  article.append(accent, header, message, footer);

  if (notice.url) {
    const link = document.createElement("a");
    link.className = "website-notice-link external-link";
    link.href = String(notice.url);
    link.rel = "noreferrer";
    link.textContent = "Read more";
    footer.append(link);
  }
  article.addEventListener("click", (event) => {
    if (!event.target.closest("a")) {
      toggleExpandedNotification(article);
    }
  });
  article.addEventListener("keydown", (event) => {
    if (!event.target.closest("a") && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      toggleExpandedNotification(article);
    }
  });
  return article;
}

function toggleExpandedNotification(selected) {
  const shouldExpand = !selected.classList.contains("expanded");
  document.querySelectorAll(".website-notice.expanded").forEach((notice) => {
    notice.classList.remove("expanded");
    notice.setAttribute("aria-expanded", "false");
  });
  if (shouldExpand) {
    selected.classList.add("expanded");
    selected.setAttribute("aria-expanded", "true");
  }
}

function formatNoticeDate(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) {
    return String(value || "");
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function filterFaq(query) {
  const panel = document.querySelector("#panel-faq");
  if (!panel) {
    return;
  }
  const searchText = String(query || "").trim().toLocaleLowerCase();
  const entries = [...panel.querySelectorAll(":scope > details")];
  let visible = 0;
  for (const entry of entries) {
    const matches = !searchText || entry.textContent.toLocaleLowerCase().includes(searchText);
    entry.hidden = !matches;
    visible += Number(matches);
  }
  const status = document.querySelector("#faq-search-status");
  if (status) {
    status.textContent = searchText && !visible ? "No matching answers." : "";
  }
}

async function submitSupportRequest(form) {
  const button = form.querySelector('button[type="submit"]');
  const status = document.querySelector("#support-status");
  const data = new FormData(form);
  const message = String(data.get("message") || "").trim();
  if (message.length < 10) {
    status.textContent = "Please provide a little more detail.";
    status.className = "inline-status invalid";
    return;
  }
  button.disabled = true;
  status.textContent = "Sending...";
  status.className = "inline-status";
  try {
    const response = await fetch("/api/report", {
      method: "POST",
      headers: {"content-type": "application/json", accept: "application/json"},
      body: JSON.stringify({
        kind: "support",
        message,
        contact: String(data.get("contact") || "").trim(),
        website: String(data.get("website") || ""),
        metadata: {
          source: "website",
          category: String(data.get("category") || "question"),
          page: window.location.origin,
        },
      }),
    });
    const result = await readJsonResponse(response, "Support request");
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "The support request could not be sent.");
    }
    form.reset();
    status.textContent = "Request sent.";
    status.className = "inline-status valid";
  } catch (error) {
    status.textContent = error.message || "The support request could not be sent.";
    status.className = "inline-status invalid";
  } finally {
    button.disabled = false;
  }
}

const downloadTargetLabels = {
  "windows-x64": "Windows x64",
  "macos-x64": "macOS Intel x64",
  "macos-arm64": "macOS Apple silicon",
  "linux-x64": "Linux x64",
};

let detectedHomeDownloadKey = "";

function updateHomeDownload(target) {
  const button = document.querySelector("#home-download-button");
  if (!button || !target) return;
  detectedHomeDownloadKey = target.dataset.downloadStandard || "";
  const label = downloadTargetLabels[target.value] || "your platform";
  button.textContent = `Download Mountlet for ${label}`;
}

function selectedDownloadInput() {
  return document.querySelector('input[name="download-target"]:checked');
}

function selectedDownloadKey() {
  const input = selectedDownloadInput();
  if (!input) return "";
  return document.querySelector("#lean-build")?.checked
    ? input.dataset.downloadLean || ""
    : input.dataset.downloadStandard || "";
}

function updateDownloadSelection() {
  const input = selectedDownloadInput();
  const lean = Boolean(document.querySelector("#lean-build")?.checked);
  const button = document.querySelector("#selected-download-button");
  const note = document.querySelector("#download-selection-note");
  const label = downloadTargetLabels[input?.value] || "selected platform";
  const version = document.querySelector("#release-version")?.value || "";
  if (button) {
    button.disabled = !input;
    button.textContent = `Download ${lean ? "lean " : ""}Mountlet${version ? ` v${version}` : ""} for ${label}`;
  }
  if (note) {
    note.textContent = lean
      ? "The lean build requires a compatible rclone installed on the computer."
      : "The standard build includes an app-local rclone.";
  }
}

async function initializeReleaseSelection() {
  const select = document.querySelector("#release-version");
  if (!select) return;
  const catalog = await loadReleaseCatalog();
  if (!catalog?.releases?.length) {
    select.disabled = true;
    select.title = "Only the current legacy release is available.";
    return;
  }
  select.replaceChildren(...catalog.releases.map((release, index) => {
    const option = document.createElement("option");
    option.value = release.version;
    option.textContent = `v${release.version}${index === 0 ? " (latest)" : ""}`;
    return option;
  }));
  select.value = catalog.latest || catalog.releases[0].version;
  updateDownloadSelection();
}

function normalizedDownloadPlatform(value) {
  const platform = String(value || "").toLowerCase();
  if (platform.includes("win")) return "windows";
  if (platform.includes("mac")) return "macos";
  if (platform.includes("linux")) return "linux";
  return "";
}

function normalizedDownloadArchitecture(value, bitness = "") {
  const architecture = `${value || ""} ${bitness || ""}`.toLowerCase();
  if (architecture.includes("arm") || architecture.includes("aarch64")) return "arm64";
  if (architecture.includes("x86") || architecture.includes("x64") || architecture.includes("64")) return "x64";
  return "";
}

async function initializeDownloadSelection() {
  let platform = normalizedDownloadPlatform(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent);
  let architecture = "";
  let architectureDetected = false;
  if (navigator.userAgentData?.getHighEntropyValues) {
    try {
      const details = await navigator.userAgentData.getHighEntropyValues(["architecture", "bitness", "platform"]);
      platform = normalizedDownloadPlatform(details.platform) || platform;
      architecture = normalizedDownloadArchitecture(details.architecture, details.bitness);
      architectureDetected = Boolean(architecture);
    } catch (_error) {
      // Browser privacy settings may withhold architecture details.
    }
  }
  if (!architecture && platform !== "macos") {
    architecture = normalizedDownloadArchitecture(navigator.userAgent);
    architectureDetected = Boolean(architecture);
  }
  if (platform === "macos" && !architecture) {
    architecture = "x64";
  }
  if ((platform === "windows" || platform === "linux") && architecture !== "arm64") {
    architecture = "x64";
  }
  const target = platform && architecture
    ? document.querySelector(`input[name="download-target"][value="${platform}-${architecture}"]`)
    : null;
  if (target) {
    target.checked = true;
    updateHomeDownload(target);
    const status = document.querySelector("#download-selection-status");
    if (status) {
      status.textContent = architectureDetected
        ? `Detected ${downloadTargetLabels[target.value]}.`
        : `Detected macOS, but not its architecture; selected ${downloadTargetLabels[target.value]} as the safer default.`;
    }
  }
  updateDownloadSelection();
}

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest(".tab-link, .tab-action");
  if (tabButton && tabButton.dataset.tab) {
    setActiveTab(tabButton.dataset.tab);
    const scrollTarget = tabButton.dataset.scrollTarget;
    if (scrollTarget) {
      window.requestAnimationFrame(() => {
        const target = document.getElementById(scrollTarget);
        const header = document.querySelector(".site-header");
        if (target) {
          target.style.scrollMarginTop = `${Math.ceil((header?.getBoundingClientRect().height || 0) + 16)}px`;
          target.scrollIntoView({block: "start"});
        }
      });
    }
    return;
  }

  const checkoutButton = event.target.closest(".checkout-button");
  if (checkoutButton) {
    startCheckout(checkoutButton);
    return;
  }

  const validateButton = event.target.closest("#validate-license-key");
  if (validateButton) {
    validateLicenseKey();
    return;
  }

  const betaButton = event.target.closest("#use-beta-key");
  if (betaButton) {
    usePublicBetaKey();
    return;
  }

  const refreshNotifications = event.target.closest("#refresh-notifications");
  if (refreshNotifications) {
    loadNotifications({force: true}).catch(() => {});
    return;
  }

  const downloadButton = event.target.closest(".download-button");
  if (downloadButton) {
    openConfiguredLink("downloads", downloadButton.dataset.download)
      .catch((error) => window.alert(error.message || "Could not load the release file list."));
  }

  const selectedDownloadButton = event.target.closest("#selected-download-button");
  if (selectedDownloadButton) {
    const key = selectedDownloadKey();
    if (!key) {
      window.alert("Choose a supported platform and architecture.");
      return;
    }
    openConfiguredLink("downloads", key)
      .catch((error) => window.alert(error.message || "Could not load the release file list."));
    return;
  }

  const homeDownloadButton = event.target.closest("#home-download-button");
  if (homeDownloadButton) {
    if (!detectedHomeDownloadKey) {
      setActiveTab("download");
      return;
    }
    openConfiguredLink("downloads", detectedHomeDownloadKey)
      .catch((error) => window.alert(error.message || "Could not load the release file list."));
    return;
  }

  const copyLicenseButton = event.target.closest("#copy-license-key");
  if (copyLicenseButton) {
    const key = document.querySelector("#license-key-output")?.textContent || "";
    navigator.clipboard?.writeText(key);
    const status = document.querySelector("#copy-license-status");
    if (status) {
      status.textContent = "Copied.";
      setTimeout(() => {
        status.textContent = "";
      }, 1800);
    }
  }

  const emailLicenseButton = event.target.closest("#email-license-key");
  if (emailLicenseButton) {
    emailLicenseKey(emailLicenseButton);
  }
});

async function emailLicenseKey(button) {
  const status = document.querySelector("#email-license-status");
  if (!currentCheckoutSessionId) {
    if (status) {
      status.textContent = "Checkout session is missing.";
      status.classList.add("invalid");
    }
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Sending...";
  if (status) {
    status.textContent = "";
    status.classList.remove("valid", "invalid");
  }
  try {
    const response = await fetch("/api/license/email", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({sessionId: currentCheckoutSessionId}),
    });
    const data = await readJsonResponse(response, "License email");
    if (!response.ok || data.error || !data.ok) {
      throw new Error(data.error || "Could not send the license email.");
    }
    if (status) {
      status.textContent = "Sent.";
      status.classList.add("valid");
    }
  } catch (error) {
    if (status) {
      status.textContent = error.message || "Could not send the license email.";
      status.classList.add("invalid");
    }
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.addEventListener("input", (event) => {
  if (event.target.matches("#notification-search")) {
    renderNotifications(loadedNotifications, event.target.value);
  }
  if (event.target.matches("#faq-search")) {
    filterFaq(event.target.value);
  }
  if (event.target.matches("#add-device-count")) {
    updateAddDevicePrice();
  }
  if (event.target.matches("#existing-license-key")) {
    const normalized = event.target.value.toUpperCase();
    if (event.target.value !== normalized) {
      event.target.value = normalized;
    }
    clearTimeout(validateTimer);
    validatedLicenseDevices = 0;
    validatedUsedDevices = 0;
    validatedBillingModel = "";
    validatedExpiresAt = "";
    validatedLicenseKind = "";
    setLicenseStatus("", "");
    setAddDeviceEnabled(false);
    updateValidateButtonState();
    if (LICENSE_KEY_PATTERN.test(normalized.trim())) {
      validateTimer = setTimeout(validateLicenseKey, 250);
    }
  }
});

document.addEventListener("submit", (event) => {
  if (event.target.matches("#support-form")) {
    event.preventDefault();
    submitSupportRequest(event.target);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches('input[name="license-action"]')) {
    updatePricingMode();
  }
  if (event.target.matches('input[name="license-plan"]')) {
    updateCart();
  }
  if (event.target.matches('input[name="download-target"], #lean-build, #release-version')) {
    updateDownloadSelection();
  }
});

document.addEventListener("keydown", (event) => {
  if (!event.target.closest(".tab-nav")) {
    return;
  }

  const tabs = Array.from(document.querySelectorAll(".tab-nav .tab-link"));
  const currentIndex = tabs.findIndex((tab) => tab.classList.contains("active"));
  if (currentIndex < 0) {
    return;
  }

  let nextIndex = currentIndex;
  if (event.key === "ArrowRight") {
    nextIndex = Math.min(tabs.length - 1, currentIndex + 1);
  } else if (event.key === "ArrowLeft") {
    nextIndex = Math.max(0, currentIndex - 1);
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = tabs.length - 1;
  } else {
    return;
  }

  event.preventDefault();
  tabs[nextIndex].focus();
  setActiveTab(tabs[nextIndex].dataset.tab);
});

window.addEventListener("popstate", () => {
  setActiveTab(window.location.hash, {skipHash: true});
});

applyConfiguredLinks();
setActiveTab(window.location.hash, {skipHash: true});
openLinkedFaq();
const shouldValidatePrefilledLicense = applyPricingUrlParams();
updateAddDevicePrice();
updatePricingMode();
if (shouldValidatePrefilledLicense) {
  validateLicenseKey();
}
loadCheckoutLicense();
initializeDownloadSelection();
initializeReleaseSelection();
