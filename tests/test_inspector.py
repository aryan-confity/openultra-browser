from pathlib import Path

from openultra_browser.cli import build_parser
from openultra_browser.inspector import (
    STATIC_ROOT,
    InspectorController,
    _bounded_float,
    _bounded_int,
    _bounded_text,
)


def test_inspector_command_is_available():
    args = build_parser().parse_args(["inspect", "--port", "9876", "--no-open"])

    assert args.command == "inspect"
    assert args.port == 9876
    assert args.no_open is True


def test_inspector_assets_are_task_first_live_and_single_screen():
    html = (STATIC_ROOT / "inspector.html").read_text()
    script = (STATIC_ROOT / "inspector.js").read_text()

    assert 'id="goal"' in html
    assert 'id="timer"' in html
    assert 'id="decision-ms"' in html
    assert 'id="decision-rate"' in html
    assert "OpenUltra" in html
    assert 'class="app-shell"' in html
    assert "Run automatically" not in html
    assert "Choose next" not in html
    assert "Execute choice" not in html
    assert "requestAnimationFrame(drawTimer)" in script
    assert 'id="start-url"' not in html
    assert "Run constraints" not in html
    assert "goal," in script
    assert 'fetch("/api/frame"' in script
    assert "setInterval(refreshFrame, 400)" in script
    assert "requestAnimationFrame(() => requestAnimationFrame(resolve))" in script
    assert 'id="continue-task"' in html
    assert 'await call(command' in script
    assert 'requestTask("retask")' in script
    assert "await runAutomatically()" in script
    assert "payload.run_id = state.run_id" in script
    assert 'id="voice-mode"' in html
    assert 'id="cancel-voice"' in html
    assert 'id="run-transcript"' in html
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in script
    assert "navigator.brave.isBrave" in script
    assert "does not yet provide a working recognition engine" in script
    assert "This local address is secure" in script
    assert "not caused by localhost or missing HTTPS" in script
    assert "nextRecognition.continuous = true" in script
    assert "event.resultIndex" in script
    assert "Listening for next command" in script
    assert "startRecognitionCycle(SpeechRecognition, generation)" in script
    assert "voiceRestartTimer = setTimeout" in script
    assert 'voiceSession.nextCommand = "retask"' in script
    assert "voiceSession.tasks.push" in script
    assert 'fetch("/api/voice-plan"' in script
    assert "Acting while you speak" in script
    assert "Run the captured transcript" in script


def test_inspector_static_assets_are_packaged_below_the_module():
    assert STATIC_ROOT == Path(__file__).parents[1] / "src/openultra_browser/static"
    assert {path.name for path in STATIC_ROOT.iterdir()} == {
        "inspector.css",
        "inspector.html",
        "inspector.js",
    }


def test_inspector_request_limits_fail_closed():
    assert _bounded_text(" task ", "goal", 10) == "task"
    assert _bounded_int("20", "steps", 1, 60) == 20
    assert _bounded_float("2.5", "seconds", 1, 10) == 2.5

    for function, value in (
        (lambda: _bounded_text("", "goal", 10), "characters"),
        (lambda: _bounded_int(True, "steps", 1, 60), "between"),
        (lambda: _bounded_float(20, "seconds", 1, 10), "between"),
    ):
        try:
            function()
        except ValueError as error:
            assert value in str(error)
        else:
            raise AssertionError("invalid inspector input was accepted")


def test_stale_run_cannot_issue_commands(monkeypatch):
    class FakeInteractiveAgent:
        def __init__(self, _config, *, decision_engine):
            self.closed = False

        def state(self):
            return {"status": "ready"}

        def close(self):
            self.closed = True

        def retask(self, _config):
            return {"status": "ready", "page": {"url": "https://example.org/current"}}

    monkeypatch.setattr("openultra_browser.inspector.InteractiveAgent", FakeInteractiveAgent)
    monkeypatch.setattr(
        "openultra_browser.inspector.OpenUltraDecisionEngine", lambda _model: object()
    )
    controller = InspectorController("model")
    first = controller.reset({"goal": "Open https://example.com"})
    first_run_id = controller.run_id
    second = controller.reset({"goal": "Open https://example.org"})

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert controller.run_id != first_run_id
    try:
        controller.command("predict", {"run_id": first_run_id})
    except ValueError as error:
        assert "replaced" in str(error)
    else:
        raise AssertionError("a stale inspector run controlled the current task")


def test_retask_rotates_run_identity_without_replacing_the_agent(monkeypatch):
    class FakeInteractiveAgent:
        def __init__(self, config, *, decision_engine):
            self.config = config
            self.closed = False

        def state(self):
            return {"status": "ready", "page": {"url": "https://example.com/current"}}

        def retask(self, config):
            self.config = config
            return self.state()

        def close(self):
            self.closed = True

    monkeypatch.setattr("openultra_browser.inspector.InteractiveAgent", FakeInteractiveAgent)
    monkeypatch.setattr(
        "openultra_browser.inspector.OpenUltraDecisionEngine", lambda _model: object()
    )
    controller = InspectorController("model")
    controller.reset({"goal": "Open https://example.com"})
    agent = controller.agent
    first_run_id = controller.run_id

    state = controller.command(
        "retask",
        {"run_id": first_run_id, "goal": "Review the current page"},
    )

    assert controller.agent is agent
    assert controller.run_id != first_run_id
    assert state["run_id"] == controller.run_id
    assert agent.config.start_url == "https://example.com/current"


def test_retask_recovers_the_browser_owned_http_page_before_config_validation(monkeypatch):
    class FakeInteractiveAgent:
        def __init__(self, config, *, decision_engine):
            self.config = config

        def state(self):
            return {"status": "ready", "page": {"url": "about:blank"}}

        def continuation_url(self):
            return "https://google.com/travel/flights"

        def retask(self, config):
            self.config = config
            return self.state()

        def close(self):
            return None

    monkeypatch.setattr("openultra_browser.inspector.InteractiveAgent", FakeInteractiveAgent)
    monkeypatch.setattr(
        "openultra_browser.inspector.OpenUltraDecisionEngine", lambda _model: object()
    )
    controller = InspectorController("model")
    controller.reset({"goal": "Open https://example.com"})

    controller.retask({"goal": "Go back"})

    assert controller.agent.config.start_url == "https://google.com/travel/flights"


def test_voice_plan_requires_model_and_deterministic_agreement():
    class VoiceEngine:
        def classify_voice_command(self, transcript, candidate):
            assert transcript == "open wikipedia and search for Alan Turing"
            assert candidate == "open wikipedia"
            return {
                "kind": "reversible_closed_set",
                "confidence": 0.88,
                "completeness": 0.92,
                "inference_ms": 41.0,
            }

    controller = InspectorController("model")
    controller.engine = VoiceEngine()

    result = controller.command(
        "voice-plan",
        {"transcript": "open wikipedia and search for Alan Turing"},
    )

    assert result["decision"] == "act"
    assert result["candidate"] == "open wikipedia"
    assert result["inference_ms"] == 41.0


def test_voice_plan_rejects_open_ended_payload_without_model_call():
    class UnusedEngine:
        def classify_voice_command(self, *_args):
            raise AssertionError("payload speech must not reach early-commit inference")

    controller = InspectorController("model")
    controller.engine = UnusedEngine()

    result = controller.command(
        "voice-plan",
        {"transcript": "search for Alan"},
    )

    assert result["decision"] == "wait"
    assert result["inference_ms"] == 0.0
