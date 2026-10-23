FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 runline \
    && useradd --uid 10001 --gid runline --home-dir /app --no-create-home --shell /usr/sbin/nologin runline

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=runline:runline manage.py ./
COPY --chown=runline:runline runlive ./runlive
COPY --chown=runline:runline tracking ./tracking
COPY deploy/entrypoint.sh /usr/local/bin/runline-entrypoint
RUN chmod 755 /usr/local/bin/runline-entrypoint \
    && mkdir -p /app/staticfiles /data \
    && chown -R runline:runline /app /data

USER runline

EXPOSE 8000

ENTRYPOINT ["runline-entrypoint"]
CMD ["gunicorn", "runlive.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "60", \
     "--error-logfile", "-"]
