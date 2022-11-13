

## CLI Version

`docker run -it anthonyrussano/devops-newsfeeds:cli -c=/root/.newsboat/cache/cache.db`

## Web Version

`docker run -p 8080:7681 --rm anthonyrussano/devops-newsfeeds:web`

Each user should connect with a unique cache file name parameter in the url.

`http://localhost:8080/?arg=-c=/root/.newsboat/cache/unique_cache_name.db`


