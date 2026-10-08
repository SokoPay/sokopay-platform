# SokoPay backend image: one image, three ECS services (api / worker / beat differ by command).
#
#   docker build -t sokopay-backend .
#
# Multi-stage: dependencies are built in a throwaway stage so the runtime image carries no
# compilers. Runs as a non-root user. Static files are collected at build time (WhiteNoise).

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels
COPY --chown=app:app . .
# collectstatic needs settings to import; these build-only values are never used at runtime.
RUN DJANGO_SETTINGS_MODULE=config.settings.dev DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    SECRET_KEY=build-only-not-used-at-runtime DEBUG=False \
    python manage.py collectstatic --noinput && rm -f /tmp/build.sqlite3
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "45"]
