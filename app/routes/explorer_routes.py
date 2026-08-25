import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse
from app.services.file_service import FileService
from app.routes.auth_routes import get_current_user
from fastapi import UploadFile, File as FastAPIFile
from pydantic import BaseModel


class RenameBody(BaseModel):
    path: str
    new_name: str


class MoveBody(BaseModel):
    src: str
    dst_dir: str


class CopyBody(BaseModel):
    src: str
    dst_dir: str


class MkdirBody(BaseModel):
    parent: str
    name: str


class DeleteBody(BaseModel):
    path: str


class CompressBody(BaseModel):
    paths: list[str]
    output_name: str  # 확장자 없이, .zip 자동
    output_dir: str


class DuplicateBody(BaseModel):
    path: str


class TrashRestoreBody(BaseModel):
    id: str


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ROOT_DIR = os.getenv("ROOT_DIR", "/")

# ── 휴지통 ──
TRASH_DIRNAME = ".trash"
TRASH_DIR = os.path.join(os.path.abspath(ROOT_DIR), TRASH_DIRNAME)
_trash_meta_lock = threading.Lock()


def _trash_dir() -> str:
    os.makedirs(TRASH_DIR, exist_ok=True)
    return TRASH_DIR


def _trash_meta_path() -> str:
    return os.path.join(_trash_dir(), ".meta.json")


def _load_trash_meta() -> dict:
    try:
        with open(_trash_meta_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_trash_meta(meta: dict) -> None:
    with open(_trash_meta_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _next_available_name(path: str, label: str = "사본") -> str:
    """path가 이미 존재하면 '이름 (label)', '이름 (label 2)'... 형태로 빈 이름을 찾는다"""
    if not os.path.exists(path):
        return path
    parent = os.path.dirname(path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    n = 1
    while True:
        candidate_name = f"{stem} ({label})" if n == 1 else f"{stem} ({label} {n})"
        candidate = os.path.join(parent, candidate_name + ext)
        if not os.path.exists(candidate):
            return candidate
        n += 1

EXT_MAP = {
    "image": (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".ico",
        ".tiff",
    ),
    "video": (".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv"),
    "audio": (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"),
    "document": (".pdf",),
    "text": (
        ".txt",
        ".json",
        ".jsonl",
        ".ndjson",
        ".jsonc",
        ".csv",
        ".tsv",
        ".md",
        ".markdown",
        ".rst",
        ".adoc",
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".js",
        ".mjs",
        ".cjs",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".log",
        ".cfg",
        ".ini",
        ".conf",
        ".properties",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".java",
        ".rs",
        ".go",
        ".mod",
        ".sum",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".svelte",
        ".sql",
        ".r",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".lua",
        ".dart",
        ".pl",
        ".pm",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".clj",
        ".cljs",
        ".groovy",
        ".gradle",
        ".vim",
        ".el",
        ".bat",
        ".ps1",
        ".psm1",
        ".env",
        ".gitignore",
        ".gitattributes",
        ".dockerignore",
        ".dockerfile",
        ".editorconfig",
        ".diff",
        ".patch",
        ".graphql",
        ".gql",
        ".proto",
        ".tf",
        ".tfvars",
        ".lock",
        ".srt",
        ".vtt",
    ),
    "archive": (".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz"),
    "spreadsheet": (".xlsx", ".xls", ".csv"),
    "presentation": (".pptx", ".ppt"),
    "word": (".docx", ".doc"),
}

# 확장자가 없거나 특이한(예: Dockerfile, Makefile) 잘 알려진 텍스트 파일명
KNOWN_TEXT_FILENAMES = {
    "dockerfile",
    "makefile",
    "license",
    "license.txt",
    "readme",
    "changelog",
    "authors",
    "contributing",
    "gemfile",
    "gemfile.lock",
    "rakefile",
    "procfile",
    "vagrantfile",
    "cmakelists.txt",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".npmignore",
    ".editorconfig",
    ".env",
    ".bashrc",
    ".zshrc",
    ".vimrc",
    ".npmrc",
    ".prettierrc",
    ".eslintrc",
    ".babelrc",
    ".flake8",
    ".pylintrc",
}

# ── 비디오/오디오 MIME 타입 명시적 매핑 ──
# mimetypes.guess_type()은 서버 OS의 /etc/mime.types에 의존하기 때문에
# .mkv, .avi 같은 확장자는 None을 반환하는 경우가 많다 (→ 브라우저가 재생 대신
# application/octet-stream으로 받아 다운로드를 시도하거나 아무 반응이 없음).
# 그래서 흔한 확장자는 직접 매핑해서 확실하게 지정한다.
VIDEO_MIME_MAP = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogg": "video/ogg",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
}
AUDIO_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


def guess_media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_MIME_MAP:
        return VIDEO_MIME_MAP[ext]
    if ext in AUDIO_MIME_MAP:
        return AUDIO_MIME_MAP[ext]
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    if ext in EXT_MAP.get("text", ()):
        return "text/plain"
    return "application/octet-stream"


# ── 인증 의존성 ──
def require_auth(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


# ── 유틸 ──
def get_dir_size(path: str) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.path == TRASH_DIR:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(entry.path)
                else:
                    total += entry.stat().st_size
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total


def classify_file(ext: str, name: str = "") -> str:
    ext = ext.lower()
    for file_type, extensions in EXT_MAP.items():
        if ext in extensions:
            return file_type
    if name.lower() in KNOWN_TEXT_FILENAMES:
        return "text"
    return "other"


def _looks_like_text(path: str, sample_size: int = 8192) -> bool:
    """확장자로 분류되지 않은 파일이 실제로는 텍스트인지 내용을 살짝 엿봐서 판단한다
    (미리보기를 최대한 많은 형식에서 지원하기 위함). 파일 전체가 아니라 앞부분만 읽는다."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return False
    if not chunk:
        return True
    if b"\x00" in chunk:
        return False
    control_bytes = sum(1 for b in chunk if b < 9 or (13 < b < 32))
    return (control_bytes / len(chunk)) < 0.05


def build_file_info(entry_or_path, is_scandir=True):
    if is_scandir:
        name = entry_or_path.name
        path = entry_or_path.path
        is_dir = entry_or_path.is_dir()
        size = entry_or_path.stat().st_size if not is_dir else 0
        mtime = entry_or_path.stat().st_mtime
    else:
        name = os.path.basename(entry_or_path)
        path = entry_or_path
        is_dir = os.path.isdir(path)
        stat = os.stat(path)
        size = stat.st_size if not is_dir else 0
        mtime = stat.st_mtime

    if is_dir:
        try:
            child_count = len(list(os.scandir(path)))
        except PermissionError:
            child_count = 0
        return {
            "name": name,
            "path": path,
            "is_dir": True,
            "type": "directory",
            "size_raw": 0,
            "size": "",
            "ext": "",
            "mtime": mtime,
            "child_count": child_count,
        }

    ext = os.path.splitext(name)[1].lower()
    return {
        "name": name,
        "path": path,
        "is_dir": False,
        "type": classify_file(ext, name),
        "size_raw": size,
        "size": FileService.format_size(size),
        "ext": ext,
        "mtime": mtime,
    }


def _safe_path(path: str) -> str:
    """ROOT_DIR 밖으로 나가는 경로 차단"""
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(ROOT_DIR)
    if abs_path != abs_root and not abs_path.startswith(abs_root + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")
    return abs_path


def _raise_fs_error(e: Exception):
    """파일시스템 에러를 그대로 500으로 흘려보내지 않고 적절한 상태코드로 변환"""
    if isinstance(e, PermissionError):
        raise HTTPException(
            status_code=403, detail=f"권한이 없습니다: {e.filename or ''}"
        ) from e
    if isinstance(e, FileNotFoundError):
        raise HTTPException(
            status_code=404, detail=f"찾을 수 없습니다: {e.filename or ''}"
        ) from e
    if isinstance(e, FileExistsError):
        raise HTTPException(
            status_code=409, detail=f"이미 존재합니다: {e.filename or ''}"
        ) from e
    if isinstance(e, OSError):
        raise HTTPException(status_code=500, detail=str(e)) from e
    raise


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
READ_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB
MAX_RANGE_CHUNK = 32 * 1024 * 1024  # 한 응답 최대 32MB (열린 범위 요청 방어용)
MAX_CONCURRENT_READS_PER_FILE = 2

_file_semaphores: dict[str, threading.Semaphore] = {}
_file_semaphores_lock = threading.Lock()


def _get_file_semaphore(path: str) -> threading.Semaphore:
    with _file_semaphores_lock:
        sem = _file_semaphores.get(path)
        if sem is None:
            sem = threading.Semaphore(MAX_CONCURRENT_READS_PER_FILE)
            _file_semaphores[path] = sem
        return sem


def _iter_file_range_hdd(path: str, start: int, end: int):
    """세마포어로 동시 접근을 제한하고, fadvise로 readahead 힌트를 준 뒤
    큰 청크 단위로 순차 읽기. 세마포어를 쥐고 있는 동안만 실제로 디스크를
    건드리기 때문에, 브라우저가 여러 Range를 동시에 열어도 서버 입장에서는
    최대 MAX_CONCURRENT_READS_PER_FILE개만 실제로 seek한다."""
    sem = _get_file_semaphore(path)
    length = end - start + 1
    with sem:
        fd = os.open(path, os.O_RDONLY)
        try:
            try:
                os.posix_fadvise(fd, start, length, os.POSIX_FADV_SEQUENTIAL)
                os.posix_fadvise(fd, start, length, os.POSIX_FADV_WILLNEED)
            except (AttributeError, OSError):
                pass  # posix_fadvise 미지원 플랫폼(macOS 등)은 조용히 건너뜀
            os.lseek(fd, start, os.SEEK_SET)
            remaining = length
            while remaining > 0:
                read_size = min(READ_CHUNK_SIZE, remaining)
                data = os.read(fd, read_size)
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            os.close(fd)


def _range_response(request: Request, path: str, file_size: int, media_type: str):
    range_header = request.headers.get("range")

    if range_header:
        m = RANGE_RE.match(range_header)
        if m:
            start_str, end_str = m.groups()
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1, start + MAX_RANGE_CHUNK - 1)

            if start > end or start >= file_size:
                return StreamingResponse(
                    iter(()),
                    status_code=416,
                    headers={
                        "Content-Range": f"bytes */{file_size}",
                        "Accept-Ranges": "bytes",
                    },
                )

            content_length = end - start + 1
            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Cache-Control": "no-cache",
            }
            return StreamingResponse(
                _iter_file_range_hdd(path, start, end),
                status_code=206,
                media_type=media_type,
                headers=headers,
            )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(
        _iter_file_range_hdd(path, 0, file_size - 1),
        media_type=media_type,
        headers=headers,
    )


# ── ROUTES ──


@router.get("/api/dir_size")
async def api_dir_size(path: str, user=Depends(require_auth)):
    path = _safe_path(path)
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Directory not found")
    size = get_dir_size(path)
    return {"path": path, "size_raw": size, "size": FileService.format_size(size)}


@router.get("/search")
async def search(
    request: Request,
    q: str = "",
    path: str = None,
    page: int = 1,
    page_size: int = 200,
    sort: str = "relevance",
    order: str = "asc",
    user=Depends(require_auth),
):
    search_root = _safe_path(path) if path else os.path.abspath(ROOT_DIR)
    if not os.path.isdir(search_root):
        search_root = os.path.abspath(ROOT_DIR)
    if q:
        # 디스크 전체를 훑는 무거운 작업이라 이벤트 루프를 막지 않도록 별도 스레드에서 실행
        all_results, truncated = await run_in_threadpool(FileService.search_recursive, search_root, q)
    else:
        all_results, truncated = [], False
    items = []
    for item in all_results:
        if item.get("is_dir"):
            items.append(
                {
                    **item,
                    "type": "directory",
                    "size": "",
                    "ext": "",
                }
            )
        else:
            ext = os.path.splitext(item.get("name", ""))[1].lower()
            items.append(
                {
                    **item,
                    "type": classify_file(ext, item.get("name", "")),
                    "ext": ext,
                    "size": item.get("size", ""),
                }
            )

    reverse = order == "desc"
    key_map = {
        # 관련도순은 파일/폴더 구분 없이 검색어와 얼마나 잘 맞는지만으로 정렬
        "relevance": lambda x: -x.get("score", 0),
        "name": lambda x: (not x["is_dir"], x["name"].lower()),
        "date": lambda x: (not x["is_dir"], x["mtime"]),
        "size": lambda x: (not x["is_dir"], x["size_raw"]),
        "type": lambda x: (not x["is_dir"], x["type"]),
    }
    items.sort(key=key_map.get(sort, key_map["relevance"]), reverse=reverse)

    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    paged_items = items[(page - 1) * page_size : page * page_size]

    folder_name = f'검색: "{q}"'
    if truncated:
        folder_name += " (일부 결과만 표시됨)"

    return templates.TemplateResponse(
        name="explorer.html",
        request=request,
        context={
            "request": request,
            "items": paged_items,
            "query": q,
            "mode": "search",
            "current_path": ROOT_DIR,
            "search_root": search_root,
            "folder_name": folder_name,
            "parent_path": None,
            "root_path": ROOT_DIR,
            "breadcrumbs": [],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": sort,
            "order": order,
            "username": user,
        },
    )


@router.get("/view")
async def view(
    request: Request,
    path: str = None,
    page: int = 1,
    page_size: int = 200,
    sort: str = "name",
    order: str = "asc",
    show_hidden: int = 0,
    user=Depends(require_auth),
):
    current_path = _safe_path(path) if path else os.path.abspath(ROOT_DIR)
    if not os.path.exists(current_path):
        raise HTTPException(status_code=404, detail="Path not found")

    if os.path.isfile(current_path):
        parent = os.path.dirname(current_path)
        return RedirectResponse(
            url=f"/explorer/view?path={parent}&selected={os.path.basename(current_path)}",
            status_code=302,
        )

    items = []
    try:
        for entry in os.scandir(current_path):
            try:
                if entry.path == TRASH_DIR:
                    continue
                if not show_hidden and entry.name.startswith("."):
                    continue
                items.append(build_file_info(entry))
            except (PermissionError, OSError):
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    reverse = order == "desc"
    key_map = {
        "name": lambda x: (not x["is_dir"], x["name"].lower()),
        "date": lambda x: (not x["is_dir"], x["mtime"]),
        "size": lambda x: (not x["is_dir"], x["size_raw"]),
        "type": lambda x: (not x["is_dir"], x["type"]),
    }
    items.sort(key=key_map.get(sort, key_map["name"]), reverse=reverse)

    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    paged_items = items[(page - 1) * page_size : page * page_size]

    parent_path = os.path.dirname(current_path)
    if not os.path.abspath(parent_path).startswith(os.path.abspath(ROOT_DIR)):
        parent_path = None

    breadcrumbs = []
    rel = os.path.relpath(current_path, ROOT_DIR)
    if rel != ".":
        parts = rel.split(os.sep)
        accumulated = ROOT_DIR
        breadcrumbs.append({"name": "Root", "path": ROOT_DIR})
        for part in parts:
            accumulated = os.path.join(accumulated, part)
            breadcrumbs.append({"name": part, "path": accumulated})
    else:
        breadcrumbs.append({"name": "Root", "path": ROOT_DIR})

    return templates.TemplateResponse(
        name="explorer.html",
        request=request,
        context={
            "request": request,
            "items": paged_items,
            "query": "",
            "mode": "view",
            "current_path": current_path,
            "folder_name": os.path.basename(current_path) if rel != "." else "Root",
            "parent_path": parent_path,
            "root_path": ROOT_DIR,
            "breadcrumbs": breadcrumbs,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": sort,
            "order": order,
            "username": user,
        },
    )


@router.get("/download_file")
async def download_file(path: str, user=Depends(require_auth)):
    path = _safe_path(path)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=path,
        filename=os.path.basename(path),
        media_type=guess_media_type(path),
    )


@router.get("/serve_file")
async def serve_file(request: Request, path: str, user=Depends(require_auth)):
    """미리보기/재생용 엔드포인트. 로컬 HDD의 랜덤 seek 비용을 줄이기 위해
    동시 읽기 제한 + fadvise + 큰 청크 순차읽기를 쓰는 커스텀 스트리밍을 사용."""
    path = _safe_path(path)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = guess_media_type(path)
    file_size = os.path.getsize(path)
    return _range_response(request, path, file_size, media_type)


@router.get("/api/list")
async def api_list(
    path: str, page: int = 1, page_size: int = 200, user=Depends(require_auth)
):
    path = _safe_path(path)
    if not os.path.exists(path):
        return {"items": [], "total": 0, "page": 1, "total_pages": 1}

    items = []
    try:
        for entry in os.scandir(path):
            try:
                if entry.path == TRASH_DIR:
                    continue
                items.append(build_file_info(entry))
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return {"items": [], "total": 0, "page": 1, "total_pages": 1}

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    return {
        "items": items[(page - 1) * page_size : page * page_size],
        "total": total,
        "page": page,
        "total_pages": total_pages,
    }


@router.get("/api/file_info")
async def api_file_info(path: str, user=Depends(require_auth)):
    path = _safe_path(path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    info = build_file_info(path, is_scandir=False)
    previewable_as_text = info["type"] == "text" or (
        info["type"] == "other"
        and 0 < info["size_raw"] < 2 * 1024 * 1024
        and _looks_like_text(path)
    )
    if previewable_as_text and info["size_raw"] < 2 * 1024 * 1024:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                info["content"] = f.read(200_000)
        except Exception:
            info["content"] = None
    return info


def _reject_separator(name: str, label: str = "이름"):
    if not name or os.sep in name or (os.altsep and os.altsep in name):
        raise HTTPException(status_code=400, detail=f"{label}에 경로 구분자를 포함할 수 없습니다")


@router.delete("/api/delete")
async def api_delete(body: DeleteBody, user=Depends(require_auth)):
    path = _safe_path(body.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    if path == TRASH_DIR or os.path.dirname(path) == TRASH_DIR:
        raise HTTPException(status_code=400, detail="휴지통 항목은 휴지통 API로 삭제하세요")

    name = os.path.basename(path)
    is_dir = os.path.isdir(path)
    trash_id = uuid.uuid4().hex
    dest = os.path.join(_trash_dir(), trash_id)
    try:
        shutil.move(path, dest)
    except OSError as e:
        _raise_fs_error(e)

    with _trash_meta_lock:
        meta = _load_trash_meta()
        meta[trash_id] = {
            "name": name,
            "original_path": path,
            "is_dir": is_dir,
            "deleted_at": time.time(),
        }
        _save_trash_meta(meta)
    return {"ok": True, "trash_id": trash_id}


@router.post("/api/rename")
async def api_rename(body: RenameBody, user=Depends(require_auth)):
    _reject_separator(body.new_name, "새 이름")
    src = _safe_path(body.path)
    dst = _safe_path(os.path.join(os.path.dirname(src), body.new_name))
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="이미 존재하는 이름입니다")
    try:
        os.rename(src, dst)
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "new_path": dst}


@router.post("/api/mkdir")
async def api_mkdir(body: MkdirBody, user=Depends(require_auth)):
    _reject_separator(body.name, "폴더 이름")
    parent = _safe_path(body.parent)
    new_dir = _safe_path(os.path.join(parent, body.name))
    if os.path.exists(new_dir):
        raise HTTPException(status_code=409, detail="이미 존재합니다")
    try:
        os.makedirs(new_dir)
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "path": new_dir}


@router.post("/api/newfile")
async def api_newfile(body: MkdirBody, user=Depends(require_auth)):
    _reject_separator(body.name, "파일 이름")
    parent = _safe_path(body.parent)
    new_path = _safe_path(os.path.join(parent, body.name))
    try:
        with open(new_path, "x"):
            pass
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "path": new_path}


@router.post("/api/duplicate")
async def api_duplicate(body: DuplicateBody, user=Depends(require_auth)):
    src = _safe_path(body.path)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    dest = _next_available_name(src)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "new_path": dest}


def _safe_getsize(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


@router.get("/api/trash/list")
async def api_trash_list(user=Depends(require_auth)):
    with _trash_meta_lock:
        meta = _load_trash_meta()
    items = []
    for tid, info in meta.items():
        entry_path = os.path.join(_trash_dir(), tid)
        if os.path.isdir(entry_path):
            size = get_dir_size(entry_path)
        else:
            size = _safe_getsize(entry_path)
        items.append(
            {
                "id": tid,
                "name": info["name"],
                "original_path": info["original_path"],
                "is_dir": info["is_dir"],
                "deleted_at": info["deleted_at"],
                "size": FileService.format_size(size),
                "size_raw": size,
            }
        )
    items.sort(key=lambda x: x["deleted_at"], reverse=True)
    return {"items": items, "total": len(items)}


@router.post("/api/trash/restore")
async def api_trash_restore(body: TrashRestoreBody, user=Depends(require_auth)):
    with _trash_meta_lock:
        meta = _load_trash_meta()
        info = meta.get(body.id)
        if not info:
            raise HTTPException(status_code=404, detail="휴지통에서 찾을 수 없습니다")

        src = os.path.join(_trash_dir(), body.id)
        if not os.path.exists(src):
            del meta[body.id]
            _save_trash_meta(meta)
            raise HTTPException(status_code=404, detail="휴지통 파일이 유실되었습니다")

        dest = info["original_path"]
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except OSError as e:
            _raise_fs_error(e)
        if os.path.exists(dest):
            dest = _next_available_name(dest, label="복원됨")

        try:
            shutil.move(src, dest)
        except OSError as e:
            _raise_fs_error(e)

        del meta[body.id]
        _save_trash_meta(meta)
    return {"ok": True, "restored_to": dest}


@router.delete("/api/trash/empty")
async def api_trash_empty(user=Depends(require_auth)):
    with _trash_meta_lock:
        meta = _load_trash_meta()
        errors = []
        for tid in list(meta.keys()):
            entry_path = os.path.join(_trash_dir(), tid)
            try:
                if os.path.exists(entry_path):
                    if meta[tid]["is_dir"]:
                        shutil.rmtree(entry_path)
                    else:
                        os.remove(entry_path)
            except OSError as e:
                errors.append(f'{meta[tid]["name"]}: {e}')
                continue
            del meta[tid]
        _save_trash_meta(meta)
    return {"ok": True, "errors": errors}


@router.delete("/api/trash/{trash_id}")
async def api_trash_delete(trash_id: str, user=Depends(require_auth)):
    with _trash_meta_lock:
        meta = _load_trash_meta()
        info = meta.get(trash_id)
        if not info:
            raise HTTPException(status_code=404, detail="찾을 수 없습니다")
        entry_path = os.path.join(_trash_dir(), trash_id)
        try:
            if os.path.exists(entry_path):
                if info["is_dir"]:
                    shutil.rmtree(entry_path)
                else:
                    os.remove(entry_path)
        except OSError as e:
            _raise_fs_error(e)
        del meta[trash_id]
        _save_trash_meta(meta)
    return {"ok": True}


@router.post("/api/move")
async def api_move(body: MoveBody, user=Depends(require_auth)):
    src = _safe_path(body.src)
    dst_dir = _safe_path(body.dst_dir)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="대상에 같은 이름이 존재합니다")
    try:
        shutil.move(src, dst)
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "new_path": dst}


@router.post("/api/copy")
async def api_copy(body: CopyBody, user=Depends(require_auth)):
    src = _safe_path(body.src)
    dst_dir = _safe_path(body.dst_dir)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="대상에 같은 이름이 존재합니다")
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "new_path": dst}


@router.post("/api/upload")
async def api_upload(
    path: str,
    files: list[UploadFile] = FastAPIFile(...),
    user=Depends(require_auth),
):
    dir_path = _safe_path(path)
    if not os.path.isdir(dir_path):
        raise HTTPException(status_code=400, detail="대상이 디렉토리가 아닙니다")
    saved = []
    renamed = {}
    try:
        for file in files:
            dst = os.path.join(dir_path, file.filename)
            if os.path.exists(dst):
                new_dst = _next_available_name(dst)
                renamed[file.filename] = os.path.basename(new_dst)
                dst = new_dst
            with open(dst, "wb") as f:
                f.write(await file.read())
            saved.append(os.path.basename(dst))
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "saved": saved, "renamed": renamed}


@router.post("/api/compress")
async def api_compress(body: CompressBody, user=Depends(require_auth)):
    import zipfile

    out_dir = _safe_path(body.output_dir)
    out_path = os.path.join(out_dir, body.output_name + ".zip")
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in body.paths:
                p = _safe_path(p)
                if os.path.isdir(p):
                    for root, _, files in os.walk(p):
                        for f in files:
                            full = os.path.join(root, f)
                            zf.write(full, os.path.relpath(full, os.path.dirname(p)))
                else:
                    zf.write(p, os.path.basename(p))
    except OSError as e:
        if os.path.exists(out_path):
            os.unlink(out_path)
        _raise_fs_error(e)
    return {"ok": True, "path": out_path}


@router.post("/api/extract")
async def api_extract(path: str, user=Depends(require_auth)):
    import zipfile, tarfile

    path = _safe_path(path)
    out_dir = os.path.splitext(path)[0]
    try:
        os.makedirs(out_dir, exist_ok=True)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(out_dir)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as tf:
                tf.extractall(out_dir)
        else:
            raise HTTPException(status_code=400, detail="지원하지 않는 압축 형식")
    except OSError as e:
        _raise_fs_error(e)
    return {"ok": True, "extracted_to": out_dir}


@router.get("/download_dir")
async def download_dir(path: str, user=Depends(require_auth)):
    import zipfile

    path = _safe_path(path)
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Directory not found")

    dir_name = os.path.basename(path)

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()

    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for file in files:
                    full = os.path.join(root, file)
                    arcname = os.path.join(dir_name, os.path.relpath(full, path))
                    zf.write(full, arcname)
    except Exception as e:
        os.unlink(tmp.name)
        raise HTTPException(status_code=500, detail=str(e))

    # 기존 코드는 응답 후 임시 zip을 지우는 로직이 없어서 디스크에 계속 쌓였다.
    # BackgroundTask로 응답 전송이 끝난 뒤 삭제하도록 수정.
    return FileResponse(
        path=tmp.name,
        filename=f"{dir_name}.zip",
        media_type="application/zip",
        background=BackgroundTask(os.unlink, tmp.name),
        headers={"Content-Disposition": f'attachment; filename="{dir_name}.zip"'},
    )