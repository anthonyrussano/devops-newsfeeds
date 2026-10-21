#!/usr/bin/env python3
"""Identify, check, and curate Newsboat feeds in a local browser dashboard."""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import secrets
import shlex
import sqlite3
import ssl
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

import generate_static as feeds

ACTIONS = {"unreviewed", "keep", "investigate", "retire"}


class DuplicateFeed(ValueError):
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"This feed is already in your collection: {url}")


def normalize_feed_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("Enter an HTTP or HTTPS feed URL")
    value = value.strip()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Feed URLs cannot contain spaces or control characters")
    try:
        parsed = httpx.URL(value).copy_with(fragment=None)
        if not feeds.safe_url(str(parsed)) or parsed.userinfo:
            raise ValueError("Use an HTTP or HTTPS URL without embedded credentials")
        parts = urlsplit(str(parsed))
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    except httpx.InvalidURL as exc:
        raise ValueError("Enter a valid HTTP or HTTPS feed URL") from exc


def now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        temp = Path(f.name)
        try:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            temp.chmod(mode)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_decision(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("action") not in ACTIONS:
        raise ValueError("Choose unreviewed, keep, investigate, or retire")
    title, tags, notes = value.get("title", ""), value.get("tags", []), value.get("notes", "")
    if not isinstance(title, str) or len(title) > 500:
        raise ValueError("Title must be text of at most 500 characters")
    if not isinstance(notes, str) or len(notes) > 4000:
        raise ValueError("Notes must be text of at most 4000 characters")
    if not isinstance(tags, list) or len(tags) > 50:
        raise ValueError("Categories must be a list of up to 50 tags")
    for text in [title, *tags]:
        if not isinstance(text, str) or any(c in text for c in ("\n", "\r", "\0")):
            raise ValueError("Titles and categories must be single-line text")
    if any(not tag.strip() or len(tag) > 100 or tag.startswith(("!", "~")) for tag in tags):
        raise ValueError(
            "Categories must be nonempty, at most 100 characters, and not start with ! or ~"
        )
    return {
        "action": value["action"],
        "title": title.strip(),
        "tags": list(dict.fromkeys(tag.strip() for tag in tags)),
        "notes": notes,
        "reviewed_at": now(),
    }


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def trailing_comment(line: str) -> str:
    quoted, escaped = False, False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "#" and not quoted:
            return line[index:].rstrip("\r\n")
    return ""


def plan_changes(original: str, decisions: dict) -> dict:
    """Only explicitly kept/retired sources change; query feeds and comments survive."""
    lines, changed, kept_tags = [], 0, set()
    for line in original.splitlines(keepends=True):
        parts = shlex.split(line, comments=True)
        decision = decisions.get(parts[0]) if parts and feeds.safe_url(parts[0]) else None
        if not decision or decision.get("action") not in {"keep", "retire"}:
            lines.append(line)
            continue
        decision = validate_decision(decision)
        if decision["action"] == "retire":
            replacement = "# retired: " + line
        else:
            kept_tags.update(decision["tags"])
            old_title = next((tag[1:] for tag in reversed(parts[1:]) if tag.startswith("~")), "")
            old_tags = [tag for tag in parts[1:] if not tag.startswith(("!", "~"))]
            if old_title == decision["title"] and old_tags == decision["tags"]:
                lines.append(line)
                continue
            special = [tag for tag in parts[1:] if tag.startswith("!")]
            tokens = [quote(parts[0])]
            if decision["title"]:
                tokens.append(quote("~" + decision["title"]))
            tokens.extend(quote(tag) for tag in [*special, *decision["tags"]])
            comment = trailing_comment(line)
            replacement = line[: len(line) - len(line.lstrip())] + " ".join(tokens)
            if comment:
                replacement += " " + comment
            replacement += "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        changed += replacement != line
        lines.append(replacement)
    proposed = "".join(lines)
    # New categories need query groups because most feeds in this collection are hidden.
    queries = [
        shlex.split(line, comments=True)[0]
        for line in original.splitlines()
        if line.lstrip().startswith('"query:')
    ]
    missing = [
        tag
        for tag in sorted(kept_tags)
        if not any(f"tags # {quote(tag)}" in query for query in queries)
    ]
    if missing:
        groups = [quote(f"query:{tag.replace(':', ' / ')}:tags # {quote(tag)}") for tag in missing]
        proposed = "# Categories added during feed review\n" + "\n".join(groups) + "\n\n" + proposed
        changed += len(groups)
    return make_plan(original, proposed, changed, missing)


def make_plan(original: str, proposed: str, changed: int, missing: list[str]) -> dict:
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile="urls",
            tofile="urls (proposed)",
        )
    )
    return {
        "changed_lines": changed,
        "diff": diff,
        "proposed": proposed,
        "revision": hashlib.sha256((original + "\0" + proposed).encode()).hexdigest(),
        "new_categories": missing,
    }


def plan_addition(original: str, url: str, decision: dict) -> dict:
    decision = validate_decision({**decision, "action": "keep"})
    tokens = [quote(normalize_feed_url(url))]
    if decision["title"]:
        tokens.append(quote("~" + decision["title"]))
    if decision["tags"]:
        tokens.append("!")
    tokens.extend(quote(tag) for tag in decision["tags"])
    separator = "" if not original or original.endswith("\n") else "\n"
    appended = original + separator + "\n# Added during feed review\n" + " ".join(tokens) + "\n"
    grouped = plan_changes(appended, {normalize_feed_url(url): decision})
    return make_plan(
        original, grouped["proposed"], 1 + grouped["changed_lines"], grouped["new_categories"]
    )


class ReviewStore:
    def __init__(
        self,
        urls: Path,
        cache: Path,
        state: Path,
        decisions: Path,
        report: Path | None = None,
        ca_bundle: Path | None = None,
        workers: int = 8,
        timeout: int = 15,
    ):
        self.urls, self.cache, self.state, self.decisions_path = urls, cache, state, decisions
        state.mkdir(parents=True, exist_ok=True)
        self.guard = sqlite3.connect(state / "curator.db", timeout=0)
        try:
            self.guard.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            self.guard.close()
            raise ValueError(
                "Another curator is using this state directory; use its dashboard"
            ) from exc
        self.lock = threading.RLock()
        self.catalog = load_json(state / "catalog.json", {})
        self.decisions = load_json(decisions, {"version": 1, "feeds": {}})
        if self.decisions.get("version") != 1 or not isinstance(self.decisions.get("feeds"), dict):
            raise ValueError("Unsupported decisions file")
        self.pending: set[str] = set()
        self.scan_errors: dict[str, str] = {}
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.token = secrets.token_urlsafe(32)
        self.add_candidates: dict[str, dict] = {}
        self.add_checks = threading.BoundedSemaphore(2)
        context = ssl.create_default_context()
        if ca_bundle:
            context.load_verify_locations(cafile=str(ca_bundle))
        self.client = httpx.Client(
            verify=context,
            follow_redirects=True,
            max_redirects=5,
            timeout=timeout,
            headers={"User-Agent": "devops-newsfeeds-review/0.2"},
            limits=httpx.Limits(max_connections=workers),
        )
        self.cache.mkdir(parents=True, exist_ok=True)
        previous = load_json(report, {}) if report else {}
        previous_feeds = {f["url"]: f for f in previous.get("feeds", [])}
        for source in feeds.read_sources(urls):
            if source.url in self.catalog:
                continue
            cached = feeds.read_cache(cache / f"{source.slug}.json")
            record = {**cached.get("feed", {}), **previous_feeds.get(source.url, {})}
            record.setdefault("feed_title", cached.get("feed", {}).get("title", ""))
            record["articles"] = cached.get("feed", {}).get("articles", [])[:5]
            record.setdefault("fetched_at", cached.get("fetched_at", ""))
            if source.url in previous_feeds:
                record.setdefault("checked_at", previous.get("generated_at", ""))
            record.setdefault("status", "cached" if cached else "unchecked")
            self.catalog[source.url] = record

    def snapshot(self) -> dict:
        with self.lock:
            sources = feeds.read_sources(self.urls)
            occurrences: dict[str, list[int]] = {}
            for number, line in enumerate(self.urls.read_text().splitlines(), 1):
                parts = shlex.split(line, comments=True)
                if parts and feeds.safe_url(parts[0]):
                    occurrences.setdefault(parts[0], []).append(number)
            rows = []
            for source in sources:
                record = self.catalog.get(source.url, {})
                articles = record.get("articles", [])
                decision = self.decisions["feeds"].get(source.url, {})
                feed_title = record.get("feed_title", record.get("title", "")) or ""
                row = {
                    **record,
                    "url": source.url,
                    "slug": source.slug,
                    "title": source.title or feed_title or source.url,
                    "feed_title": feed_title,
                    "override": source.title,
                    "tags": source.tags,
                    "domain": urlsplit(source.url).hostname,
                    "lines": occurrences[source.url],
                    "decision": decision,
                    "checking": source.url in self.pending,
                    "articles": articles[:5],
                    "latest_timestamp": max((a.get("timestamp", 0) for a in articles), default=0),
                }
                rows.append(row)
            return {
                "feeds": rows,
                "pending": len(self.pending),
                "token": self.token,
                "categories": sorted({tag for source in sources for tag in source.tags}),
                "decisions_file": str(self.decisions_path),
                "scan_errors": self.scan_errors,
            }

    def save_decision(self, url: str, decision: dict) -> None:
        with self.lock:
            if url not in {source.url for source in feeds.read_sources(self.urls)}:
                raise ValueError("Feed is not in the current urls file")
            self.decisions["feeds"][url] = validate_decision(decision)
            save_json(self.decisions_path, self.decisions)

    def schedule(self, url: str | None = None) -> int:
        with self.lock:
            sources = feeds.read_sources(self.urls)
            if url is not None:
                sources = [source for source in sources if source.url == url]
                if not sources:
                    raise ValueError("Feed is not in the current urls file")
            count = 0
            for source in sources:
                if source.url not in self.pending:
                    self.pending.add(source.url)
                    self.pool.submit(self._check, source)
                    count += 1
            return count

    def _check(self, source: feeds.Source) -> None:
        try:
            result = feeds.fetch_feed(self.client, source, self.cache)
            with self.lock:
                previous = self.catalog.get(source.url, {})
                history = previous.get("history", [])
                history.append(
                    {
                        key: result.get(key)
                        for key in ("checked_at", "status", "http_status", "error")
                    }
                )
                # Preserve identity from an earlier success when this check fails.
                self.catalog[source.url] = {
                    **previous,
                    **result,
                    "history": history[-10:],
                    "articles": result.get("articles", [])[:5],
                }
                self.scan_errors.pop(source.url, None)
        except Exception as exc:
            with self.lock:
                self.scan_errors[source.url] = str(exc)
        finally:
            with self.lock:
                self.pending.discard(source.url)
                if len(self.pending) % 10 == 0:
                    save_json(self.state / "catalog.json", self.catalog)
                    print(f"{len(self.pending)} checks remaining", flush=True)

    def plan(self) -> dict:
        with self.lock:
            return plan_changes(self.urls.read_text(encoding="utf-8"), self.decisions["feeds"])

    def reject_duplicate(self, url: str, final_url: str = "") -> None:
        identities = {normalize_feed_url(value) for value in (url, final_url) if value}
        for source in feeds.read_sources(self.urls):
            known = [source.url, self.catalog.get(source.url, {}).get("final_url", "")]
            if any(normalize_feed_url(value) in identities for value in known if value):
                raise DuplicateFeed(source.url)

    def check_addition(self, value: str) -> dict:
        url = normalize_feed_url(value)
        with self.lock:
            self.reject_duplicate(url)
        if not self.add_checks.acquire(blocking=False):
            raise ValueError("Two new-feed checks are already running; try again shortly")
        try:
            result = feeds.fetch_feed(self.client, feeds.Source(url), self.cache)
        finally:
            self.add_checks.release()
        if result["status"] not in {"ok", "unchanged"}:
            raise ValueError("Could not validate a live RSS/Atom feed: " + result["error"])
        with self.lock:
            self.reject_duplicate(url, result.get("final_url", ""))
            self.add_candidates = {
                key: candidate
                for key, candidate in self.add_candidates.items()
                if candidate["expires"] > time.monotonic()
            }
            if len(self.add_candidates) >= 10:
                self.add_candidates.pop(next(iter(self.add_candidates)))
            token = secrets.token_urlsafe(24)
            self.add_candidates[token] = {
                "url": url,
                "result": result,
                "expires": time.monotonic() + 1800,
            }
            return {"check_token": token, "feed": {**result, "articles": result["articles"][:5]}}

    def addition_candidate(self, token: str) -> dict:
        candidate = self.add_candidates.get(token)
        if not candidate or candidate["expires"] <= time.monotonic():
            raise ValueError("This feed check expired. Check the feed again before adding it.")
        self.reject_duplicate(candidate["url"], candidate["result"].get("final_url", ""))
        return candidate

    def preview_addition(self, token: str, decision: dict) -> dict:
        with self.lock:
            candidate = self.addition_candidate(token)
            validated = validate_decision({**decision, "action": "keep"})
            plan = plan_addition(self.urls.read_text(encoding="utf-8"), candidate["url"], validated)
            candidate.update(decision=validated, plan=plan)
            return plan

    def add_feed(self, token: str, revision: str) -> dict:
        with self.lock:
            candidate = self.addition_candidate(token)
            if "plan" not in candidate:
                raise ValueError("Preview the addition before saving the feed")
            current = plan_addition(
                self.urls.read_text(encoding="utf-8"), candidate["url"], candidate["decision"]
            )
            if revision != current["revision"] or revision != candidate["plan"]["revision"]:
                raise ValueError("The file or feed details changed. Preview the addition again.")
            url = candidate["url"]
            # Save review metadata first; the urls file is the final commit of the addition.
            self.catalog[url] = {
                **candidate["result"],
                "articles": candidate["result"]["articles"][:5],
            }
            self.decisions["feeds"][url] = candidate["decision"]
            save_json(self.state / "catalog.json", self.catalog)
            save_json(self.decisions_path, self.decisions)
            result = self.write_urls(current)
            self.add_candidates.pop(token)
            return {**result, "url": url}

    def export_csv(self) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Display name",
                "Feed title",
                "URL",
                "Publisher/author",
                "Website",
                "Description",
                "Categories",
                "Fetch result",
                "HTTP status",
                "Last checked",
                "Last content fetch",
                "Latest article date",
                "Error",
                "Decision",
                "Notes",
                "Source lines",
            ]
        )
        for row in self.snapshot()["feeds"]:
            values = [
                row.get("title"),
                row.get("feed_title"),
                row["url"],
                row.get("publisher"),
                row.get("homepage"),
                row.get("description"),
                "; ".join(row["tags"]),
                row.get("status"),
                row.get("http_status"),
                row.get("checked_at"),
                row.get("fetched_at"),
                row["articles"][0].get("published") if row["articles"] else "",
                row.get("error"),
                row["decision"].get("action", "unreviewed"),
                row["decision"].get("notes"),
                ", ".join(map(str, row["lines"])),
            ]
            # Publisher text is untrusted input; keep spreadsheet formulas inert.
            safe = []
            for value in values:
                text = "" if value is None else str(value)
                safe.append("'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text)
            writer.writerow(safe)
        return output.getvalue().encode("utf-8-sig")

    def apply(self, revision: str) -> dict:
        with self.lock:
            plan = self.plan()
            if revision != plan["revision"]:
                raise ValueError("The file or decisions changed. Preview the changes again.")
            if not plan["changed_lines"]:
                return {"changed_lines": 0}
            return self.write_urls(plan)

    def write_urls(self, plan: dict) -> dict:
        backup = self.state / "backups" / f"urls-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        atomic_write(backup, self.urls.read_text(encoding="utf-8"))
        atomic_write(self.urls, plan["proposed"])
        return {"changed_lines": plan["changed_lines"], "backup": str(backup)}

    def close(self, cancel: bool = False):
        self.pool.shutdown(wait=True, cancel_futures=cancel)
        save_json(self.state / "catalog.json", self.catalog)
        self.client.close()
        self.guard.close()


def make_server(store: ReviewStore, port: int = 8082) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, value, content_type="application/json"):
            body = json.dumps(value).encode() if content_type == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"
            )
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") in {
                f"localhost:{self.server.server_port}",
                f"127.0.0.1:{self.server.server_port}",
            }

        def do_GET(self):
            if not self.valid_host():
                self.send(403, {"error": "Loopback host required"})
                return
            try:
                if self.path == "/api/feeds":
                    self.send(200, store.snapshot())
                elif self.path == "/api/plan":
                    self.send(200, store.plan())
                elif self.path == "/api/catalog.csv":
                    self.send(200, store.export_csv(), "text/csv; charset=utf-8")
                elif self.path == "/api/export":
                    with store.lock:
                        self.send(200, store.decisions)
                else:
                    paths = {
                        "/": ("templates/curate.html", "text/html; charset=utf-8"),
                        "/curate.js": ("assets/curate.js", "text/javascript; charset=utf-8"),
                        "/style.css": ("assets/style.css", "text/css; charset=utf-8"),
                        "/curate.css": ("assets/curate.css", "text/css; charset=utf-8"),
                    }
                    if self.path not in paths:
                        self.send(404, {"error": "Not found"})
                        return
                    path, content_type = paths[self.path]
                    self.send(200, (feeds.ROOT / path).read_bytes(), content_type)
            except (OSError, ValueError) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.valid_host() or self.headers.get("X-Review-Token") != store.token:
                self.send(403, {"error": "Reload the local dashboard before making changes"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < size <= 256000:
                    raise ValueError("Expected a JSON request smaller than 256 KB")
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError("Expected a JSON object")
                if self.path == "/api/decision":
                    store.save_decision(value["url"], value["decision"])
                    self.send(200, {"saved": True})
                elif self.path == "/api/check":
                    self.send(202, {"scheduled": store.schedule(value.get("url"))})
                elif self.path == "/api/apply":
                    self.send(200, store.apply(value["revision"]))
                elif self.path == "/api/add/check":
                    self.send(200, store.check_addition(value["url"]))
                elif self.path == "/api/add/preview":
                    self.send(200, store.preview_addition(value["check_token"], value["decision"]))
                elif self.path == "/api/add/save":
                    self.send(200, store.add_feed(value["check_token"], value["revision"]))
                else:
                    self.send(404, {"error": "Not found"})
            except DuplicateFeed as exc:
                self.send(409, {"error": str(exc), "duplicate_url": exc.url})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)})

        def log_message(self, *_args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["serve", "scan", "plan"], default="serve")
    parser.add_argument("--urls", type=Path, default=feeds.ROOT / "urls")
    parser.add_argument("--cache-dir", type=Path, default=feeds.ROOT / ".cache/feeds")
    parser.add_argument("--state-dir", type=Path, default=feeds.ROOT / ".cache/feed-review")
    parser.add_argument("--decisions", type=Path, default=feeds.ROOT / "feed-decisions.json")
    parser.add_argument("--report", type=Path, default=feeds.ROOT / "site/report.json")
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--workers", type=feeds.positive, default=8)
    parser.add_argument("--timeout", type=feeds.positive, default=15)
    args = parser.parse_args()
    try:
        store = ReviewStore(
            args.urls,
            args.cache_dir,
            args.state_dir,
            args.decisions,
            args.report,
            args.ca_bundle,
            args.workers,
            args.timeout,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        if args.command == "plan":
            print(store.plan()["diff"] or "No accepted changes to apply.")
        elif args.command == "scan":
            print(f"Checking {store.schedule()} feeds…", flush=True)
            store.pool.shutdown(wait=True)
            print(f"Catalog saved to {args.state_dir / 'catalog.json'}")
        else:
            server = make_server(store, args.port)
            print(f"Review feeds at http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    finally:
        store.close(cancel=True)


if __name__ == "__main__":
    main()
