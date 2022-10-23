```
docker run -d -p 8080:7681 --rm -v newsfeed:/root/.newsboat anthonyrussano/devops-newsfeeds:latest

OR

docker compose up -d
```

Example docker-compose.yml:
```
---
version: "3.5"
services:
  devops-newsfeed:
    image: anthonyrussano/devops-newsfeeds
    volumes:
      - ~/.newsfeed:/root/.newsfeed
    ports:
      - 8080:7681
```
