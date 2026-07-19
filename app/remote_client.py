"""Async client for the Remote sandbox API.

Responsibilities:
  - authenticate with the Bearer token (from env, never logged)
  - page through the employments list
  - fetch every employment's detail concurrently (bounded), with retry/backoff
  - map each raw response to a minimal Person and DISCARD the raw payload

Nothing in this module logs, returns, or persists a raw API response. The only
things that cross its boundary are Person and FetchError objects.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings, get_settings
from app.models import FetchError, Person

logger = logging.getLogger("remote_client")

# Status codes worth retrying: rate limiting and transient server errors.
_RETRYABLE = {429, 500, 502, 503, 504}


class RemoteClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        if not self._s.remote_api_token:
            raise RuntimeError("REMOTE_API_TOKEN is not configured")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._s.remote_api_base_url,
            headers={"Authorization": f"Bearer {self._s.remote_api_token}"},
            timeout=self._s.request_timeout_seconds,
        )

    async def fetch_all_people(self) -> tuple[list[Person], list[FetchError]]:
        """Fetch the full roster as minimal Person models.

        Returns (people, fetch_errors). A person is always present for every
        list id — if their detail fetch fails, we fall back to the list item
        (manager unknown) and record a FetchError.
        """
        async with self._client() as client:
            items = await self._fetch_list(client)
            sem = asyncio.Semaphore(self._s.max_concurrent_requests)
            results = await asyncio.gather(
                *(self._resolve_person(client, item, sem) for item in items)
            )

        people = [p for p, _ in results]
        errors = [e for _, e in results if e is not None]
        return people, errors

    async def fetch_company_name(self) -> str | None:
        """Company display name from /v1/identity/current (data.company.name).

        Fail-soft: returns None on any error so a hiccup here never blocks the
        org chart.
        """
        try:
            async with self._client() as client:
                data = await self._get_json(client, "/v1/identity/current")
            return data["data"]["company"]["name"]
        except Exception as exc:
            logger.warning("company name fetch failed: %s", type(exc).__name__)
            return None

    async def _fetch_list(self, client: httpx.AsyncClient) -> list[dict]:
        """Page through /v1/employments and return all raw list items."""
        first = await self._get_json(
            client, "/v1/employments", params={"page": 1, "page_size": self._s.list_page_size}
        )
        data = first["data"]
        items = list(data["employments"])
        total_pages = data.get("total_pages", 1)

        for page in range(2, total_pages + 1):
            more = await self._get_json(
                client,
                "/v1/employments",
                params={"page": page, "page_size": self._s.list_page_size},
            )
            items.extend(more["data"]["employments"])
        return items

    async def _resolve_person(
        self, client: httpx.AsyncClient, item: dict, sem: asyncio.Semaphore
    ) -> tuple[Person, FetchError | None]:
        """Fetch one detail (bounded by sem); fail soft to the list item."""
        emp_id = item["id"]
        async with sem:
            try:
                detail = await self._get_json(client, f"/v1/employments/{emp_id}")
                # Map + discard raw immediately: `emp` is the only thing we read.
                emp = detail["data"]["employment"]
                return Person.from_detail(emp), None
            except Exception as exc:  # fail soft — never drop a person
                logger.warning("detail fetch failed for %s: %s", emp_id, type(exc).__name__)
                return Person.from_list_item(item), FetchError(id=emp_id, error=str(exc))

    async def _get_json(self, client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
        """GET with exponential backoff on 429/5xx (up to max_retries)."""
        last_exc: Exception | None = None
        for attempt in range(self._s.max_retries + 1):
            try:
                resp = await client.get(url, params=params)
                if resp.status_code in _RETRYABLE:
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in _RETRYABLE:
                    raise  # non-retryable (e.g. 404/401) — fail immediately
            except httpx.HTTPError as exc:
                last_exc = exc  # network/timeout — retryable

            if attempt < self._s.max_retries:
                await asyncio.sleep(2**attempt)  # 1s, 2s, 4s
        assert last_exc is not None
        raise last_exc
