# newsfeed

## Background

Newsboat is an RSS/Atom feed reader for the text console.

`newsfeed` is a docker image that runs `newsboat` with a curated list of RSS feeds and a config file with custom settings.

## Usage

This image is currently built to run in interactive mode.

Mounting a volume is not necessary but it enables you to maintain a cache of the articles you have already downloaded.

To run with a named volume (copy mode enabled by default):

`$ docker run -it --rm -v newsfeed:/root/.newsboat anthonyrussano/newsfeed:latest`

To run without a volume (your cache will not be saved):

`$ docker run -it --rm anthonyrussano/newsfeed:latest`

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

Expected Output:

```
{
   "schemaVersion": 2,
   "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
   "manifests": [
      {
         "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
         "size": 1568,
         "digest": "sha256:e028aaefe02ffe38f8593fae2fa1dd0c6a656a8710ee1637b97f22171333af65",
         "platform": {
            "architecture": "amd64",
            "os": "linux"
         }
      },
      {
         "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
         "size": 1568,
         "digest": "sha256:590e8ea85b5cf239acf90934dbba51094573cd1d08b7a56f0c9a3ec04d9323c2",
         "platform": {
            "architecture": "arm64",
            "os": "linux",
            "variant": "v8"
         }
      }
   ]
}
```