# Client layer: pgbench from the SAME postgres image as the server (matching major
# version) plus the Python runner (locked dependencies, Python 3.12 via uv).
ARG POSTGRES_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${POSTGRES_IMAGE}
COPY --from=uv /uv /uvx /usr/local/bin/
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates procps zstd; \
    rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
RUN uv python install 3.12 && uv sync --locked --no-install-project --no-dev \
    && chmod -R a+rX /opt/python /opt/venv
# The repo is bind-mounted at /work at runtime; run the package from there so the
# locked config, workloads and results are the same files the host sees.
ENV PYTHONPATH=/work PATH=/opt/venv/bin:$PATH PLAYGROUND_IN_CONTAINER=1
RUN printf '#!/bin/sh\ncd /work && exec /opt/venv/bin/python -m playground.cli "$@"\n' \
      > /usr/local/bin/playground && chmod +x /usr/local/bin/playground
WORKDIR /work
CMD ["sleep", "infinity"]
