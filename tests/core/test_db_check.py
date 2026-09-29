import pymysql
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core import db_check


def driver_error(number: int, message: str) -> OperationalError:
    return OperationalError(
        "SELECT 1", {}, pymysql.err.OperationalError(number, message)
    )


@pytest.fixture
def in_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_check, "_in_container", lambda: True)


def hints_for(url: str, error: BaseException) -> str:
    return " ".join(db_check.explain(make_url(url), error))


def test_loopback_inside_container_points_to_socket_setup(
    in_container: None,
) -> None:
    hints = hints_for(
        "mysql+aiomysql://u:p@127.0.0.1:3306/db",
        driver_error(2003, "Can't connect"),
    )

    assert "container itself" in hints
    assert "docker-compose.mysql-socket.yml" in hints


def test_zero_address_is_explained() -> None:
    hints = hints_for(
        "mysql+aiomysql://u:p@0.0.0.0:3306/db",
        driver_error(2003, "Can't connect"),
    )

    assert "listen address" in hints


def test_host_docker_internal_points_to_bind_address() -> None:
    hints = hints_for(
        "mysql+aiomysql://u:p@host.docker.internal:3306/db",
        driver_error(2003, "Can't connect"),
    )

    assert "bind-address" in hints


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1045, "percent-encode"),
        (1130, "'172.%'"),
        (1049, "CREATE DATABASE db"),
    ],
)
def test_server_errors_are_explained(number: int, expected: str) -> None:
    hints = hints_for(
        "mysql+aiomysql://u:p@10.0.0.5:3306/db", driver_error(number, "x")
    )

    assert expected in hints


def test_missing_socket_is_reported_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+aiomysql://u:hunter2@localhost/db"
        "?unix_socket=/nonexistent/mysqld.sock",
    )
    db_check.get_settings.cache_clear()
    try:
        assert db_check.wait_for_database(wait_seconds=0) is False
    finally:
        db_check.get_settings.cache_clear()

    output = capsys.readouterr().err
    assert "socket /nonexistent/mysqld.sock" in output
    assert "Mount the host's MySQL socket" in output
    assert "hunter2" not in output


def test_success_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def reachable(_url: object) -> None:
        return None

    monkeypatch.setattr(db_check, "_ping", reachable)

    assert db_check.wait_for_database(wait_seconds=0) is True
    assert "is reachable" in capsys.readouterr().err
