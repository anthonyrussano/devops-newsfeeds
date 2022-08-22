FROM alpine:3.16
WORKDIR /root/.newsboat
COPY config /root/.newsboat
COPY urls /root/.newsboat
RUN sed -i -e 's/v[[:digit:]]\.[[:digit:]]/v3.1/g' /etc/apk/repositories
RUN apk update && apk add newsboat && rm -rf /var/cache/apk/*
ENTRYPOINT ["newsboat"]