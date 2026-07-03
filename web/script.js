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

document.addEventListener("click", (event) => {
  const checkoutButton = event.target.closest(".checkout-button");
  if (checkoutButton) {
    openConfiguredLink("checkout", checkoutButton.dataset.plan);
    return;
  }

  const downloadButton = event.target.closest(".download-button");
  if (downloadButton) {
    openConfiguredLink("downloads", downloadButton.dataset.download);
  }
});
