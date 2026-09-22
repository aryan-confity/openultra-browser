import json
import re
import socket
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from openultra_browser import inspector


@pytest.fixture
def inspector_server(monkeypatch):
    created = threading.Event()
    servers = []

    class CapturedServer(ThreadingHTTPServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.append(self)
            created.set()

    class FakeAgent:
        def __init__(self, config, *, decision_engine):
            self.config = config
            self.closed = False

        def state(self):
            return {
                "status": "ready",
                "page": {"url": self.config.start_url},
                "history": [],
            }

        def continuation_url(self):
            return self.config.start_url

        def predict(self):
            return {"status": "predicted"}

        def act(self, fingerprint):
            return {"status": "ready", "fingerprint": fingerprint}

        def tick(self):
            return {"status": "ready"}

        def retask(self, config):
            self.config = config
            return self.state()

        def capture_frame(self):
            return {"screenshot": "frame", "fresh": True}

        def close(self):
            self.closed = True

    monkeypatch.setattr(inspector, "ThreadingHTTPServer", CapturedServer)
    monkeypatch.setattr(inspector, "InteractiveAgent", FakeAgent)
    monkeypatch.setattr(inspector, "OpenUltraDecisionEngine", lambda _model: object())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    thread = threading.Thread(
        target=inspector.run_inspector,
        kwargs={"port": port, "model": "test-model", "open_browser": False},
        daemon=True,
    )
    thread.start()
    assert created.wait(3)
    root = f"http://127.0.0.1:{port}"
    with urlopen(root, timeout=3) as response:
        html = response.read().decode()
    token = re.search(r'<meta name="inspector-token" content="([^"]+)"', html).group(1)
    yield root, token
    servers[0].shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def request(root, path, *, body=None, token=None, origin=None, host=None):
    headers = {}
    if token:
        headers["X-Inspector-Token"] = token
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    if body is not None:
        headers["Content-Type"] = "application/json"
    payload = json.dumps(body).encode() if body is not None else None
    req = Request(root + path, data=payload, headers=headers)
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


def test_static_routes_and_host_boundary(inspector_server):
    root, _token = inspector_server
    for path, mime in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
        status, content, headers = request(root, path)
        assert status == 200
        assert content
        assert mime in headers["Content-Type"]
        assert headers["Cache-Control"] == "no-store"
    assert request(root, "/missing")[0] == 404
    assert request(root, "/", host="other.example")[0] == 403


def test_frame_token_and_task_commands(inspector_server):
    root, token = inspector_server
    assert request(root, "/api/frame")[0] == 403
    assert request(root, "/api/frame", token=token)[0] == 204
    assert json.loads(request(root, "/api/state")[1])["status"] == "idle"
    status, content, _ = request(
        root, "/api/reset", body={"goal": "Open https://example.com"}, token=token, origin=root
    )
    assert status == 200
    run_id = json.loads(content)["run_id"]
    assert json.loads(request(root, "/api/frame", token=token)[1])["fresh"] is True
    for command in ("predict", "tick"):
        status, content, _ = request(
            root, f"/api/{command}", body={"run_id": run_id}, token=token, origin=root
        )
        assert status == 200
        assert json.loads(content)["run_id"] == run_id
    status, content, _ = request(
        root, "/api/act", body={"run_id": run_id, "fingerprint": "abc"},
        token=token, origin=root,
    )
    assert status == 200
    assert json.loads(content)["fingerprint"] == "abc"
    status, content, _ = request(
        root, "/api/retask", body={"run_id": run_id, "goal": "Go back"},
        token=token, origin=root,
    )
    assert status == 200
    assert json.loads(content)["run_id"] != run_id


def test_mutation_rejects_bad_origin_token_and_payload(inspector_server):
    root, token = inspector_server
    payload = {"goal": "Open https://example.com"}
    assert request(root, "/api/reset", body=payload, origin=root)[0] == 403
    assert request(root, "/api/reset", body=payload, token=token)[0] == 403
    assert request(root, "/api/reset", body=payload, token=token, origin="http://bad")[0] == 403
    assert request(root, "/api/reset", body={}, token=token, origin=root)[0] == 400
    status, content, _ = request(root, "/api/reset", body=payload, token=token, origin=root)
    assert status == 200
    run_id = json.loads(content)["run_id"]
    assert request(
        root, "/api/predict", body={"run_id": "old"}, token=token, origin=root
    )[0] == 400
    assert request(
        root, "/api/unknown", body={"run_id": run_id}, token=token, origin=root
    )[0] == 400
