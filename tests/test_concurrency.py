import asyncio
import contextlib
import threading
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.services.io_service import file_io
from app.services import job_service


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_file_pool_does_not_block_event_loop(self):
        release = threading.Event()
        started = 0

        @file_io
        def blocked():
            nonlocal started
            started += 1
            release.wait(3)
            return "done"

        tasks = [asyncio.create_task(blocked()) for _ in range(8)]
        try:
            for _ in range(100):
                if started == 8:
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(started, 8)
            with self.assertRaises(HTTPException) as error:
                await blocked()
            self.assertEqual(error.exception.status_code, 503)

            # The event loop still processes a separate task while every file worker is busy.
            async def quick_request():
                return "responsive"

            self.assertEqual(await asyncio.wait_for(quick_request(), 0.1), "responsive")
            tasks[0].cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tasks[0]
            # Cancellation must not free a slot while its underlying thread is still working.
            with self.assertRaises(HTTPException):
                await blocked()
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0.02)
        self.assertEqual(await blocked(), "done")

    async def test_job_timeout_kills_worker_and_releases_slot(self):
        original = asyncio.create_subprocess_exec
        processes = []

        async def slow_worker(*args, **kwargs):
            process = await original(
                job_service.sys.executable,
                "-c",
                "import time; time.sleep(60)",
                **kwargs,
            )
            processes.append(process)
            return process

        with (
            patch.object(job_service, "JOB_TIMEOUT", 0.1),
            patch.object(job_service.asyncio, "create_subprocess_exec", slow_worker),
        ):
            with self.assertRaises(HTTPException) as error:
                await job_service.run_file_job("size", path="/tmp")
        self.assertEqual(error.exception.status_code, 504)
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(job_service._active_jobs, 0)
