"""Keep long operations outside the event loop and cap their resource use."""

import asyncio
import json
import sys
from pathlib import Path
from fastapi import HTTPException
from app.services.settings_service import get_root

MAX_JOBS = 2
JOB_TIMEOUT = 30
_active_jobs = 0


async def run_file_job(operation, **arguments):
    global _active_jobs
    # This counter is touched only on the event-loop thread, before any await.
    if _active_jobs >= MAX_JOBS:
        raise HTTPException(
            503,
            "다른 대용량 작업이 실행 중입니다. 잠시 후 다시 시도하세요.",
            headers={"Retry-After": "5"},
        )
    _active_jobs += 1
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.services.file_jobs",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        payload = json.dumps(
            dict(arguments, operation=operation, root=get_root())
        ).encode()
        try:
            output, _ = await asyncio.wait_for(
                process.communicate(payload), timeout=JOB_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "작업이 30초를 초과해 중단되었습니다.")
        if process.returncode:
            raise HTTPException(500, "파일 작업을 완료할 수 없습니다.")
        result = json.loads(output)
        if "error" in result:
            raise HTTPException(result.get("status", 500), result["error"])
        return result
    finally:
        if process and process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        _active_jobs -= 1
