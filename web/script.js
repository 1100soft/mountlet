let releaseFilesPromise = null;

async function openConfiguredLink(group, key) {
  if (group === "downloads") {
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

async function getReleaseDownloadFile(key) {
  const releases = await loadReleaseFiles();
  return releases.downloads && releases.downloads[key];
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
    setLicenseStatus(error.message || "License key is not valid.", "invalid");
    updateCart();
  }
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

function setActiveTab(nextTab, options = {}) {
  const tabName = normalizeTabName(nextTab);
  const panel = document.querySelector(`[data-panel="${tabName}"]`);
  if (!panel) {
    setActiveTab("home", options);
    return;
  }

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
}

function setDownloadPlatform(nextPlatform) {
  const platformName = String(nextPlatform || "windows");
  const panel = document.querySelector(`[data-platform-panel="${platformName}"]`);
  if (!panel) {
    setDownloadPlatform("windows");
    return;
  }

  document.querySelectorAll("[data-platform-panel]").forEach((candidate) => {
    const isActive = candidate === panel;
    candidate.classList.toggle("active", isActive);
    candidate.hidden = !isActive;
  });

  document.querySelectorAll(".platform-choice").forEach((button) => {
    const isActive = button.dataset.platform === platformName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  });
}

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest(".tab-link, .tab-action");
  if (tabButton && tabButton.dataset.tab) {
    setActiveTab(tabButton.dataset.tab);
    return;
  }

  const platformButton = event.target.closest(".platform-choice");
  if (platformButton) {
    setDownloadPlatform(platformButton.dataset.platform);
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

  const downloadButton = event.target.closest(".download-button");
  if (downloadButton) {
    openConfiguredLink("downloads", downloadButton.dataset.download)
      .catch((error) => window.alert(error.message || "Could not load the release file list."));
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
    setLicenseStatus("", "");
    setAddDeviceEnabled(false);
    updateValidateButtonState();
    if (LICENSE_KEY_PATTERN.test(normalized.trim())) {
      validateTimer = setTimeout(validateLicenseKey, 250);
    }
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches('input[name="license-action"]')) {
    updatePricingMode();
  }
  if (event.target.matches('input[name="license-plan"]')) {
    updateCart();
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
const shouldValidatePrefilledLicense = applyPricingUrlParams();
updateAddDevicePrice();
updatePricingMode();
if (shouldValidatePrefilledLicense) {
  validateLicenseKey();
}
loadCheckoutLicense();
