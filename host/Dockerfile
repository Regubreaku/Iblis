# syntax=docker/dockerfile:1
# Supply an audited Debian bookworm image by digest. There is intentionally no default.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends python3 wine wine64 wine32:i386 && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp WINEPREFIX=/tmp/wine WINEDEBUG=-all
USER 65532:65532
WORKDIR /work
# No compiler, game, private repository or credentials are copied into this image.
ENTRYPOINT ["python3", "/input/runner_entry.py"]
