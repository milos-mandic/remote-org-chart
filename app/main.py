"""FastAPI application: routes + in-memory cache.

The built org chart is expensive (~30-60s cold fetch), so we cache the fully
assembled OrgResponse in memory with a TTL. The first request after a cold
start triggers the build; a single asyncio lock serializes builds so a burst
of first requests does not fan out into many concurrent fetches.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.models import (
    BUCKET_CURRENT,
    BUCKET_FORMER,
    BUCKET_ONBOARDING,
    OrgResponse,
    SourceCounts,
    classify,
    display_defaults,
)
from app.remote_client import RemoteClient
from app.tree import build_forest

logger = logging.getLogger("main")

app = FastAPI(title="Remote Org Chart")

_BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=_BASE / "static"), name="static")
templates = Jinja2Templates(directory=_BASE / "templates")


def _initials(name: str) -> str:
    """First + last initial for an avatar monogram (single-word → first two chars)."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _hue(name: str) -> int:
    """Stable 0-359 hue from a name, so each person gets a consistent avatar color."""
    return sum(ord(c) for c in (name or "")) % 360


templates.env.filters["initials"] = _initials
templates.env.filters["hue"] = _hue


class OrgCache:
    """Holds the last built OrgResponse plus a lock to serialize rebuilds."""

    def __init__(self) -> None:
        self.response: OrgResponse | None = None
        self._built_monotonic: float | None = None
        self.lock = asyncio.Lock()

    def store(self, response: OrgResponse) -> None:
        self.response = response
        self._built_monotonic = time.monotonic()

    def age_seconds(self) -> float | None:
        if self._built_monotonic is None:
            return None
        return time.monotonic() - self._built_monotonic

    def is_fresh(self, ttl: int) -> bool:
        age = self.age_seconds()
        return age is not None and age < ttl


cache = OrgCache()


async def build_org_response(settings: Settings) -> OrgResponse:
    """Fetch, classify, split into buckets, and build the forest. Pure assembly
    on top of remote_client + tree + classify — no caching concerns here."""
    client = RemoteClient(settings)
    people, fetch_errors = await client.fetch_all_people()
    company_name = await client.fetch_company_name()

    for p in people:
        p.bucket, p.data_inconsistent = classify(p)

    current = [p for p in people if p.bucket == BUCKET_CURRENT]
    onboarding = [p for p in people if p.bucket == BUCKET_ONBOARDING]
    former = [p for p in people if p.bucket == BUCKET_FORMER]

    # known_ids = the whole roster, so build_forest can tell a FORMER manager
    # (manager_former) apart from one that does not exist (manager_unknown).
    forest = build_forest(current, known_ids={p.id for p in people})

    by_name = lambda p: p.full_name.casefold()  # noqa: E731
    return OrgResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        company_name=company_name,
        source_counts=SourceCounts(
            total=len(people),
            current=len(current),
            onboarding=len(onboarding),
            former=len(former),
        ),
        forest=forest.roots,
        unassigned=forest.unassigned,
        onboarding=[display_defaults(p) for p in sorted(onboarding, key=by_name)],
        former=[display_defaults(p) for p in sorted(former, key=by_name)],
        fetch_errors=fetch_errors,
    )


async def get_org(*, force: bool) -> OrgResponse:
    """Return the cached org chart, (re)building it when needed.

    Serves fresh cache immediately. Otherwise builds under a lock, re-checking
    freshness after acquiring it so a burst of cold-start requests triggers a
    single build. If a build fails but we still hold a prior response, we serve
    that stale copy rather than erroring; only a failure with no cache at all
    surfaces as 503.
    """
    settings = get_settings()
    ttl = settings.cache_ttl_seconds

    if not force and cache.is_fresh(ttl):
        return cache.response  # type: ignore[return-value]

    async with cache.lock:
        if not force and cache.is_fresh(ttl):
            return cache.response  # type: ignore[return-value]
        try:
            response = await build_org_response(settings)
        except Exception as exc:
            if cache.response is not None:
                logger.warning("rebuild failed (%s); serving stale cache", type(exc).__name__)
                return cache.response
            logger.error("org build failed with no cache to fall back on: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Upstream API unavailable; no cached data yet.")
        cache.store(response)
        return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Server-render the org chart when we have data; otherwise show a loading
    page that triggers the (blocking) build client-side and reloads when ready.

    We never build inside GET / — a ~30-60s cold fetch there would hang the
    browser with no feedback. The loading page's fetch to /api/org does the
    build, then reloads into the rendered tree.
    """
    if cache.response is not None:
        return templates.TemplateResponse(
            request, "index.html", {"mode": "ready", "org": cache.response}
        )
    return templates.TemplateResponse(request, "index.html", {"mode": "loading"})


@app.get("/api/org", response_model=OrgResponse)
async def api_org() -> OrgResponse:
    return await get_org(force=False)


@app.post("/api/refresh", response_model=OrgResponse)
async def api_refresh() -> OrgResponse:
    return await get_org(force=True)


@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe for the deployment platform: token presence + cache age."""
    settings = get_settings()
    return {
        "status": "ok",
        "token_configured": bool(settings.remote_api_token),
        "cache_age_seconds": cache.age_seconds(),
    }
