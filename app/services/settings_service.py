"""Persistent server settings and one root snapshot per request."""

import os
import sqlite3
import stat
from contextvars import ContextVar
from pathlib import Path
from fastapi import HTTPException

STATE_DIR = Path(
    os.getenv(
        "LINUXPLORER_STATE_DIR", str(Path(__file__).resolve().parents[2] / ".state")
    )
)
DB_PATH = STATE_DIR / "settings.sqlite3"
root_context = ContextVar("explorer_root", default=None)
PROTECTED_NAMES = {
    ".git",
    ".ssh",
    ".aws",
    ".codex",
    ".agents",
    ".gnupg",
    ".state",
    ".venv",
}
PROTECTED_SYSTEM = ("/proc", "/sys", "/dev", "/etc", "/root", "/run")


def protected(path):
    path = Path(os.path.abspath(path))
    resolved = path.resolve()
    for candidate in (path, resolved):
        if any(
            part in PROTECTED_NAMES
            or part == ".env"
            or part.startswith(".env.")
            or part in {"id_rsa", "id_ed25519"}
            for part in candidate.parts
        ):
            return True
        if candidate == STATE_DIR.resolve() or STATE_DIR.resolve() in candidate.parents:
            return True
        if any(
            candidate == Path(p) or Path(p) in candidate.parents
            for p in PROTECTED_SYSTEM
        ):
            return True
    return False


def configured_root():
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as db:
            row = db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
            if row:
                return row[0]
    return os.path.realpath(os.getenv("ROOT_DIR", str(Path.home())))


def get_root():
    return root_context.get() or configured_root()


def validate_root(value):
    if not value or "\x00" in value or not os.path.isabs(value):
        raise HTTPException(400, "절대 경로를 입력하세요.")
    path = os.path.realpath(value)
    if protected(value):
        raise HTTPException(403, "보호된 디렉토리는 루트로 사용할 수 없습니다.")
    if not os.path.isdir(path) or not os.access(path, os.R_OK | os.X_OK):
        raise HTTPException(400, "읽을 수 있는 디렉토리를 입력하세요.")
    return path


def set_root(value):
    path = validate_root(value)
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO settings VALUES ('root', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (path,),
        )
    DB_PATH.chmod(0o600)
    return path


def safe_path(value, *, allow_trash=False):
    if not value or "\x00" in value:
        raise HTTPException(400, "잘못된 경로입니다.")
    root = os.path.realpath(get_root())
    absolute = os.path.abspath(value)
    resolved = os.path.realpath(absolute)
    if os.path.commonpath([root, resolved]) != root or protected(absolute):
        raise HTTPException(403, "접근할 수 없는 경로입니다.")
    if not allow_trash and ".trash" in Path(absolute).parts:
        raise HTTPException(403, "휴지통 API를 사용하세요.")
    if os.path.exists(resolved):
        mode = os.stat(resolved).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise HTTPException(403, "특수 파일에는 접근할 수 없습니다.")
    return resolved


def visible_path(path):
    try:
        safe_path(path)
        return not os.path.islink(path)
    except HTTPException, ValueError:
        return False
