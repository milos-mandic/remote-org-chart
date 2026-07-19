"""Run the fetch once against the live sandbox and print counts.

Usage:  .venv/bin/python -m scripts.fetch_once

Prints only aggregate counts and structural facts — never PII, never raw
payloads. Useful to sanity-check Phase 2 against the live API.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from app.remote_client import RemoteClient


async def main() -> None:
    started = time.monotonic()
    people, errors = await RemoteClient().fetch_all_people()
    elapsed = time.monotonic() - started

    total = len(people)
    with_manager = sum(1 for p in people if p.manager_employment_id)
    roots = sum(1 for p in people if not p.manager_employment_id)

    ids = {p.id for p in people}
    dangling = sum(
        1
        for p in people
        if p.manager_employment_id and p.manager_employment_id not in ids
    )

    print(f"fetched {total} people in {elapsed:.1f}s  ({len(errors)} fetch errors)")
    print(f"  with manager_employment_id : {with_manager}")
    print(f"  roots (no manager)         : {roots}")
    print(f"  manager id not in roster   : {dangling}")

    print("  by status:")
    for k, v in sorted(Counter(p.status for p in people).items(), key=lambda x: str(x[0])):
        print(f"    {k!s:<12} {v}")
    print("  by lifecycle stage:")
    for k, v in sorted(Counter(p.employment_lifecycle_stage for p in people).items(), key=lambda x: str(x[0])):
        print(f"    {k!s:<28} {v}")
    print("  by employment_model:")
    for k, v in sorted(Counter(p.employment_model for p in people).items(), key=lambda x: str(x[0])):
        print(f"    {k!s:<16} {v}")

    if errors:
        print("  fetch error ids:")
        for e in errors:
            print(f"    {e.id}  {e.error[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
