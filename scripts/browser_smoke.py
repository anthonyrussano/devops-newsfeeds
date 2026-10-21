"""Exercise live ttyd sessions and a local static-site fixture in Chromium."""

import argparse
import functools
import json
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_static as app  # noqa: E402


def sessions() -> set[str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "devops-newsfeed",
            "sh",
            "-c",
            "find /tmp -maxdepth 1 -name 'newsboat.*'",
        ],
        cwd=app.ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return set(result.stdout.splitlines())


def wait_for(page, condition, message):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if condition():
            return
        page.wait_for_timeout(100)
    raise AssertionError(message)


def check_web(browser, url: str, screenshots: Path):
    baseline = sessions()
    context = browser.new_context(viewport={"width": 1360, "height": 1000})
    first = context.new_page()
    frames = []
    first.on("websocket", lambda ws: ws.on("framereceived", lambda data: frames.append(data)))
    first.goto(url)
    wait_for(
        first,
        lambda: any(
            b"Apache Foundation" in data if isinstance(data, bytes) else "Apache Foundation" in data
            for data in frames
        ),
        "Newsboat did not render in the browser terminal",
    )
    first.screenshot(path=str(screenshots / "web.png"))
    second = context.new_page()
    second.goto(url)
    wait_for(second, lambda: len(sessions() - baseline) == 2, "Sessions did not get unique caches")
    first.locator(".xterm-helper-textarea").focus()
    first.keyboard.press("q")
    wait_for(second, lambda: len(sessions() - baseline) == 1, "Quit did not clean the cache")
    assert second.locator(".xterm-screen").is_visible()
    second.close()
    wait_for(first, lambda: sessions() == baseline, "Disconnect did not clean the cache")
    context.close()
    print("PASS: live Newsboat, concurrent sessions, keyboard quit, disconnect cleanup")


def check_static(browser, url: str, screenshots: Path):
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    assert page.locator(".feed-card").count() == 2
    page.locator("#search").fill("no-matching-story-000")
    assert page.locator(".filterable:visible").count() == 0
    page.locator("#search").fill("")
    page.locator("#category").select_option("two words")
    assert page.locator(".feed-card:visible").count() == 1
    assert page.locator(".story:visible").count() == 1
    page.locator("#category").select_option("")
    page.screenshot(path=str(screenshots / "static-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.body.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(screenshots / "static-mobile.png"))
    page.locator(".feed-card").first.click()
    assert page.locator("h1").inner_text() == "One"
    assert not errors, errors
    page.close()
    print("PASS: static search, multiword category filters, feed links, mobile layout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-url", default="http://127.0.0.1:8080")
    parser.add_argument("--screenshots", type=Path, default=app.ROOT / ".cache/browser")
    parser.add_argument("--browser-executable", help="Use an already installed Chromium binary")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        feeds = []
        for title, tag in (("One", "two words"), ("Two", "clouds")):
            source = app.Source(f"https://example.com/{title}", title, [tag])
            feeds.append(
                {
                    "url": source.url,
                    "title": title,
                    "tags": [tag],
                    "slug": source.slug,
                    "status": "ok",
                    "error": "",
                    "articles": [
                        {
                            "title": f"{title} story",
                            "link": "https://example.com/story",
                            "published": "2026-10-20",
                            "timestamp": 1,
                        }
                    ],
                }
            )
        app.render_site(feeds, root, 30, 300)
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.browser_executable)
                try:
                    check_web(browser, args.web_url, args.screenshots)
                    check_static(
                        browser, f"http://127.0.0.1:{server.server_port}", args.screenshots
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(json.dumps({"screenshots": str(args.screenshots)}))


if __name__ == "__main__":
    main()
