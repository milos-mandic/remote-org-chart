"""build_forest — the pure heart of the project.

Takes a list of CURRENT Person models and returns a Forest: sorted root nodes
with recursively nested children, plus an `unassigned` list for everyone who
could not be placed (orphaned manager, cycle member). No I/O: data in, data
out. The only side effect is logging.warning on anomalies, which CLAUDE.md
explicitly asks for.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from app.models import Forest, Person, TreeNode, UnassignedPerson, display_defaults

logger = logging.getLogger("tree")

# Unassigned reasons.
REASON_MANAGER_FORMER = "manager_former"
REASON_MANAGER_UNKNOWN = "manager_unknown"
REASON_CYCLE = "cycle_detected"


def build_forest(people: list[Person], *, known_ids: set[str] | None = None) -> Forest:
    """Build the reporting forest from the CURRENT population.

    `people` is the CURRENT bucket only. `known_ids` is the set of ALL roster
    ids (current + onboarding + former); it lets us tell a manager who is
    FORMER/onboarding (manager_former) apart from one who does not exist at all
    (manager_unknown). When omitted, only the current ids are known, so any
    off-tree manager reads as manager_unknown.
    """
    current_by_id = {p.id: p for p in people}
    current_ids = set(current_by_id)
    known_ids = known_ids or current_ids

    # Nodes pulled out of cycles: each becomes unassigned, which severs the
    # cycle for everyone who reported into it.
    broken = _cycle_break_ids(people, current_ids)

    # Group every person under the parent edge we will actually USE, then build
    # each subtree once. Roots and unassigned are both subtree-roots (used
    # parent == None); the only difference is which collection they land in.
    # Building this way means an orphaned manager keeps their reports instead
    # of dropping them.
    children: dict[str, list[Person]] = defaultdict(list)
    subtree_roots: list[Person] = []
    for p in people:
        parent = _used_parent(p, current_ids, broken)
        if parent is None:
            subtree_roots.append(p)
        else:
            _crosscheck_name(p, current_by_id[parent])
            children[parent].append(p)

    roots: list[TreeNode] = []
    unassigned: list[UnassignedPerson] = []
    for p in subtree_roots:
        node = _build_node(p, children)
        reason = _root_reason(p, current_ids, known_ids, broken)
        if reason is None:
            roots.append(node)
        else:
            unassigned.append(
                UnassignedPerson(person=node.person, reason=reason, children=node.children)
            )

    roots.sort(key=lambda n: n.person.full_name.casefold())
    unassigned.sort(key=lambda u: u.person.full_name.casefold())
    return Forest(roots=roots, unassigned=unassigned)


def _used_parent(p: Person, current_ids: set[str], broken: set[str]) -> str | None:
    """The parent edge to actually build with, or None if p is a subtree root.

    Returns None (subtree root) when: p was removed to break a cycle; p has no
    manager or points at itself; p's manager was removed to break a cycle; or
    p's manager is off-tree (former/unknown). Otherwise returns the current
    manager's id.
    """
    if p.id in broken:
        return None
    mgr = p.manager_employment_id
    if mgr is None or mgr == p.id:
        if mgr == p.id:
            logger.warning("self-referential manager on %s (%s); treating as root", p.id, p.full_name)
        return None
    if mgr in broken:
        return None  # manager removed by cycle-break → this person becomes a root
    if mgr in current_ids:
        return mgr
    return None  # orphan: manager is former or unknown


def _root_reason(p: Person, current_ids: set[str], known_ids: set[str], broken: set[str]) -> str | None:
    """Why this subtree root is unassigned, or None if it is a legitimate root."""
    if p.id in broken:
        return REASON_CYCLE
    mgr = p.manager_employment_id
    if mgr is None or mgr == p.id or mgr in broken or mgr in current_ids:
        return None  # true root, or root severed from a cycle
    if mgr in known_ids:
        return REASON_MANAGER_FORMER
    return REASON_MANAGER_UNKNOWN


def _build_node(p: Person, children: dict[str, list[Person]]) -> TreeNode:
    """Build a node and its subtree, sorted by full_name at every level."""
    kids = [_build_node(c, children) for c in children.get(p.id, [])]
    kids.sort(key=lambda n: n.person.full_name.casefold())
    return TreeNode(person=display_defaults(p), children=kids)


def _cycle_break_ids(people: list[Person], current_ids: set[str]) -> set[str]:
    """Find every reporting cycle and return the id to remove from each.

    The graph is functional (each node has at most one manager), so cycles are
    simple. We break each deterministically by removing its lowest-sorting id;
    picking the min id makes the outcome independent of input order.
    """
    parent: dict[str, str | None] = {}
    for p in people:
        mgr = p.manager_employment_id
        # A self-reference or an off-tree manager is not a cycle edge.
        parent[p.id] = mgr if (mgr in current_ids and mgr != p.id) else None

    broken: set[str] = set()
    settled: set[str] = set()  # ids whose chain has been fully resolved

    for start in parent:
        if start in settled:
            continue
        path: list[str] = []
        seen_at: dict[str, int] = {}
        node: str | None = start
        while node is not None and node not in settled:
            if node in seen_at:
                cycle = path[seen_at[node]:]
                broken.add(min(cycle))
                logger.warning("reporting cycle detected among %s; breaking at %s", cycle, min(cycle))
                break
            seen_at[node] = len(path)
            path.append(node)
            node = parent[node]
        settled.update(path)

    return broken


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _crosscheck_name(person: Person, manager: Person) -> None:
    """Warn if the `manager` display string disagrees with the resolved name.

    The link itself is authoritative (manager_employment_id); this is a sanity
    check only. The `manager` string is often a shortened form of the full
    name ("Pablo Navarro" vs "Pablo Navarro Jimenez"), so we compare on token
    containment rather than exact equality to avoid noise from formatting.
    """
    claimed = person.manager_name
    if not claimed:
        return
    a = set(_TOKEN_RE.findall(claimed.casefold()))
    b = set(_TOKEN_RE.findall(manager.full_name.casefold()))
    if a and b and not (a <= b or b <= a):
        logger.warning(
            "manager name/id mismatch for %s: link resolves to %r but record says %r",
            person.full_name,
            manager.full_name,
            claimed,
        )
