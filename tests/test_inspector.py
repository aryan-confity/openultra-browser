from pathlib import Path

from laya_browser.cli import build_parser
from laya_browser.inspector import STATIC_ROOT, _bounded_float, _bounded_int, _bounded_text


def test_inspector_command_is_available():
    args = build_parser().parse_args(["inspect", "--port", "9876", "--no-open"])

    assert args.command == "inspect"
    assert args.port == 9876
    assert args.no_open is True


def test_inspector_assets_include_task_timer_and_manual_controls():
    html = (STATIC_ROOT / "inspector.html").read_text()
    script = (STATIC_ROOT / "inspector.js").read_text()

    assert 'id="goal"' in html
    assert 'id="timer"' in html
    assert "Run automatically" in html
    assert "Choose next" in html
    assert "Execute choice" in html
    assert "requestAnimationFrame(drawTimer)" in script


def test_inspector_static_assets_are_packaged_below_the_module():
    assert STATIC_ROOT == Path(__file__).parents[1] / "src/laya_browser/static"
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
