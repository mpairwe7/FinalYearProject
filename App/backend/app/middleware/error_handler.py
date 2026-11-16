"""Global error handling: the HTTP exception handler every route's errors pass through."""

from __future__ import annotations

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler as _fastapi_http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException



async def http_exception_with_challenge(request: Request, exc: StarletteHTTPException):
    """Give every 401 a ``WWW-Authenticate: Bearer`` challenge (RFC 9110 §15.5.2).

    The API's 401s (no login, a bad or expired token) had none, so a client
    could not tell which scheme would work. A 401 that names its own challenge
    keeps it; every other status, and the rest of the response, is FastAPI's
    default. Any other response that is a 401 (MCP's ``wire.py``) builds its own
    challenge and does not come through here.
    """
    if exc.status_code == 401 and not any(k.lower() == "www-authenticate" for k in (exc.headers or {})):
        exc = StarletteHTTPException(
            exc.status_code, detail=exc.detail, headers={**(exc.headers or {}), "WWW-Authenticate": "Bearer"}
        )
    return await _fastapi_http_exception_handler(request, exc)
