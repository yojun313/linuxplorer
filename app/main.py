import hmac
import logging
import os
import secrets
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.concurrency import run_in_threadpool
from app.routes import auth_routes, explorer_routes, settings_routes
from app.services.settings_service import configured_root, root_context

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("linuxplorer")
app = FastAPI(
    title="Linuxplorer", docs_url=None, redoc_url=None, openapi_url=None, debug=False
)
allowed_hosts = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()
]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


def secure_request(request):
    return (
        request.url.scheme == "https"
        or os.getenv("COOKIE_SECURE", "").lower() == "true"
    )


@app.middleware("http")
async def security(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (
        not length.isdigit()
        or int(length)
        > (1024**3 if request.url.path.endswith("/api/upload") else 64 * 1024)
    ):
        return JSONResponse(
            status_code=413, content={"detail": "요청 크기 제한을 초과합니다."}
        )
    csrf_cookie = request.cookies.get("csrf_token")
    csrf = csrf_cookie or secrets.token_urlsafe(32)
    request.state.csrf_token = csrf
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        # TLS may terminate at a reverse proxy; compare the public Host, not the internal scheme.
        if fetch_site == "cross-site" or (
            origin
            and urlsplit(origin).netloc.lower()
            != request.headers.get("host", "").lower()
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "다른 사이트의 요청은 허용하지 않습니다."},
            )
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied and request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16 * 1024:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "로그인 요청이 너무 큽니다."},
                    )
            # BaseHTTPMiddleware replays the cached body for downstream form parsing.
            request._body = bytes(body)
            supplied = parse_qs(body.decode("utf-8", errors="replace")).get(
                "csrf_token", [""]
            )[0]
        if not csrf_cookie or not hmac.compare_digest(supplied, csrf_cookie):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "요청을 확인할 수 없습니다. 페이지를 새로고침하세요."
                },
            )
    snapshot = root_context.set(await run_in_threadpool(configured_root))
    try:
        response = await call_next(request)
    finally:
        root_context.reset(snapshot)
    if not csrf_cookie:
        response.set_cookie(
            "csrf_token",
            csrf,
            httponly=True,
            secure=secure_request(request),
            samesite="strict",
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = (
        "public, max-age=86400"
        if request.url.path.startswith("/static/fonts/")
        else "no-store"
    )
    if request.url.path == "/explorer/serve_file":
        # User-controlled HTML/SVG must not run scripts with the app's origin.
        response.headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
    if secure_request(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_routes.router)
app.include_router(explorer_routes.router, prefix="/explorer")
app.include_router(settings_routes.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500, content={"detail": "서버 오류가 발생했습니다."}
    )


@app.get("/")
async def root():
    return RedirectResponse(url="/explorer/view")
