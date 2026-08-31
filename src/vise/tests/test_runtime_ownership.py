"""Ownership decides what may run in parallel, so its errors are asymmetric.

A false conflict costs wall-clock. A missed conflict costs a working tree with
two agents' half-edits in it. These tests pin the asymmetry: several of them
assert a conflict for a pair a naive matcher would clear, and that is the point
rather than an over-strict implementation to be relaxed later.
"""
from __future__ import annotations

import pytest

from vise.runtime import ownership as own


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (["src/auth/**"], ["web/src/login/**"], False),
        (["src/a/**"], ["src/b/**"], False),
        (["docs/*.md"], ["docs/api.rst"], False),
        (["tests/**"], ["src/**"], False),
        # subtree containment
        (["src/**"], ["src/auth/token.py"], True),
        (["src/auth/**"], ["src/**"], True),
        # bare directory versus a file inside it
        (["src/auth"], ["src/auth/token.py"], True),
        # both patterns wildcarded, no literal containment either way
        (["a*"], ["*b"], True),
        (["src/*/models.py"], ["src/auth/*.py"], True),
        (["docs/*.md"], ["docs/api.md"], True),
        # identical claims
        (["src/x.py"], ["src/x.py"], True),
    ],
)
def test_conflict_matrix(a, b, expected):
    assert own.conflicts(a, b) is expected
    assert own.conflicts(b, a) is expected, "conflict must be symmetric"


def test_an_undeclared_claim_owns_everything():
    """The safe reading. The unsafe one is 'claims nothing, conflicts with nothing'."""
    assert own.normalize([]) == own.OWNS_EVERYTHING
    assert own.conflicts([], ["anything/at/all.py"]) is True


def test_a_trailing_slash_means_the_whole_subtree():
    assert own.normalize(["src/auth/"]) == ("src/auth/**",)
    assert own.conflicts(["src/auth/"], ["src/auth/deep/nested/x.py"]) is True


def test_normalize_strips_leading_dot_slash_and_root_slash():
    assert own.normalize(["./src/x.py", "/etc/y"]) == ("src/x.py", "etc/y")


def test_a_character_class_is_read_as_could_be_anything():
    """Reading '[abc]' literally would clear a pair that can collide."""
    assert own.conflicts(["src/[abc].py"], ["src/a.py"]) is True


def test_matches_decides_whether_a_concrete_path_is_owned():
    assert own.matches("src/auth/token.py", ["src/auth/**"]) is True
    assert own.matches("src/db/schema.py", ["src/auth/**"]) is False


def test_escaped_names_the_paths_rather_than_counting_them():
    escaped = own.escaped(
        ["src/auth/a.py", "src/db/b.py", "README.md"], ["src/auth/**"]
    )
    assert escaped == ("src/db/b.py", "README.md")


def test_escaped_is_empty_when_everything_stayed_inside():
    assert own.escaped(["src/auth/a.py"], ["src/auth/**"]) == ()


def test_partition_groups_only_non_conflicting_tasks():
    groups = own.partition({
        "auth": ["src/auth/**"],
        "web": ["web/**"],
        "everything": ["src/**"],
    })
    flat = [t for g in groups for t in g]
    assert sorted(flat) == ["auth", "everything", "web"]
    for group in groups:
        for i, x in enumerate(group):
            for y in group[i + 1:]:
                assert not own.conflicts(
                    {"auth": ["src/auth/**"], "web": ["web/**"], "everything": ["src/**"]}[x],
                    {"auth": ["src/auth/**"], "web": ["web/**"], "everything": ["src/**"]}[y],
                )


def test_partition_is_stable_across_calls():
    """Greedy, not optimal — but a grouping that shifts between runs is worse."""
    claims = {"a": ["x/**"], "b": ["x/y.py"], "c": ["z/**"]}
    assert own.partition(claims) == own.partition(claims)


# --- the bare-directory claim: admission and refusal must agree -----------


def test_matches_and_conflicts_agree_on_a_bare_directory():
    """`conflicts` reads `src/auth` as the subtree; `matches` must too.

    When they disagree, admission control lets the task in on the strength of
    the subtree reading and the honesty gate then refuses its every write on
    the strength of the other one. The task cannot succeed at anything.
    """
    assert own.conflicts(["src/auth"], ["src/auth/token.py"])
    assert own.matches("src/auth/token.py", ["src/auth"])


@pytest.mark.parametrize("claim", ["src/auth", "src/auth/", "src/auth/**"])
def test_a_write_inside_a_claimed_directory_never_escapes(claim):
    assert own.escaped(["src/auth/token.py"], [claim]) == ()
    assert own.escaped(["src/auth/deep/nested/thing.py"], [claim]) == ()


def test_a_bare_directory_claim_still_refuses_a_sibling():
    """Widening the claim must not widen it past the directory named."""
    assert own.escaped(["src/authz/token.py"], ["src/auth"]) == ("src/authz/token.py",)
    assert own.escaped(["src/other.py"], ["src/auth"]) == ("src/other.py",)


def test_a_bare_directory_claim_does_not_match_its_own_prefix():
    """`src/auth` is a claim on what is under it, not on `src` itself."""
    assert not own.matches("src", ["src/auth"])
    assert not own.matches("src/app.py", ["src/auth"])
