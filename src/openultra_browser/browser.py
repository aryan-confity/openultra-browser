"""Persistent Chrome CDP transport with guarded, one-shot observed actions."""

from __future__ import annotations

import json
import re
import sys
import time

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

from .models import ActionKind, BrowserSnapshot, CandidateAction
from .observation import OBSERVE_SCRIPT, decode_observation


class ExecutionUncertain(RuntimeError):
    """Browser input may have executed; automatic retry is unsafe."""


class StalePage(RuntimeError):
    """The observed page changed before browser input began."""


VALIDATE_TARGET_SCRIPT = r"""
({ nodeId, expectedGuard, kind, selectLabel, selectValue }) => {
  const cache = window.__openUltraBrowser;
  const node = cache?.nodes.get(nodeId);
  if (!node?.isConnected || node.matches(':disabled') ||
      node.closest('[aria-disabled="true"],[inert]') ||
      !node.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) return null;
  const rect = node.getBoundingClientRect();
  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  if (!rect.width || !rect.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return null;
  if (!node.contains(document.elementFromPoint(x, y))) return null;
  const normal = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const name = (item, seen = new Set()) => {
    if (!item || seen.has(item)) return '';
    seen.add(item);
    const referenced = normal(item.getAttribute?.('aria-labelledby')).split(' ').filter(Boolean)
      .map((id) => name(document.getElementById(id), seen)).filter(Boolean).join(' ');
    const labels = item.labels ? Array.from(item.labels).map((label) => name(label, seen)).join(' ') : '';
    const children = item.tagName === 'INPUT' ? '' : Array.from(item.childNodes || []).map((child) =>
      child.nodeType === 3 ? child.textContent :
      child.nodeType === 1 && child.getAttribute('aria-hidden') !== 'true' ? name(child, seen) : ''
    ).join(' ');
    return normal(referenced || item.getAttribute?.('aria-label') || labels ||
      (['button', 'submit', 'reset'].includes(item.type) ? item.value : '') ||
      item.getAttribute?.('alt') || children || item.getAttribute?.('title') ||
      item.getAttribute?.('placeholder'));
  };
  const roles = ['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const explicit = node.getAttribute('role');
  let role = roles.includes(explicit) ? explicit : null;
  if (!role && (node.tagName === 'BUTTON' || node.tagName === 'SUMMARY')) role = 'button';
  if (!role && node.tagName === 'A') role = 'link';
  if (!role && node.tagName === 'SELECT') role = 'combobox';
  if (!role && (node.tagName === 'TEXTAREA' || node.isContentEditable)) role = 'textbox';
  if (!role && node.tagName === 'INPUT') {
    if (['checkbox', 'radio'].includes(node.type)) role = node.type;
    else if (['button', 'submit', 'reset', 'image'].includes(node.type)) role = 'button';
    else if (node.type === 'search') role = 'searchbox';
    else if (node.type === 'number') role = 'spinbutton';
    else if (['text', 'email', 'url', 'tel', ''].includes(node.type)) role = 'textbox';
  }
  const label = name(node).slice(0, 140) || role;
  const scope = node.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || node.parentElement;
  const guard = JSON.stringify([
    nodeId, role, label, node.value ?? null, node.checked ?? null, node.selectedIndex ?? null,
    node.readOnly ?? null, node.matches(':disabled'), node.getAttribute('aria-disabled'),
    node.getAttribute('aria-expanded'), node.getAttribute('aria-checked'),
    node.getAttribute('aria-pressed'), node.getAttribute('aria-selected'),
    node.getAttribute('aria-busy'), node.getAttribute('href'),
    normal(scope?.innerText).slice(0, 800)
  ]);
  if (guard !== expectedGuard) return null;
  if (kind === 'fill' && (node.readOnly || node.getAttribute('aria-readonly') === 'true')) return null;
  if (selectLabel !== null) {
    if (node.tagName !== 'SELECT') return null;
    const option = Array.from(node.options).find((item) => item.text.trim() === selectLabel &&
      String(item.value) === selectValue && !item.disabled && !item.closest('optgroup[disabled]'));
    if (!option) return null;
    node.value = option.value;
    node.dispatchEvent(new Event('input', {bubbles:true}));
    node.dispatchEvent(new Event('change', {bubbles:true}));
  } else {
    node.focus();
  }
  return {x, y, autocomplete: kind === 'fill' &&
    (node.getAttribute('role') === 'combobox' || node.hasAttribute('aria-controls') ||
      node.hasAttribute('aria-owns')), submitOnEnter:
    (kind === 'fill' || kind === 'press_enter') &&
    ['INPUT', 'TEXTAREA'].includes(node.tagName) && Boolean(node.form) && (
      ['search', 'go'].includes(normal(node.getAttribute('enterkeyhint')).toLowerCase()) ||
      node.type === 'search' || role === 'searchbox' ||
      (role === 'combobox' && label.toLowerCase().includes('search')) ||
      ['q', 'query', 'search'].includes(normal(node.getAttribute('name')).toLowerCase())
    )};
}
"""

SUBMISSION_OUTCOME_SCRIPT = r"""
({ startedAt, beforeUrl, timeoutMs }) => new Promise((resolve) => {
  const finishAt = performance.now() + timeoutMs;
  const check = () => {
    if (location.href !== beforeUrl) {
      resolve({ verified: true, status: null, path: new URL(location.href).pathname });
      return;
    }
    const entries = performance.getEntriesByType('resource').filter((entry) =>
      entry.startTime >= startedAt && ['fetch', 'xmlhttprequest'].includes(entry.initiatorType)
    ).map((entry) => {
      const url = new URL(entry.name, location.href);
      return { status: Number(entry.responseStatus || 0), path: url.pathname, sameOrigin: url.origin === location.origin };
    }).filter((entry) => entry.sameOrigin && entry.path !== '/api/auth/session');
    const failure = [...entries].reverse().find((entry) => entry.status >= 400);
    if (failure) {
      resolve({ verified: false, status: failure.status, path: failure.path });
      return;
    }
    const success = [...entries].reverse().find((entry) => entry.status >= 200 && entry.status < 400);
    if (success) {
      resolve({ verified: true, status: success.status, path: success.path });
      return;
    }
    if (performance.now() >= finishAt) {
      resolve({ verified: false, status: null, path: null });
      return;
    }
    setTimeout(check, 50);
  };
  check();
})
"""

SEMANTIC_SETTLE_SCRIPT = r"""
({ quietMs, timeoutMs }) => new Promise((resolve) => {
  const started = performance.now();
  let stableSince = started;
  let previous = '';
  const signature = () => {
    const active = document.activeElement;
    const busy = Boolean(document.querySelector(
      '[aria-busy="true"],[data-loading="true"],.loading,.spinner,[role="progressbar"]'
    ));
    const controls = Array.from(document.querySelectorAll(
      'a[href],button,input,textarea,select,[role="button"],[role="link"],'+
      '[role="textbox"],[role="searchbox"],[role="combobox"],[role="option"]'
    )).filter((node) => node.isConnected && !node.closest('[aria-hidden="true"],[inert]'));
    const tail = controls.slice(-20).map((node) => [
      node.tagName, node.getAttribute('role'), node.getAttribute('aria-busy'),
      node.getAttribute('aria-expanded'), node.getAttribute('aria-pressed'),
      'value' in node ? String(node.value).slice(0, 80) : '',
      (node.innerText || node.getAttribute('aria-label') || '').trim().slice(0, 80)
    ]);
    return JSON.stringify([
      location.href, document.readyState, busy, controls.length,
      document.body?.innerText?.length || 0, active?.tagName || '', tail
    ]);
  };
  const check = () => {
    const now = performance.now();
    const current = signature();
    if (current !== previous) {
      previous = current;
      stableSince = now;
    }
    const busy = Boolean(document.querySelector('[aria-busy="true"],[data-loading="true"]'));
    if ((!busy && document.readyState !== 'loading' && now - stableSince >= quietMs) ||
        now - started >= timeoutMs) {
      resolve({quiet: !busy && now - stableSince >= quietMs, waitedMs: now - started});
      return;
    }
    setTimeout(check, 25);
  };
  check();
})
"""


class Browser:
    def __init__(self, url: str, *, text_limit: int = 3_500) -> None:
        ensure_daemon()
        self.text_limit = text_limit
        self.last_action_evidence: str | None = None
        self.target = cdp("Target.createTarget", url="about:blank", background=False)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=1120,
            height=780,
            deviceScaleFactor=1,
            mobile=False,
        )
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") in {"interactive", "complete"}:
                return
            time.sleep(0.02)
        self.close()
        raise TimeoutError(f"Browser did not load {url!r} within 15 seconds")

    @property
    def url(self) -> str:
        return self.evaluate("location.href")

    def call(self, method: str, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression: str, *, await_promise: bool = False):
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=await_promise,
            returnByValue=True,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError("Document changed during browser evaluation")
        return result.get("result", {}).get("value")

    def observe(self) -> BrowserSnapshot:
        expression = f"({OBSERVE_SCRIPT})({json.dumps({'textLimit': self.text_limit})})"
        for attempt in range(10):
            try:
                return decode_observation(self.evaluate(expression))
            except RuntimeError:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise RuntimeError("Browser page did not settle")

    def screenshot(self) -> str:
        return self.call("Page.captureScreenshot", format="jpeg", quality=78)["data"]

    def act(
        self,
        action: CandidateAction,
        snapshot: BrowserSnapshot,
        prepared_inputs: dict[str, str],
    ) -> bool:
        self.last_action_evidence = None
        before_url = self.url
        if before_url != snapshot.url:
            raise StalePage("Page navigated after observation; refusing a stale action")
        if action.kind == ActionKind.SCROLL_DOWN:
            self.call(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=550,
                y=650,
                deltaX=0,
                deltaY=560,
            )
            self._settle(action.kind)
            return False
        if action.kind == ActionKind.SCROLL_UP:
            self.call(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=550,
                y=130,
                deltaX=0,
                deltaY=-560,
            )
            self._settle(action.kind)
            return False
        if action.kind == ActionKind.BACK:
            try:
                self.evaluate("history.back(); true")
                self._settle(action.kind, before_url=before_url, expect_navigation=True)
            except Exception as error:
                raise ExecutionUncertain(
                    "History navigation may have executed; inspect before retrying"
                ) from error
            return False
        if action.kind == ActionKind.WAIT:
            time.sleep(0.1)
            return False
        if action.kind in {ActionKind.DONE, ActionKind.BLOCKED}:
            return False
        if not action.element_id:
            raise ValueError(f"{action.kind} requires an observed element")

        element = next(
            (item for item in snapshot.elements if item.element_id == action.element_id), None
        )
        if element is None:
            raise RuntimeError("Observed target is absent from the bound snapshot")
        if action.kind == ActionKind.FILL and action.input_key not in prepared_inputs:
            raise RuntimeError("Prepared input is unavailable")
        is_submit = bool(
            action.kind == ActionKind.CLICK
            and element.role == "button"
            and (
                element.input_type == "submit"
                or re.search(r"\b(?:send|submit)\b", element.name, re.IGNORECASE)
            )
        )
        submission_started_at = self.evaluate("performance.now()") if is_submit else None
        request = {
            "nodeId": int(action.element_id.removeprefix("e")),
            "expectedGuard": element.guard,
            "kind": action.kind.value,
            "selectLabel": action.option if action.kind == ActionKind.SELECT else None,
            "selectValue": action.option_value if action.kind == ActionKind.SELECT else None,
        }
        try:
            target = self.evaluate(f"({VALIDATE_TARGET_SCRIPT})({json.dumps(request)})")
        except RuntimeError as error:
            if action.kind == ActionKind.SELECT:
                raise ExecutionUncertain(
                    "Dropdown change may have executed; inspect before retrying"
                ) from error
            raise StalePage("Document changed before browser input began") from error
        if target is None:
            raise StalePage("Observed target changed, became hidden, or is covered")
        try:
            if action.kind == ActionKind.CLICK:
                for event in ("mousePressed", "mouseReleased"):
                    self.call(
                        "Input.dispatchMouseEvent",
                        type=event,
                        x=target["x"],
                        y=target["y"],
                        button="left",
                        clickCount=1,
                    )
            elif action.kind == ActionKind.FILL:
                modifiers = 4 if sys.platform == "darwin" else 2
                for event in ("keyDown", "keyUp"):
                    self.call(
                        "Input.dispatchKeyEvent",
                        type=event,
                        key="a",
                        code="KeyA",
                        modifiers=modifiers,
                        commands=["selectAll"] if event == "keyDown" else [],
                    )
                self.call("Input.insertText", text=prepared_inputs[action.input_key])
            elif action.kind == ActionKind.PRESS_ENTER:
                for event in ("rawKeyDown", "keyUp"):
                    self.call(
                        "Input.dispatchKeyEvent",
                        type=event,
                        key="Enter",
                        code="Enter",
                        windowsVirtualKeyCode=13,
                        nativeVirtualKeyCode=13,
                    )
            elif action.kind != ActionKind.SELECT:
                raise ValueError(f"Unsupported action kind: {action.kind}")
            return self._settle(
                action.kind,
                before_url=before_url,
                expect_navigation=bool(
                    (action.target_url and action.target_url != before_url)
                    or action.kind == ActionKind.PRESS_ENTER
                ),
                autocomplete=bool(target.get("autocomplete")),
                submission_started_at=submission_started_at,
            )
        except ExecutionUncertain:
            raise
        except Exception as error:
            raise ExecutionUncertain(
                "Browser input started but completion could not be confirmed; inspect before retrying"
            ) from error

    def _settle(
        self,
        kind: ActionKind,
        *,
        before_url: str | None = None,
        expect_navigation: bool = False,
        autocomplete: bool = False,
        submission_started_at: float | None = None,
    ) -> bool:
        if kind == ActionKind.FILL:
            self.evaluate(
                f"""new Promise((resolve) => {{
                  let frames = 0, stopped = false;
                  const finish = () => {{ stopped = true; resolve(); }};
                  setTimeout(finish, {200 if autocomplete else 50});
                  const check = () => {{
                    if (stopped) return;
                    frames += 1;
                    const option = Array.from(document.querySelectorAll('[role="option"]')).some((node) => {{
                      const rect = node.getBoundingClientRect();
                      return rect.width && rect.height && rect.bottom > 0 && rect.top < innerHeight;
                    }});
                    if (frames >= 2 && ({str(autocomplete).lower()} ? option : true)) finish();
                    else requestAnimationFrame(check);
                  }};
                  requestAnimationFrame(check);
                }})""",
                await_promise=True,
            )
            return False
        if kind == ActionKind.PRESS_ENTER and expect_navigation and before_url:
            navigated = self._wait_for_navigation(before_url)
            if not navigated:
                submitted = self.evaluate(
                    """(() => {
                      const field = document.activeElement;
                      const form = field?.form;
                      if (!form || typeof form.requestSubmit !== 'function') return false;
                      form.requestSubmit();
                      return true;
                    })()"""
                )
                if submitted:
                    self._wait_for_navigation(before_url)
            return False
        if expect_navigation and before_url:
            return self._wait_for_navigation(before_url)
        if submission_started_at is not None and before_url:
            outcome = self.evaluate(
                f"({SUBMISSION_OUTCOME_SCRIPT})({json.dumps({'startedAt': submission_started_at, 'beforeUrl': before_url, 'timeoutMs': 8_000})})",
                await_promise=True,
            )
            if outcome and outcome.get("status") and outcome["status"] >= 400:
                self.last_action_evidence = (
                    f"Submission request reached {outcome.get('path') or 'the site'} "
                    f"and received HTTP {outcome['status']}; it was not retried"
                )
                return True
            if outcome and outcome.get("verified"):
                status = f"HTTP {outcome['status']}" if outcome.get("status") else "navigation"
                self.last_action_evidence = (
                    f"Submission request reached {outcome.get('path') or 'the destination'} "
                    f"with {status} evidence"
                )
            return bool(outcome and outcome.get("verified"))
        try:
            self._wait_for_semantic_quiet()
        except RuntimeError:
            pass
        return False

    def _wait_for_semantic_quiet(
        self, *, quiet_ms: int = 100, timeout_ms: int = 1_500
    ) -> None:
        self.evaluate(
            f"({SEMANTIC_SETTLE_SCRIPT})({json.dumps({'quietMs': quiet_ms, 'timeoutMs': timeout_ms})})",
            await_promise=True,
        )

    def _wait_for_navigation(self, before_url: str, timeout_seconds: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                current_url = self.url
                if current_url != before_url:
                    if self.evaluate("document.readyState") in {"interactive", "complete"}:
                        self._wait_for_semantic_quiet(quiet_ms=125, timeout_ms=1_500)
                        return True
            except RuntimeError:
                pass
            time.sleep(0.02)
        return False

    def close(self) -> None:
        target = self.target
        self.target = None
        if not target:
            return
        try:
            cdp("Target.closeTarget", targetId=target)
        except RuntimeError as error:
            if "No target with given id found" not in str(error):
                raise

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()
