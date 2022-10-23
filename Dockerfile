FROM tsl0922/ttyd:alpine
WORKDIR /root/.newsboat
COPY config /root/.newsboat
COPY urls /root/.newsboat
RUN apk add newsboat
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["ttyd", "newsboat"]