import copy
from typing import Any

from httpx import AsyncClient

from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.sessions.conftest import (
    AADHAAR,
    OTP,
    TOOL_DEFINITION,
    start,
)

TOOL_URL = "/v1/integration-tools/acme-identity"
ADMIN_FIELDS_URL = f"{TOOL_URL}/fields"
CLIENT_FIELDS_URL = "/v1/client/integration-tools/acme-identity/fields"

GLOBAL_FIELDS = [
    {"flow": "aadhaar_otp", "key": "full_name", "label": "Full name"},
    {"flow": "aadhaar_otp", "key": "dob", "label": "Date of birth"},
    {
        "flow": "aadhaar_otp",
        "key": "address.zip",
        "label": "PIN code",
        "value_type": "string",
    },
]


def client_fields_url(client_id: str) -> str:
    return f"/v1/clients/{client_id}/tools/acme-identity/fields"


async def save_tool(
    client: AsyncClient, setup: dict[str, Any], fields: list | None
) -> Any:
    definition = copy.deepcopy(TOOL_DEFINITION)
    if fields is not None:
        definition["fields"] = fields
    return await client.put(TOOL_URL, json=definition, headers=setup["admin"])


def keys(page: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (field["flow"], field["key"], field["scope"])
        for field in page["items"]
    ]


async def test_global_fields_are_saved_with_the_tool(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    saved = await save_tool(client, setup, GLOBAL_FIELDS)

    listed = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert saved.status_code == 200, saved.text
    body = listed.json()
    assert keys(body) == [
        ("aadhaar_otp", "address.zip", "global"),
        ("aadhaar_otp", "dob", "global"),
        ("aadhaar_otp", "full_name", "global"),
    ]
    dob = next(field for field in body["items"] if field["key"] == "dob")
    assert (dob["label"], dob["value_type"]) == ("Date of birth", "string")


async def test_saving_the_tool_without_fields_keeps_them(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool(client, setup, GLOBAL_FIELDS)
    await save_tool(client, setup, None)
    kept = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])
    await save_tool(client, setup, [])
    cleared = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert kept.json()["total"] == 3
    assert cleared.json()["total"] == 0


async def test_fields_replace_the_previous_list(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool(client, setup, GLOBAL_FIELDS)
    await save_tool(client, setup, [{"flow": "aadhaar_otp", "key": "gender"}])

    listed = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert keys(listed.json()) == [("aadhaar_otp", "gender", "global")]


async def test_invalid_fields_are_rejected(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    unknown_flow = await save_tool(
        client, setup, [{"flow": "no_such_flow", "key": "x"}]
    )
    duplicate = await save_tool(
        client,
        setup,
        [
            {"flow": "aadhaar_otp", "key": "dob"},
            {"flow": "aadhaar_otp", "key": "dob"},
        ],
    )
    bad_key = await save_tool(
        client, setup, [{"flow": "aadhaar_otp", "key": "address..zip"}]
    )
    bad_type = await save_tool(
        client,
        setup,
        [{"flow": "aadhaar_otp", "key": "dob", "value_type": "date"}],
    )

    assert unknown_flow.status_code == 422
    assert unknown_flow.json()["error"]["code"] == "unknown_tool_flows"
    assert duplicate.status_code == 422
    assert bad_key.status_code == 422
    assert bad_type.status_code == 422


async def test_client_fields_add_to_global_ones(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool(client, setup, GLOBAL_FIELDS[:1])
    saved = await client.put(
        client_fields_url(setup["client_id"]),
        json={
            "fields": [
                {
                    "flow": "aadhaar_otp",
                    "key": "care_of",
                    "label": "Care of",
                }
            ]
        },
        headers=setup["admin"],
    )
    admin_view = await client.get(
        client_fields_url(setup["client_id"]), headers=setup["admin"]
    )
    client_view = await client.get(CLIENT_FIELDS_URL, headers=setup["key"])

    assert saved.status_code == 200, saved.text
    assert keys(saved.json()) == [("aadhaar_otp", "care_of", "client")]
    assert keys(admin_view.json()) == [("aadhaar_otp", "care_of", "client")]
    assert keys(client_view.json()) == [
        ("aadhaar_otp", "care_of", "client"),
        ("aadhaar_otp", "full_name", "global"),
    ]


async def test_client_fields_are_private_to_their_client(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await client.put(
        client_fields_url(setup["client_id"]),
        json={"fields": [{"flow": "aadhaar_otp", "key": "care_of"}]},
        headers=setup["admin"],
    )
    other = await create_client(client, setup["admin"], code="other-portal")
    for code in ("confirm", "gather"):
        await client.put(
            f"/v1/clients/{other['id']}/integrations/{code}",
            json={"tool_code": "acme-identity"},
            headers=setup["admin"],
        )
    other_key = await issue_api_key(client, setup["admin"], other["id"])

    response = await client.get(
        CLIENT_FIELDS_URL, headers={"X-API-Key": other_key["api_key"]}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_client_fields_need_an_existing_client_and_tool(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    unknown_client = await client.put(
        client_fields_url("00000000-0000-4000-8000-000000000000"),
        json={"fields": []},
        headers=setup["admin"],
    )
    unknown_tool = await client.get(
        f"/v1/clients/{setup['client_id']}/tools/no-such-tool/fields",
        headers=setup["admin"],
    )

    assert unknown_client.status_code == 404
    assert unknown_client.json()["error"]["code"] == "client_not_found"
    assert unknown_tool.status_code == 404
    assert unknown_tool.json()["error"]["code"] == "integration_tool_not_found"


async def test_completed_kyc_no_longer_records_fields(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR, otp=OTP)

    listed = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert started.json()["status"] == "completed"
    assert listed.json()["items"] == []


async def test_only_super_admins_manage_fields(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    with_api_key = await client.put(
        client_fields_url(setup["client_id"]),
        json={"fields": []},
        headers=setup["key"],
    )
    anonymous = await client.get(ADMIN_FIELDS_URL)
    client_anonymous = await client.get(CLIENT_FIELDS_URL)

    assert with_api_key.status_code == 401
    assert anonymous.status_code == 401
    assert client_anonymous.status_code == 401


def ids_by_key(page: dict[str, Any]) -> dict[str, str]:
    return {field["key"]: field["id"] for field in page["items"]}


async def test_every_field_has_an_id(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool(client, setup, GLOBAL_FIELDS)

    response = await client.get(CLIENT_FIELDS_URL, headers=setup["key"])

    ids = ids_by_key(response.json())
    assert set(ids) == {"full_name", "dob", "address.zip"}
    assert len(set(ids.values())) == 3


async def test_saving_again_keeps_field_ids(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool(client, setup, GLOBAL_FIELDS)
    before = ids_by_key(
        (await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])).json()
    )

    relabelled = [
        {**GLOBAL_FIELDS[0], "label": "Name as on Aadhaar"},
        GLOBAL_FIELDS[1],
        {"flow": "aadhaar_otp", "key": "gender", "label": "Gender"},
    ]
    await save_tool(client, setup, relabelled)
    after_page = (
        await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])
    ).json()
    after = ids_by_key(after_page)

    # Kept fields keep their id, even when their label changes.
    assert after["full_name"] == before["full_name"]
    assert after["dob"] == before["dob"]
    # Removed fields are gone; new ones get a new id.
    assert "address.zip" not in after
    assert after["gender"] not in before.values()
    full_name = next(f for f in after_page["items"] if f["key"] == "full_name")
    assert full_name["label"] == "Name as on Aadhaar"


async def test_client_field_ids_are_kept_too(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    url = client_fields_url(setup["client_id"])
    body = {"fields": [{"flow": "aadhaar_otp", "key": "care_of"}]}

    first = await client.put(url, json=body, headers=setup["admin"])
    second = await client.put(url, json=body, headers=setup["admin"])

    assert ids_by_key(first.json()) == ids_by_key(second.json())
