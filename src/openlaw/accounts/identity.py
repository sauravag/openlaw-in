"""Delete the Supabase (GoTrue) identity behind an erased account.

Run only AFTER the local erasure has committed: deleting the identity first
and then failing locally would leave a person unable to sign in to retry the
erasure of data that still exists.

GoTrue has no self-serve delete, so this needs the service-role key, which
bypasses every row-level policy. It lives only in the backend's settings and
is never logged, returned or sent to the frontend. Nothing here puts the key,
a response body or an exception message in a log line: only a status code or
an exception class name.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from openlaw.config import Settings

logger = logging.getLogger(__name__)

#: Bound on the admin call so a GoTrue outage cannot hang the request.
ADMIN_TIMEOUT_SECONDS = 10.0
_GONE_STATUS = 404


def _is_removed(status: int) -> bool:
    # 404: the identity is already gone, which is the outcome we want.
    return httpx.codes.is_success(status) or status == _GONE_STATUS


def remove_supabase_identity(
    settings: Settings, user_id: str, client: httpx.Client | None = None
) -> bool:
    """Delete `user_id`'s Supabase identity; True only if it is now gone.

    Never raises: any failure (no key, no URL, network error, a malformed
    key or URL, error status)
    returns False so the caller can say plainly that it was NOT removed.
    `client` is for tests; production builds a bounded one.
    """
    key = settings.supabase_service_role_key
    if not key:
        return False
    if not settings.supabase_url:
        logger.warning("identity removal skipped: OPENLAW_SUPABASE_URL is not set")
        return False
    base = settings.supabase_url.rstrip("/")
    url = f"{base}/auth/v1/admin/users/{quote(user_id, safe='')}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        if client is not None:
            response = client.delete(url, headers=headers)
        else:
            with httpx.Client(timeout=ADMIN_TIMEOUT_SECONDS) as owned:
                response = owned.delete(url, headers=headers)
    except Exception as error:  # noqa: BLE001 -- must never undo a committed erasure
        logger.warning("identity removal failed: %s", type(error).__name__)
        return False
    if not _is_removed(response.status_code):
        logger.warning("identity removal refused, status %s", response.status_code)
        return False
    return True
