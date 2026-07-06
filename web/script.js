function openConfiguredLink(group, key) {
  const config = window.MOUNTLET_SITE_CONFIG || {};
  const url = config[group] && config[group][key];
  if (!url || url.includes("replace-")) {
    const label = group === "checkout" ? "Stripe Payment Link" : "release asset URL";
    window.alert(`Set the ${label} for "${key}" in web/config.js before launch.`);
    return;
  }
  window.location.href = url;
}

async function startCheckout(button) {
  const plan = button.dataset.plan;
  const config = window.MOUNTLET_SITE_CONFIG || {};
  const configuredUrl = config.checkout && config.checkout[plan];
  if (configuredUrl && !configuredUrl.includes("replace-")) {
    window.location.href = configuredUrl;
    return;
  }

  const countInput = document.querySelector(`.device-count[data-plan="${plan}"]`);
  const deviceCount = Number(countInput && countInput.value) || undefined;
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
      body: JSON.stringify({plan, deviceCount}),
    });
    const data = await response.json();
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

  const downloadButton = event.target.closest(".download-button");
  if (downloadButton) {
    openConfiguredLink("downloads", downloadButton.dataset.download);
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

setActiveTab(window.location.hash, {skipHash: true});
