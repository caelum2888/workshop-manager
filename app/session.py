"""Middleware mínimo de sessão assinada em cookie, sem armazenamento externo."""

import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SignedCookieSessionMiddleware:
    """Expõe ``request.session`` usando JSON assinado com HMAC-SHA256."""

    def __init__(
        self,
        app,
        *,
        secret_key: str,
        session_cookie: str,
        https_only: bool,
        max_age: int = 43_200,
    ):
        self.app = app
        self.secret = secret_key.encode("utf-8")
        self.session_cookie = session_cookie
        self.https_only = https_only
        self.max_age = max_age

    def _decode(self, value: str) -> dict:
        try:
            payload, signature = value.rsplit(".", 1)
            expected = hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return {}
            decoded = json.loads(_b64decode(payload))
            if int(decoded["iat"]) + self.max_age < int(time.time()):
                return {}
            return decoded["data"] if isinstance(decoded["data"], dict) else {}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {}

    def _encode(self, data: dict) -> str:
        payload = _b64encode(
            json.dumps(
                {"data": data, "iat": int(time.time())},
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        signature = hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def _cookie_header(self, value: str, *, delete: bool = False) -> str:
        cookie = SimpleCookie()
        cookie[self.session_cookie] = value
        morsel = cookie[self.session_cookie]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "lax"
        if self.https_only:
            morsel["secure"] = True
        if delete:
            morsel["max-age"] = 0
        else:
            morsel["max-age"] = self.max_age
        return morsel.OutputString()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        cookies = SimpleCookie()
        cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
        had_cookie = self.session_cookie in cookies
        value = cookies[self.session_cookie].value if had_cookie else ""
        scope["session"] = self._decode(value) if value else {}

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if scope["session"]:
                    response_headers.append(
                        "set-cookie", self._cookie_header(self._encode(scope["session"]))
                    )
                elif had_cookie:
                    response_headers.append("set-cookie", self._cookie_header("", delete=True))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

