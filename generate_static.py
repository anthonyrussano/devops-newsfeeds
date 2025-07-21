#!/usr/bin/env python3
"""Generate a static website with the latest articles from the feeds listed in the
newsboat `urls` file.
"""
import os
import re
import datetime
from pathlib import Path

import feedparser
import requests

URLS_FILE = Path("urls")
OUTPUT_DIR = Path("site")
MAX_FEEDS = int(os.environ.get("MAX_FEEDS", "0"))  # limit number of feeds when testing


def parse_urls(file_path: Path):
    urls = []
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("\"") or line.startswith("query:"):
            continue
        match = re.match(r"(https?://\S+)", line)
        if match:
            urls.append(match.group(1))
    return urls


def sanitize(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    return name[:50] or "feed"


def write_feed_page(feed, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        fh.write("<html><head><meta charset='utf-8'><title>{title}</title></head><body>\n".format(title=feed.feed.get("title", "Feed")))
        fh.write(f"<h1>{feed.feed.get('title', 'Feed')}</h1>\n")
        fh.write("<ul>\n")
        for entry in feed.entries:
            title = entry.get("title", "(no title)")
            link = entry.get("link", "#")
            fh.write(f"  <li><a href='{link}'>{title}</a></li>\n")
        fh.write("</ul>\n")
        fh.write("</body></html>\n")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    urls = parse_urls(URLS_FILE)
    index_entries = []
    for url in urls:
        if MAX_FEEDS and len(index_entries) >= MAX_FEEDS:
            break
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            print(f"Failed to fetch {url}: {exc}")
            continue
        title = feed.feed.get("title", url)
        slug = sanitize(title)
        dest = OUTPUT_DIR / f"{slug}.html"
        write_feed_page(feed, dest)
        index_entries.append((title, dest.name))

    with (OUTPUT_DIR / "index.html").open("w", encoding="utf-8") as index:
        index.write("<html><head><meta charset='utf-8'><title>Newsboat Static</title></head><body>\n")
        index.write(f"<h1>Newsboat Static - {datetime.date.today()}</h1>\n")
        index.write("<ul>\n")
        for title, filename in sorted(index_entries):
            index.write(f"  <li><a href='{filename}'>{title}</a></li>\n")
        index.write("</ul>\n")
        index.write("</body></html>\n")


if __name__ == "__main__":
    main()
