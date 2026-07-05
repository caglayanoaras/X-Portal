# -*- coding: utf-8 -*-
"""Mount the embedded Dash app into FastAPI, guarded by the portal session.

The Dash app is a Flask (WSGI) app; a2wsgi bridges it into ASGI. Because a
mounted sub-app bypasses FastAPI's dependency system, this wrapper re-checks the
portal's JWT cookie and the `data_panels` module assignment on every request —
so the dashboards are protected exactly like the rest of the portal.
"""
import jwt
from a2wsgi import WSGIMiddleware
from sqlmodel import Session
from starlette.responses import RedirectResponse

from app.core.config import settings
from app.core.database import engine
from app.dependencies.auth import get_user
from app.models import UserType, ModuleKey
from app.data_panels.dash_app import create_dash_app

DASH_MOUNT_PATH = "/data_panels/dash"

_dash_app = create_dash_app()
_wsgi_app = WSGIMiddleware(_dash_app.server)


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def _is_authorized(token: str | None) -> bool:
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.InvalidTokenError:
        return False
    username = payload.get("sub")
    if not username:
        return False
    with Session(engine) as db:
        user = get_user(db, username)
        if not user or not user.is_active:
            return False
        if user.usertype == UserType.superadmin:
            return True
        return any(m.key == ModuleKey.data_panels.value for m in user.modules)


class GuardedDashApp:
    """ASGI app: serve the Dash app only to logged-in users who have the module."""
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await _wsgi_app(scope, receive, send)
            return
        cookie_header = ""
        for key, value in scope.get("headers", []):
            if key == b"cookie":
                cookie_header = value.decode("latin-1")
                break
        token = _cookie_value(cookie_header, settings.COOKIE_NAME)
        if not _is_authorized(token):
            await RedirectResponse("/", status_code=302)(scope, receive, send)
            return
        await _wsgi_app(scope, receive, send)


guarded_dash_app = GuardedDashApp()
