import {PUBLIC_VARS} from "./public-vars.js";

export const DEFAULT_SITE_URL = PUBLIC_VARS.productionSiteUrl;
export const DEFAULT_LICENSE_API_URL = `${DEFAULT_SITE_URL}/api/license`;

export const ENV_NAMES = PUBLIC_VARS.env;

export function siteUrl(env, requestUrl = "") {
  const configured = String(env?.[ENV_NAMES.licenseSiteUrl] || "").trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  if (requestUrl) {
    try {
      return new URL(requestUrl).origin.replace(/\/+$/, "");
    } catch (_error) {
      // Fall through to production default.
    }
  }
  return DEFAULT_SITE_URL;
}

export function pricingUrl(env, requestUrl = "", params = {}) {
  const url = new URL(`${siteUrl(env, requestUrl)}/`);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value).trim()) {
      url.searchParams.set(key, String(value).trim());
    }
  }
  url.hash = "pricing";
  return url.toString();
}
