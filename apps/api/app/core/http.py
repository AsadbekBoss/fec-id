"""Shared settings for outgoing HTTP calls to the internal services.

Creating an httpx client builds a new TLS context (loading the CA bundle) unless one is passed:
about 250 ms on Windows, paid on every call because the service clients are short-lived. One
cached context makes a client cost ~1 ms. Certificate verification stays on.
"""

from __future__ import annotations

import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())
