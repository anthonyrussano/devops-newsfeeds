## CLI Version

`docker run -it -v /home/$USER/newsfeed:/root/.newsboat/cache anthonyrussano/devops-newsfeeds:cli`

## Web Version

`docker run -p 8080:7681 --rm anthonyrussano/devops-newsfeeds:web`

Each user must connect with a unique cache name in the url.

`http://localhost:8080/?arg=-c=unique_cache_name.db`
