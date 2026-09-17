FROM node:24.21.0-bookworm-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS vendor
WORKDIR /vendor
COPY package.json package-lock.json ./
COPY scripts/vendor.mjs scripts/vendor.mjs
COPY scripts/editor-vendor.js scripts/editor-vendor.js
RUN npm ci --omit=dev && npm run vendor

FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=false DJANGO_SETTINGS_MODULE=website.production_settings MEDIA_ROOT=/app/media
WORKDIR /app
ARG STUDY_RELEASE=unknown
LABEL org.opencontainers.image.revision=$STUDY_RELEASE
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY manage.py ./
COPY website/ website/
COPY study/ study/
COPY templates/account/ templates/account/
COPY js/config.js js/website-state.js js/rendering.js js/level-circles.js js/animation.js js/website-graph.js js/
COPY --from=vendor /vendor/study/static/vendor/ study/static/vendor/
COPY deploy/entrypoint.sh deploy/recovery.py deploy/
RUN DJANGO_SETTINGS_MODULE=website.build_settings python manage.py collectstatic --noinput \
    && groupadd --gid 10001 study && useradd --uid 10001 --gid 10001 --no-create-home study \
    && mkdir /app/media && chown 10001:10001 /app/media \
    && chmod -R a-w /app/staticfiles && chmod +x /app/deploy/entrypoint.sh
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "website.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "60"]
