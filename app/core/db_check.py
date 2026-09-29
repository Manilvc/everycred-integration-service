"""Wait for the database, then explain any failure in plain words.

Run before migrations (``python -m app.core.db_check``). It retries for a
while, because MySQL may still be starting when the containers come up
after a reboot, and on failure prints what went wrong and how to fix it
instead of a driver traceback. The password is never printed.

Exit code 0 means the database answered; 1 means it did not.
"""

import asyncio
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

DEFAULT_WAIT_SECONDS = 30
RETRY_DELAY_SECONDS = 2
CONNECT_TIMEOUT_SECONDS = 5
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# MySQL client and server error numbers.
_CANNOT_CONNECT = {2002, 2003, 2005, 2006, 2013}
_ACCESS_DENIED = 1045
_HOST_NOT_ALLOWED = 1130
_UNKNOWN_DATABASE = 1049


def _in_container() -> bool:
    return os.path.exists("/.dockerenv")


def _error_number(error: BaseException) -> int | None:
    original = getattr(error, "orig", None) or error
    arguments = getattr(original, "args", ())
    if arguments and isinstance(arguments[0], int):
        return arguments[0]
    return None


def _target(url: URL) -> str:
    socket_path = url.query.get("unix_socket")
    if socket_path:
        return f"socket {socket_path}"
    return f"{url.host}:{url.port or 3306}"


def explain(url: URL, error: BaseException) -> list[str]:
    """Return likely causes and fixes for a failed connection."""
    number = _error_number(error)
    socket_path = url.query.get("unix_socket")
    host = url.host or ""
    in_container = _in_container()
    hints: list[str] = []

    if socket_path and not os.path.exists(str(socket_path)):
        hints.append(
            f"The socket {socket_path} does not exist here. Mount the "
            "host's MySQL socket directory into the container (see "
            "docker-compose.mysql-socket.yml), and check the path with "
            '`mysql -e "SELECT @@socket"` on the server.'
        )
    elif number in _CANNOT_CONNECT:
        if host == "0.0.0.0":  # noqa: S104 - checking, not binding
            hints.append(
                "0.0.0.0 is a listen address for the MySQL server, not an "
                "address to connect to. Use the socket setup or a real "
                "host name."
            )
        elif host in _LOOPBACK_HOSTS and in_container and not socket_path:
            hints.append(
                f"Inside a container, {host} is the container itself, not "
                "the server. For MySQL on the same server, use its socket: "
                "run with docker-compose.mysql-socket.yml and set "
                "DATABASE_URL=mysql+aiomysql://<user>:<password>@localhost"
                "/<database>?unix_socket=/var/run/mysqld/mysqld.sock"
                "&charset=utf8mb4"
            )
        elif host == "host.docker.internal":
            hints.append(
                "The server's MySQL is not accepting connections from "
                "Docker: it likely listens on 127.0.0.1 only "
                "(bind-address), or a firewall blocks port 3306. The "
                "socket setup avoids both."
            )
        else:
            hints.append(
                "Nothing answered. Check that MySQL is running, and the "
                "host and port in DATABASE_URL."
            )
    elif number == _ACCESS_DENIED:
        hints.append(
            "MySQL rejected the user or password. Check DATABASE_URL, and "
            "percent-encode special characters in the password "
            "(@ -> %40, : -> %3A, # -> %23). Over the socket, the account "
            "must exist for 'localhost'."
        )
    elif number == _HOST_NOT_ALLOWED:
        hints.append(
            "The MySQL user may not connect from this address. Create it "
            "for the Docker network ('<user>'@'172.%'), or use the socket "
            "setup, where the account for 'localhost' applies."
        )
    elif number == _UNKNOWN_DATABASE:
        hints.append(
            f"The database '{url.database}' does not exist. Create it: "
            f"CREATE DATABASE {url.database} CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_unicode_ci;"
        )
    if not hints:
        hints.append("See the driver error above and docs/deployment.md.")
    return hints


async def _ping(url: URL) -> None:
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


class _SocketMissingError(OSError):
    """The configured MySQL socket file does not exist (yet)."""


def _check_socket(url: URL) -> None:
    # MySQL creates the socket when it starts, so its absence is retried
    # like any other connection failure. Checking first also gives a clear
    # message instead of a driver error about the missing file.
    socket_path = url.query.get("unix_socket")
    if socket_path and not os.path.exists(str(socket_path)):
        raise _SocketMissingError(f"socket {socket_path} not found")


def wait_for_database(wait_seconds: float) -> bool:
    """Try to connect until success or ``wait_seconds`` pass."""
    url = make_url(get_settings().database_url.get_secret_value())
    target = _target(url)
    deadline = time.monotonic() + wait_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            _check_socket(url)
            asyncio.run(_ping(url))
        except (DBAPIError, OSError) as exc:
            if time.monotonic() < deadline:
                print(
                    f"Waiting for database at {target} (attempt {attempt})...",
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            error_text = str(getattr(exc, "orig", exc))
            print(
                f"\nCannot reach the database at {target} "
                f"({url.render_as_string(hide_password=True)}):\n"
                f"  {error_text}\n",
                file=sys.stderr,
            )
            for hint in explain(url, exc):
                print(f"  -> {hint}", file=sys.stderr)
            return False
        print(f"Database at {target} is reachable.", file=sys.stderr)
        return True


def main() -> int:
    """Entry point for ``python -m app.core.db_check``."""
    wait_seconds = float(
        os.environ.get("DB_WAIT_SECONDS", DEFAULT_WAIT_SECONDS)
    )
    return 0 if wait_for_database(wait_seconds) else 1


if __name__ == "__main__":
    sys.exit(main())
