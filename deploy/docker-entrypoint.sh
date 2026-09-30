#!/bin/sh
# Container entrypoint for the integration service.
#
#   serve    Run the API with uvicorn (default).
#   migrate  Wait for the database (explaining any failure in plain words),
#            apply Alembic migrations, then exit.
#   worker   Run the background worker: webhook delivery, session expiry,
#            and deletion of expired session results. Run exactly one.
#   *        Run the given command as-is (for example `sh` when debugging).
#
# Settings for `serve`:
#   PORT             Port to listen on inside the container (default 8030).
#   ROOT_PATH        Public path prefix nginx strips, e.g. /integration, so
#                    generated URLs and the docs pages include it.
#   WEB_CONCURRENCY  Number of uvicorn worker processes (default 2).
#   FORWARDED_ALLOW_IPS
#                    Proxies trusted for X-Forwarded-* headers. The compose
#                    file publishes the port on 127.0.0.1 only, so the only
#                    caller is nginx on the host; "*" is safe there.
#
# Settings for `migrate`:
#   DB_WAIT_SECONDS  How long to keep retrying the database (default 30).
set -eu

case "${1:-serve}" in
    serve)
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port "${PORT:-8030}" \
            --root-path "${ROOT_PATH:-}" \
            --workers "${WEB_CONCURRENCY:-2}" \
            --proxy-headers \
            --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
            --no-server-header \
            --timeout-graceful-shutdown 20
        ;;
    migrate)
        python -m app.core.db_check
        exec alembic upgrade head
        ;;
    worker)
        exec python -m app.worker
        ;;
    *)
        exec "$@"
        ;;
esac
