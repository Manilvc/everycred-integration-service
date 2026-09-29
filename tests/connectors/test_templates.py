import pytest

from app.connectors.base import MissingParametersError
from app.connectors.http.templates import (
    TemplateSyntaxError,
    find_placeholders,
    render,
)

VALUES = {
    "kwargs": {"id_number": "ABCDE1234F", "count": 3, "flag": False},
    "args": ["first", 2],
    "credentials": {"api_token": "tok"},
    "settings": {"region": "in"},
    "user_uuid": "u-1",
    "client_id": "c-1",
}


def test_whole_placeholder_keeps_value_type() -> None:
    rendered = render(
        {"n": "{kwargs.count}", "f": "{kwargs.flag}", "a": "{args.1}"}, VALUES
    )

    assert rendered == {"n": 3, "f": False, "a": 2}


def test_embedded_placeholder_becomes_text() -> None:
    assert render("Bearer {credentials.api_token}", VALUES) == "Bearer tok"
    assert render("/users/{user_uuid}/x", VALUES) == "/users/u-1/x"


def test_nested_structures_are_rendered() -> None:
    template = {"outer": [{"id": "{kwargs.id_number}"}, "{settings.region}"]}

    assert render(template, VALUES) == {"outer": [{"id": "ABCDE1234F"}, "in"]}


def test_all_missing_values_are_reported_together() -> None:
    with pytest.raises(MissingParametersError) as error:
        render(
            {
                "a": "{kwargs.dob}",
                "b": "{credentials.customer_id}",
                "c": "{args.5}",
            },
            VALUES,
        )

    assert error.value.missing == [
        "args.5",
        "credentials.customer_id",
        "kwargs.dob",
    ]


def test_non_string_values_pass_through() -> None:
    assert render({"n": 1, "b": True, "x": None}, VALUES) == {
        "n": 1,
        "b": True,
        "x": None,
    }


def test_find_placeholders_walks_everything() -> None:
    found = find_placeholders(
        {"h": "Bearer {credentials.api_token}", "b": ["{kwargs.id}"]}
    )

    assert found == {"credentials.api_token", "kwargs.id"}


@pytest.mark.parametrize(
    "template", ["{env.PATH}", "{kwargs}", "{user_uuid.x}", "{__class__}"]
)
def test_unknown_placeholder_forms_are_rejected(template: str) -> None:
    with pytest.raises(TemplateSyntaxError):
        find_placeholders(template)


def test_text_without_placeholders_is_unchanged() -> None:
    assert render("consent=Y {not a placeholder", VALUES) == (
        "consent=Y {not a placeholder"
    )
