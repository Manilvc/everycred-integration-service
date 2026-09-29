from collections.abc import Iterator

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import SecretStoreBackend, get_settings
from app.core.secret_store import (
    AwsSecretsManagerStore,
    LocalSecretStore,
    SecretStoreError,
)

REGION = "ap-south-1"


@pytest.fixture
def aws() -> Iterator[dict]:
    """A mocked AWS account with one KMS key."""
    with mock_aws():
        key_id = boto3.client("kms", region_name=REGION).create_key()[
            "KeyMetadata"
        ]["KeyId"]
        client = boto3.client("secretsmanager", region_name=REGION)
        settings = get_settings().model_copy(
            update={
                "secret_store_backend": SecretStoreBackend.AWS,
                "aws_region": REGION,
                "secrets_kms_key_id": key_id,
            }
        )
        yield {
            "client": client,
            "key_id": key_id,
            "store": AwsSecretsManagerStore(client, settings),
        }


async def test_aws_store_round_trip_uses_kms_key(aws: dict) -> None:
    store = aws["store"]

    reference = await store.create(
        "clients/c1/tools/surepass",
        {"api_token": "tok-1"},
        tags={"client_id": "c1"},
    )

    assert reference.startswith("arn:aws:secretsmanager:")
    description = aws["client"].describe_secret(SecretId=reference)
    assert description["Name"] == (
        "everycred/integration-service/clients/c1/tools/surepass"
    )
    assert description["KmsKeyId"] == aws["key_id"]
    assert {"Key": "client_id", "Value": "c1"} in description["Tags"]
    assert await store.read(reference) == {"api_token": "tok-1"}


async def test_aws_store_replace_keeps_reference(aws: dict) -> None:
    store = aws["store"]
    reference = await store.create("a/b", {"v": 1}, tags={})

    await store.replace(reference, {"v": 2})

    assert await store.read(reference) == {"v": 2}


async def test_aws_store_create_reuses_existing_name(aws: dict) -> None:
    store = aws["store"]
    first = await store.create("same/name", {"v": 1}, tags={})

    second = await store.create("same/name", {"v": 2}, tags={})

    assert second == first
    assert await store.read(first) == {"v": 2}


async def test_aws_store_restores_secret_pending_deletion(aws: dict) -> None:
    store = aws["store"]
    reference = await store.create("to/restore", {"v": 1}, tags={})
    await store.delete(reference)

    recreated = await store.create("to/restore", {"v": 2}, tags={})

    assert recreated == reference
    assert await store.read(reference) == {"v": 2}


async def test_aws_store_delete_schedules_recovery_window(aws: dict) -> None:
    store = aws["store"]
    reference = await store.create("to/delete", {"v": 1}, tags={})

    await store.delete(reference)

    description = aws["client"].describe_secret(SecretId=reference)
    assert "DeletedDate" in description


async def test_aws_store_deleting_missing_secret_is_ignored(
    aws: dict,
) -> None:
    await aws["store"].delete(
        f"arn:aws:secretsmanager:{REGION}:123456789012:secret:missing-AbCdEf"
    )


async def test_aws_store_wraps_errors_without_details(aws: dict) -> None:
    with pytest.raises(SecretStoreError) as error:
        await aws["store"].read("arn:aws:secretsmanager:nope")

    assert error.value.error_code == "secret_store_unavailable"


async def test_local_store_round_trip_is_encrypted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = LocalSecretStore(session, get_settings())
        reference = await store.create("x/y", {"token": "abc123"}, tags={})
        await store.replace(reference, {"token": "def456"})
        await session.commit()

        assert reference.startswith("local:")
        assert await store.read(reference) == {"token": "def456"}

        await store.delete(reference)
        await session.commit()
        with pytest.raises(SecretStoreError):
            await store.read(reference)


async def test_local_store_rejects_malformed_reference(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = LocalSecretStore(session, get_settings())
        with pytest.raises(SecretStoreError):
            await store.read("local:not-a-uuid")


async def test_aws_store_restores_when_aws_reports_invalid_request(
    aws: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real AWS answers InvalidRequestException for a name pending deletion.

    moto answers ResourceExistsException instead, which hid this case, so
    the client is patched to behave like AWS.
    """
    store = aws["store"]
    reference = await store.create("aws/behaviour", {"v": 1}, tags={})
    await store.delete(reference)
    real_create = aws["client"].create_secret

    def create_like_real_aws(**kwargs):
        try:
            description = aws["client"].describe_secret(
                SecretId=kwargs["Name"]
            )
        except ClientError:
            return real_create(**kwargs)
        if description.get("DeletedDate"):
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidRequestException",
                        "Message": "already scheduled for deletion",
                    }
                },
                "CreateSecret",
            )
        return real_create(**kwargs)

    monkeypatch.setattr(aws["client"], "create_secret", create_like_real_aws)

    recreated = await store.create("aws/behaviour", {"v": 2}, tags={})

    assert recreated == reference
    assert await store.read(reference) == {"v": 2}
    assert "DeletedDate" not in aws["client"].describe_secret(
        SecretId=reference
    )
