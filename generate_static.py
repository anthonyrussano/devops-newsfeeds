#!/usr/bin/env python3
"""Build a searchable static reader from the shared Newsboat urls file."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import shlex
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 5 * 1024 * 1024


def plain_text(value: str) -> str:
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = Text()
    parser.feed(str(value))
    return " ".join(" ".join(parser.parts).split())[:2000]


@dataclass
class Source:
    url: str
    title: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:24]


def read_sources(path: Path) -> list[Source]:
    """Read Newsboat's quoted titles/tags, merging repeated URLs in file order."""
    sources: dict[str, Source] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            parts = shlex.split(line, comments=True)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if not parts or not safe_url(parts[0]):
            continue
        source = sources.setdefault(parts[0], Source(parts[0]))
        for tag in parts[1:]:
            if tag.startswith("~"):
                source.title = tag[1:]
            elif not tag.startswith("!") and tag not in source.tags:
                source.tags.append(tag)
    return list(sources.values())


def parse_urls(file_path: Path) -> list[str]:
    """Compatibility helper for callers of the original generator."""
    return [source.url for source in read_sources(file_path)]


def safe_url(value: str, base: str = "") -> str:
    value = urljoin(base, str(value).strip())
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
            return value
    except ValueError:
        pass
    return ""


def normalize_feed(body: bytes, base: str) -> dict:
    parsed = feedparser.parse(body)
    if not parsed.version:
        raise ValueError("Response is not an RSS or Atom feed")
    articles = []
    seen = set()
    for entry in parsed.entries:
        link = safe_url(entry.get("link", ""), base) if entry.get("link") else ""
        key = link or str(entry.get("id", entry.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        date = entry.get("published_parsed") or entry.get("updated_parsed")
        try:
            timestamp = calendar.timegm(date) if date else 0
            published = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d") if date else ""
        except (ValueError, OverflowError, TypeError, OSError):
            timestamp, published = 0, ""
        articles.append(
            {
                "title": str(entry.get("title", "(untitled)")),
                "link": link,
                "published": published,
                "timestamp": timestamp,
            }
        )
    articles.sort(key=lambda article: article["timestamp"], reverse=True)
    title = plain_text(parsed.feed.get("title", ""))
    return {
        "metadata_version": 1,
        "title": title or base,
        "feed_title": title,
        "description": plain_text(parsed.feed.get("subtitle", parsed.feed.get("description", ""))),
        "homepage": safe_url(parsed.feed.get("link", ""), base) if parsed.feed.get("link") else "",
        "publisher": plain_text(parsed.feed.get("author", parsed.feed.get("publisher", ""))),
        "language": str(parsed.feed.get("language", "")),
        "format": parsed.version,
        "parser_warning": str(parsed.bozo_exception)[:300] if parsed.get("bozo") else "",
        "articles": articles,
    }


def read_cache(path: Path) -> dict:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("version") == 1 and isinstance(cached.get("feed", {}).get("articles"), list):
            return cached
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def fetch_feed(
    client: httpx.Client, source: Source, cache_dir: Path, offline: bool = False
) -> dict:
    cache_path = cache_dir / f"{source.slug}.json"
    cached = read_cache(cache_path)
    result = {**asdict(source), "slug": source.slug, "status": "ok", "error": ""}
    started = time.monotonic()
    if not offline:
        result["checked_at"] = datetime.now(UTC).isoformat()
        result.update(http_status=None, final_url="", content_type="")
    try:
        if offline:
            if not cached:
                raise ValueError("No cached feed available")
            result.update(cached["feed"], status="cached")
        else:
            headers = {}
            # Older caches need one full fetch to collect the richer feed metadata.
            has_metadata = cached.get("feed", {}).get("metadata_version") == 1
            if has_metadata and cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if has_metadata and cached.get("modified"):
                headers["If-Modified-Since"] = cached["modified"]
            with client.stream("GET", source.url, headers=headers) as response:
                result.update(
                    http_status=response.status_code,
                    final_url=str(response.url),
                    content_type=response.headers.get(
                        "content-type", cached.get("content_type", "")
                    ),
                )
                if response.status_code == 304 and cached:
                    result.update(cached["feed"], status="unchanged")
                else:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError("Feed exceeds the 5 MiB response limit")
                    feed = normalize_feed(bytes(body), str(response.url))
                    result.update(feed)
                    payload = {
                        "version": 1,
                        "feed": feed,
                        "etag": response.headers.get("etag", ""),
                        "modified": response.headers.get("last-modified", ""),
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "http_status": response.status_code,
                        "final_url": str(response.url),
                        "content_type": response.headers.get("content-type", ""),
                    }
                    temp = cache_path.with_suffix(".tmp")
                    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    temp.replace(cache_path)
                    cached = payload
        result["fetched_at"] = cached.get("fetched_at", "")
    except (httpx.HTTPError, ValueError, OSError) as exc:
        result.update(status="failed", error=str(exc), articles=[])
        if cached:
            result.update(cached["feed"], status="stale", fetched_at=cached.get("fetched_at", ""))
    result["title"] = source.title or result.get("title") or source.url
    result["elapsed_seconds"] = round(time.monotonic() - started, 2) if not offline else None
    return result


def render_site(results: list[dict], output: Path, max_entries: int, latest_limit: int) -> None:
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"])
    )
    output.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    feeds = sorted(results, key=lambda item: item["title"].casefold())
    latest = []
    for feed in feeds:
        feed["articles"] = feed["articles"][:max_entries]
        for article in feed["articles"]:
            latest.append({**article, "feed": feed})
        page = env.get_template("feed.html").render(feed=feed, generated=generated)
        (output / f"{feed['slug']}.html").write_text(page, encoding="utf-8")
    latest.sort(key=lambda item: item["timestamp"], reverse=True)
    counts = {
        status: sum(f["status"] == status for f in feeds)
        for status in ("ok", "unchanged", "cached", "stale", "failed")
    }
    index = env.get_template("index.html").render(
        feeds=feeds,
        latest=latest[:latest_limit],
        generated=generated,
        counts=counts,
        tags=sorted({tag for feed in feeds for tag in feed["tags"]}),
    )
    (output / "index.html").write_text(index, encoding="utf-8")
    for asset in ("style.css", "search.js"):
        (output / asset).write_bytes((ROOT / "assets" / asset).read_bytes())
    report = {
        "generated_at": generated,
        "counts": counts,
        "feeds": [
            {key: value for key, value in feed.items() if key != "articles"} for feed in feeds
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", type=Path, default=ROOT / "urls")
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/feeds"))
    parser.add_argument("--max-feeds", type=nonnegative, default=os.environ.get("MAX_FEEDS", "0"))
    parser.add_argument("--workers", type=positive, default=8)
    parser.add_argument(
        "--timeout", type=positive, default=15, help="Connect/read timeout in seconds"
    )
    parser.add_argument("--max-entries", type=positive, default=30, help="Articles per feed")
    parser.add_argument("--latest-limit", type=positive, default=300)
    parser.add_argument("--ca-bundle", type=Path, help="Additional PEM CA certificates")
    parser.add_argument("--offline", action="store_true", help="Rebuild using cached feeds only")
    parser.add_argument("--strict", action="store_true", help="Fail if any feed is failed or stale")
    args = parser.parse_args(argv)
    try:
        sources = read_sources(args.urls)
        if args.max_feeds:
            sources = sources[: args.max_feeds]
        if not sources:
            parser.error("No HTTP(S) feeds found")
        context = ssl.create_default_context()
        if args.ca_bundle:
            context.load_verify_locations(cafile=str(args.ca_bundle))
        args.cache_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    results = []
    with (
        httpx.Client(
            verify=context,
            max_redirects=5,
            timeout=args.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "devops-newsfeeds/0.2 (+https://github.com/anthonyrussano/devops-newsfeeds)"
            },
            limits=httpx.Limits(
                max_connections=args.workers, max_keepalive_connections=args.workers
            ),
        ) as client,
        ThreadPoolExecutor(max_workers=args.workers) as pool,
    ):
        futures = [
            pool.submit(fetch_feed, client, source, args.cache_dir, args.offline)
            for source in sources
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"[{result['status']}] {result['url']} {result['error']}", flush=True)
    if not any(result["status"] != "failed" for result in results):
        print("No feeds available; existing site left untouched.", file=sys.stderr)
        return 1
    render_site(results, args.output, args.max_entries, args.latest_limit)
    failed = sum(result["status"] in {"failed", "stale"} for result in results)
    print(f"Built {args.output}/index.html: {len(results)} feeds, {failed} failed or stale.")
    return int(args.strict and failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
