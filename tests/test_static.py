import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import generate_static as app

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Same &amp; title</title><link>https://example.com</link><description>Test</description>
<item><title>&lt;script&gt;alert(1)&lt;/script&gt;</title><link>javascript:alert(1)</link></item>
<item><title>Recent story</title><link>/new</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Older story</title><link>/old</link>
<pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Duplicate story</title><link>/new</link></item>
</channel></rss>"""


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        broken = False

        def do_GET(self):
            self.requests.append(dict(self.headers))
            if self.broken or self.path == "/broken":
                self.send_error(503)
                return
            if self.headers.get("If-None-Match") == '"feed-v1"':
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("ETag", '"feed-v1"')
            self.end_headers()
            self.wfile.write(RSS)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", Handler
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_newsboat_sources(tmp_path):
    urls = tmp_path / "urls"
    urls.write_text("""# comment
"query:Clouds:tags # \\"clouds\\""
https://example.com/feed "~A custom title" ! "two words" clouds
https://example.com/feed security # merge tags
https://example.com/other ! clouds
""")
    sources = app.read_sources(urls)
    assert len(sources) == 2
    assert sources[0].title == "A custom title"
    assert sources[0].tags == ["two words", "clouds", "security"]
    assert app.parse_urls(urls) == [s.url for s in sources]


def test_repository_collection_parses():
    sources = app.read_sources(app.ROOT / "urls")
    assert sources
    assert any("apache" in source.tags for source in sources)


def test_reject_malformed_quotes(tmp_path):
    urls = tmp_path / "urls"
    urls.write_text('https://example.com "unclosed')
    with pytest.raises(ValueError, match=r"urls:1"):
        app.read_sources(urls)


def test_feed_links_dates_and_deduplication():
    feed = app.normalize_feed(RSS, "https://example.com/feed")
    assert len(feed["articles"]) == 3
    assert feed["articles"][0]["link"] == "https://example.com/new"
    assert feed["articles"][0]["published"] == "2026-10-20"
    assert feed["articles"][-1]["link"] == ""
    with pytest.raises(ValueError, match="not an RSS"):
        app.normalize_feed(b"<html>Not a feed</html>", "https://example.com")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,test",
        "//",
        "https://[",
        "https://user:pass@example.com",
    ],
)
def test_unsafe_urls(url):
    assert app.safe_url(url) == ""


def test_conditional_cache_stale_and_offline(server, tmp_path):
    base, handler = server
    source = app.Source(base + "/rss", "Override", ["test"])
    with httpx.Client(trust_env=False) as client:
        first = app.fetch_feed(client, source, tmp_path)
        second = app.fetch_feed(client, source, tmp_path)
        assert first["status"] == "ok"
        assert second["status"] == "unchanged"
        assert handler.requests[-1]["If-None-Match"] == '"feed-v1"'
        handler.broken = True
        stale = app.fetch_feed(client, source, tmp_path)
        assert stale["status"] == "stale"
        assert stale["articles"] == first["articles"]
        assert stale["fetched_at"] == first["fetched_at"]
        assert stale["title"] == "Override"
        count = len(handler.requests)
        offline = app.fetch_feed(client, source, tmp_path, offline=True)
        assert offline["status"] == "cached"
        assert len(handler.requests) == count


def test_response_size_limit(server, tmp_path, monkeypatch):
    base, _ = server
    monkeypatch.setattr(app, "MAX_BYTES", 10)
    with httpx.Client(trust_env=False) as client:
        result = app.fetch_feed(client, app.Source(base + "/rss"), tmp_path)
    assert result["status"] == "failed"
    assert "response limit" in result["error"]


def test_build_escape_collision_and_failure_report(server, tmp_path):
    base, _ = server
    urls = tmp_path / "urls"
    urls.write_text(f"{base}/one ! clouds\n{base}/two ! security\n{base}/broken ! clouds\n")
    output = tmp_path / "site"
    args = ["--urls", str(urls), "--output", str(output), "--cache-dir", str(tmp_path / "cache")]
    assert app.main(args) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["counts"]["ok"] == 2
    assert report["counts"]["failed"] == 1
    pages = list(output.glob("*.html"))
    assert len(pages) == 4  # two feeds with identical titles, failure page, index
    for page in pages:
        content = page.read_text()
        assert "<script>alert(1)</script>" not in content
        assert 'href="javascript:' not in content
    assert "&lt;script&gt;" in (output / "index.html").read_text()
    assert app.main([*args, "--strict"]) == 1
    assert app.main([*args, "--offline"]) == 0


def test_all_failed_keeps_existing_site(tmp_path):
    urls = tmp_path / "urls"
    urls.write_text("https://example.com/rss")
    output = tmp_path / "site"
    output.mkdir()
    index = output / "index.html"
    index.write_text("previous successful build")
    assert (
        app.main(
            [
                "--urls",
                str(urls),
                "--output",
                str(output),
                "--offline",
                "--cache-dir",
                str(tmp_path / "cache"),
            ]
        )
        == 1
    )
    assert index.read_text() == "previous successful build"


@pytest.mark.parametrize("flag", ["--max-feeds", "--workers", "--timeout", "--max-entries"])
def test_invalid_limits(flag):
    with pytest.raises(SystemExit) as exc:
        app.main([flag, "-1"])
    assert exc.value.code == 2
