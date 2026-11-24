"""BIDGUARD #50: authenticated collector LAN/WAN upload endpoint policy.

Values originate only from controller administrator environment, not from
GitHub, browser, arbitrary headers, or an unverified user-supplied host.
The LAN and WAN addresses terminate at the SAME upload handler and wallet.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

UPLOAD_PATH = "/api/v1/collector/upload"


def verify_upload_url(url):
    value = str(url or "").strip()
    if not value:
        raise ValueError("empty upload URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ("", "/", UPLOAD_PATH)):
        raise ValueError("administrator HTTPS controller URL required")
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("loopback is not a collector access URL")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError("invalid TLS port")
    base = value.rstrip("/")
    return base if parsed.path == UPLOAD_PATH else base + UPLOAD_PATH


def configured_addresses(env=None):
    env = os.environ if env is None else env
    addresses = []
    for kind, key in (
        ("lan", "BIDGUARD_COLLECTOR_UPLOAD_LAN_URL"),
        ("wan", "BIDGUARD_COLLECTOR_UPLOAD_WAN_URL"),
    ):
        raw = str(env.get(key, "") or "").strip()
        if raw:
            address = verify_upload_url(raw)
            if address not in (row["url"] for row in addresses):
                addresses.append({"network": kind, "url": address})
    return addresses


def attach_upload_policy(base, env=None):
    """Do not modify existing policy; add only registered device route hints."""
    policy = dict(base) if isinstance(base, dict) else {}
    addresses = configured_addresses(env)
    policy["collector_upload"] = {
        "enabled": bool(addresses),
        "mode": "collector_to_controller_to_nas",
        "require_https": True,
        "endpoints": addresses,
        "receipt": "controller_stored_only",
        "nas_confirmation": "NAS_VERIFIED_separate",
    }
    return policy
