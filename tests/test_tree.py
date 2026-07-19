"""Tests for build_forest — every edge case listed in CLAUDE.md."""

import logging

from app.models import Person
from app.tree import (
    REASON_CYCLE,
    REASON_MANAGER_FORMER,
    REASON_MANAGER_UNKNOWN,
    build_forest,
)


def person(id, name=None, manager=None, manager_name=None, **kw) -> Person:
    return Person(
        id=id,
        full_name=name or f"Person {id}",
        manager_employment_id=manager,
        manager_name=manager_name,
        **kw,
    )


def reasons(forest) -> dict[str, str]:
    return {u.person.id: u.reason for u in forest.unassigned}


def child_ids(node) -> list[str]:
    return [c.person.id for c in node.children]


# --- Multiple roots → forest ----------------------------------------------

def test_multiple_roots_form_a_forest():
    people = [person("a"), person("b"), person("c", manager="a")]
    forest = build_forest(people)
    assert [n.person.id for n in forest.roots] == ["a", "b"]
    a = next(n for n in forest.roots if n.person.id == "a")
    assert child_ids(a) == ["c"]
    assert forest.unassigned == []


def test_nested_children_three_levels_deep():
    people = [
        person("a"),
        person("b", manager="a"),
        person("c", manager="b"),
    ]
    forest = build_forest(people)
    a = forest.roots[0]
    assert child_ids(a) == ["b"]
    assert child_ids(a.children[0]) == ["c"]


# --- Sorting by full_name at every level ----------------------------------

def test_sorted_by_full_name_at_every_level():
    people = [
        person("root", name="Root"),
        person("z", name="Zoe", manager="root"),
        person("a", name="Amy", manager="root"),
        person("m", name="Max", manager="root"),
    ]
    forest = build_forest(people)
    assert [c.person.full_name for c in forest.roots[0].children] == ["Amy", "Max", "Zoe"]


def test_roots_sorted_and_unassigned_sorted():
    people = [
        person("r2", name="Bob"),
        person("r1", name="Alice"),
        person("o1", name="Yan", manager="ghost"),
        person("o2", name="Xena", manager="ghost"),
    ]
    forest = build_forest(people)
    assert [n.person.full_name for n in forest.roots] == ["Alice", "Bob"]
    assert [u.person.full_name for u in forest.unassigned] == ["Xena", "Yan"]


# --- Orphaned references ---------------------------------------------------

def test_orphan_manager_unknown_when_manager_not_in_roster():
    people = [person("a", manager="does-not-exist")]
    forest = build_forest(people, known_ids={"a"})
    assert reasons(forest) == {"a": REASON_MANAGER_UNKNOWN}
    assert forest.roots == []


def test_orphan_manager_former_when_manager_off_current_but_in_roster():
    # "former" points at someone who exists in the roster but is not CURRENT.
    people = [person("a", manager="former")]
    forest = build_forest(people, known_ids={"a", "former"})
    assert reasons(forest) == {"a": REASON_MANAGER_FORMER}


def test_orphans_are_not_silently_promoted_or_dropped():
    people = [person("a", manager="ghost"), person("b")]
    forest = build_forest(people, known_ids={"a", "b"})
    # b is a legit root; a is neither dropped nor promoted to root.
    assert [n.person.id for n in forest.roots] == ["b"]
    assert reasons(forest) == {"a": REASON_MANAGER_UNKNOWN}


def test_reports_under_an_orphaned_manager_are_kept_not_dropped():
    # x -> y -> (former). y is orphaned (manager_former); x reports to y.
    # x must NOT vanish: it stays nested under y in the unassigned section.
    people = [
        person("y", manager="gone"),
        person("x", manager="y"),
    ]
    forest = build_forest(people, known_ids={"x", "y", "gone"})
    assert forest.roots == []
    assert len(forest.unassigned) == 1
    y = forest.unassigned[0]
    assert y.person.id == "y"
    assert y.reason == REASON_MANAGER_FORMER
    assert [c.person.id for c in y.children] == ["x"]


def test_everyone_is_accounted_for():
    # No person may be dropped: placed-in-tree + unassigned-subtrees == input.
    people = [
        person("root"),
        person("a", manager="root"),
        person("b", manager="orphan-mgr"),   # orphan subtree root
        person("c", manager="b"),            # nested under an orphan
        person("d", manager="d"),            # self-ref -> root
    ]
    forest = build_forest(people, known_ids={p.id for p in people} | {"orphan-mgr"})

    def count(nodes):
        return sum(1 + count(n.children) for n in nodes)

    # unassigned entries count as 1 person each (the person) plus their children
    total = count(forest.roots) + len(forest.unassigned) + sum(count(u.children) for u in forest.unassigned)
    assert total == len(people)


# --- Cycles ----------------------------------------------------------------

def test_two_node_cycle_broken_at_lowest_id():
    # a <-> b: lowest id (a) goes unassigned, b becomes a root.
    people = [person("b", manager="a"), person("a", manager="b")]
    forest = build_forest(people)
    assert reasons(forest) == {"a": REASON_CYCLE}
    assert [n.person.id for n in forest.roots] == ["b"]
    assert child_ids(forest.roots[0]) == []  # a is unassigned, not under b


def test_three_node_cycle_broken_at_lowest_id():
    # a->b->c->a ; break at a. c becomes root, b hangs under c.
    people = [
        person("a", manager="b"),
        person("b", manager="c"),
        person("c", manager="a"),
    ]
    forest = build_forest(people)
    assert reasons(forest) == {"a": REASON_CYCLE}
    assert [n.person.id for n in forest.roots] == ["c"]
    assert child_ids(forest.roots[0]) == ["b"]


def test_cycle_logs_warning(caplog):
    people = [person("b", manager="a"), person("a", manager="b")]
    with caplog.at_level(logging.WARNING, logger="tree"):
        build_forest(people)
    assert any("cycle detected" in r.message for r in caplog.records)


# --- Self-reference --------------------------------------------------------

def test_self_reference_treated_as_root_with_warning(caplog):
    people = [person("a", manager="a")]
    with caplog.at_level(logging.WARNING, logger="tree"):
        forest = build_forest(people)
    assert [n.person.id for n in forest.roots] == ["a"]
    assert forest.unassigned == []
    assert any("self-referential" in r.message for r in caplog.records)


# --- Missing fields → display defaults, never crash ------------------------

def test_null_job_title_and_department_get_defaults():
    people = [person("a", job_title=None, department=None)]
    forest = build_forest(people)
    node = forest.roots[0]
    assert node.person.job_title == "—"
    assert node.person.department == "No department"


def test_defaults_applied_to_unassigned_too():
    people = [person("a", manager="ghost", job_title=None, department=None)]
    forest = build_forest(people, known_ids={"a"})
    u = forest.unassigned[0]
    assert u.person.job_title == "—"
    assert u.person.department == "No department"


# --- Name / id cross-check -------------------------------------------------

def test_name_mismatch_logs_warning(caplog):
    people = [
        person("mgr", name="Kate Roy"),
        person("emp", name="Anna", manager="mgr", manager_name="Someone Else"),
    ]
    with caplog.at_level(logging.WARNING, logger="tree"):
        build_forest(people)
    assert any("name/id mismatch" in r.message for r in caplog.records)


def test_shortened_name_does_not_warn(caplog):
    # "Pablo Navarro" is a shortened form of "Pablo Navarro Jimenez" — no warning.
    people = [
        person("mgr", name="Pablo Navarro Jimenez"),
        person("emp", name="Emp", manager="mgr", manager_name="Pablo Navarro"),
    ]
    with caplog.at_level(logging.WARNING, logger="tree"):
        build_forest(people)
    assert not any("name/id mismatch" in r.message for r in caplog.records)


def test_matching_name_does_not_warn(caplog):
    people = [
        person("mgr", name="Kate Roy"),
        person("emp", name="Anna", manager="mgr", manager_name="Kate Roy"),
    ]
    with caplog.at_level(logging.WARNING, logger="tree"):
        build_forest(people)
    assert not any("name/id mismatch" in r.message for r in caplog.records)
