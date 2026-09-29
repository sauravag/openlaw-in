"""Encryption at rest for user-uploaded document content (Plan 6 Task 3,
extended by Task 3b with a key identifier so rotation stops being data loss).

Every stored byte of a `Document`/`DocumentExcerptRow`'s content is
AES-256-GCM ciphertext -- never plaintext, never a reversible encoding.
GCM is authenticated encryption (AEAD): decryption fails loudly on any
tampering, wrong key, or ciphertext moved to the wrong row, rather than
silently returning garbage or someone else's data.

**AAD binds ciphertext to its row.** AES-GCM alone (encrypt-then-store) does
not stop a ciphertext blob from being copied into a different row's
`ciphertext`/`nonce` columns and decrypting there as if it belonged --
same key, same cipher, a structurally valid ciphertext, just relocated. The
associated data (AAD) parameter closes that: `encrypt`/`decrypt` take an
`associated_id` -- `Document.id` for a document blob, `DocumentExcerptRow
.excerpt_id` for an excerpt (see those models' docstrings in
`openlaw.db.models`) -- encoded into the authentication tag but never
into the ciphertext itself. Decrypting with any `associated_id` other than
the exact one used to encrypt fails authentication, so a ciphertext
relocated to another document's row, or presented against another user's
id, cannot decrypt into readable content.

**Nonce.** A fresh 96-bit (12-byte) nonce is drawn from a CSPRNG
(`os.urandom`) for every encryption and stored alongside the ciphertext --
GCM's security bound breaks down if a (key, nonce) pair is ever reused, so
nonces are never derived, counted, or reused across rows.

**Key.** `load_key` reads `Settings.document_encryption_key`: a
base64-encoded 32-byte value, decoded once into the raw AES-256 key bytes
`encrypt`/`decrypt` take. There is no key derivation step -- the configured
value IS the key -- so operators must generate it with a CSPRNG (see
`Settings.document_encryption_key`'s docstring in `openlaw.config` for the
exact command) rather than a password or a phrase.

**Key identifier, not key rotation.** `key_fingerprint` derives a short,
non-secret label from a key -- `Document.key_id`/`DocumentExcerptRow.key_id`
(Task 2's schema, extended by Task 3b's migration) store this label, never
the key itself. `encrypt` always returns the fingerprint of the key it
used; `decrypt` is handed a `keyring` (`load_keyring`) and the row's stored
`key_id`, and looks the right key up by that id rather than assuming
whichever key it was given is the right one. The label is DERIVED, not
hand-assigned: an operator generates a new key and it is automatically
identified by its own fingerprint, nothing to invent or track.

This module still does not implement rotation -- there is no re-encryption
job, and nothing here migrates an old row onto a new key. What it does
support cheaply: `load_keyring` also decodes `Settings
.document_encryption_key_previous` when set, so a row encrypted under a
retiring key keeps decrypting (its `key_id` selects the previous key out of
the keyring) while `encrypt` (and therefore every new upload) moves
immediately to the current key. A deployment that never sets the previous
key behaves exactly as before Task 3b: one key, one fingerprint, no visible
difference.

**Nothing secret ever reaches an exception.** Every error path below
raises `DocumentKeyError` with a message built from context that is
already public within this process (a setting's name, a byte length, a
static description) -- never the key, its base64 form, or the plaintext.
`key_id` values are non-secret by design (they are stored in cleartext
alongside the ciphertext they identify), but a key-mismatch error still
uses the same one generic message as every other decryption failure below,
rather than spelling out which id was expected or found -- collapsing
"wrong key", "wrong associated_id", and "key not in keyring" into one
message is what keeps a decrypt failure from handing an attacker a signal
about which of those is closer to true.
"""

import base64
import binascii
from hashlib import sha256
from os import urandom

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from openlaw.config import Settings

#: The `OPENLAW_`-prefixed environment variable name shown in error
#: messages -- matches `Settings.model_config`'s `env_prefix` (see
#: `openlaw.config`) so an operator can act on the message without having
#: to know pydantic-settings' naming convention.
_ENV_VAR_NAME = "OPENLAW_DOCUMENT_ENCRYPTION_KEY"
#: The retiring-key counterpart of `_ENV_VAR_NAME`, named in `load_keyring`'s
#: own error messages so a bad previous key is never confused with a bad
#: current one.
_PREVIOUS_ENV_VAR_NAME = "OPENLAW_DOCUMENT_ENCRYPTION_KEY_PREVIOUS"
#: AES-256 key length in bytes.
_KEY_BYTES = 32
#: AES-GCM's standard 96-bit nonce length in bytes.
_NONCE_BYTES = 12
#: Length, in hex characters, of a `key_fingerprint` result -- 64 bits of the
#: underlying SHA-256 digest. Short enough to be an obviously non-secret
#: label (nowhere near enough of the hash to help reconstruct the key it
#: was derived from), long enough that two different operator-generated
#: keys colliding is not a practical concern. Matches `Document.key_id`'s
#: and `DocumentExcerptRow.key_id`'s column length exactly.
_KEY_ID_HEX_CHARS = 16
#: Shown in every `load_key` error -- the exact command from
#: `Settings.document_encryption_key`'s docstring, kept in one place so the
#: three failure messages below cannot drift from each other.
_GENERATE_KEY_HINT = (
    'python -c "import base64,os;'
    'print(base64.b64encode(os.urandom(32)).decode())"'
)


class DocumentKeyError(RuntimeError):
    """Raised when the document-encryption key is missing/invalid, or when
    decryption fails for any reason (wrong key, unknown `key_id`, wrong
    `associated_id`, or a tampered ciphertext).

    Deliberately one error type for every failure mode: a caller checking
    "did this document read back correctly" needs exactly one thing to
    catch, and collapsing "bad key", "bad ciphertext", and "key not in
    keyring" into the same type is also what keeps a decrypt failure from
    leaking *which* of those happened -- that distinction is itself a hint
    useful to an attacker probing whether a relocated ciphertext, a guessed
    key, or a guessed key_id is closer.
    """


def key_fingerprint(key: bytes) -> str:
    """Return a short, non-secret label identifying `key`.

    A truncated SHA-256 digest of the raw key bytes, hex-encoded. One-way
    (the key cannot be recovered from it) and short enough on top of that
    to make clear it is a label, not a hash an attacker would bother
    attacking -- see `_KEY_ID_HEX_CHARS`. Deterministic, so the same key
    always produces the same fingerprint: that is what lets `encrypt`
    record "which key" without an operator ever assigning or tracking a
    name for it, and what lets `decrypt` recognise a row's key again later
    just by re-deriving this same value from whatever key is configured.
    """
    return sha256(key).hexdigest()[:_KEY_ID_HEX_CHARS]


def _decode_key(raw_value: str, env_var_name: str) -> bytes:
    """Return the 32-byte AES-256 key decoded from `raw_value`.

    Shared by `load_key` and `load_keyring` so the base64/length validation
    -- and its error messages -- cannot drift between the current key and
    the previous one. Never returns a fallback key; raises `DocumentKeyError`
    naming `env_var_name` on any decode or length failure.
    """
    try:
        key = base64.b64decode(raw_value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DocumentKeyError(
            f"{env_var_name} is not valid base64. Generate a new key with: "
            f"{_GENERATE_KEY_HINT}"
        ) from exc

    if len(key) != _KEY_BYTES:
        raise DocumentKeyError(
            f"{env_var_name} must decode to exactly {_KEY_BYTES} bytes "
            f"(got {len(key)}). Generate a new key with: {_GENERATE_KEY_HINT}"
        )
    return key


def load_key(settings: Settings) -> bytes:
    """Return the 32-byte AES-256 key from `settings.document_encryption_key`.

    Raises `DocumentKeyError` -- never returns a fallback or default key --
    when the setting is absent, blank, not valid base64, or does not decode
    to exactly 32 bytes. A blank `OPENLAW_DOCUMENT_ENCRYPTION_KEY=` in the
    environment resolves to `""`, not `None` (pydantic-settings' usual
    behaviour, matching every other key field in `Settings`), so this
    checks falsiness rather than `is None` -- an empty string must fail the
    same way an unset variable does, never decode as a zero-length key.
    """
    raw_value = settings.document_encryption_key
    if not raw_value:
        raise DocumentKeyError(
            f"{_ENV_VAR_NAME} is not set. Uploads cannot be encrypted at "
            f"rest without it. Generate one with: {_GENERATE_KEY_HINT} and "
            "set it in your environment or .env file before enabling "
            "uploads."
        )
    return _decode_key(raw_value, _ENV_VAR_NAME)


def load_keyring(settings: Settings) -> dict[str, bytes]:
    """Return every key `decrypt` may use, keyed by `key_fingerprint`.

    Always contains the active key (`settings.document_encryption_key`,
    via `load_key` -- so a missing/invalid current key still fails exactly
    as `load_key` alone would). Also contains the retiring key
    (`settings.document_encryption_key_previous`) when one is configured,
    so a document encrypted before a key rotation keeps decrypting: its
    stored `key_id` selects the previous key out of this dict instead of
    the current one. `encrypt` never consults this function or the
    previous key -- new uploads always move to the current key
    immediately, only reads may still need the old one.

    A deployment that never sets `document_encryption_key_previous` gets a
    single-entry keyring, identical in effect to calling `load_key` alone --
    the single-key case is unchanged.
    """
    active_key = load_key(settings)
    keyring = {key_fingerprint(active_key): active_key}

    previous_raw = settings.document_encryption_key_previous
    if previous_raw:
        previous_key = _decode_key(previous_raw, _PREVIOUS_ENV_VAR_NAME)
        keyring[key_fingerprint(previous_key)] = previous_key
    return keyring


def encrypt(plaintext: str, key: bytes, associated_id: str) -> tuple[bytes, bytes, str]:
    """Return `(ciphertext, nonce, key_id)` for `plaintext`, AAD-bound to
    `associated_id`.

    A fresh CSPRNG nonce is drawn for every call -- never reused, never
    derived from `associated_id` or any counter -- so two encryptions of
    the same plaintext under the same key produce different ciphertext.
    `key_id` is `key_fingerprint(key)`: the caller stores it alongside
    `ciphertext`/`nonce` (`Document.key_id`/`DocumentExcerptRow.key_id`) so
    a later `decrypt` knows which key produced this row without anyone
    having to record that by hand.
    """
    nonce = urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(
        nonce, plaintext.encode("utf-8"), associated_id.encode("utf-8")
    )
    return ciphertext, nonce, key_fingerprint(key)


def decrypt(
    ciphertext: bytes,
    nonce: bytes,
    keyring: dict[str, bytes],
    associated_id: str,
    key_id: str,
) -> str:
    """Return the decrypted plaintext, raising `DocumentKeyError` on any
    failure: `key_id` not present in `keyring` (no configured key produced
    this row), wrong `associated_id` (a ciphertext relocated to another
    row), or a tampered ciphertext/nonce.

    `key_id` is the row's own stored fingerprint, looked up in `keyring`
    (`load_keyring`) to select the matching key -- this is the "selection"
    a key identifier exists for: the caller never has to guess or track
    which of the configured keys goes with which row. A `key_id` missing
    from `keyring` fails closed immediately, before AES-GCM is even
    invoked, with the same generic message every other failure below uses.
    """
    key = keyring.get(key_id)
    if key is None:
        raise DocumentKeyError(
            "document decryption failed: wrong key, wrong document/excerpt "
            "id, or corrupted ciphertext"
        )

    aesgcm = AESGCM(key)
    try:
        plaintext_bytes = aesgcm.decrypt(
            nonce, ciphertext, associated_id.encode("utf-8")
        )
    except InvalidTag as exc:
        raise DocumentKeyError(
            "document decryption failed: wrong key, wrong document/excerpt "
            "id, or corrupted ciphertext"
        ) from exc
    return plaintext_bytes.decode("utf-8")
