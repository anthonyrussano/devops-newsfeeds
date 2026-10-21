# DevOps Newsfeeds

One feed collection, three ways to read it:

- **CLI:** Newsboat with persistent read state and keyboard navigation.
- **Web app:** the same Newsboat interface through ttyd, with a separate temporary session for each connection.
- **Static site:** searchable sources and recent articles, category filters, and individual feed pages. No application server or external JavaScript services are required after generation.

`urls` is the shared source of truth, using Newsboat's quoted title and tag syntax.
`archive` is an inactive collection and is not fetched. Container builds use Alpine
3.24 packages for Newsboat, elinks, and ttyd. Python work uses `uv` and a committed lockfile.

## Prerequisites

Use Docker with the Compose plugin for the CLI and web containers. For the static
generator and review dashboard, install [uv](https://docs.astral.sh/uv/getting-started/installation/)
0.11 or newer, then run `uv sync --locked`. The project selects Python 3.12;
Python 3.13 and 3.14 are also tested. Add corporate CA certificates as described
below if your network inspects HTTPS traffic.

## CLI Version

Build and run locally:

```bash
docker build -f Dockerfile-cli -t devops-newsfeeds:cli .
docker run --rm -it \
  --mount source=newsboat-cache,target=/home/worker/.newsboat/cache \
  devops-newsfeeds:cli
```

The named volume preserves articles and read state across container runs. Both images
run as the unprivileged `worker` user (UID 1000). The old `/root/.newsboat` path no
longer applies. For existing caches, copy `cache.db` into the new volume and make
it writable by UID 1000 before use; keep your original cache as a backup.

Useful keys: `Enter` opens a feed/article, `q` goes back or quits, `r` refreshes a
feed, `R` refreshes all feeds, `/` searches, `T` selects a tag, and `?` shows help.
The configured browser is elinks, so opening article links stays in the terminal.
Newsboat checks feeds automatically every 30 minutes with eight reload workers.

```bash
# Export the collection using Newsboat's native OPML support.
docker run --rm devops-newsfeeds:cli --export-to-opml2 > feeds.opml
```

## Web Version

Build and start the web app:

```bash
docker compose up --build -d --wait
# Open http://localhost:8080
docker compose logs -f
# Stop when finished:
docker compose down
```

Each browser connection gets its own database and writable home under `/tmp`.
Read state lasts for that connection and is removed when it exits or disconnects.
There is no login or persistent user identity. URL `?arg=` parameters are disabled;
unique cache filenames are no longer needed. The config and feed list live at
`/etc/newsboat`, so cache mounts cannot hide them.

Compose binds to `127.0.0.1:8080`, drops capabilities, and uses a read-only root
filesystem with a temporary writable `/tmp`. This is an interactive terminal app;
for remote access, put it behind an authenticated HTTPS proxy. ttyd checks
WebSocket origins. Session cache isolation separates reading state; it is not
isolation between mutually untrusted users inside the container.

To customize feeds or configuration without rebuilding, add read-only bind mounts
for `./urls:/etc/newsboat/urls:ro` and `./config:/etc/newsboat/config:ro` in a Compose
override. Do not mount your entire host `~/.newsboat` directory over `/etc/newsboat`.

After the dashboard atomically replaces a bind-mounted `urls` file, recreate the
web container so its mount points to the new file:

```bash
docker compose up -d --no-build --force-recreate
```

Start a new CLI container to load the updated collection. Without bind mounts,
rebuild the images to include the updated `urls` file.

## Review and curate the collection

Start the local feed review dashboard:

```bash
uv run --locked curate_feeds.py
# Open http://localhost:8082
```

If your corporate CA is not in system trust, supply your local certificate:

```bash
uv run --locked curate_feeds.py --ca-bundle certs/netskope-root-ca.crt
```

That certificate is not distributed with the repository. The dashboard starts
using the existing feed cache and status report; it does not
contact every publisher until you choose **Check all feeds**. **Check this feed
now** refreshes just the selected source. Checks run in the background while you
review. A full refresh can take several minutes on this collection.

For each source you can see:

- The feed's declared title, your current display-name override, publisher/author
  when supplied, website, description, and original URL.
- Existing categories, line numbers, and repeated URL occurrences.
- Last check time, HTTP status, redirects, response type, errors, and recent check
  history, along with the last successful content fetch.
- Recent article titles and the latest publication date. Availability and activity
  are separate: a feed can be reachable while its content is over 180 days old.

To add a source, choose **Add feed**, paste an RSS/Atom URL, and select **Check
feed**. The dashboard validates the feed, shows its identity and sample articles,
and detects duplicates, including known redirect aliases. Common equivalent URL
spellings (host casing, default ports, fragments) are treated as duplicates.
For a duplicate, **Open existing feed** takes you to its review.

Choose the display name, comma-separated categories, and optional notes, then
select **Preview addition**. **Save feed to urls** writes only that addition,
creates a backup, and immediately selects the new source in the dashboard.
New categories get Newsboat query groups. Untagged feeds remain visible as
individual Newsboat sources. Checking, previewing, or closing the form does not
add the feed. Changed details require another preview; checks expire after 30
minutes. Existing staged reviews remain available for separate application.

Search by name, URL, description, or publisher; filter by category, fetch health,
review decision, unknown identity, duplicate URL, or publication activity. **Next
unreviewed** advances through sources matching your current filters.

1. Inspect the source, its website, and sample articles; recheck if needed.
2. Edit its display name and comma-separated categories, and add a review note.
3. Choose **Keep with these names / tags**, **Investigate**, or **Retire**. These
   save decisions in `feed-decisions.json`, independently of `urls`.
4. Choose **Preview urls changes** to review a unified diff. Only Keep and Retire
   decisions are applied. Retired sources are commented out, and accepted names
   become Newsboat `~title` overrides. Existing comments, hidden-feed markers, and
   query groups are preserved. Newly accepted categories get query groups so they
   appear in Newsboat.
5. Choose **Apply this preview to urls**. A dated backup is saved under
   `.cache/feed-review/backups/`. A changed file or changed decision invalidates an
   older preview. Regenerate the static site afterward. Rebuild the reader images,
   or recreate the containers if using the documented read-only feed-list mounts.

**Export catalog CSV** downloads all sources with their identities, categories,
check results, publication dates, and review notes for spreadsheet review. CSV
edits are not imported; use the dashboard to save decisions.
**Export decisions** downloads a JSON copy of your reviews. The saved decisions
can be committed alongside `urls` if desired. Generated metadata and the last ten
checks per feed live in `.cache/feed-review/catalog.json`; the article cache is
shared with the static generator. Failed checks retain previously identified
names and cached articles. A failed fetch is evidence about this network check,
not an automatic retirement decision. If a publisher supplies no author or feed
description, those fields remain unknown.

For a refresh without opening the browser, stop the dashboard and run:

```bash
uv run --locked curate_feeds.py scan
uv run --locked curate_feeds.py plan
```

Use `--workers` and `--timeout` to tune a scan. The defaults are eight workers and
15 seconds per network operation. A faster first pass can use
`--workers 12 --timeout 5`; recheck timeouts individually before deciding a source
is unavailable. The scan command also accepts `--ca-bundle` when needed.

One curator process can use a state directory at a time. Multiple tabs can use the
same dashboard. The service binds to loopback and requires its per-session token
for writes. It serves only the review UI and its APIs, not repository files.

## Static site

Read the published snapshot at
[DevOps Newsfeeds](https://anthonyrussano.github.io/devops-newsfeeds/).
GitHub Pages refreshes after pushes to `main`, daily at 06:23 UTC, or when
**Publish static site** is run manually in Actions. Scheduled runs can be delayed
by GitHub. The home page shows when the snapshot was generated and how many feeds
refreshed, used cached content, or failed; individual source pages explain failures.
The full machine-readable results are available in
[`report.json`](https://anthonyrussano.github.io/devops-newsfeeds/report.json).

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (0.11 or newer),
then build a small preview:

```bash
uv sync --locked
uv run --locked generate_static.py --max-feeds 12
uv run --locked python -m http.server 8081 --bind 127.0.0.1 --directory site
# Open http://localhost:8081
```

Build the full collection or rebuild without making network requests:

```bash
uv run --locked generate_static.py
uv run --locked generate_static.py --offline
```

HTML, CSS, JavaScript, and `report.json` are written to `site/`. Upload the directory
to any static web host, including a subdirectory. Links are relative. You can also
open `site/index.html` directly. Search and category filters cover the generated
stories and source list; each source page contains its own articles.

The generator deduplicates URLs while merging tags, respects Newsboat `~title`
overrides, uses URL hashes for stable filenames, escapes feed text, and permits
only HTTP(S) article links. It fetches eight feeds concurrently, limits each
response to 5 MiB, sorts articles by date, and stores conditional-request metadata
and parsed articles in `.cache/feeds/`. Feeds returning HTML are reported as failed.
Successful requests can reuse HTTP 304 responses. A failed refresh retains saved
articles and marks that source stale. A build with no usable feeds exits with code
1 and leaves the existing site untouched. Partial builds succeed by default;
`--strict` returns code 1 when any source is failed or stale, after writing the report.

| Option | Default | Purpose |
| --- | --- | --- |
| `--urls PATH` | repository `urls` | Alternate Newsboat source file |
| `--output PATH` | `site` | Output directory |
| `--cache-dir PATH` | `.cache/feeds` | Saved feeds and HTTP validators |
| `--max-feeds N` | `0` (all) | Attempt only the first N unique feeds |
| `--workers N` | `8` | Concurrent requests |
| `--timeout N` | `15` | Connect/read timeout in seconds, per network operation |
| `--max-entries N` | `30` | Articles per feed page |
| `--latest-limit N` | `300` | Stories on the home page |
| `--ca-bundle PATH` | system trust | Add a PEM corporate CA or bundle |
| `--offline` | off | Read cached feeds without refreshing |
| `--strict` | off | Fail the build on failed or stale sources |

The older `MAX_FEEDS` environment variable is still supported. Static pages are a
snapshot; rerun the generator to refresh them. Browser read state is not shared
between the CLI, web sessions, and static site. Old generated feed pages are not
automatically removed; use a fresh output directory when retiring sources.

## Corporate certificates / Netskope

Place PEM-encoded CA certificates in `certs/` with a `.crt` extension **before**
building either container. The build adds them to the initial trust bundle before
`apk` connects, then updates runtime trust for Newsboat and elinks. TLS verification
stays enabled. Certificates are ignored by Git; see [certs/README.md](certs/README.md).
Local images contain these trusted certificates, so use certificate-free builds
for public image distribution.

`uv` uses the system certificate store through `[tool.uv] system-certs = true`.
The static generator also uses system trust. If your CA is not installed there:

```bash
uv run --locked generate_static.py --ca-bundle certs/netskope-root-ca.crt
```

If `uv sync` itself fails TLS verification, install your corporate CA in your host
trust store or set `SSL_CERT_FILE` to an approved complete CA bundle. The generator's
`--ca-bundle` adds certificates to system trust; `SSL_CERT_FILE` selects the base
bundle. Container certificate files do not fix Docker daemon trust errors while
pulling the base image; configure Docker Desktop/daemon trust separately for those.

## Maintenance and automation

```bash
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked pytest -q
docker compose config --quiet
```

With the web app running, the optional browser check exercises real terminal
connections, independent caches, cleanup, static search, category filters, and
mobile layout using a local feed fixture:

```bash
uv sync --locked --group browser
uv run --locked --group browser playwright install chromium
uv run --locked --group browser python scripts/browser_smoke.py
uv run --locked --group browser python scripts/curation_smoke.py
```

Screenshots are saved to `.cache/browser/`. The web container CI job runs this
check and retains screenshots as an artifact.

On Linux, use `playwright install --with-deps chromium` if browser system
libraries are missing. The curation browser check uses temporary files and does
not change your real collection.

GitHub Actions runs Python checks on 3.12, 3.13, and 3.14 and builds/smoke-tests both
containers on pull requests and pushes to `main`. After those checks pass on `main`,
it publishes `:cli`, `:web`, `:cli-<commit>`, and `:web-<commit>` tags using the
`DOCKER_USERNAME` and `DOCKER_PASSWORD` repository secrets. Use a Docker Hub access
token for `DOCKER_PASSWORD`. The previous shared commit tag is replaced by variant
tags. Actions are pinned to verified release commits; Dependabot proposes updates.
The publishing workflow builds both Linux amd64 and arm64 images after checks pass.

See [Checks](https://github.com/anthonyrussano/devops-newsfeeds/actions/workflows/ci.yml)
for test and container-publication results, and
[Publish static site](https://github.com/anthonyrussano/devops-newsfeeds/actions/workflows/static-site.yml)
for GitHub Pages deployment results.

The **Publish static site** workflow restores the previous feed cache, fetches the
collection, and deploys `site/` to GitHub Pages. Its `github-pages` artifact is the
transfer between build and deployment; readers use the public site URL. Deployment
is restricted to `main`. To enable this in a fork, choose **Settings → Pages →
Build and deployment → Source → GitHub Actions**, then run the workflow on `main`.
The deploy job requires `pages: write` and `id-token: write` and uses the
`github-pages` environment.

Feed availability depends on the network doing the fetch. GitHub-hosted runners
can fail on feeds that work locally, especially YouTube sources. Partial builds
publish with visible status counts; cached articles survive failed refreshes.
If every source fails and no cached feed is available, the build fails and the
previous deployment stays live. Local builds can use your system/corporate trust
configuration and may have better coverage. Neither workflow publishes corporate
CA files or the local curation dashboard.

Dependabot checks Python, Docker, and GitHub Actions dependencies weekly.

See [the maintenance review](docs/maintenance-review.md) for findings, validation,
and suggested next steps.
