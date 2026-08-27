# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS backend-build
ARG VCPKG_COMMIT=127402f1c75bb3d5ff6bce04b285faa4930a5aca
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bison build-essential ca-certificates cmake curl flex git ninja-build pkg-config python3 tar unzip zip \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --filter=blob:none --no-checkout https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && git -C /opt/vcpkg checkout --detach "${VCPKG_COMMIT}" \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics
WORKDIR /src
COPY CMakeLists.txt vcpkg.json ./
COPY backend ./backend
RUN cmake -S . -B /build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake \
      -DBABEL_MIGRATION_DIRECTORY=/opt/babel/migrations \
      -DBABEL_ADMIN_ASSET_DIRECTORY=/opt/babel/admin \
    && cmake --build /build --target babel_backend_cli --parallel 2

FROM backend-build AS backend-test
RUN cmake -S . -B /build-test -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=ON \
      -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake \
    && cmake --build /build-test --parallel 2 \
    && ctest --test-dir /build-test --output-on-failure

FROM debian:bookworm-slim AS backend
ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin babel
COPY --from=backend-build /build/backend/babel_backend /usr/local/bin/babel_backend
COPY backend/migrations /opt/babel/migrations
COPY backend/admin /opt/babel/admin
USER 10001:10001
ENTRYPOINT ["babel_backend"]
CMD ["serve"]

FROM python:3.12-slim-bookworm AS online-runtime
ARG SOURCE_COMMIT=unknown
ARG UV_VERSION=0.12.3
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /var/lib/babel-online --create-home babel \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /opt/babel/online
COPY online/pyproject.toml online/uv.lock ./
COPY online/src ./src
RUN uv sync --frozen --no-dev \
      --extra pgvector --extra kafka --extra parquet --extra qwen \
    && chown -R 10001:10001 /opt/babel/online /var/lib/babel-online
ENV PATH="/opt/babel/online/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    BABEL_ONLINE_STATE_ROOT=/var/lib/babel-online/state \
    BABEL_ONLINE_MODEL_ARTIFACT_CACHE=/var/lib/babel-online/cache/model-artifact \
    BABEL_ONLINE_QWEN_CACHE=/var/lib/babel-online/cache/qwen-base
USER 10001:10001

FROM online-runtime AS serving
ENTRYPOINT ["babel-recommendation-server"]

FROM online-runtime AS trainer
ENTRYPOINT ["babel-online-trainer"]
