"""Isolated, bounded heavy file operations. Never run in the web process."""

import json
import os
import shutil
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from fastapi import HTTPException
from app.services.settings_service import root_context, safe_path, visible_path

MAX_ENTRIES = 100000
MAX_BYTES = 2 * 1024**3


def walk_files(path):
    count = 0
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if visible_path(os.path.join(base, d))]
        for name in files:
            full = os.path.join(base, name)
            if not visible_path(full):
                continue
            count += 1
            if count > MAX_ENTRIES:
                raise HTTPException(413, "파일 수 제한을 초과합니다.")
            yield full


def perform(job):
    root_context.set(job["root"])
    operation = job["operation"]
    if operation == "size":
        path = safe_path(job["path"])
        size = sum(os.stat(p).st_size for p in walk_files(path))
        return {"size_raw": size}
    if operation == "copy":
        src, dst = safe_path(job["src"]), safe_path(job["dst"])
        if os.path.exists(dst):
            raise HTTPException(409, "대상에 같은 이름이 존재합니다.")
        if os.path.isdir(src):
            if os.path.commonpath([src, dst]) == src:
                raise HTTPException(
                    400, "폴더를 자신의 하위 폴더로 복사할 수 없습니다."
                )

            def ignore(base, names):
                return [n for n in names if not visible_path(os.path.join(base, n))]

            shutil.copytree(src, dst, symlinks=True, ignore=ignore)
        else:
            shutil.copy2(src, dst)
        return {"ok": True, "new_path": dst}
    if operation == "move":
        src, dst = safe_path(job["src"]), safe_path(job["dst"])
        if os.path.exists(dst):
            raise HTTPException(409, "대상에 같은 이름이 존재합니다.")
        if os.path.isdir(src) and os.path.commonpath([src, dst]) == src:
            raise HTTPException(400, "폴더를 자신의 하위 폴더로 이동할 수 없습니다.")
        shutil.move(src, dst)
        return {"ok": True, "new_path": dst}
    if operation == "compress":
        output = job["output"] if job.get("temporary") else safe_path(job["output"])
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            count = 0
            for value in job["paths"]:
                path = safe_path(value)
                for full in walk_files(path) if os.path.isdir(path) else [path]:
                    if full == output:
                        continue
                    count += 1
                    if count > MAX_ENTRIES:
                        raise HTTPException(413, "파일 수 제한을 초과합니다.")
                    archive.write(full, os.path.relpath(full, os.path.dirname(path)))
        return {"ok": True, "path": output}
    if operation == "extract":
        path, output = safe_path(job["path"]), safe_path(job["output"])
        if os.path.exists(output):
            raise HTTPException(409, "압축 해제 대상이 이미 존재합니다.")

        def validate(name):
            if "\\" in name or "\x00" in name:
                raise HTTPException(403, "안전하지 않은 압축 경로입니다.")
            destination = safe_path(os.path.join(output, name))
            if os.path.commonpath([output, destination]) != output:
                raise HTTPException(403, "안전하지 않은 압축 경로입니다.")

        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if (
                    len(members) > 10000
                    or sum(m.file_size for m in members) > MAX_BYTES
                ):
                    raise HTTPException(413, "압축 해제 용량 제한을 초과합니다.")
                for member in members:
                    validate(member.filename)
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise HTTPException(
                            403, "압축 파일의 링크는 허용하지 않습니다."
                        )
                archive.extractall(output)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                members = archive.getmembers()
                if len(members) > 10000 or sum(m.size for m in members) > MAX_BYTES:
                    raise HTTPException(413, "압축 해제 용량 제한을 초과합니다.")
                for member in members:
                    validate(member.name)
                    if not (member.isfile() or member.isdir()):
                        raise HTTPException(
                            403, "압축 파일의 링크는 허용하지 않습니다."
                        )
                archive.extractall(output, filter="data")
        else:
            raise HTTPException(400, "지원하지 않는 압축 형식입니다.")
        return {"ok": True, "extracted_to": output}
    if operation == "trash_size":
        # Size is omitted for trashed directories to avoid an automatic full scan.
        return {"size_raw": 0}
    raise HTTPException(400, "지원하지 않는 작업입니다.")


if __name__ == "__main__":
    try:
        result = perform(json.load(sys.stdin))
        print(json.dumps(result, ensure_ascii=False))
    except HTTPException as error:
        print(
            json.dumps(
                {"error": error.detail, "status": error.status_code}, ensure_ascii=False
            )
        )
    except Exception:
        print(
            json.dumps(
                {"error": "파일 작업에 실패했습니다.", "status": 500},
                ensure_ascii=False,
            )
        )
