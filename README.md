```
docker run -p 8080:7681 --rm -v /home/$USER/newsfeed:/root/.newsboat/cache anthonyrussano/devops-newsfeeds:web

docker run -it -v /home/$USER/newsfeed:/root/.newsboat/cache anthonyrussano/devops-newsfeeds:cli
```
