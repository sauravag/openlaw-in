"""Task 3: encryption at rest for user-uploaded document content.

Every stored byte of document/excerpt content must be AES-256-GCM
ciphertext, bound by AAD to the exact row it belongs to, so a ciphertext
copied into another row's columns fails to decrypt rather than silently
returning the wrong user's material. See `openlaw.documents.crypto`'s
module docstring for the construction.
"""

import base64

import pytest

from openlaw.config import Settings
from openlaw.documents.crypto import DocumentKeyError, decrypt, encrypt, load_key

VALID_KEY = base64.b64encode(b"0" * 32).decode()
OTHER_KEY = base64.b64encode(b"1" * 32).decode()


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "vaquill_root": "/tmp/vaquill",
        "kanoongpt_root": "/tmp/kanoongpt",
        "document_encryption_key": VALID_KEY,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- encrypt/decrypt round trip ---------------------------------------------


def test_round_trip_recovers_the_plaintext() -> None:
    key = load_key(_settings())
    ciphertext, nonce = encrypt("eviction notice contents", key, "doc-123")

    plaintext = decrypt(ciphertext, nonce, key, "doc-123")

    assert plaintext == "eviction notice contents"


def test_decrypt_fails_with_the_wrong_key() -> None:
    key = load_key(_settings())
    wrong_key = load_key(_settings(document_encryption_key=OTHER_KEY))
    ciphertext, nonce = encrypt("sensitive text", key, "doc-123")

    with pytest.raises(DocumentKeyError):
        decrypt(ciphertext, nonce, wrong_key, "doc-123")


def test_decrypt_fails_when_associated_id_does_not_match() -> None:
    """A ciphertext moved into another document's row must not decrypt.

    This is the AAD-binding guarantee: `associated_id` is baked into the
    authentication tag at encryption time, so presenting the ciphertext
    alongside a different id -- as would happen if it were copied into
    another row -- fails authentication rather than returning plaintext
    under the wrong identity.
    """
    key = load_key(_settings())
    ciphertext, nonce = encrypt("belongs to doc-123", key, "doc-123")

    with pytest.raises(DocumentKeyError):
        decrypt(ciphertext, nonce, key, "doc-456")


def test_decrypt_fails_when_a_ciphertext_byte_is_flipped() -> None:
    key = load_key(_settings())
    ciphertext, nonce = encrypt("integrity matters", key, "doc-123")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]

    with pytest.raises(DocumentKeyError):
        decrypt(tampered, nonce, key, "doc-123")


def test_two_encryptions_of_the_same_plaintext_use_different_nonces() -> None:
    key = load_key(_settings())

    _, nonce_one = encrypt("same text", key, "doc-123")
    _, nonce_two = encrypt("same text", key, "doc-123")

    assert nonce_one != nonce_two


def test_nonce_is_96_bits() -> None:
    key = load_key(_settings())

    _, nonce = encrypt("some text", key, "doc-123")

    assert len(nonce) == 12


def test_ciphertexts_of_the_same_plaintext_also_differ() -> None:
    """Random-nonce AEAD: identical plaintext must not produce identical
    ciphertext across calls."""
    key = load_key(_settings())

    ciphertext_one, _ = encrypt("same text", key, "doc-123")
    ciphertext_two, _ = encrypt("same text", key, "doc-123")

    assert ciphertext_one != ciphertext_two


# --- load_key ----------------------------------------------------------------


def test_load_key_returns_32_bytes_for_a_valid_key() -> None:
    key = load_key(_settings())

    assert isinstance(key, bytes)
    assert len(key) == 32


def test_load_key_rejects_a_missing_key() -> None:
    with pytest.raises(DocumentKeyError):
        load_key(_settings(document_encryption_key=None))


def test_load_key_rejects_a_blank_env_value() -> None:
    """Task 1's documented trap: `OPENLAW_DOCUMENT_ENCRYPTION_KEY=` resolves
    to `""`, not `None` -- this must be treated as no key configured, never
    as the empty-string key."""
    with pytest.raises(DocumentKeyError):
        load_key(_settings(document_encryption_key=""))


def test_load_key_rejects_non_base64_value() -> None:
    with pytest.raises(DocumentKeyError):
        load_key(_settings(document_encryption_key="not valid base64!!"))


def test_load_key_rejects_a_key_shorter_than_32_bytes() -> None:
    short_key = base64.b64encode(b"too-short").decode()

    with pytest.raises(DocumentKeyError):
        load_key(_settings(document_encryption_key=short_key))


def test_load_key_error_names_the_missing_setting() -> None:
    with pytest.raises(DocumentKeyError, match="OPENLAW_DOCUMENT_ENCRYPTION_KEY"):
        load_key(_settings(document_encryption_key=None))


# --- no secret material ever leaks into an exception ------------------------


def test_missing_key_error_never_contains_key_material() -> None:
    with pytest.raises(DocumentKeyError) as excinfo:
        load_key(_settings(document_encryption_key=None))

    assert VALID_KEY not in str(excinfo.value)


def test_invalid_key_error_never_contains_the_offending_value() -> None:
    bad_key = "not valid base64!!"

    with pytest.raises(DocumentKeyError) as excinfo:
        load_key(_settings(document_encryption_key=bad_key))

    assert bad_key not in str(excinfo.value)


def test_short_key_error_never_contains_key_material() -> None:
    short_key = base64.b64encode(b"too-short").decode()

    with pytest.raises(DocumentKeyError) as excinfo:
        load_key(_settings(document_encryption_key=short_key))

    assert short_key not in str(excinfo.value)
    assert base64.b64decode(short_key) not in str(excinfo.value).encode()


def test_wrong_key_decrypt_error_never_contains_key_or_plaintext() -> None:
    key = load_key(_settings())
    wrong_key = load_key(_settings(document_encryption_key=OTHER_KEY))
    ciphertext, nonce = encrypt("a very secret medical record", key, "doc-123")

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, wrong_key, "doc-123")

    message = str(excinfo.value)
    assert VALID_KEY not in message
    assert OTHER_KEY not in message
    assert "a very secret medical record" not in message
    assert base64.b64encode(key).decode() not in message
    assert base64.b64encode(wrong_key).decode() not in message


def test_associated_id_mismatch_error_never_contains_plaintext_or_key() -> None:
    key = load_key(_settings())
    ciphertext, nonce = encrypt("FIR details for case 42", key, "doc-123")

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, key, "doc-456")

    message = str(excinfo.value)
    assert "FIR details for case 42" not in message
    assert base64.b64encode(key).decode() not in message


def test_document_key_error_repr_has_no_key_material() -> None:
    key = load_key(_settings())
    ciphertext, nonce = encrypt("private text", key, "doc-123")

    try:
        decrypt(ciphertext, nonce, key, "doc-456")
    except DocumentKeyError as exc:
        assert base64.b64encode(key).decode() not in repr(exc)
        assert "private text" not in repr(exc)
    else:
        pytest.fail("expected DocumentKeyError")
