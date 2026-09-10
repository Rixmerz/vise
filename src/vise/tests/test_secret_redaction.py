"""A credential written into an experience store crosses repositories.

The recorder files a commit subject and body into the *global* store, and
`experience_injector` reads that store back into sessions working on unrelated
projects. `_untrusted` already made the text read as data; nothing made it stop
being a secret. These pin the filter that does — on both writers, because a rule
enforced on one of them is a rule the other reimplements without.
"""
from __future__ import annotations

import pytest

from vise.core import experience_rules as rules

# Fabricated credentials, shaped like the real thing and valid nowhere.
#
# Assembled at import rather than written out, so the source file never holds a
# complete one. GitHub push protection rejected this file when it did — a test
# for secret redaction blocked by secret scanning, on a token that authenticates
# to nothing. Both tools match on shape, which is the whole point of the
# fixtures, so the shape has to exist at runtime and not at rest. Do not inline
# these; the push fails and the remedy on offer is to allowlist a secret.
GITHUB = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
GITHUB_PAT = "github_pat_" + "11ABCDEFG0abcdefghijklmnop"
OPENAI = "sk-" + "ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
SLACK = "xoxb-" + "1234567890-ABCDEFGHIJKLMNO"
JWT = ("eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
       "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk")


@pytest.mark.parametrize("secret", [GITHUB, GITHUB_PAT, OPENAI, AWS, SLACK, JWT])
def test_a_credential_with_no_key_beside_it_is_masked(secret: str) -> None:
    """The larger risk: a token pasted into a commit body, bare."""
    out = rules.redact(f"fix: the deploy was using {secret} directly")
    assert secret not in out
    assert rules.REDACTED in out


def test_a_pem_block_is_masked_to_its_end() -> None:
    body = ("hotfix: remove the inlined key -----BEGIN RSA PRIVATE KEY----- "
            "MIIEowIBAAKCAQEAx7Vn -----END RSA PRIVATE KEY----- from the config")
    out = rules.redact(body)
    assert "MIIEowIBAAKCAQEAx7Vn" not in out
    assert "from the config" in out


@pytest.mark.parametrize("text", [
    "api_key=s3cr3tvalue",
    "API-KEY: s3cr3tvalue",
    "X-Api-Key: 's3cr3tvalue'",
    "the api key = s3cr3tvalue",
    'client_secret="s3cr3tvalue"',
    "PASSWORD=s3cr3tvalue",
    "Authorization: s3cr3tvalue",
])
def test_a_secret_named_by_its_key_is_masked(text: str) -> None:
    """One entry in the suffix list has to cover every way a key is punctuated."""
    out = rules.redact(f"chore: {text} in the env file")
    assert "s3cr3tvalue" not in out
    assert rules.REDACTED in out


def test_url_userinfo_is_masked_but_the_host_survives() -> None:
    """The shape no key-name rule sees, and the one a connection-string fix has."""
    out = rules.redact("fix: point at postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert "admin" not in out
    assert "db.internal:5432/app" in out


@pytest.mark.parametrize("subject", [
    "refactor: rename token to symbol in the lexer",
    "feat: add password reset flow to the account page",
    "chore: bump ruff to 0.6.2 and fix E501 in validators.py",
    "docs: explain why the secret scanner runs on every commit",
    "fix(auth): the session cookie was not being cleared on logout",
    # These two are the ones that pay for the compound-only key list: both put
    # a bare `token:` / `secret:` in key position, which is where a parser error
    # quoted in a commit body and an `area: subject` convention both land.
    "fix(lexer): token: unexpected identifier at line 40",
    "secret: document how the scanner decides what to flag",
])
def test_ordinary_prose_survives_untouched(subject: str) -> None:
    """A filter that mangles commit subjects is one somebody switches off.

    This is the half that makes the other half survivable, which is why the key
    list is compound: a bare `token:` or `secret:` matches all of these.
    """
    assert rules.redact(subject) == subject


def test_a_masked_value_does_not_leave_a_stray_bracket() -> None:
    """Shapes run after key/value, not before.

    The other order redacts the token first, and the key/value value pattern
    then stops at the `]` it just inserted and emits a second one.
    """
    out = rules.redact(f"fix: rotate api_key={GITHUB} after the leak")
    assert out == "fix: rotate api_key=[REDACTED] after the leak"


def test_redaction_runs_before_the_cap_not_after() -> None:
    """A token straddling the cut is the case truncate-first gets wrong.

    Truncating to 200 first leaves a 10-character fragment of the token, which
    is too short for any shape to match — so the fragment is stored in clear.
    """
    from vise.hooks.experience_recorder import _untrusted

    body = ("x" * 190) + " " + GITHUB
    out = _untrusted(body, 200)
    assert "ghp_" not in out
    assert len(out) <= 200


def test_the_commit_hook_redacts_what_it_files() -> None:
    from vise.hooks.experience_recorder import _untrusted

    out = _untrusted(f"fix: drop the hardcoded {AWS} from the uploader")
    assert AWS not in out
    assert rules.REDACTED in out


def test_the_store_redacts_both_prose_fields() -> None:
    """`record` files runtime lessons, which quote error strings verbatim."""
    from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore

    store = ExperienceMemoryStore()
    store.load("global")
    saved = store.record(ExperienceEntry(
        type="run_blocked",
        file_pattern="run:feature-dev:implement",
        domain="runtime",
        description=f"drain_failed in run r1 — 401 from {GITHUB}",
        resolution=f"retried with Authorization: {JWT}",
    ))
    assert GITHUB not in saved.description
    assert JWT not in saved.resolution
    assert rules.REDACTED in saved.description
    assert rules.REDACTED in saved.resolution


def test_redaction_of_empty_text_is_empty() -> None:
    assert rules.redact("") == ""
    assert rules.redact(None) == ""  # type: ignore[arg-type]


def test_a_pair_deep_in_a_long_body_is_still_masked() -> None:
    """The key search runs in a window behind each separator, not from zero."""
    body = ("note: a b c; " * 500) + "client_secret=hunter2seekrit"
    out = rules.redact(body)
    assert "hunter2seekrit" not in out
    assert rules.REDACTED in out


def test_a_large_body_does_not_stall_the_commit_hook() -> None:
    """Searching the whole prefix at every separator is quadratic.

    `experience_recorder` redacts before it caps, so the input here is a commit
    body of no fixed size, inside a hook that runs on every `git commit`.
    Measured on this repo at 104 KB / 8k separators: 20 ms windowed against
    9.6 s searching from zero. The bound below is generous by two orders of
    magnitude against the first and still catches the second.
    """
    import time

    body = ("note: a b c; " * 8000) + "api_key=hunter2seekrit"
    start = time.perf_counter()
    out = rules.redact(body)
    elapsed = time.perf_counter() - start
    assert "hunter2seekrit" not in out
    assert elapsed < 2.0, f"redact took {elapsed:.1f}s on {len(body)} chars"
