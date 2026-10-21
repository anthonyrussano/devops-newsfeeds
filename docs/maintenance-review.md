# Maintenance review — 2026-10-20

## Direction

Keep Newsboat as the terminal reader and ttyd as the web terminal. This preserves
the keyboard workflow, query feeds, tagging, native OPML support, and article
browsing with elinks. A separate static reader is useful for sharing a snapshot
without running terminal sessions or a backend. All three read the same `urls`
collection. A database-backed replacement web application would add maintenance
and authentication work without being necessary for these uses.

## Findings addressed

| Area | Finding | Change |
| --- | --- | --- |
| Container base | CLI used Alpine 3.16; web inherited a floating upstream image | Both use Alpine 3.24 and distribution packages |
| TLS inspection | Package installation needed corporate trust before `apk` could connect | Shared bootstrap installs supplied CA roots before package downloads and updates runtime trust; verification remains enabled |
| CLI state | Old examples used root's cache path without persistence | Unprivileged UID 1000, named-volume example, explicit config/feed/cache paths |
| Web state | Normal connections shared one SQLite database; URL arguments were enabled | Server creates a separate temporary database and writable home per connection; URL arguments disabled |
| Web startup | A read-only filesystem prevented Newsboat from creating history/config directories | Session HOME and XDG directories now live in its temporary directory |
| Compose | Whole-directory mount hid image config and URLs | Bundled config stays in `/etc/newsboat`; Compose uses a read-only root and temporary `/tmp` |
| Feed fetching | Original generator fetched sequentially, lacked dependency metadata, and accepted HTML | `uv.lock`, bounded worker pool, HTTP status/format validation, response limit, system CA trust, explicit CA option |
| Feed cache | Every build downloaded everything; failed feeds disappeared | Conditional HTTP requests, cached parsed articles, stale fallback, offline mode, status report |
| Static rendering | Unescaped text/URLs and title-based filenames allowed unsafe content and collisions | Autoescaped templates, HTTP(S)-only links, URL-hash filenames |
| Static usability | Unstyled list of feed links | Responsive light/dark layouts, search, category filtering, latest stories, source pages |
| Collection curation | Opaque URLs and no source-review workflow | Local feed-review dashboard with publisher metadata, status history, article samples, categories, saved decisions, diff preview, backups, and CSV export |
| CI | Old actions, retired `set-output`, and CLI/web commit-tag collisions | Current action majors, `GITHUB_OUTPUT`, separate variant commit tags, checks before publishing |
| Validation | No automated tests | Python regression tests, live browser smoke test, CI container checks, dependency updates |

The previous local Netskope changes were preserved in purpose and extended into
the shared certificate bootstrap. The existing CA remains local and ignored by
Git. Public CI uses the certificate-free repository context. See
[Alpine's release policy](https://alpinelinux.org/releases/),
[uv's system certificate setting](https://docs.astral.sh/uv/reference/settings/#system-certs),
and [ttyd's documented server options](https://github.com/tsl0922/ttyd#usage).

## Initial local validation

- Both Linux amd64 images built locally using the supplied corporate root CA.
  Installed versions: Newsboat 2.43.0 and ttyd 1.7.7.
- CLI interactive startup loaded the full collection and rendered query feeds.
  A bounded live reload of the Airflow feed fetched 41 articles. A second container
  recovered the same 41 unread articles from the named volume.
- Compose started successfully with a read-only root, capability removal, and
  tmpfs. Container HTTP health checks passed.
- Real Chromium connections rendered Newsboat, created two separate session
  databases, accepted keyboard quit, and cleaned caches after quit/disconnect.
- Static pages passed browser checks for search, categories, multiword tags,
  feed links, and a 390-pixel mobile viewport. No JavaScript exceptions were seen.
- Python regression tests passed on Python 3.12, 3.13, and 3.14. Tests cover real
  local HTTP responses, conditional caching, stale/offline behavior, bad formats,
  duplicate titles/URLs, unsafe content, size limits, and failure exit behavior.
- Curation tests verify edit validation, preserved Newsboat syntax, category query
  creation, preview invalidation, backups, and local API write protection. A real
  browser test checks identity/status review, saved decisions, filters, and the
  complete preview/apply workflow against a temporary collection.
- The Add feed workflow validates RSS/Atom URLs, rejects canonical URL duplicates
  and known redirect aliases, previews the addition and category groups, and saves
  with a backup. Browser checks cover duplicate navigation, invalid HTML responses,
  cancellation, edited/stale previews, successful addition, and mobile layout.
- Ruff lint/format, Compose configuration validation, JavaScript syntax checking,
  actionlint, and whitespace checks passed.

This section records the local checks before publication. Local image validation
covers amd64. The publication workflow builds amd64/arm64 after CI passes; arm64
was not built locally. For subsequent checks and container publication, see the
[Checks workflow](https://github.com/anthonyrussano/devops-newsfeeds/actions/workflows/ci.yml).
The [static-site workflow](https://github.com/anthonyrussano/devops-newsfeeds/actions/workflows/static-site.yml)
creates downloadable artifacts; website hosting is separate.

## Feed collection check

The collection has 427 URL entries, 424 unique URLs, and 26 tags. On 2026-10-20,
a full build used 12 workers, five-second per-operation timeouts, and the local
corporate CA. It produced:

```bash
uv run --locked generate_static.py --timeout 5 --workers 12 \
  --ca-bundle certs/netskope-root-ca.crt
```

| Result | Count |
| --- | ---: |
| Successful fresh responses | 380 |
| Successful conditional HTTP 304 responses | 5 |
| Failed sources | 39 |
| Total unique feeds checked | 424 |

| Failure category | Count |
| --- | ---: |
| HTTP 404 | 12 |
| Response was not RSS/Atom | 11 |
| HTTP 403 | 8 |
| TLS failures | 3 |
| DNS failure | 1 |
| Timeout | 1 |
| Response larger than 5 MiB | 1 |
| HTTP 410 | 1 |
| HTTP 429 | 1 |

These are observations from this network and run. A 403, timeout, or certificate
failure does not establish that a feed is permanently unavailable. The check did
not establish article freshness or editorial quality. The local
`site/report.json` contains individual results and the
generated site includes failure pages. `site/` and `.cache/` are generated and
ignored by Git; recreate the report with the documented generator command.

Examples requiring attention include the retired Python Insider FeedBurner URL,
old Rancher category feeds returning HTML, old CISA endpoints returning 404/HTML,
and Data Engineering Podcast's `/feed/mp3/` returning 410. Planet SciPy returned a
hostname-mismatched certificate. Those TLS checks were not bypassed.

A later curator scan that day recorded 368 fresh responses, 19 conditional
responses, and 37 failures. It collected 377 declared titles, 122 descriptions,
and 265 declared authors. These are separate dated snapshots; recheck a source in
the dashboard before acting on a failure.

Three repeated URLs remain in `urls`: Marketplace Videos, Copyright Creativity,
and a CNBC feed labeled both EARNINGS and BUSINESS. The static generator merges
duplicates and uses the last explicit title override. CNBC's labels should be
reviewed against its current feed categories before choosing a replacement URL.

## Suggested next work

1. **Curate sources using repeated observations.** Check the failing URLs on a
   second network, discover replacements from publishers' own RSS links, and
   explicitly retire confirmed discontinued feeds. Keep uncertain sources until
   their failures can be distinguished from access/network restrictions.
2. **Choose the static publishing destination and cadence.** The manual workflow
   already builds a downloadable artifact. A daily GitHub Pages deployment is a
   small next step once the destination and public feed selection are decided.
3. **Choose web-state expectations.** Sessions are intentionally temporary. If
   persistent per-user read state is needed, put authentication in front and map
   verified identities to server-selected cache locations; do not re-enable
   client-supplied Newsboat command arguments.
4. **Consider a focused default collection.** University/YouTube sources account
   for much of this collection. A smaller DevOps subset would make initial web
   sessions faster. Preserve the full collection as an alternate input and retain
   the existing query groups for the CLI workflow.
5. **Improve collection portability.** Native `--export-to-opml2` already provides
   tagged OPML export. A future importer could make subscriptions easier to move
   between readers while preserving the Newsboat query groups separately.

## Current operating boundaries

Web session caches and read state disappear on disconnect. Session directories
are separate, but all browser processes use the same Unix account; this is not a
security boundary between untrusted terminal users. Compose is local-only by
default. Remote use needs an authenticated HTTPS entry point.

Static output is a snapshot and displays at most 300 home-page stories and 30
articles per source by default. Removed sources' old generated files are not
pruned automatically. Generate into a fresh output directory when retiring feeds.
Network timeouts apply per operation; an entire build can take several minutes.
