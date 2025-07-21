# DevOps Newsfeeds

This repository stores Newsboat configuration and a large collection of RSS feed URLs.

## CLI Version

Run Newsboat in a container:

```bash
docker run -it anthonyrussano/devops-newsfeeds:cli -c=/root/.newsboat/cache/cache.db
```

## Web Version

Run the ttyd-based web interface:

```bash
docker run -p 8080:7681 --rm anthonyrussano/devops-newsfeeds:web
```

Each user should connect with a unique cache file name parameter in the URL:

```bash
http://localhost:8080/?arg=-c=/root/.newsboat/cache/unique_cache_name.db
```

## Static website generator

A small script `generate_static.py` can build a static website containing the latest entries from all feeds listed in `urls`.
Install dependencies and run:

```bash
pip install feedparser
python generate_static.py
```

The generated HTML files will appear in the `site/` directory.
