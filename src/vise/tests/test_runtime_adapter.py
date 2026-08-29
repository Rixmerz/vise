"""The adapter is the only module that can spend money, so every decision about
whether to spend it is tested without doing so.

The two rules with teeth: changed paths come from git rather than the model, and
a reply with no verdict block is inconclusive rather than a pass.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vise.runtime.adapters.claude_code import (
    RESULT_FENCE,
    AdapterError,
    ClaudeCodeWorker,
    extract_result_block,
)
from vise.runtime.contracts import (
    FailureKind,
    TaskBrief,
    TaskBudget,
    Verdict,
)


def _brief(**kw) -> TaskBrief:
    base = dict(run_id="r1", task_id="auth", name="JWT", role="backend",
                model="sonnet", effort="medium",
                acceptance=("an expired token is rejected with 401",))
    base.update(kw)
    return TaskBrief(**base)


def _envelope(result_text: str, **kw) -> str:
    payload = {
        "type": "result", "is_error": False, "num_turns": 3,
        "duration_ms": 4200, "total_cost_usd": 0.42,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "result": result_text,
    }
    payload.update(kw)
    return json.dumps(payload)


def _block(**fields) -> str:
    body = {"verdict": "pass", "summary": "done", "evidence": "$ pytest\n1 passed",
            "checks": "$ ruff\nok"}
    body.update(fields)
    return f"Some prose first.\n\n```{RESULT_FENCE}\n{json.dumps(body)}\n```"


def _worker(stdout="", returncode=0, stderr="", **kw) -> ClaudeCodeWorker:
    def runner(argv, **_):
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    kw.setdefault("runner", runner)
    return ClaudeCodeWorker(**kw)


# --- argv -----------------------------------------------------------------


def test_argv_carries_the_routed_model_and_effort():
    argv = _worker().build_argv(_brief(model="opus", effort="high"))
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"


def test_argv_always_carries_a_turn_ceiling():
    """A task with no turn limit that misunderstands its brief spends the run's
    budget discovering that, and the first sign is the bill."""
    assert "--max-turns" in _worker().build_argv(_brief())


def test_a_task_budget_overrides_the_adapter_turn_ceiling():
    brief = _brief(budget=TaskBudget(max_turns=3))
    argv = _worker(max_turns=50).build_argv(brief)
    assert argv[argv.index("--max-turns") + 1] == "3"


def test_permissions_are_not_widened_by_default():
    """An orchestrator that hands every worker --dangerously-skip-permissions
    has removed the one check a human still had."""
    argv = _worker().build_argv(_brief())
    assert not any("permission" in a or "dangerously" in a for a in argv)


def test_a_permission_mode_is_passed_through_when_set():
    argv = _worker(permission_mode="acceptEdits").build_argv(_brief())
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_the_prompt_carries_the_brief_and_the_answer_contract():
    prompt = _worker().compose_prompt(_brief())
    assert "an expired token is rejected with 401" in prompt
    assert RESULT_FENCE in prompt
    assert "Do not list the files you changed" in prompt


def test_blocked_tools_reach_the_prompt():
    prompt = _worker().compose_prompt(_brief(tools_blocked=("WebFetch",)))
    assert "may not use these tools: WebFetch" in prompt


def test_every_dispatch_is_recorded_for_explain():
    worker = _worker(stdout=_envelope(_block()))
    worker.run(_brief())
    assert len(worker.calls) == 1 and worker.calls[0][0] == "claude"


# --- the result block -----------------------------------------------------


def test_a_well_formed_block_is_parsed():
    result = _worker(stdout=_envelope(_block())).run(_brief())
    assert result.verdict is Verdict.PASS
    assert "1 passed" in result.evidence
    assert result.usage.cost_usd == pytest.approx(0.42)
    assert result.usage.tokens_in == 1200
    assert result.usage.wall_time_s == pytest.approx(4.2)


def test_a_missing_block_is_inconclusive_not_a_pass():
    """Reading a zero exit as success makes a model that ignored its
    instructions indistinguishable from one that succeeded."""
    result = _worker(stdout=_envelope("I did the thing! Everything works.")).run(_brief())
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "no vise-result block" in result.summary


def test_two_blocks_are_inconclusive_because_nothing_decided():
    text = _block() + "\n" + _block(verdict="fail")
    assert extract_result_block(text) is None


def test_an_unparseable_block_is_inconclusive():
    text = f"```{RESULT_FENCE}\n{{not json}}\n```"
    assert extract_result_block(text) is None


def test_an_unknown_verdict_string_is_inconclusive():
    result = _worker(stdout=_envelope(_block(verdict="probably fine"))).run(_brief())
    assert result.verdict is Verdict.INCONCLUSIVE


def test_a_failure_classification_is_read_when_present():
    result = _worker(stdout=_envelope(
        _block(verdict="fail", classification="test_bug")
    )).run(_brief())
    assert result.classification is FailureKind.TEST_BUG


def test_a_classification_on_a_pass_is_ignored():
    result = _worker(stdout=_envelope(_block(classification="code_bug"))).run(_brief())
    assert result.classification is None


def test_artifacts_are_lifted_out_of_the_block():
    result = _worker(stdout=_envelope(_block(
        artifacts=[{"kind": "research", "payload": {"n": 1}}]
    ))).run(_brief())
    assert [a.kind for a in result.artifacts] == ["research"]
    assert result.artifacts[0].payload == {"n": 1}


def test_a_verifier_artifact_is_addressed_to_the_task_not_the_verify_id():
    result = _worker(stdout=_envelope(_block(
        artifacts=[{"kind": "verification", "payload": {"verdict": "pass"}}]
    ))).run(_brief(task_id="auth::verify"))
    assert result.artifacts[0].task_id == "auth"


def test_a_malformed_artifact_entry_is_dropped_not_fatal():
    result = _worker(stdout=_envelope(_block(
        artifacts=["nonsense", {"payload": {}}, {"kind": "plan", "payload": {"k": 1}}]
    ))).run(_brief())
    assert [a.kind for a in result.artifacts] == ["plan"]


# --- envelopes and failures ----------------------------------------------


def test_no_json_envelope_is_an_environment_failure():
    result = _worker(stdout="not json at all", stderr="boom", returncode=1).run(_brief())
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.classification is FailureKind.ENVIRONMENT_BUG
    assert "boom" in result.summary


def test_an_errored_session_is_an_environment_failure():
    result = _worker(stdout=_envelope("", is_error=True, subtype="max_turns")).run(_brief())
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.classification is FailureKind.ENVIRONMENT_BUG
    assert "max_turns" in result.summary


def test_a_stream_of_json_lines_uses_the_last_object():
    stdout = '{"type":"assistant"}\n' + _envelope(_block())
    assert _worker(stdout=stdout).run(_brief()).verdict is Verdict.PASS


def test_a_json_array_envelope_uses_the_last_object():
    stdout = json.dumps([{"type": "assistant"}, json.loads(_envelope(_block()))])
    assert _worker(stdout=stdout).run(_brief()).verdict is Verdict.PASS


def test_a_timeout_is_inconclusive_and_classified_as_environment():
    def runner(argv, **_):
        raise subprocess.TimeoutExpired(argv, 5)

    result = ClaudeCodeWorker(runner=runner, timeout_s=5).run(_brief())
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.classification is FailureKind.ENVIRONMENT_BUG
    assert "timeout" in result.summary


def test_a_missing_cli_is_a_configuration_error_not_a_failed_task():
    """Degrading it to a task failure spends the ladder proving `claude` is
    still not installed."""
    def runner(argv, **_):
        raise FileNotFoundError(argv[0])

    with pytest.raises(AdapterError):
        ClaudeCodeWorker(runner=runner).run(_brief())


# --- changed paths come from git -----------------------------------------


def _repo(tmp_path: Path) -> Path:
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "seed.txt").write_text("s\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


def test_changed_paths_are_read_from_git_not_from_the_model(tmp_path):
    """The one party being checked must not supply the evidence."""
    repo = _repo(tmp_path)

    def runner(argv, **_):
        (repo / "src_written.py").write_text("x = 1\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, _envelope(_block(changed_paths=["a-lie.py"])), ""
        )

    result = ClaudeCodeWorker(project_dir=repo, runner=runner).run(_brief())
    assert result.changed_paths == ("src_written.py",)
    assert "a-lie.py" not in result.changed_paths


def test_a_worker_that_wrote_nothing_reports_no_changed_paths(tmp_path):
    repo = _repo(tmp_path)
    worker = _worker(stdout=_envelope(_block()), project_dir=repo)
    assert worker.run(_brief()).changed_paths == ()


def test_changed_paths_are_empty_outside_a_repository(tmp_path):
    """Cannot tell is reported as nothing, not guessed at."""
    worker = _worker(stdout=_envelope(_block()), project_dir=tmp_path)
    assert worker.run(_brief()).changed_paths == ()


def test_the_adapter_satisfies_the_worker_protocol():
    from vise.runtime.worker import Worker

    assert isinstance(_worker(), Worker)
