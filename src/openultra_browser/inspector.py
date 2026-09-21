"""Loopback-only OpenUltra browser application."""

from __future__ import annotations

import atexit
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import RunConfig, default_model_path
from .decision import OpenUltraDecisionEngine
from .interactive import InteractiveAgent
from .task_setup import plan_task

STATIC_ROOT = Path(__file__).with_name("static")


class InspectorController:
    def __init__(self, model: str) -> None:
        self.model = model
        self.engine = None
        self.agent: InteractiveAgent | None = None
        self.run_id: str | None = None

    def state(self) -> dict:
        if self.agent:
            return {**self.agent.state(), "run_id": self.run_id}
        return {
            "status": "idle",
            "reason": "Ready for a task",
            "goal": "",
            "page": None,
            "actions": [],
            "decision": None,
            "policy": None,
            "history": [],
            "elapsed_ms": 0,
            "started_at_epoch_ms": None,
            "max_steps": 20,
            "max_seconds": 120,
            "model": self.model,
            "network_model_calls": 0,
            "run_id": None,
        }

    def _config(self, body: dict, *, current_url: str | None = None) -> RunConfig:
        goal = _bounded_text(body.get("goal"), "goal", 4_000)
        planned = plan_task(goal, continuation=current_url is not None)
        start_url = current_url or _optional_text(body.get("url"), 2_000) or planned.start_url
        input_name = str(body.get("input_name") or "query").strip()
        input_value = str(body.get("input_value") or "")
        if input_value and (not input_name or len(input_name) > 80 or len(input_value) > 4_000):
            raise ValueError("Prepared input must have a short name and at most 4,000 characters")
        raw_domains = body.get("allowed_domains") or []
        if not isinstance(raw_domains, list) or len(raw_domains) > 20:
            raise ValueError("allowed_domains must be a short list")
        allowed_domains = frozenset(
            _bounded_text(value, "allowed domain", 255) for value in raw_domains
        )
        return RunConfig(
            goal=goal,
            start_url=start_url,
            model=self.model,
            allowed_domains=allowed_domains,
            prepared_inputs=(
                {input_name: input_value} if input_value else planned.prepared_inputs
            ),
            success_text=_optional_text(body.get("success_text"), 1_000),
            success_url_prefix=_optional_text(body.get("success_url_prefix"), 2_000),
            success_url_regex=_optional_text(body.get("success_url_regex"), 2_000),
            max_steps=_bounded_int(body.get("max_steps", 20), "max_steps", 1, 60),
            max_seconds=_bounded_float(body.get("max_seconds", 120), "max_seconds", 5, 600),
            max_candidates=_bounded_int(
                body.get("max_candidates", 20), "max_candidates", 6, 40
            ),
            visible_text_chars=3_500,
            allow_risky=body.get("allow_risky") is True,
            allow_external_navigation=body.get("url") in (None, ""),
        )

    def reset(self, body: dict) -> dict:
        config = self._config(body)
        if self.engine is None:
            self.engine = OpenUltraDecisionEngine(self.model)
        next_agent = InteractiveAgent(config, decision_engine=self.engine)
        self.close()
        self.agent = next_agent
        self.run_id = secrets.token_urlsafe(12)
        return self.agent.state()

    def retask(self, body: dict) -> dict:
        if self.agent is None:
            raise ValueError("Start a task first")
        current_url = self.agent.state()["page"]["url"]
        config = self._config(body, current_url=current_url)
        state = self.agent.retask(config)
        self.run_id = secrets.token_urlsafe(12)
        return state

    def frame(self) -> dict:
        if self.agent is None:
            raise ValueError("Start a task first")
        return self.agent.capture_frame()

    def command(self, name: str, body: dict) -> dict:
        if name == "reset":
            state = self.reset(body)
            return {**state, "run_id": self.run_id}
        if self.agent is None:
            raise ValueError("Start a task first")
        if body.get("run_id") != self.run_id:
            raise ValueError("This task was replaced by a newer run")
        if name == "retask":
            return {**self.retask(body), "run_id": self.run_id}
        if name == "predict":
            return {**self.agent.predict(), "run_id": self.run_id}
        if name == "act":
            return {
                **self.agent.act(_bounded_text(body.get("fingerprint"), "fingerprint", 128)),
                "run_id": self.run_id,
            }
        if name == "tick":
            return {**self.agent.tick(), "run_id": self.run_id}
        raise ValueError("Unknown inspector command")

    def close(self) -> None:
        agent = self.agent
        self.agent = None
        self.run_id = None
        if agent:
            agent.close()


def _bounded_text(value, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1-{maximum} characters")
    return value.strip()


def _optional_text(value, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"Optional text must contain at most {maximum} characters")
    return value.strip() or None


def _bounded_int(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be between {minimum} and {maximum}") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _bounded_float(value, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be between {minimum} and {maximum}") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def run_inspector(
    *,
    port: int = 8766,
    model: str | None = None,
    open_browser: bool = True,
) -> None:
    if not 1_024 <= port <= 65_535:
        raise ValueError("port must be between 1024 and 65535")
    origin = f"http://127.0.0.1:{port}"
    expected_host = f"127.0.0.1:{port}"
    token = secrets.token_urlsafe(32)
    controller = InspectorController(model or default_model_path())
    lock = threading.Lock()
    reset_requested = threading.Event()
    atexit.register(controller.close)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: str | bytes, mime: str) -> None:
            payload = content if isinstance(content, bytes) else content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, value: dict) -> None:
            self._send(status, json.dumps(value), "application/json; charset=utf-8")

        def _local_host(self) -> bool:
            return self.headers.get("Host") == expected_host

        def do_GET(self) -> None:
            if not self._local_host():
                self._send(403, "Forbidden", "text/plain; charset=utf-8")
                return
            path = urlparse(self.path).path
            if path == "/api/state":
                with lock:
                    self._json(200, controller.state())
                return
            if path == "/api/frame":
                if self.headers.get("X-Inspector-Token") != token:
                    self._json(403, {"error": "Local inspector requests only"})
                    return
                if not lock.acquire(blocking=False):
                    self.send_response(204)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                try:
                    self._json(200, controller.frame())
                except (ValueError, RuntimeError):
                    self.send_response(204)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                finally:
                    lock.release()
                return
            files = {
                "/": ("inspector.html", "text/html; charset=utf-8"),
                "/app.js": ("inspector.js", "text/javascript; charset=utf-8"),
                "/style.css": ("inspector.css", "text/css; charset=utf-8"),
            }
            if path not in files:
                self._send(404, "Not found", "text/plain; charset=utf-8")
                return
            filename, mime = files[path]
            content = (STATIC_ROOT / filename).read_text(encoding="utf-8")
            if filename == "inspector.html":
                content = content.replace("__TOKEN__", token)
            self._send(200, content, mime)

        def do_POST(self) -> None:
            if (
                not self._local_host()
                or self.headers.get("X-Inspector-Token") != token
                or self.headers.get("Origin") != origin
            ):
                self._json(403, {"error": "Local inspector requests only"})
                return
            command_name = self.path.removeprefix("/api/")
            is_reset = command_name == "reset"
            if is_reset:
                reset_requested.set()
            elif reset_requested.is_set():
                self._json(409, {"error": "A newer task is taking control"})
                return
            acquired = lock.acquire(timeout=120)
            if not acquired:
                if is_reset:
                    reset_requested.clear()
                self._json(503, {"error": "The browser command did not finish in time"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 32_768:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be an object")
                result = controller.command(command_name, body)
                self._json(200, result)
            except (ValueError, RuntimeError, TimeoutError) as error:
                self._json(400, {"error": str(error)})
            except Exception:
                self._json(500, {"error": "Inspector command failed; no automatic retry occurred"})
            finally:
                lock.release()
                if is_reset:
                    reset_requested.clear()

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"OpenUltra: {origin}", flush=True)
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(origin)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()
