"""Treat user URLs as untrusted: block internal addresses and runaway downloads.

Every URL (including each redirect hop and every request the headless browser makes)
goes through `check_url` before we connect to it.

Known limitation: we resolve DNS here and the HTTP client resolves it again when
connecting, so a hostile DNS server could answer differently the second time
("DNS rebinding"). Pinning the resolved IP would close that; out of scope for now.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024


class UnsafeURL(Exception):
    pass


class TooLarge(Exception):
    pass


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str.split("%")[0])  # drop IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_url(url: str) -> None:
    """Raise UnsafeURL unless `url` is http(s) and every IP it resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme '{parts.scheme or 'none'}' not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURL("no host in URL")
    try:
        ips = await _resolve(host)
    except socket.gaierror as e:
        raise UnsafeURL(f"cannot resolve host '{host}': {e}") from e
    if not ips:
        raise UnsafeURL(f"host '{host}' resolved to no addresses")
    for ip in ips:
        if not _is_public(ip):
            raise UnsafeURL(f"'{host}' resolves to non-public IP {ip}")


def max_bytes_for(content_type: str | None) -> int:
    return MAX_PDF_BYTES if content_type == "pdf" else MAX_HTML_BYTES
