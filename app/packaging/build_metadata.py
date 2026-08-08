from __future__ import annotations

import os
from datetime import datetime, timezone

PRODUCTION_SITE_URL = "https://mountlet.app"
PREVIEW_SITE_URL = "https://wip.mountlet.pages.dev"
BUILD_CHANNELS = {"production", "preview", "local"}


def build_channel() -> str:
    configured = os.environ.get("MOUNTLET_BUILD_CHANNEL", "").strip().lower()
    if configured in BUILD_CHANNELS:
        return configured
    if os.environ.get("GITHUB_EVENT_NAME", "").strip() == "pull_request":
        return "preview"
    head_ref = os.environ.get("GITHUB_HEAD_REF", "").strip()
    ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    if head_ref == "wip" or ref_name == "wip":
        return "preview"
    if ref_name == "main" or ref_name.startswith("v"):
        return "production"
    return "local"


def build_identifier(channel: str | None = None) -> str:
    configured = os.environ.get("MOUNTLET_BUILD_ID", "").strip()
    if configured:
        return configured
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip() or "1"
    revision = os.environ.get("GITHUB_SHA", "").strip()[:8]
    if run_number:
        suffix = f"-{revision}" if revision else ""
        return f"r{run_number}.{run_attempt}{suffix}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{channel or build_channel()}-{timestamp}"


def build_info_data() -> dict[str, str]:
    channel = build_channel()
    production_site = os.environ.get("MOUNTLET_PRODUCTION_SITE_URL", "").strip() or PRODUCTION_SITE_URL
    preview_site = os.environ.get("MOUNTLET_PREVIEW_SITE_URL", "").strip() or PREVIEW_SITE_URL
    default_site = preview_site if channel == "preview" else production_site
    report_api_url = os.environ.get("MOUNTLET_DEFAULT_REPORT_API_URL", "").strip()
    license_api_url = os.environ.get("MOUNTLET_DEFAULT_LICENSE_API_URL", "").strip()
    license_site_url = os.environ.get("MOUNTLET_DEFAULT_LICENSE_SITE_URL", "").strip()
    notice_api_url = os.environ.get("MOUNTLET_DEFAULT_NOTICE_API_URL", "").strip()
    if not report_api_url:
        report_api_url = f"{default_site}/api/report"
    if not license_api_url:
        license_api_url = f"{default_site}/api/license"
    if not license_site_url:
        license_site_url = default_site
    if not notice_api_url:
        notice_api_url = f"{default_site}/api/notices"
    return {
        "channel": channel,
        "buildId": build_identifier(channel),
        "licenseApiUrl": license_api_url,
        "licenseSiteUrl": license_site_url,
        "noticeApiUrl": notice_api_url,
        "reportApiUrl": report_api_url,
    }
