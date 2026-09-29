"""Task 3: encryption at rest for user-uploaded document content, extended
by Task 3b with a key identifier so rotation stops being data loss.

Every stored byte of document/excerpt content must be AES-256-GCM
ciphertext, bound by AAD to the exact row it belongs to, so a ciphertext
copied into another row's columns fails to decrypt rather than silently
returning the wrong user's material. See `openlaw.documents.crypto`'s
module docstring for the construction, and for how `key_fingerprint`/
`load_keyring` let a row record which key produced it without an operator
having to invent or track a label.
"""

import base64

import pytest

from openlaw.config import Settings
from openlaw.documents.crypto import (
    DocumentKeyError,
    decrypt,
    encrypt,
    key_fingerprint,
    load_key,
    load_keyring,
)

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
    keyring = load_keyring(_settings())
    ciphertext, nonce, key_id = encrypt("eviction notice contents", key, "doc-123")

    plaintext = decrypt(ciphertext, nonce, keyring, "doc-123", key_id)

    assert plaintext == "eviction notice contents"


def test_decrypt_fails_with_a_different_key_configured() -> None:
    """A row encrypted under one key, decrypted with a different key
    configured, must fail closed -- never silently return garbage, and
    never reveal which key_id was expected or found (the generic message is
    the same one every decryption failure below uses)."""
    key = load_key(_settings())
    other_keyring = load_keyring(_settings(document_encryption_key=OTHER_KEY))
    ciphertext, nonce, key_id = encrypt("sensitive text", key, "doc-123")

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, other_keyring, "doc-123", key_id)

    message = str(excinfo.value)
    assert key_id not in message
    assert next(iter(other_keyring)) not in message


def test_decrypt_fails_when_associated_id_does_not_match() -> None:
    """A ciphertext moved into another document's row must not decrypt.

    This is the AAD-binding guarantee: `associated_id` is baked into the
    authentication tag at encryption time, so presenting the ciphertext
    alongside a different id -- as would happen if it were copied into
    another row -- fails authentication rather than returning plaintext
    under the wrong identity.
    """
    key = load_key(_settings())
    keyring = load_keyring(_settings())
    ciphertext, nonce, key_id = encrypt("belongs to doc-123", key, "doc-123")

    with pytest.raises(DocumentKeyError):
        decrypt(ciphertext, nonce, keyring, "doc-456", key_id)


def test_decrypt_fails_when_a_ciphertext_byte_is_flipped() -> None:
    key = load_key(_settings())
    keyring = load_keyring(_settings())
    ciphertext, nonce, key_id = encrypt("integrity matters", key, "doc-123")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]

    with pytest.raises(DocumentKeyError):
        decrypt(tampered, nonce, keyring, "doc-123", key_id)


def test_two_encryptions_of_the_same_plaintext_use_different_nonces() -> None:
    key = load_key(_settings())

    _, nonce_one, _ = encrypt("same text", key, "doc-123")
    _, nonce_two, _ = encrypt("same text", key, "doc-123")

    assert nonce_one != nonce_two


def test_nonce_is_96_bits() -> None:
    key = load_key(_settings())

    _, nonce, _ = encrypt("some text", key, "doc-123")

    assert len(nonce) == 12


def test_ciphertexts_of_the_same_plaintext_also_differ() -> None:
    """Random-nonce AEAD: identical plaintext must not produce identical
    ciphertext across calls."""
    key = load_key(_settings())

    ciphertext_one, _, _ = encrypt("same text", key, "doc-123")
    ciphertext_two, _, _ = encrypt("same text", key, "doc-123")

    assert ciphertext_one != ciphertext_two


# --- key_fingerprint / key_id -------------------------------------------------


def test_key_fingerprint_is_deterministic() -> None:
    key = load_key(_settings())

    assert key_fingerprint(key) == key_fingerprint(key)


def test_key_fingerprint_differs_for_different_keys() -> None:
    key = load_key(_settings())
    other_key = load_key(_settings(document_encryption_key=OTHER_KEY))

    assert key_fingerprint(key) != key_fingerprint(other_key)


def test_key_fingerprint_never_contains_the_key_itself() -> None:
    key = load_key(_settings())

    fingerprint = key_fingerprint(key)

    assert key.hex() not in fingerprint
    assert base64.b64encode(key).decode() not in fingerprint


def test_key_fingerprint_is_short_and_non_secret_length() -> None:
    key = load_key(_settings())

    assert len(key_fingerprint(key)) == 16


def test_encrypt_records_the_key_id_that_matches_key_fingerprint() -> None:
    key = load_key(_settings())

    _, _, key_id = encrypt("some text", key, "doc-123")

    assert key_id == key_fingerprint(key)


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


# --- load_keyring --------------------------------------------------------------


def test_load_keyring_has_one_entry_when_no_previous_key_is_configured() -> None:
    """Unchanged single-key deployment: an operator who has not thought
    about rotation sees no difference."""
    keyring = load_keyring(_settings())

    assert len(keyring) == 1
    key = load_key(_settings())
    assert keyring[key_fingerprint(key)] == key


def test_load_keyring_rejects_a_missing_current_key_even_with_a_previous_one() -> None:
    with pytest.raises(DocumentKeyError):
        load_keyring(
            _settings(
                document_encryption_key=None,
                document_encryption_key_previous=OTHER_KEY,
            )
        )


def test_load_keyring_includes_both_current_and_previous_keys() -> None:
    keyring = load_keyring(
        _settings(document_encryption_key_previous=OTHER_KEY)
    )

    current_key = load_key(_settings())
    previous_key = load_key(_settings(document_encryption_key=OTHER_KEY))
    assert keyring == {
        key_fingerprint(current_key): current_key,
        key_fingerprint(previous_key): previous_key,
    }


def test_load_keyring_rejects_an_invalid_previous_key() -> None:
    with pytest.raises(DocumentKeyError):
        load_keyring(
            _settings(document_encryption_key_previous="not valid base64!!")
        )


def test_a_row_encrypted_under_the_previous_key_still_decrypts_after_rotation() -> None:
    """The cheap piece of rotation-readiness this module supports: reading
    under an old key while writing under a new one, with no re-encryption
    job and no data loss, as long as the old key is still configured."""
    old_key = load_key(_settings())
    ciphertext, nonce, key_id = encrypt("pre-rotation content", old_key, "doc-123")

    rotated_keyring = load_keyring(
        _settings(
            document_encryption_key=OTHER_KEY,
            document_encryption_key_previous=VALID_KEY,
        )
    )
    plaintext = decrypt(ciphertext, nonce, rotated_keyring, "doc-123", key_id)

    assert plaintext == "pre-rotation content"


def test_new_uploads_after_rotation_use_the_new_key_not_the_previous_one() -> None:
    """`encrypt` never consults `document_encryption_key_previous` -- new
    material always moves to the active key immediately."""
    rotated_settings = _settings(
        document_encryption_key=OTHER_KEY,
        document_encryption_key_previous=VALID_KEY,
    )
    new_key = load_key(rotated_settings)

    _, _, key_id = encrypt("post-rotation content", new_key, "doc-123")

    assert key_id == key_fingerprint(new_key)
    assert key_id != key_fingerprint(load_key(_settings()))


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
    other_keyring = load_keyring(_settings(document_encryption_key=OTHER_KEY))
    ciphertext, nonce, key_id = encrypt(
        "a very secret medical record", key, "doc-123"
    )

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, other_keyring, "doc-123", key_id)

    message = str(excinfo.value)
    assert VALID_KEY not in message
    assert OTHER_KEY not in message
    assert "a very secret medical record" not in message
    assert base64.b64encode(key).decode() not in message


def test_associated_id_mismatch_error_never_contains_plaintext_or_key() -> None:
    key = load_key(_settings())
    keyring = load_keyring(_settings())
    ciphertext, nonce, key_id = encrypt("FIR details for case 42", key, "doc-123")

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, keyring, "doc-456", key_id)

    message = str(excinfo.value)
    assert "FIR details for case 42" not in message
    assert base64.b64encode(key).decode() not in message


def test_document_key_error_repr_has_no_key_material() -> None:
    key = load_key(_settings())
    keyring = load_keyring(_settings())
    ciphertext, nonce, key_id = encrypt("private text", key, "doc-123")

    try:
        decrypt(ciphertext, nonce, keyring, "doc-456", key_id)
    except DocumentKeyError as exc:
        assert base64.b64encode(key).decode() not in repr(exc)
        assert "private text" not in repr(exc)
    else:
        pytest.fail("expected DocumentKeyError")


def test_unknown_key_id_fails_closed_without_leaking_which_id_was_expected() -> None:
    """A row's `key_id` naming a key nobody configured -- e.g. a key that
    was rotated away without being kept as the previous key -- must fail
    the same generic way as every other decryption failure, not with a
    message spelling out the missing id."""
    key = load_key(_settings())
    keyring = load_keyring(_settings())
    ciphertext, nonce, _ = encrypt("orphaned content", key, "doc-123")

    with pytest.raises(DocumentKeyError) as excinfo:
        decrypt(ciphertext, nonce, keyring, "doc-123", "0" * 16)

    assert "0" * 16 not in str(excinfo.value)
