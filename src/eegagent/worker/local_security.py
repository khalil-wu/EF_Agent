"""Local-only request guard: same policy as the Go gateway's server.LocalOnly.

Blocks browser cross-origin calls and DNS-rebinding against the loopback app:
Host must be localhost/loopback, Origin must match scheme+authority exactly,
cross-site fetches are refused. Not authentication — a same-user process is
out of scope.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_SECURE_HEADERS = [(b"x-content-type-options", b"nosniff"),
                   (b"referrer-policy", b"no-referrer"),
                   (b"x-frame-options", b"DENY")]
_GUARDED_HEADERS = ("host", "origin", "sec-fetch-site")


def _port_num(suffix: str) -> int | None:
    """Parse ":<digits>" into a port; ASCII digits only, 1..65535."""
    if not suffix.startswith(":") or len(suffix) == 1:
        return None
    digits = suffix[1:]
    if not digits.isascii() or not digits.isdigit() or len(digits) > 5:
        return None
    num = int(digits)
    return num if 1 <= num <= 65535 else None


def _split_host(host: str) -> tuple[str, int] | None:
    """hostname, port (0 when absent); None when malformed."""
    if not host:
        return None
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        suffix = host[end + 1:]
        if not suffix:
            return host[1:end], 0
        port = _port_num(suffix)
        return (host[1:end], port) if port is not None else None
    if ":" in host:
        name, _, rest = host.partition(":")
        if ":" in rest:
            return None
        port = _port_num(":" + rest)
        return (name, port) if port is not None else None
    return host, 0


def _norm(hostname: str) -> str:
    """Lowercase and remove one optional trailing dot."""
    name = hostname.lower()
    return name[:-1] if name.endswith(".") else name


def _is_local(hostname: str) -> bool:
    if _norm(hostname) == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _origin_allowed(scheme: str, host: str, origin: str) -> bool:
    if not origin:
        return True
    if origin == "null" or any(c in origin for c in " \t,"):
        return False
    try:
        url = urlsplit(origin)
        oport = url.port
    except ValueError:
        return False
    if (url.scheme != scheme or url.username is not None or url.password is not None
            or url.path or url.query or url.fragment or url.hostname is None
            or url.port == 0 or (url.port is None and url.netloc.endswith(":"))):
        return False
    req = _split_host(host)
    if req is None or _norm(url.hostname) != _norm(req[0]):
        return False
    default = 443 if scheme == "https" else 80
    return (oport or default) == (req[1] or default)


class LocalOnlyMiddleware:
    """Pure-ASGI version of the gateway guard; wraps the whole FastAPI app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = scope.get("headers", [])
        names = [k.decode("latin-1").lower() for k, _ in raw]
        headers = dict(zip(names, (v.decode("latin-1") for _, v in raw)))
        parsed = _split_host(headers.get("host", ""))
        if (parsed is None or not _is_local(parsed[0])
                or any(names.count(n) > 1 for n in _GUARDED_HEADERS)
                or not _origin_allowed(scope.get("scheme", "http"), headers.get("host", ""),
                                       headers.get("origin", ""))
                or headers.get("sec-fetch-site") == "cross-site"):
            body = b'{"error":"local host only"}'
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())] + _SECURE_HEADERS})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_guarded(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + _SECURE_HEADERS
            await send(message)

        await self.app(scope, receive, send_guarded)
