"""Encryption at rest for user-uploaded document content (Plan 6 Task 3).

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

**No rotation support, by design (documented cost).** Neither this module
nor the `documents`/`document_excerpts` schema (Plan 6 Task 2) records a
key identifier alongside the ciphertext, because that schema is already
fixed by that task and adding one now would require a second migration
out of this task's scope. The consequence -- already called out on
`Settings.document_encryption_key` -- is that rotating the key makes every
existing upload permanently unreadable: there is no re-encryption path, no
"try the old key on decrypt failure" fallback (that would silently widen
`DocumentKeyError` into a real ambiguity about which upload used which
key), and no incremental migration. Treat the key exactly like a
long-lived secret with no rotation story until a later task adds a
`key_id` column and a re-encryption job.

**Nothing secret ever reaches an exception.** Every error path below
raises `DocumentKeyError` with a message built from context that is
already public within this process (a setting's name, a byte length, a
static description) -- never the key, its base64 form, or the plaintext.
"""

import base64
import binascii
from os import urandom

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from openlaw.config import Settings

#: The `OPENLAW_`-prefixed environment variable name shown in error
#: messages -- matches `Settings.model_config`'s `env_prefix` (see
#: `openlaw.config`) so an operator can act on the message without having
#: to know pydantic-settings' naming convention.
_ENV_VAR_NAME = "OPENLAW_DOCUMENT_ENCRYPTION_KEY"
#: AES-256 key length in bytes.
_KEY_BYTES = 32
#: AES-GCM's standard 96-bit nonce length in bytes.
_NONCE_BYTES = 12
#: Shown in every `load_key` error -- the exact command from
#: `Settings.document_encryption_key`'s docstring, kept in one place so the
#: three failure messages below cannot drift from each other.
_GENERATE_KEY_HINT = (
    'python -c "import base64,os;'
    'print(base64.b64encode(os.urandom(32)).decode())"'
)


class DocumentKeyError(RuntimeError):
    """Raised when the document-encryption key is missing/invalid, or when
    decryption fails for any reason (wrong key, wrong `associated_id`, or a
    tampered ciphertext).

    Deliberately one error type for every failure mode: a caller checking
    "did this document read back correctly" needs exactly one thing to
    catch, and collapsing "bad key" and "bad ciphertext" into the same type
    is also what keeps a decrypt failure from leaking *which* of the two
    happened -- that distinction is itself a hint useful to an attacker
    probing whether a relocated ciphertext or a guessed key is closer.
    """


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

    try:
        key = base64.b64decode(raw_value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DocumentKeyError(
            f"{_ENV_VAR_NAME} is not valid base64. Generate a new key with: "
            f"{_GENERATE_KEY_HINT}"
        ) from exc

    if len(key) != _KEY_BYTES:
        raise DocumentKeyError(
            f"{_ENV_VAR_NAME} must decode to exactly {_KEY_BYTES} bytes "
            f"(got {len(key)}). Generate a new key with: {_GENERATE_KEY_HINT}"
        )
    return key


def encrypt(plaintext: str, key: bytes, associated_id: str) -> tuple[bytes, bytes]:
    """Return `(ciphertext, nonce)` for `plaintext`, AAD-bound to
    `associated_id`.

    A fresh CSPRNG nonce is drawn for every call -- never reused, never
    derived from `associated_id` or any counter -- so two encryptions of
    the same plaintext under the same key produce different ciphertext.
    """
    nonce = urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(
        nonce, plaintext.encode("utf-8"), associated_id.encode("utf-8")
    )
    return ciphertext, nonce


def decrypt(ciphertext: bytes, nonce: bytes, key: bytes, associated_id: str) -> str:
    """Return the decrypted plaintext, raising `DocumentKeyError` on any
    authentication failure: wrong key, wrong `associated_id` (a ciphertext
    relocated to another row), or a tampered ciphertext/nonce.
    """
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
