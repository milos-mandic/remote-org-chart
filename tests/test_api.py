"""Route + cache behavior for main.py, with the build stubbed (no network)."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import OrgResponse, Person, SourceCounts, TreeNode

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_cache():
    main.cache.response = None
    main.cache._built_monotonic = None
    yield


def make_response(name: str) -> OrgResponse:
    return OrgResponse(
        generated_at="2026-07-19T00:00:00+00:00",
        company_name=name,
        source_counts=SourceCounts(total=1, current=1, onboarding=0, former=0),
    )


def stub_build(monkeypatch, *, name="Acme", calls=None, raises=False):
    async def _fake(settings):
        if calls is not None:
            calls.append(1)
        if raises:
            raise RuntimeError("upstream down")
        return make_response(name)

    monkeypatch.setattr(main, "build_org_response", _fake)


# --- healthz ---------------------------------------------------------------

def test_healthz_reports_no_cache_before_first_build():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cache_age_seconds"] is None


def test_healthz_reports_age_after_build(monkeypatch):
    stub_build(monkeypatch)
    client.get("/api/org")
    body = client.get("/healthz").json()
    assert body["cache_age_seconds"] is not None
    assert body["cache_age_seconds"] >= 0


# --- caching ---------------------------------------------------------------

def test_org_builds_once_then_serves_cache(monkeypatch):
    calls = []
    stub_build(monkeypatch, calls=calls)
    r1 = client.get("/api/org")
    r2 = client.get("/api/org")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["company_name"] == "Acme"
    assert len(calls) == 1  # second request came from cache


def test_refresh_forces_a_rebuild(monkeypatch):
    calls = []
    stub_build(monkeypatch, calls=calls, name="First")
    client.get("/api/org")
    assert len(calls) == 1
    # New data on refresh.
    stub_build(monkeypatch, calls=calls, name="Second")
    r = client.post("/api/refresh")
    assert r.json()["company_name"] == "Second"
    assert len(calls) == 2


# --- error behavior --------------------------------------------------------

def test_503_when_build_fails_and_no_cache(monkeypatch):
    stub_build(monkeypatch, raises=True)
    r = client.get("/api/org")
    assert r.status_code == 503
    assert "detail" in r.json()


def test_serves_stale_cache_when_rebuild_fails(monkeypatch):
    stub_build(monkeypatch, name="Good")
    client.get("/api/org")  # populate cache
    stub_build(monkeypatch, raises=True)
    r = client.post("/api/refresh")  # rebuild fails, but we have stale cache
    assert r.status_code == 200
    assert r.json()["company_name"] == "Good"


# --- GET / (HTML) ----------------------------------------------------------

def test_index_shows_loading_page_when_cold():
    r = client.get("/")
    assert r.status_code == 200
    assert "Building org chart" in r.text
    assert 'id="search"' not in r.text  # search UI only exists in ready mode


def test_index_renders_tree_and_breadcrumb_when_ready():
    boss = Person(id="1", full_name="Kate Roy", job_title="CTO", bucket="current")
    report = Person(id="2", full_name="Anna Muller", job_title="Engineer", bucket="current")
    resp = make_response("Acme")
    resp.company_name = "Acme"
    resp.forest = [TreeNode(person=boss, children=[TreeNode(person=report)])]
    main.cache.store(resp)

    r = client.get("/")
    assert r.status_code == 200
    assert 'id="search"' in r.text
    assert "Acme" in r.text
    assert "Kate Roy" in r.text and "Anna Muller" in r.text
    # the report shows its reporting line (ancestor chain) to its manager
    assert "Kate Roy ›" in r.text
