from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from app.services.file_service import FileService
from app.routes.auth_routes import get_current_user
import os
import mimetypes
import shutil
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


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ROOT_DIR = os.getenv("ROOT_DIR", "/")

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
        ".csv",
        ".md",
        ".py",
        ".sh",
        ".js",
        ".html",
        ".css",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".log",
        ".cfg",
        ".ini",
        ".conf",
        ".cpp",
        ".c",
        ".h",
        ".java",
        ".rs",
        ".go",
        ".ts",
        ".tsx",
        ".jsx",
        ".sql",
        ".r",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".lua",
        ".bat",
        ".ps1",
        ".env",
        ".gitignore",
        ".dockerfile",
    ),
    "archive": (".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz"),
    "spreadsheet": (".xlsx", ".xls", ".csv"),
    "presentation": (".pptx", ".ppt"),
    "word": (".docx", ".doc"),
}


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
                if entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(entry.path)
                else:
                    total += entry.stat().st_size
            except PermissionError, OSError:
                continue
    except PermissionError, OSError:
        pass
    return total


def classify_file(ext: str) -> str:
    ext = ext.lower()
    for file_type, extensions in EXT_MAP.items():
        if ext in extensions:
            return file_type
    return "other"


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
        "type": classify_file(ext),
        "size_raw": size,
        "size": FileService.format_size(size),
        "ext": ext,
        "mtime": mtime,
    }


def _safe_path(path: str) -> str:
    """ROOT_DIR 밖으로 나가는 경로 차단"""
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(ROOT_DIR)
    if not abs_path.startswith(abs_root):
        raise HTTPException(status_code=403, detail="Access denied")
    return abs_path


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
    page: int = 1,
    page_size: int = 200,
    user=Depends(require_auth),
):
    all_results = FileService.search_recursive(ROOT_DIR, q) if q else []
    items = []
    for item in all_results:
        if item.get("is_dir"):
            items.append(
                {
                    **item,
                    "type": "directory",
                    "size": "",
                    "ext": "",
                    "size_raw": 0,
                    "mtime": 0,
                }
            )
        else:
            ext = os.path.splitext(item.get("name", ""))[1].lower()
            items.append(
                {
                    **item,
                    "type": classify_file(ext),
                    "ext": ext,
                    "size": item.get("size", ""),
                    "size_raw": 0,
                    "mtime": 0,
                }
            )

    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    paged_items = items[(page - 1) * page_size : page * page_size]

    return templates.TemplateResponse(
        name="explorer.html",
        request=request,
        context={
            "request": request,
            "items": paged_items,
            "query": q,
            "mode": "search",
            "current_path": ROOT_DIR,
            "folder_name": f'검색: "{q}"',
            "parent_path": None,
            "root_path": ROOT_DIR,
            "breadcrumbs": [],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": "name",
            "order": "asc",
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
                if not show_hidden and entry.name.startswith('.'):
                    continue                                          # ← 추가
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
    mime, _ = mimetypes.guess_type(path)
    return FileResponse(
        path=path,
        filename=os.path.basename(path),
        media_type=mime or "application/octet-stream",
    )


@router.get("/serve_file")
async def serve_file(request: Request, path: str, user=Depends(require_auth)):
    path = _safe_path(path)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        ext = os.path.splitext(path)[1].lower()
        mime = (
            "text/plain"
            if ext in EXT_MAP.get("text", ())
            else "application/octet-stream"
        )
    return FileResponse(
        path=path,
        media_type=mime,
        headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
    )


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
                items.append(build_file_info(entry))
            except PermissionError, OSError:
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
    if info["type"] == "text" and info["size_raw"] < 2 * 1024 * 1024:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                info["content"] = f.read(200_000)
        except Exception:
            info["content"] = None
    return info


@router.delete("/api/delete")
async def api_delete(body: DeleteBody, user=Depends(require_auth)):
    path = _safe_path(body.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.post("/api/rename")
async def api_rename(body: RenameBody, user=Depends(require_auth)):
    src = _safe_path(body.path)
    dst = _safe_path(os.path.join(os.path.dirname(src), body.new_name))
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="이미 존재하는 이름입니다")
    os.rename(src, dst)
    return {"ok": True, "new_path": dst}


@router.post("/api/mkdir")
async def api_mkdir(body: MkdirBody, user=Depends(require_auth)):
    parent = _safe_path(body.parent)
    new_dir = _safe_path(os.path.join(parent, body.name))
    if os.path.exists(new_dir):
        raise HTTPException(status_code=409, detail="이미 존재합니다")
    os.makedirs(new_dir)
    return {"ok": True, "path": new_dir}


@router.post("/api/move")
async def api_move(body: MoveBody, user=Depends(require_auth)):
    src = _safe_path(body.src)
    dst_dir = _safe_path(body.dst_dir)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Not found")
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail="대상에 같은 이름이 존재합니다")
    shutil.move(src, dst)
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
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
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
    for file in files:
        dst = os.path.join(dir_path, file.filename)
        with open(dst, "wb") as f:
            f.write(await file.read())
        saved.append(file.filename)
    return {"ok": True, "saved": saved}


@router.post("/api/compress")
async def api_compress(body: CompressBody, user=Depends(require_auth)):
    import zipfile

    out_dir = _safe_path(body.output_dir)
    out_path = os.path.join(out_dir, body.output_name + ".zip")
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
    return {"ok": True, "path": out_path}


@router.post("/api/extract")
async def api_extract(path: str, user=Depends(require_auth)):
    import zipfile, tarfile

    path = _safe_path(path)
    out_dir = os.path.splitext(path)[0]
    os.makedirs(out_dir, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            zf.extractall(out_dir)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as tf:
            tf.extractall(out_dir)
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 압축 형식")
    return {"ok": True, "extracted_to": out_dir}
