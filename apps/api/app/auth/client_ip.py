"""Client IP resolution. X-Forwarded-For is honoured only when the direct peer is a configured
trusted proxy (e.g. Nginx). Otherwise any client could spoof its IP and evade rate limits."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.core.config import get_settings


def _valid(ip: str) -> str | None:
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    peer = _valid(request.client.host) if request.client else None
    trusted = set(get_settings().trusted_proxies)
    if peer is None or peer not in trusted:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    # Walk from the right: the first address that is not a trusted proxy is the client.
    for candidate in reversed([c for c in forwarded.split(",") if c.strip()]):
        ip = _valid(candidate)
        if ip is None:
            return peer
        if ip not in trusted:
            return ip
    return peer
