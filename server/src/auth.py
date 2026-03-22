"""Minimal session auth — two hardcoded users from .env."""

import hashlib
import hmac
import time

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

from src import config

COOKIE_NAME = "mi_session"
COOKIE_MAX_AGE = 86400 * 7  # 7 days


def _sign(payload: str) -> str:
    """HMAC-sign a payload string."""
    return hmac.new(
        config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:16]


def create_session_cookie(response: Response, user_id: str):
    """Set signed session cookie."""
    ts = str(int(time.time()))
    payload = f"{user_id}|{ts}"
    sig = _sign(payload)
    value = f"{payload}|{sig}"
    response.set_cookie(
        COOKIE_NAME, value,
        httponly=True, samesite="lax", max_age=COOKIE_MAX_AGE,
    )


def get_current_user(request: Request) -> str | None:
    """Extract user_id from session cookie. Returns None if invalid."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    parts = cookie.split("|")
    if len(parts) != 3:
        return None
    user_id, ts, sig = parts
    expected = _sign(f"{user_id}|{ts}")
    if not hmac.compare_digest(sig, expected):
        return None
    # Check expiry
    try:
        if time.time() - int(ts) > COOKIE_MAX_AGE:
            return None
    except ValueError:
        return None
    return user_id


def check_credentials(username: str, password: str) -> bool:
    """Validate username/password against AUTH_USERS config."""
    if not config.AUTH_USERS:
        return False
    expected = config.AUTH_USERS.get(username)
    if expected is None:
        return False
    return hmac.compare_digest(expected, password)


def require_auth(request: Request) -> str:
    """Get user_id or raise redirect. For use in route handlers."""
    user_id = get_current_user(request)
    if not user_id:
        raise _redirect_to_login()
    return user_id


def _redirect_to_login():
    """Return a RedirectResponse exception-style."""
    from fastapi import HTTPException
    raise HTTPException(status_code=303, headers={"Location": "/login"})
