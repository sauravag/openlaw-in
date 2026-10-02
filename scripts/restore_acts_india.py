"""Restore the published acts_india collection into local Qdrant.

Downloads ~11.2 GB. Run: python scripts/restore_acts_india.py
"""

import sys

import httpx

from openlaw.config import get_settings
from openlaw.index.qdrant_restore import ACTS_INDIA, restore_collection

EXPECTED_POINTS = 1_098_577


def main() -> int:
    settings = get_settings()
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        print(f"[Restore] creating and recovering {ACTS_INDIA.name} (~11.2 GB)")
        restore_collection(ACTS_INDIA, settings.qdrant_url, client)

        info = client.get(f"{settings.qdrant_url}/collections/{ACTS_INDIA.name}").json()

    points = info["result"]["points_count"]
    print(f"[Restore] points_count = {points:,} (published: {EXPECTED_POINTS:,})")

    if points < EXPECTED_POINTS * 0.99:
        print("[Restore] FAIL: point count is more than 1% below published figure")
        return 1
    print("[Restore] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
