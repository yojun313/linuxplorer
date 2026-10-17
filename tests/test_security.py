"""Integration regressions run against an isolated server and temporary files.
Run: .venv/bin/python -m unittest discover -s tests -v
Browser checks: uv run --with playwright python -m unittest discover -s tests -v
"""

import http.cookiejar
import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class SecurityIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="linuxplorer-test-")
        cls.base = Path(cls.tmp.name)
        cls.root = cls.base / "files"
        cls.root.mkdir()
        (cls.root / "hello.txt").write_text("Hello, Linuxplorer!")
        (cls.root / "Projects").mkdir()
        (cls.root / "Projects" / "notes.txt").write_text("Project notes")
        (cls.root / ".env").write_text("must not leak")
        cls.outside = cls.base / "outside.txt"
        cls.outside.write_text("must not leak")
        (cls.root / "outside-link").symlink_to(cls.outside)
        cls.second = cls.base / "second"
        cls.second.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.env = dict(
            os.environ,
            ROOT_DIR=str(cls.root),
            LINUXPLORER_STATE_DIR=str(cls.base / "state"),
            SECRET_KEY="integration-test-key-with-at-least-32-characters",
            ADMIN_USERNAME="tester",
            ADMIN_PASSWORD="test-password-only",
            ALLOWED_HOSTS="127.0.0.1,localhost,linuxplorer.test",
            COOKIE_SECURE="false",
        )
        cls.log = open(cls.base / "server.log", "w+")
        cls.start_server()
        cls.cookies = http.cookiejar.CookieJar()
        cls.client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cls.cookies)
        )
        with cls.client.open(cls.url + "/login") as response:
            page = response.read().decode()
        cls.csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)[1]
        login = urllib.parse.urlencode(
            {
                "username": "tester",
                "password": "test-password-only",
                "csrf_token": cls.csrf,
            }
        ).encode()
        with cls.client.open(cls.url + "/login", login) as response:
            assert response.status == 200
            assert "settingsButton" in response.read().decode()

    @classmethod
    def start_server(cls):
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
            ],
            cwd=PROJECT,
            env=cls.env,
            stdout=cls.log,
            stderr=cls.log,
        )
        for _ in range(100):
            try:
                with urllib.request.urlopen(cls.url + "/login", timeout=0.5):
                    return
            except OSError, urllib.error.URLError:
                if cls.process.poll() is not None:
                    cls.log.flush()
                    cls.log.seek(0)
                    raise RuntimeError(cls.log.read())
                time.sleep(0.05)
        raise RuntimeError("Test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.tmp.cleanup()

    def request(
        self, path, method="GET", body=None, headers=None, auth=True, csrf=True
    ):
        headers = dict(headers or {})
        if method != "GET" and csrf:
            headers["X-CSRF-Token"] = self.csrf
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url + path, data=body, method=method, headers=headers
        )
        try:
            response = (
                self.client.open(request) if auth else urllib.request.urlopen(request)
            )
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_docs_and_schema_are_unavailable(self):
        for path in [
            "/docs",
            "/docs/",
            "/docs/oauth2-redirect",
            "/redoc",
            "/openapi.json",
        ]:
            for auth in [False, True]:
                self.assertEqual(self.request(path, auth=auth)[0], 404, path)

    def test_api_requires_authentication(self):
        for path in [
            "/api/settings",
            "/explorer/api/list?path=" + urllib.parse.quote(str(self.root)),
        ]:
            self.assertEqual(self.request(path, auth=False)[0], 401)

    def test_csrf_and_proxy_https_origin(self):
        body = {"root": str(self.root)}
        self.assertEqual(self.request("/api/settings", "PUT", body, csrf=False)[0], 403)
        self.assertEqual(
            self.request(
                "/api/settings", "PUT", body, headers={"Origin": "https://evil.example"}
            )[0],
            403,
        )
        self.assertEqual(
            self.request(
                "/api/settings",
                "PUT",
                body,
                headers={"Origin": f"https://127.0.0.1:{self.port}"},
            )[0],
            200,
        )
        self.assertEqual(
            self.request(
                "/api/settings", "PUT", body, headers={"Sec-Fetch-Site": "cross-site"}
            )[0],
            403,
        )

    def test_host_allowlist_and_security_headers(self):
        self.assertEqual(
            self.request("/login", headers={"Host": "evil.example"})[0], 400
        )
        status, _, headers = self.request("/login")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("object-src 'none'", headers["Content-Security-Policy"])
        self.assertIn(
            "HttpOnly",
            next(
                c
                for c in self.request("/login", auth=False)[2].get_all("Set-Cookie")
                if "csrf_token=" in c
            ),
        )

    def test_paths_and_symlinks_and_sensitive_files(self):
        for path in [
            self.outside,
            self.root / "outside-link",
            self.root / ".env",
            self.root / ".." / "outside.txt",
            "/etc/passwd",
        ]:
            self.assertEqual(
                self.request(
                    "/explorer/serve_file?path=" + urllib.parse.quote(str(path))
                )[0],
                403,
                str(path),
            )
        status, body, _ = self.request(
            "/explorer/api/list?path="
            + urllib.parse.quote(str(self.root))
            + "&page_size=0"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("outside-link", body.decode())
        self.assertNotIn(".env", body.decode())
        self.assertEqual(
            self.request("/explorer/api/delete", "DELETE", {"path": str(self.root)})[0],
            403,
        )

    def test_upload_filename_traversal(self):
        boundary = "linuxplorer-test-boundary"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="../escape.txt"\r\nContent-Type: text/plain\r\n\r\nunsafe\r\n--{boundary}--\r\n'
        ).encode()
        self.assertEqual(
            self.request(
                "/explorer/api/upload?path=" + urllib.parse.quote(str(self.root)),
                "POST",
                body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )[0],
            400,
        )
        self.assertFalse((self.base / "escape.txt").exists())

    def test_archives_cannot_escape_or_create_links(self):
        bad_zip = self.root / "bad.zip"
        with zipfile.ZipFile(bad_zip, "w") as z:
            z.writestr("../escape.txt", "unsafe")
        self.assertEqual(
            self.request(
                "/explorer/api/extract?path=" + urllib.parse.quote(str(bad_zip)), "POST"
            )[0],
            403,
        )
        self.assertFalse((self.root / "escape.txt").exists())
        bad_tar = self.root / "bad-link.tar"
        with tarfile.open(bad_tar, "w") as t:
            item = tarfile.TarInfo("link")
            item.type = tarfile.SYMTYPE
            item.linkname = str(self.outside)
            t.addfile(item)
        self.assertEqual(
            self.request(
                "/explorer/api/extract?path=" + urllib.parse.quote(str(bad_tar)), "POST"
            )[0],
            403,
        )
        self.assertFalse((self.root / "bad-link" / "link").exists())

    def test_served_active_content_is_sandboxed(self):
        file = self.root / "active.svg"
        file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )
        status, _, headers = self.request(
            "/explorer/serve_file?path=" + urllib.parse.quote(str(file))
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Security-Policy"].startswith("sandbox;"))

    def test_invalid_root_does_not_change_settings(self):
        for root in ["relative", "/etc", str(self.outside), str(self.base / "missing")]:
            self.assertIn(
                self.request("/api/settings", "PUT", {"root": root})[0], [400, 403]
            )
        self.assertEqual(
            json.loads(self.request("/api/settings")[1])["root"], str(self.root)
        )

    def test_root_persistence_and_new_boundary(self):
        try:
            self.assertEqual(
                self.request("/api/settings", "PUT", {"root": str(self.second)})[0], 200
            )
            self.assertEqual(
                self.request(
                    "/explorer/serve_file?path="
                    + urllib.parse.quote(str(self.root / "hello.txt"))
                )[0],
                403,
            )
            self.process.terminate()
            self.process.wait(timeout=10)
            self.start_server()
            self.assertEqual(
                json.loads(self.request("/api/settings")[1])["root"], str(self.second)
            )
        finally:
            self.request("/api/settings", "PUT", {"root": str(self.root)})

    def test_login_limit(self):
        jar = http.cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with client.open(self.url + "/login") as response:
            csrf = re.search(
                r'name="csrf_token" value="([^"]+)"', response.read().decode()
            )[1]
        data = urllib.parse.urlencode(
            {"username": "wrong", "password": "wrong", "csrf_token": csrf}
        ).encode()
        codes = []
        for _ in range(11):
            try:
                with client.open(self.url + "/login", data) as response:
                    codes.append(response.status)
            except urllib.error.HTTPError as error:
                codes.append(error.code)
                error.close()
        self.assertEqual(codes[-1], 429)
        # A fresh server clears this process-local limiter before browser validation.
        self.process.terminate()
        self.process.wait(timeout=10)
        self.start_server()

    def test_large_text_preview_and_concurrent_streaming(self):
        large = self.root / "large.txt"
        with large.open("wb") as f:
            f.write(b"Large file preview\n")
            f.truncate(3 * 1024**3)
        path = urllib.parse.quote(str(large))
        status, body, _ = self.request("/explorer/api/file_info?path=" + path)
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertTrue(info["content_truncated"])
        self.assertLessEqual(len(info["content"].encode()), 128 * 1024)
        streams = []
        try:
            for _ in range(2):
                response = self.client.open(
                    self.url + "/explorer/serve_file?path=" + path
                )
                response.read(1)
                streams.append(response)
            started = time.monotonic()
            self.assertEqual(self.request("/api/settings")[0], 200)
            self.assertEqual(
                self.request(
                    "/explorer/api/list?path=" + urllib.parse.quote(str(self.root))
                )[0],
                200,
            )
            self.assertLess(time.monotonic() - started, 2)
        finally:
            for response in streams:
                response.close()
            large.unlink()
        # Repeated disconnects must not permanently consume media stream slots.
        time.sleep(0.1)
        for _ in range(6):
            status, _, _ = self.request(
                "/explorer/serve_file?path="
                + urllib.parse.quote(str(self.root / "hello.txt"))
            )
            self.assertEqual(status, 200)

    def test_z_browser_layout_and_settings(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Run with Playwright for browser validation")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/google-chrome",
                headless=True,
                args=["--no-sandbox"],
            )
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, color_scheme="dark"
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.url + "/login")
            page.fill("#username", "tester")
            page.fill("#password", "test-password-only")
            page.click("button[type=submit]")
            page.wait_for_url("**/explorer/view")
            try:
                page.wait_for_selector(".file-row", timeout=5000)
            except Exception:
                print("Browser errors:", errors)
                print("Body:", page.locator("body").inner_text()[:1200])
                page.screenshot(path="/tmp/linuxplorer-failure.png")
                raise
            page.wait_for_selector("#navTree .tree-children.open")
            page.click("#sidebarCollapse")
            self.assertLessEqual(page.locator("#navPane").bounding_box()["width"], 68)
            page.reload()
            page.wait_for_selector(".file-row")
            self.assertTrue(
                page.locator("body").evaluate(
                    "e=>e.classList.contains('sidebar-collapsed')"
                )
            )
            page.click("#sidebarCollapse")
            self.assertGreater(page.locator("#navPane").bounding_box()["width"], 200)
            page.screenshot(path="/tmp/linuxplorer-desktop.png")
            page.click("#settingsButton")
            page.wait_for_selector("#settingsSave:not([disabled])")
            for theme in ["aurora", "classic", "glass", "neu", "mesh", "mono"]:
                for mode in ["light", "dark"]:
                    page.select_option("#settingTheme", theme)
                    page.select_option("#settingMode", mode)
                    page.click("#settingsSave")
                    self.assertEqual(
                        page.locator("html").get_attribute("data-theme"),
                        f"{theme}-{mode}",
                    )
                    page.click("#settingsButton")
                    page.wait_for_selector("#settingsSave:not([disabled])")
            page.select_option("#settingTheme", "aurora")
            page.select_option("#settingMode", "dark")
            page.click("#settingsSave")
            page.set_viewport_size({"width": 390, "height": 844})
            self.assertLessEqual(
                page.evaluate("document.documentElement.scrollWidth"), 390
            )
            page.wait_for_function(
                "() => document.getElementById('navPane').getBoundingClientRect().x < -200"
            )
            self.assertLess(page.locator("#navPane").bounding_box()["x"], 0)
            page.screenshot(path="/tmp/linuxplorer-mobile.png")
            page.click("#mobileFolders")
            self.assertEqual(
                page.locator("#sidebarToggle").get_attribute("aria-expanded"), "true"
            )
            page.click("#sidebarClose")
            page.click('.file-row:has-text("hello.txt") .file-more')
            self.assertTrue(page.locator("#ctxMenu").is_visible())
            page.keyboard.press("Escape")
            page.click("#settingsButton")
            page.wait_for_selector("#settingsSave:not([disabled])")
            page.screenshot(path="/tmp/linuxplorer-settings.png")
            small = page.evaluate(
                "[...document.querySelectorAll('input[type=text],input[type=password],select')].filter(e=>e.offsetParent&&parseFloat(getComputedStyle(e).fontSize)<16).map(e=>e.id)"
            )
            self.assertEqual(small, [])
            page.keyboard.press("Escape")
            for width in [320, 375, 390, 768, 1024, 1440]:
                page.set_viewport_size({"width": width, "height": 900})
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"), width
                )
            page.set_viewport_size({"width": 390, "height": 844})
            page.click('.file-row:has-text("hello.txt") .fr-name')
            self.assertTrue(
                page.locator("#fsModal").evaluate("e=>e.classList.contains('open')")
            )
            page.click(".fs-close")
            page.click('.file-row:has-text("Projects") .fr-name')
            page.wait_for_url("**/explorer/view?path=**")
            self.assertIn("notes.txt", page.locator("#fileListDetails").inner_text())
            self.assertEqual(errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
