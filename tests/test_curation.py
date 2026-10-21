import csv
import io
import json
import shlex
import threading
import time
from pathlib import Path

import httpx
import pytest

import curate_feeds as review
import generate_static as feeds

SAMPLE = b"""<rss version="2.0"><channel><title>Named publisher</title>
<link>https://example.com/home</link>
<description>A &lt;b&gt;useful&lt;/b&gt; description</description>
<managingEditor>Editor</managingEditor><language>en</language>
<item><title>Recent story</title><link>https://example.com/story</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
URLS = """# Preserve this heading
"query:Clouds:tags # \\"clouds\\""
https://example.com/one ! clouds # keep this note
https://example.com/two ! security
"""


@pytest.fixture
def store(tmp_path):
    urls = tmp_path / "urls"
    urls.write_text(URLS)
    instance = review.ReviewStore(
        urls, tmp_path / "cache", tmp_path / "state", tmp_path / "decisions.json"
    )
    yield instance
    instance.close()


def test_feed_identity_metadata():
    feed = feeds.normalize_feed(SAMPLE, "https://example.com/rss")
    assert feed["feed_title"] == "Named publisher"
    assert feed["homepage"] == "https://example.com/home"
    assert feed["description"] == "A useful description"
    assert feed["publisher"] == "Editor"
    assert feed["language"] == "en"


def test_missing_title_stays_unknown(store):
    result = feeds.normalize_feed(
        b'<rss version="2.0"><channel></channel></rss>', "https://example.com/one"
    )
    assert result["feed_title"] == ""
    store.catalog["https://example.com/one"] = result
    assert store.snapshot()["feeds"][0]["feed_title"] == ""


def test_catalog_csv_escapes_formulas_and_contains_all_sources(store):
    store.catalog["https://example.com/one"] = {
        "title": "=2+2",
        "feed_title": "=2+2",
        "publisher": "+Formula",
        "articles": [],
    }
    rows = list(csv.DictReader(io.StringIO(store.export_csv().decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[0]["Display name"] == "'=2+2"
    assert rows[0]["Publisher/author"] == "'+Formula"
    assert rows[0]["URL"] == "https://example.com/one"
    assert rows[0]["Source lines"] == "3"
    assert not store.decisions_path.exists()


def test_plan_preserves_groups_hidden_tags_and_comments():
    decision = {"action": "keep", "title": 'A "quoted" publisher', "tags": ["two words"]}
    plan = review.plan_changes(URLS, {"https://example.com/one": decision})
    assert '# Preserve this heading\n"query:Clouds:' in plan["proposed"]
    assert "# keep this note" in plan["proposed"]
    assert "https://example.com/two ! security\n" in plan["proposed"]
    parts = [shlex.split(line, comments=True) for line in plan["proposed"].splitlines()]
    first = next(p for p in parts if p and p[0] == "https://example.com/one")
    assert first == ["https://example.com/one", '~A "quoted" publisher', "!", "two words"]
    assert plan["new_categories"] == ["two words"]
    assert any(p and p[0] == 'query:two words:tags # "two words"' for p in parts)
    assert (
        review.plan_changes(plan["proposed"], {"https://example.com/one": decision})["diff"] == ""
    )


def test_existing_query_category_does_not_duplicate_group():
    decision = {"action": "keep", "title": "Cloud source", "tags": ["clouds"]}
    plan = review.plan_changes(URLS, {"https://example.com/one": decision})
    assert not plan["new_categories"]
    assert plan["proposed"].count('"query:Clouds:') == 1


def test_investigate_does_not_edit_feed_and_retire_is_reversible():
    plan = review.plan_changes(
        URLS,
        {
            "https://example.com/one": {"action": "investigate", "title": "Changed", "tags": []},
            "https://example.com/two": {"action": "retire", "title": "", "tags": []},
        },
    )
    assert "https://example.com/one ! clouds # keep this note\n" in plan["proposed"]
    assert "# retired: https://example.com/two ! security\n" in plan["proposed"]


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "delete"},
        {"action": "keep", "title": "line\nnew"},
        {"action": "keep", "tags": ["!hidden"]},
        {"action": "keep", "tags": ["~name"]},
        {"action": "keep", "tags": [4]},
        {"action": "keep", "tags": "clouds"},
    ],
)
def test_bad_decision_rejected(decision):
    with pytest.raises(ValueError):
        review.validate_decision(decision)


def test_apply_requires_matching_preview_and_writes_backup(store):
    store.save_decision(
        "https://example.com/one", {"action": "keep", "title": "Named", "tags": ["clouds"]}
    )
    assert store.urls.read_text() == URLS
    plan = store.plan()
    store.save_decision("https://example.com/one", {"action": "retire"})
    with pytest.raises(ValueError, match="Preview"):
        store.apply(plan["revision"])
    current = store.plan()
    result = store.apply(current["revision"])
    assert Path(result["backup"]).read_text() == URLS
    assert "# retired: https://example.com/one" in store.urls.read_text()
    assert store.urls.stat().st_mode & 0o777 == 0o644
    saved = json.loads(store.decisions_path.read_text())
    assert saved["feeds"]["https://example.com/one"]["action"] == "retire"


def test_external_urls_edit_invalidates_preview(store):
    store.save_decision("https://example.com/one", {"action": "retire"})
    plan = store.plan()
    store.urls.write_text(URLS + "# Someone edited this file\n")
    with pytest.raises(ValueError, match="Preview"):
        store.apply(plan["revision"])


def test_second_curator_cannot_overwrite_same_state(store):
    with pytest.raises(ValueError, match="Another curator"):
        review.ReviewStore(store.urls, store.cache, store.state, store.decisions_path)


def test_check_preserves_identity_and_collects_history(store):
    healthy = True

    def respond(request):
        return httpx.Response(200, content=SAMPLE) if healthy else httpx.Response(503)

    store.client.close()
    store.client = httpx.Client(transport=httpx.MockTransport(respond))
    source = feeds.read_sources(store.urls)[0]
    store._check(source)
    first = store.snapshot()["feeds"][0]
    assert first["feed_title"] == "Named publisher"
    assert first["http_status"] == 200
    assert first["checked_at"]
    healthy = False
    store._check(source)
    second = store.snapshot()["feeds"][0]
    assert second["status"] == "stale"
    assert second["feed_title"] == "Named publisher"
    assert second["http_status"] == 503
    assert len(second["history"]) == 2
    assert second["latest_timestamp"] > 0


def test_duplicate_source_locations(store):
    store.urls.write_text(URLS + "https://example.com/one ! security\n")
    row = store.snapshot()["feeds"][0]
    assert row["lines"] == [3, 5]
    assert row["tags"] == ["clouds", "security"]


def test_network_failure_does_not_reuse_old_http_200(store):
    source = feeds.read_sources(store.urls)[0]
    store.client.close()
    store.client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=SAMPLE))
    )
    store._check(source)
    store.client.close()

    def unavailable(request):
        raise httpx.ConnectError("DNS failed", request=request)

    store.client = httpx.Client(transport=httpx.MockTransport(unavailable))
    store._check(source)
    row = store.snapshot()["feeds"][0]
    assert row["status"] == "stale"
    assert row["http_status"] is None
    assert row["error"] == "DNS failed"


def test_api_requires_token_known_feed_and_json(store):
    server = review.make_server(store, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            snapshot = client.get("/api/feeds").json()
            assert len(snapshot["feeds"]) == 2
            assert client.get("/../urls").status_code == 404
            assert (
                client.get("/api/feeds", headers={"Host": "untrusted.example"}).status_code == 403
            )
            value = {"url": "https://example.com/one", "decision": {"action": "retire"}}
            assert client.post("/api/decision", json=value).status_code == 403
            headers = {"X-Review-Token": snapshot["token"]}
            assert client.post("/api/decision", json=value, headers=headers).status_code == 200
            assert store.urls.read_text() == URLS
            value["url"] = "https://arbitrary.example/rss"
            assert client.post("/api/decision", json=value, headers=headers).status_code == 400
            assert (
                client.post("/api/check", json={"url": value["url"]}, headers=headers).status_code
                == 400
            )
            assert (
                client.post("/api/apply", json={"revision": "wrong"}, headers=headers).status_code
                == 400
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def mock_add_feed(store, responder=None):
    store.client.close()
    store.client = httpx.Client(
        transport=httpx.MockTransport(
            responder or (lambda request: httpx.Response(200, content=SAMPLE))
        )
    )
    return store.check_addition("https://new.example/feed")


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.com/feed",
        "javascript:alert(1)",
        "/relative",
        "",
        "https://",
        "https://user:password@example.com/feed",
        "https://example.com:wrong/feed",
        "https://example.com/line\nnew",
        "https://example.com/a b",
        None,
    ],
)
def test_add_rejects_invalid_urls(value):
    with pytest.raises(ValueError):
        review.normalize_feed_url(value)


def test_add_normalizes_without_changing_encoded_paths_or_queries():
    assert (
        review.normalize_feed_url("HTTPS://EXAMPLE.COM:443/feed#fragment")
        == "https://example.com/feed"
    )
    assert review.normalize_feed_url("https://example.com?rss=1") == "https://example.com/?rss=1"
    assert (
        review.normalize_feed_url("https://example.com/a%2Fb?b=2&a=1")
        == "https://example.com/a%2Fb?b=2&a=1"
    )


def test_duplicate_rejected_before_fetch(store):
    def unexpected(_request):
        raise AssertionError("Duplicates must not cause a network request")

    store.client.close()
    store.client = httpx.Client(transport=httpx.MockTransport(unexpected))
    with pytest.raises(review.DuplicateFeed) as error:
        store.check_addition("HTTPS://EXAMPLE.COM:443/one#section")
    assert error.value.url == "https://example.com/one"


def test_redirect_to_existing_feed_rejected(store):
    def redirect(request):
        if request.url.host == "new.example":
            return httpx.Response(301, headers={"Location": "https://example.com/one"})
        return httpx.Response(200, content=SAMPLE)

    store.client.close()
    store.client = httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True)
    with pytest.raises(review.DuplicateFeed):
        store.check_addition("https://new.example/feed")


def test_known_redirect_alias_rejected(store):
    store.catalog["https://example.com/one"] = {"final_url": "https://new.example/feed"}
    with pytest.raises(review.DuplicateFeed):
        store.check_addition("https://new.example/feed")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>Not a feed</html>"),
        httpx.Response(404),
    ],
)
def test_add_requires_live_rss_or_atom(store, response):
    with pytest.raises(ValueError, match="live RSS/Atom"):
        mock_add_feed(store, lambda request: response)
    assert store.urls.read_text() == URLS
    assert not store.decisions_path.exists()


def test_add_check_preview_and_save_are_separate_and_preserve_pending_reviews(store):
    checked = mock_add_feed(store)
    assert checked["feed"]["feed_title"] == "Named publisher"
    assert store.urls.read_text() == URLS
    assert not store.decisions_path.exists()
    store.save_decision("https://example.com/two", {"action": "retire"})
    decision = {"title": 'New "named" source', "tags": ["two words"], "notes": "A useful addition"}
    plan = store.preview_addition(checked["check_token"], decision)
    assert "New" in plan["diff"]
    assert plan["new_categories"] == ["two words"]
    assert store.urls.read_text() == URLS
    result = store.add_feed(checked["check_token"], plan["revision"])
    assert Path(result["backup"]).read_text() == URLS
    assert result["url"] == "https://new.example/feed"
    current = feeds.read_sources(store.urls)
    assert len(current) == 3
    assert current[-1].title == 'New "named" source'
    assert current[-1].tags == ["two words"]
    assert "https://example.com/two ! security\n" in store.urls.read_text()
    assert "# retired:" not in store.urls.read_text()
    assert store.snapshot()["feeds"][-1]["publisher"] == "Editor"
    assert (
        json.loads(store.decisions_path.read_text())["feeds"][result["url"]]["notes"]
        == "A useful addition"
    )
    with pytest.raises(ValueError):
        store.add_feed(checked["check_token"], plan["revision"])


def test_add_requires_current_preview_and_check(store):
    checked = mock_add_feed(store)
    token = checked["check_token"]
    with pytest.raises(ValueError, match="Preview"):
        store.add_feed(token, "not-a-preview")
    plan = store.preview_addition(token, {"title": "New", "tags": []})
    store.urls.write_text(URLS + "# External edit\n")
    with pytest.raises(ValueError, match="Preview"):
        store.add_feed(token, plan["revision"])
    plan = store.preview_addition(token, {"title": "New", "tags": []})
    store.preview_addition(token, {"title": "Changed", "tags": []})
    with pytest.raises(ValueError, match="Preview"):
        store.add_feed(token, plan["revision"])
    store.add_candidates[token]["expires"] = time.monotonic() - 1
    with pytest.raises(ValueError, match="expired"):
        store.preview_addition(token, {"title": "New"})


def test_untagged_addition_is_visible_and_handles_missing_final_newline():
    plan = review.plan_addition("# Heading", "https://new.example/feed", {"title": "New"})
    assert plan["proposed"].startswith("# Heading\n")
    tokens = shlex.split(plan["proposed"].splitlines()[-1])
    assert tokens == ["https://new.example/feed", "~New"]


def test_same_feed_added_by_another_tab_invalidates_candidate(store):
    first = mock_add_feed(store)
    second = store.check_addition("https://new.example/feed")
    first_plan = store.preview_addition(first["check_token"], {"title": "New"})
    second_plan = store.preview_addition(second["check_token"], {"title": "New"})
    store.add_feed(first["check_token"], first_plan["revision"])
    with pytest.raises(review.DuplicateFeed):
        store.add_feed(second["check_token"], second_plan["revision"])


def test_atom_feed_can_be_checked_and_added(store):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom publisher</title>
<id>https://new.example/</id><updated>2026-10-20T10:00:00Z</updated>
<entry><title>Atom article</title><id>urn:article:one</id>
<link href="https://new.example/article"/><updated>2026-10-20T10:00:00Z</updated></entry></feed>"""
    checked = mock_add_feed(store, lambda request: httpx.Response(200, content=atom))
    assert checked["feed"]["format"] == "atom10"
    assert checked["feed"]["articles"][0]["title"] == "Atom article"
    token = checked["check_token"]
    plan = store.preview_addition(token, {"title": "Atom publisher", "tags": ["clouds"]})
    store.add_feed(token, plan["revision"])
    assert feeds.read_sources(store.urls)[-1].title == "Atom publisher"


def test_failed_check_cannot_use_old_content_as_live_validation(store):
    mock_add_feed(store)
    with pytest.raises(ValueError, match="live RSS/Atom"):
        mock_add_feed(store, lambda request: httpx.Response(503))
    assert store.urls.read_text() == URLS
