# newsfeed

## Usage

If you have Newsboat installed, you can run the shell script `legacy_launcher` to run the app.

To install newsboat, you can use `sudo apt install newsboat`, or `sudo snap install newsboat`.

To run this project using docker:

`docker run -it --rm -v newsfeed:/root/.newsboat anthonyrussano/newsfeed:latest`

## Docker Configuration

These are the steps I took to build the image.

### Example Dockerfile

```
FROM alpine:3.16
WORKDIR /root/.newsboat
COPY config /root/.newsboat
COPY urls /root/.newsboat
RUN sed -i -e 's/v[[:digit:]]\.[[:digit:]]/v3.1/g' /etc/apk/repositories
RUN apk update && apk add newsboat && rm -rf /var/cache/apk/*
ENTRYPOINT ["newsboat"]
```

### Build, Test, and Push the Images

- Login to your `amd64` system.
- Checkout the repo.
  - `git clone git@gitlab.com:tonycalifornia/newsfeed.git`
  - `cd` into the project directory
- Build the image and tag it
  - `docker build . -t anthonyrussano/newsfeed:amd64`
- Test the image
  - `$ docker run -it --rm anthonyrussano/newsfeed:amd64`
- Push the image
  - `$ docker push anthonyrussano/newsfeed:amd64`

Repeat these same steps for building the `arm64` image.

### Create the local manifest list for annotating and pushing to a registry

To specify multiple system architectures for our image we must create and push a manifest list. A manifest list is a list of image layers that is created by specifying more than one image.

After you have built each image (above) and pushed to hub.docker.com, follow these steps to create the manifest.

`$ docker manifest create anthonyrussano/newsfeed:latest anthonyrussano/newsfeed:amd64 anthonyrussano/newsfeed:arm64`

### Push the manifest list to a docker hub repository

`$ docker manifest push --purge anthonyrussano/newsfeed:latest`

### Verify the image manifest

`$ docker manifest inspect anthonyrussano/home-site:latest`
