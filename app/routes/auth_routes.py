from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from datetime import datetime, timedelta
import bcrypt, os, secrets, time, threading
from collections import OrderedDict
from app.services.settings_service import STATE_DIR

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or len(SECRET_KEY) < 32 or SECRET_KEY == "fallback-secret":
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = STATE_DIR / "session.key"
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    SECRET_KEY = key_path.read_text().strip()
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must contain at least 32 characters.")
ALGORITHM = "HS256"
EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 480))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD or ADMIN_PASSWORD == "changeme":
    raise RuntimeError("Set ADMIN_PASSWORD to a non-default password before starting.")
_attempts = OrderedDict()
_attempt_lock = threading.Lock()
LOGIN_WINDOW = 900
LOGIN_LIMIT = 10


def reserve_login_attempt(client):
    now = time.monotonic()
    with _attempt_lock:
        attempts = [t for t in _attempts.get(client, []) if now - t < LOGIN_WINDOW]
        if len(attempts) >= LOGIN_LIMIT:
            raise HTTPException(
                429,
                "로그인 시도가 너무 많습니다. 15분 후 다시 시도하세요.",
                headers={"Retry-After": "900"},
            )
        attempts.append(now)
        _attempts[client] = attempts
        _attempts.move_to_end(client)
        while len(_attempts) > 4096:
            _attempts.popitem(last=False)


HASHED_PASSWORD = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt())


def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require_exp": True, "require_sub": True},
        )
        return payload.get("sub") if payload.get("sub") == ADMIN_USERNAME else None
    except JWTError:
        return None


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return verify_token(token)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "csrf_token": request.state.csrf_token},
    )


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    client = request.client.host if request.client else "unknown"
    reserve_login_attempt(client)
    password_bytes = password.encode()
    valid_password = await run_in_threadpool(
        bcrypt.checkpw, password_bytes[:72], HASHED_PASSWORD
    )
    if username != ADMIN_USERNAME or len(password_bytes) > 72 or not valid_password:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
                "csrf_token": request.state.csrf_token,
            },
            status_code=401,
        )

    with _attempt_lock:
        _attempts.pop(client, None)
    token = create_token(username)
    resp = RedirectResponse(url="/explorer/view", status_code=302)
    resp.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=EXPIRE_MINUTES * 60,
        samesite="strict",
        secure=request.url.scheme == "https"
        or os.getenv("COOKIE_SECURE", "").lower() == "true",
    )
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("access_token")
    return resp
