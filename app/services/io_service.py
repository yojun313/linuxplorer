"""Independent, bounded pools for metadata, uploads, media and searches."""

import asyncio
import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException

METADATA_POOL = ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="explorer-metadata"
)
STREAM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="explorer-stream")
UPLOAD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="explorer-upload")
SEARCH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="explorer-search")
_metadata_slots = threading.BoundedSemaphore(8)
search_slots = threading.BoundedSemaphore(2)
stream_slots = threading.BoundedSemaphore(4)
upload_slots = threading.BoundedSemaphore(2)


async def pooled(pool, function, *args, **kwargs):
    context = contextvars.copy_context()
    call = functools.partial(function, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(pool, context.run, call)


def file_io(function):
    @functools.wraps(function)
    async def isolated(*args, **kwargs):
        if not _metadata_slots.acquire(blocking=False):
            raise HTTPException(
                503,
                "파일 조회 작업이 많습니다. 잠시 후 다시 시도하세요.",
                headers={"Retry-After": "2"},
            )

        def work():
            try:
                return function(*args, **kwargs)
            finally:
                _metadata_slots.release()

        return await pooled(METADATA_POOL, work)

    return isolated


def search_io(function):
    @functools.wraps(function)
    async def isolated(*args, **kwargs):
        if not search_slots.acquire(blocking=False):
            raise HTTPException(
                503, "다른 검색이 실행 중입니다. 잠시 후 다시 시도하세요."
            )

        def work():
            try:
                return function(*args, **kwargs)
            finally:
                search_slots.release()

        return await pooled(SEARCH_POOL, work)

    return isolated
