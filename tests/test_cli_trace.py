import argparse
import json
import sys

import pytest

from openultra_browser import cli, inspector, trace
from openultra_browser.models import RunResult, StepRecord


def test_prepared_inputs_require_named_values():
    assert cli._prepared_inputs(["query=local model", "empty="]) == {
        "query": "local model",
        "empty": "",
    }
    for bad in ("missing-equals", " =value"):
        with pytest.raises(argparse.ArgumentTypeError):
            cli._prepared_inputs([bad])


def test_run_command_wires_config_and_returns_result_status(monkeypatch, capsys):
    captured = []

    class FakeAgent:
        def __init__(self, config, *, on_step):
            captured.append(config)
            self.on_step = on_step

        def run(self):
            self.on_step(
                StepRecord(1, "https://example.com", "open", "open", "Open docs", 0.9, 0.2,
                           0.1, 20.0, changed=True, policy_intervened=True,
                           policy_reason="safe fallback")
            )
            return RunResult("completed", "verified", "https://example.com/docs")

    monkeypatch.setattr(cli, "BrowserAgent", FakeAgent)
    args = cli.build_parser().parse_args([
        "run", "https://example.com", "--goal", "Open docs", "--input", "query=docs",
        "--allow-domain", "docs.example.com", "--success-text", "Documentation",
    ])
    assert cli._run(args) == 0
    assert captured[0].prepared_inputs == {"query": "docs"}
    assert "docs.example.com" in captured[0].allowed_domains
    assert "policy: safe fallback" in capsys.readouterr().out

    class FailedAgent(FakeAgent):
        def run(self):
            return RunResult("blocked", "No safe action", "https://example.com")

    monkeypatch.setattr(cli, "BrowserAgent", FailedAgent)
    assert cli._run(args) == 1


def test_main_dispatches_inspector_and_run(monkeypatch):
    observed = []
    monkeypatch.setattr(
        inspector, "run_inspector", lambda **kwargs: observed.append(kwargs)
    )
    monkeypatch.setattr(sys, "argv", ["openultra-browser", "inspect", "--port", "9876", "--no-open"])
    cli.main()
    assert observed[0]["port"] == 9876
    assert observed[0]["open_browser"] is False
    monkeypatch.setattr(cli, "_run", lambda _args: 3)
    monkeypatch.setattr(sys, "argv", ["openultra-browser", "run", "https://example.com", "--goal", "Open docs"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 3


def test_trace_is_written_atomically_and_cleans_up_after_failure(tmp_path, monkeypatch):
    destination = tmp_path / "traces" / "run.json"
    result = RunResult("completed", "verified", "https://example.com")
    trace.write_trace(destination, result)
    assert json.loads(destination.read_text())["status"] == "completed"
    assert not list(destination.parent.glob(".run.json.*"))

    def fail_replace(_source, _destination):
        raise OSError("disk full")

    monkeypatch.setattr(trace.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        trace.write_trace(destination, result)
    assert json.loads(destination.read_text())["status"] == "completed"
    assert not list(destination.parent.glob(".run.json.*"))
