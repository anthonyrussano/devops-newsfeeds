"""Browser smoke test for feed identification, review, preview, and safe application."""

import argparse
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import curate_feeds as review  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        root = Path(temp)
        urls = root / "urls"
        urls.write_text(
            "# Sources\nhttps://example.com/one ! clouds\nhttps://example.com/two ! security\n"
        )
        store = review.ReviewStore(urls, root / "cache", root / "state", root / "decisions.json")
        body = b"""<rss version="2.0"><channel><title>Named publisher</title>
<link>https://example.com</link><description>Identifiable source</description>
<item><title>Useful article</title><link>https://example.com/article</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
        store.client.close()
        store.client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, content=body)
                    if request.url.path in {"/one", "/three"}
                    else httpx.Response(200, content=b"<html>Not a feed</html>")
                    if request.url.path == "/html"
                    else httpx.Response(503)
                )
            )
        )
        server = review.make_server(store, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.browser_executable)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    expect(page.locator(".source-choice")).to_have_count(2)
                    page.locator("#next-review").click()
                    expect(page.locator('.source-choice[aria-pressed="true"]')).to_contain_text(
                        "/two"
                    )
                    page.locator("#check-all").click()
                    expect(page.locator("#summary")).to_contain_text("1 fetch OK", timeout=15000)
                    expect(page.locator("#summary")).to_contain_text("1 fetch failed")
                    page.locator(".source-choice").filter(has_text="Named publisher").click()
                    expect(page.locator("#detail")).to_contain_text("Useful article")
                    page.locator("#edit-title").fill("Temporary draft")
                    page.locator("#next-review").click()
                    expect(page.locator("#message")).to_contain_text("Save the current review")
                    page.locator("#discard-draft").click()
                    expect(page.locator("#edit-title")).to_have_value("Named publisher")
                    page.locator("#edit-title").fill("My RSS source")
                    page.locator("#edit-tags").fill("clouds, two words")
                    page.locator("#edit-notes").fill("Reviewed publisher and recent content")
                    page.locator('[data-action="keep"]').click()
                    expect(page.locator("#message")).to_contain_text("Saved keep")
                    assert "My RSS source" not in urls.read_text()
                    page.locator("#health").select_option("failed")
                    expect(page.locator(".source-choice")).to_have_count(1)
                    page.locator(".source-choice").click()
                    expect(page.locator("#detail")).to_contain_text("HTTP 503")
                    page.locator('[data-action="retire"]').click()
                    expect(page.locator("#message")).to_contain_text("Saved retire")
                    page.locator("#preview").click()
                    expect(page.locator("#plan-diff")).to_contain_text("My RSS source")
                    expect(page.locator("#plan-diff")).to_contain_text("# retired:")
                    expect(page.locator("#plan-diff")).to_contain_text("two words")
                    page.locator("#apply-plan").click()
                    expect(page.locator("#message")).to_contain_text("Updated")
                    assert "My RSS source" in urls.read_text()
                    assert "# retired: https://example.com/two" in urls.read_text()
                    assert list((root / "state/backups").iterdir())
                    page.locator("#health").select_option("")
                    page.locator("#review-search").fill("My RSS")
                    expect(page.locator(".source-choice")).to_have_count(1)
                    page.reload()
                    expect(page.locator("#summary")).to_contain_text("1 reviewed")
                    with page.expect_download() as download:
                        page.locator("#export-catalog").click()
                    download.value.save_as(str(root / "catalog.csv"))
                    assert "My RSS source" in (root / "catalog.csv").read_text(encoding="utf-8-sig")
                    screenshots = review.feeds.ROOT / ".cache/browser"
                    screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshots / "curation-desktop.png"))
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.body.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(screenshots / "curation-mobile.png"))
                    page.set_viewport_size({"width": 1440, "height": 1100})
                    page.locator("#add-feed").click()
                    page.locator("#add-url").fill("https://EXAMPLE.com:443/one#duplicate")
                    page.locator("#add-check").click()
                    expect(page.locator("#add-message")).to_contain_text(
                        "already in your collection"
                    )
                    page.locator("#add-open-existing").click()
                    expect(page.locator("#add-dialog")).not_to_be_visible()
                    expect(page.locator("#detail h2")).to_have_text("My RSS source")
                    page.locator("#add-feed").click()
                    page.locator("#add-url").fill("https://example.com/html")
                    page.locator("#add-check").click()
                    expect(page.locator("#add-message")).to_contain_text("live RSS/Atom")
                    expect(page.locator("#add-result")).not_to_be_visible()
                    page.locator("#add-url").fill("https://example.com/three")
                    page.locator("#add-check").click()
                    expect(page.locator("#add-name")).to_have_value("Named publisher")
                    expect(page.locator("#add-identity")).to_contain_text("Useful article")
                    before_add = urls.read_text()
                    page.locator("#add-name").fill("New source")
                    page.locator("#add-tags").fill("clouds, feed additions")
                    page.locator("#add-preview").click()
                    expect(page.locator("#add-save")).to_be_enabled()
                    expect(page.locator("#add-diff")).to_contain_text("feed additions")
                    assert urls.read_text() == before_add
                    page.locator("#add-close").click()
                    assert urls.read_text() == before_add
                    page.locator("#add-feed").click()
                    page.locator("#add-url").fill("https://example.com/three")
                    page.locator("#add-check").click()
                    expect(page.locator("#add-name")).to_have_value("Named publisher")
                    page.locator("#add-tags").fill("clouds, feed additions")
                    page.locator("#add-preview").click()
                    expect(page.locator("#add-save")).to_be_enabled()
                    page.locator("#add-name").fill("New source")
                    expect(page.locator("#add-save")).to_be_disabled()
                    page.locator("#add-preview").click()
                    expect(page.locator("#add-save")).to_be_enabled()
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate(
                        "document.getElementById('add-dialog').scrollWidth <= "
                        "document.getElementById('add-dialog').clientWidth"
                    )
                    page.screenshot(path=str(screenshots / "curation-add-mobile.png"))
                    urls.write_text(before_add + "# External edit\n")
                    page.locator("#add-save").click()
                    expect(page.locator("#add-message")).to_contain_text(
                        "Preview the addition again"
                    )
                    assert "https://example.com/three" not in urls.read_text()
                    page.locator("#add-preview").click()
                    expect(page.locator("#add-save")).to_be_enabled()
                    page.locator("#add-save").click()
                    expect(page.locator("#add-dialog")).not_to_be_visible()
                    expect(page.locator("#message")).to_contain_text("Added feed")
                    expect(page.locator(".source-choice")).to_have_count(2)
                    expect(page.locator("#detail h2")).to_have_text("New source")
                    assert "# External edit" in urls.read_text()
                    assert review.feeds.read_sources(urls)[-1].url == "https://example.com/three"
                    assert not errors, errors
                    print(
                        "PASS: identity, checks, saved reviews, "
                        "filters, preview, backup, apply, add feed, duplicates, mobile"
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            store.close()


if __name__ == "__main__":
    main()
