"""HTTP hardening: origin check for state-changing requests and security headers.

Pure ASGI (not BaseHTTPMiddleware) so WebSocket traffic passes straight through.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityMiddleware:
    """
    - CSRF defence in depth: SameSite=Lax cookies already stop cross-site form posts,
      but "same site" includes sibling subdomains and other ports. A state-changing
      /api request whose Origin is neither listed in CORS_ORIGINS nor this server's
      own origin is refused. Requests without Origin (curl, server-to-server) pass:
      browsers always send it on fetch POST/PATCH/PUT/DELETE.
    - Adds standard security headers, and `Cache-Control: no-store` on API responses
      (they contain guest personal data).
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        is_api = path.startswith("/api/")
        if is_api and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not self._origin_allowed(origin, scope, headers):
                response = JSONResponse(
                    {"detail": "Origin not allowed", "code": "origin_not_allowed"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    response_headers.setdefault(name, value)
                if is_api:
                    response_headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _origin_allowed(self, origin: str, scope: Scope, headers: Headers) -> bool:
        if origin in self.allowed_origins:
            return True
        # Same origin as this server (e.g. client and API served from one host).
        # Behind a TLS-terminating proxy the scheme differs; list the public origin in
        # CORS_ORIGINS in that case.
        host = headers.get("host")
        return host is not None and origin == f"{scope['scheme']}://{host}"
