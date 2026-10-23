#!/bin/sh
set -eu

# SQLite accepts one writer, so the deployment runs one app container.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
