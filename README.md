# Remote org chart

I pull employee data from Remote's sandbox API, rebuild the reporting hierarchy from it,
and render it as a searchable, collapsible org chart. It's a small FastAPI app with a
single server-rendered page — no frontend framework, no database, everything held in
memory.

**Live:** https://remote-org-chart.onrender.com

## Running it locally

You'll need Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# put your sandbox token in .env:  REMOTE_API_TOKEN=ra_test_...

uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000.

One heads-up on the first load: the app has to fetch every employee's detail record to
work out who reports to whom, which takes 15–60 seconds. You'll see a "Building org
chart…" spinner and the page reloads itself when it's ready. After that it's cached for 15
minutes, so it's instant. There's a Refresh button if you want to force a rebuild.

Tests: `pytest` — 40 of them, mostly around the tree-building and classification logic.

## How it works

The flow is: fetch → strip down to a safe model → classify → build the tree → cache →
render.

- `remote_client.py` does the API calls. It pages through the employee list, then fetches
  each person's detail concurrently (max 8 at a time, with retries on rate limits and
  5xx). As soon as a detail response comes back I map it to a minimal model and throw the
  raw response away — more on why in the privacy section.
- `models.py` has that minimal `Person` model plus the classification logic.
- `tree.py` is the interesting part: a pure function that takes the list of people and
  returns the tree, plus anyone who couldn't be placed. No I/O, so it's easy to test, and
  it's where all the edge cases get handled.
- `main.py` wires up the routes and the cache.
- The frontend is one Jinja template that renders the whole tree server-side as nested
  `<details>` elements, with one small JS file for search and the profile panel.

Routes: `GET /` (the page), `GET /api/org` (the chart as JSON), `POST /api/refresh`
(rebuild), and `GET /healthz` (health check for the host).

## The API didn't match the docs

A few things I only found by poking at the live API rather than trusting the documentation:

- The base URL is `https://gateway.remote-sandbox.com` and the routes are under `/v1/...`.
  The docs pointed me at the wrong host and an `/api` prefix that doesn't exist.
- There are two manager fields and it matters which one you use. `manager_employment_id`
  is the one that links to another employment record — that's what I build the tree from.
  There's also a `manager_id`, which is a different (user-level) ID that doesn't match any
  employment. Use it by mistake and you get a silently broken chart. I checked a couple of
  records by hand to be sure I had the right one.
- The list endpoint has no manager info at all, so there's no way around fetching each
  person's detail.

## Handling the messy data

The sandbox has some deliberately contradictory records — people marked `active` but also
`offboarded` with a termination date in the past. So I sort everyone into one of three
buckets, with "former" beating "onboarding" beating "current":

- **Former:** archived, or offboarded, or a termination date before today.
- **Onboarding:** invited/created/initiated and not already former.
- **Current:** everyone else.

Only current people go in the main chart. Onboarding and former get their own smaller
sections lower down, since they aren't part of the live reporting structure. When a record
is contradictory (former but still says active) I flag it and show a small warning badge
instead of quietly picking a side.

The tree builder deals with the usual mess: several people with no manager (so it's a
forest, not one tree), managers that don't resolve (former or missing — those people go to
an "unassigned" list with a reason rather than being dropped), reporting cycles (broken in
a deterministic way), self-references, and missing titles/departments. Each of these has a
test. One bug I'm glad I caught: an early version dropped people whose manager was itself
unassigned. A simple accounting check — does everyone who went in come out either placed
or unassigned? — surfaced 12 missing people.

## Privacy

The detail endpoint returns a lot of sensitive data: bank details, national IDs, salary,
home address, birthdate. I didn't want any of that near the cache, the logs, or the
browser.

The minimal `Person` model only holds the fields I actually need to draw the chart — name,
title, department, country, manager link, status. Everything else in the raw response just
never gets read, and the raw payload is dropped right after mapping. The model has no field
that could hold PII, the logs only contain IDs, and the raw dumps I saved while exploring
the API are gitignored. I also checked the rendered HTML to make sure nothing leaks
through.

Avatars are just initials with a color derived from the name — the API has no photos, and I
didn't want to introduce any image data anyway.

## On using AI

I built this with Claude Code and leaned on it deliberately, since the brief asks to see
that.

The way I ran it: I wrote a plan up front and worked through it in phases, reviewing at the
end of each one instead of letting it run off on its own. I had it do the mechanical,
well-specified parts — the API client, the tree builder and its tests, the classification
rules, the routes, the frontend — and I made the design and judgment calls: the privacy
model, the scope, what to cut. The discovery work (checking real field names against the
live API instead of trusting the docs) was AI-driven too.

A couple of things I decided against: a React frontend, which is overkill for one
read-only page, and a fancier top-down boxes-and-lines chart that I prototyped and then
removed — it needed pan/zoom to be usable at this org's width and fought the search
behavior, and the brief explicitly says not to over-engineer. The collapsible tree is the
better fit here.

## Deploying

It runs on Render as a web service (`render.yaml` is in the repo). It has to be a
long-running process rather than serverless, because the built chart lives in an in-memory
cache behind that slow first fetch — on serverless every cold start would re-fetch from
scratch. The token goes in as an environment variable in the dashboard, not in the repo.
Python is pinned to 3.12 via `.python-version`; Render otherwise defaulted to 3.14, which
couldn't install pydantic.

One quirk of the free tier: it sleeps after about 15 minutes of no traffic, so the first
request after that is slow (container wake plus the cold fetch). To keep it warm during
review there's a small GitHub Actions job (`.github/workflows/keep-warm.yml`) that pings
the app every 10 minutes.

## What I'd add with more time

- Authentication — right now the chart is public.
- Move the refresh to a background job instead of doing it inside a request, and push
  updates rather than reloading.
- A shared cache (Redis) if it ever ran on more than one instance.
- Nicer profiles and richer interaction — and at that point a component framework would
  start to pull its weight. For a static, read-only page it doesn't.
