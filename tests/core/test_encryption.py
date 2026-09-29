import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings
from app.core.encryption import DecryptionError, decrypt_json, encrypt_json


def settings_with_keys(*keys: str) -> Settings:
    return get_settings().model_copy(
        update={"connection_encryption_keys": SecretStr(",".join(keys))}
    )


def test_round_trip_preserves_value() -> None:
    settings = get_settings()
    value = {"args": ["token-123"], "kwargs": {"region": "in", "retries": 3}}

    ciphertext = encrypt_json(value, settings)

    assert "token-123" not in ciphertext
    assert decrypt_json(ciphertext, settings) == value


def test_tampered_ciphertext_is_rejected() -> None:
    settings = get_settings()
    ciphertext = encrypt_json({"secret": "value"}, settings)
    tampered = ciphertext[:-6] + ("A" if ciphertext[-6] != "A" else "B")
    tampered += ciphertext[-5:]

    with pytest.raises(DecryptionError):
        decrypt_json(tampered, settings)


def test_old_key_still_decrypts_after_rotation() -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    ciphertext = encrypt_json({"n": 1}, settings_with_keys(old_key))

    rotated = settings_with_keys(new_key, old_key)

    assert decrypt_json(ciphertext, rotated) == {"n": 1}


def test_value_encrypted_with_unknown_key_is_rejected() -> None:
    ciphertext = encrypt_json(
        {"n": 1}, settings_with_keys(Fernet.generate_key().decode())
    )

    with pytest.raises(DecryptionError):
        decrypt_json(
            ciphertext, settings_with_keys(Fernet.generate_key().decode())
        )


def test_invalid_key_fails_settings_validation_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONNECTION_ENCRYPTION_KEYS", "not-a-real-fernet-key")

    with pytest.raises(ValidationError) as error:
        Settings()

    assert "not-a-real-fernet-key" not in str(error.value.errors()[0]["msg"])
