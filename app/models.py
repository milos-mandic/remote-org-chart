"""Internal data models.

The Person model is the ONLY representation of an employee that is allowed to
leave remote_client. Detail responses from the Remote API contain heavy PII
(bank accounts, national IDs, salary, birthdate, home address, emergency
contacts). We map each raw response into a Person and discard the raw payload
immediately, so nothing sensitive is ever cached, logged, or serialized to the
frontend. See CLAUDE.md "PRIVACY — non-negotiable".
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

# Classification buckets (see CLAUDE.md "Employee classification rules").
BUCKET_CURRENT = "current"
BUCKET_ONBOARDING = "onboarding"
BUCKET_FORMER = "former"


class Person(BaseModel):
    """Minimal, privacy-safe employee model. No PII fields exist here."""

    id: str
    full_name: str
    job_title: str | None = None
    department: str | None = None
    country_code: str | None = None
    employment_model: str | None = None
    status: str | None = None
    employment_lifecycle_stage: str | None = None
    termination_date: str | None = None
    # THE tree-building link. Joins to another employment's `id`.
    manager_employment_id: str | None = None
    # The raw `manager` display string, kept ONLY for the name/id cross-check
    # in the tree builder. Never used to build the tree.
    manager_name: str | None = None

    # Derived by classify(). Not fetched. Present so the frontend gets bucket
    # + inconsistency flag alongside the display fields.
    bucket: str | None = None
    data_inconsistent: bool = False

    @classmethod
    def from_detail(cls, emp: dict) -> "Person":
        """Map an unwrapped detail employment object into a Person.

        Extracts only the whitelisted fields; every other key in `emp`
        (bank_account_details, personal_details, address_details, ...) is
        dropped by never being read. The caller must not retain `emp`.
        """
        return cls(
            id=emp["id"],
            full_name=emp.get("full_name") or "",
            job_title=emp.get("job_title"),
            department=_department_name(emp.get("department")),
            country_code=_alpha2(emp.get("country")),
            employment_model=emp.get("employment_model"),
            status=emp.get("status"),
            employment_lifecycle_stage=emp.get("employment_lifecycle_stage"),
            termination_date=emp.get("termination_date"),
            manager_employment_id=emp.get("manager_employment_id"),
            manager_name=emp.get("manager"),
        )

    @classmethod
    def from_list_item(cls, item: dict) -> "Person":
        """Map a list-endpoint item into a Person.

        Used as the fail-soft fallback when a detail fetch fails: list items
        carry name/title/department/country/status but NO manager fields, so
        the person appears in the chart with manager unknown.
        """
        return cls(
            id=item["id"],
            full_name=item.get("full_name") or "",
            job_title=item.get("job_title"),
            department=_department_name(item.get("department")),
            country_code=_alpha2(item.get("country")),
            employment_model=item.get("employment_model"),
            status=item.get("status"),
            employment_lifecycle_stage=item.get("employment_lifecycle_stage"),
            termination_date=item.get("termination_date"),
            manager_employment_id=None,
            manager_name=None,
        )


class FetchError(BaseModel):
    """A detail fetch that ultimately failed; person kept via list fallback."""

    id: str
    error: str


class TreeNode(BaseModel):
    """A person plus their (recursively nested) direct reports."""

    person: Person
    children: list["TreeNode"] = []


class UnassignedPerson(BaseModel):
    """A current person we could not attach to a root, with the reason why.

    Carries `children`: an orphaned manager still keeps their own reports, so
    the whole disconnected subtree stays visible instead of being dropped.
    """

    person: Person
    reason: str  # manager_former | manager_unknown | cycle_detected
    children: list[TreeNode] = []


class Forest(BaseModel):
    """Output of build_forest: the roots plus everyone we couldn't place."""

    roots: list[TreeNode] = []
    unassigned: list[UnassignedPerson] = []


TreeNode.model_rebuild()  # resolve the self-referential `children` type


class SourceCounts(BaseModel):
    total: int
    current: int
    onboarding: int
    former: int


class OrgResponse(BaseModel):
    """The full /api/org payload — the only thing served to the frontend."""

    generated_at: str
    company_name: str | None = None
    source_counts: SourceCounts
    forest: list[TreeNode] = []          # CURRENT main chart (root nodes)
    unassigned: list[UnassignedPerson] = []
    onboarding: list[Person] = []        # separate "starting soon" section
    former: list[Person] = []            # collapsed flat list at the bottom
    fetch_errors: list[FetchError] = []


def display_defaults(p: Person) -> Person:
    """Return a copy with render-safe defaults for nullable display fields.

    Applied at the boundary so no null ever reaches the template. Keeps the
    fetched values untouched on the original model.
    """
    return p.model_copy(
        update={
            "job_title": p.job_title or "—",
            "department": p.department or "No department",
        }
    )


def _department_name(dept) -> str | None:
    """department is a plain string in this API; guard for the odd null/dict."""
    if isinstance(dept, str):
        return dept or None
    if isinstance(dept, dict):
        return dept.get("name")
    return None


def _alpha2(country) -> str | None:
    """country is an object; the 2-letter alpha_2_code is our country_code."""
    if isinstance(country, dict):
        return country.get("alpha_2_code") or country.get("code")
    if isinstance(country, str):
        return country
    return None


# Status/lifecycle value sets that signal onboarding (see CLAUDE.md rules).
_ONBOARDING_STATUS = {"invited", "created", "initiated"}
_ONBOARDING_LIFECYCLE = {"employee_self_enrollment", "employment_creation"}


def classify(p: Person, today: date | None = None) -> tuple[str, bool]:
    """Classify a person into exactly one bucket; flag planted inconsistencies.

    Precedence is FORMER > ONBOARDING > CURRENT — the sandbox plants records
    that satisfy several signals at once (e.g. status=active yet
    lifecycle=offboarded), and the classification rules resolve them in that
    order. Returns (bucket, data_inconsistent).

    `today` is injectable so tests are deterministic; defaults to today.
    """
    today = today or date.today()

    is_former = (
        p.status == "archived"
        or p.employment_lifecycle_stage == "offboarded"
        or _terminated_in_past(p.termination_date, today)
    )
    is_onboarding = (
        p.status in _ONBOARDING_STATUS
        or p.employment_lifecycle_stage in _ONBOARDING_LIFECYCLE
    )

    if is_former:
        # Contradiction: former by lifecycle/termination yet still status=active.
        # This is the deliberately planted inconsistency; surface it, don't hide.
        return BUCKET_FORMER, p.status == "active"
    if is_onboarding:
        return BUCKET_ONBOARDING, False
    return BUCKET_CURRENT, False


def _terminated_in_past(termination_date: str | None, today: date) -> bool:
    """True if there is a valid termination date strictly before today.

    Tolerates date or datetime strings ("2024-01-15", "2024-01-15T00:00:00Z")
    and never raises on a malformed value — an unparseable date is ignored.
    """
    if not termination_date:
        return False
    try:
        parsed = date.fromisoformat(termination_date[:10])
    except ValueError:
        return False
    return parsed < today
