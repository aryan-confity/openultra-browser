"""Single-call visible DOM observation and operation-aware action construction."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urljoin, urlparse

from .models import ActionKind, BrowserSnapshot, CandidateAction, ObservedElement, ObservedOption

OBSERVE_SCRIPT = r"""
({ textLimit }) => {
  if (!document.body) return null;
  const cache = window.__openUltraBrowser ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = (node) => {
    if (!cache.ids.has(node)) cache.ids.set(node, cache.next++);
    const id = cache.ids.get(node);
    cache.nodes.set(id, node);
    return id;
  };
  for (const [id, node] of cache.nodes) if (!node.isConnected) cache.nodes.delete(id);

  const normal = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const safe = (node) => !['password', 'file', 'hidden'].includes((node.type || '').toLowerCase());
  const visible = (node) => !node.closest('[aria-hidden="true"],[inert]') &&
    node.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
  const name = (node, seen = new Set()) => {
    if (!node || seen.has(node)) return '';
    seen.add(node);
    const referenced = normal(node.getAttribute?.('aria-labelledby')).split(' ').filter(Boolean)
      .map((id) => name(document.getElementById(id), seen)).filter(Boolean).join(' ');
    const labels = node.labels ? Array.from(node.labels).map((label) => name(label, seen)).join(' ') : '';
    const children = node.tagName === 'INPUT' ? '' : Array.from(node.childNodes || []).map((child) =>
      child.nodeType === 3 ? child.textContent :
      child.nodeType === 1 && child.getAttribute('aria-hidden') !== 'true' ? name(child, seen) : ''
    ).join(' ');
    return normal(referenced || node.getAttribute?.('aria-label') || labels ||
      (['button', 'submit', 'reset'].includes(node.type) ? node.value : '') ||
      node.getAttribute?.('alt') || children || node.getAttribute?.('title') ||
      node.getAttribute?.('placeholder'));
  };
  const roles = ['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map((role) => `[role="${role}"]`).join(',');
  const implicitRole = (node) => {
    const explicit = node.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (node.tagName === 'BUTTON' || node.tagName === 'SUMMARY') return 'button';
    if (node.tagName === 'A') return 'link';
    if (node.tagName === 'SELECT') return 'combobox';
    if (node.tagName === 'TEXTAREA' || node.isContentEditable) return 'textbox';
    if (node.tagName === 'INPUT') {
      if (['checkbox', 'radio'].includes(node.type)) return node.type;
      if (['button', 'submit', 'reset', 'image'].includes(node.type)) return 'button';
      if (node.type === 'search') return 'searchbox';
      if (node.type === 'number') return 'spinbutton';
      if (['text', 'email', 'url', 'tel', ''].includes(node.type)) return 'textbox';
    }
    return null;
  };
  const guard = (node, nodeId, role, label) => {
    const scope = node.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || node.parentElement;
    return JSON.stringify([
      nodeId, role, label, node.value ?? null, node.checked ?? null, node.selectedIndex ?? null,
      node.readOnly ?? null, node.matches(':disabled'), node.getAttribute('aria-disabled'),
      node.getAttribute('aria-expanded'), node.getAttribute('aria-checked'),
      node.getAttribute('aria-selected'), node.getAttribute('href'),
      normal(scope?.innerText).slice(0, 800)
    ]);
  };

  const elements = [];
  for (const node of document.querySelectorAll(selector)) {
    if (elements.length >= 500 || !safe(node) || !visible(node) || node.matches(':disabled') ||
        node.closest('[aria-disabled="true"]')) continue;
    const rect = node.getBoundingClientRect();
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    if (!rect.width || !rect.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    const role = implicitRole(node);
    if (!role || (role === 'gridcell' && node.querySelector('button,[role="button"]'))) continue;
    const nodeId = identity(node);
    const elementId = `e${nodeId}`;
    const label = name(node).slice(0, 140) || role;
    node.setAttribute('data-openultra-browser-id', elementId);
    elements.push({
      element_id: elementId,
      role,
      name: label,
      tag: node.tagName.toLowerCase(),
      input_type: normal(node.getAttribute('type')).toLowerCase(),
      value: 'value' in node ? normal(String(node.value)).slice(0, 100) : '',
      href: node.tagName === 'A' ? node.href : '',
      disabled: false,
      checked: typeof node.checked === 'boolean' ? node.checked : null,
      selected: node.getAttribute('aria-selected') === null ? null : node.getAttribute('aria-selected') === 'true',
      expanded: node.getAttribute('aria-expanded') === null ? null : node.getAttribute('aria-expanded') === 'true',
	  submit_on_enter: ['INPUT', 'TEXTAREA'].includes(node.tagName) && Boolean(node.form) && (
	    ['search', 'go'].includes(normal(node.getAttribute('enterkeyhint')).toLowerCase()) ||
	    node.type === 'search' || role === 'searchbox' ||
	    (role === 'combobox' && label.toLowerCase().includes('search')) ||
	    ['q', 'query', 'search'].includes(normal(node.getAttribute('name')).toLowerCase())
	  ),
	  options: node.tagName === 'SELECT' ? Array.from(node.options)
        .filter((option) => !option.selected && !option.disabled && !option.closest('optgroup[disabled]'))
        .map((option) => ({label: normal(option.text), value: String(option.value)})).slice(0, 20) : [],
      x: Math.round(rect.x), y: Math.round(rect.y),
      width: Math.round(rect.width), height: Math.round(rect.height),
      guard: guard(node, nodeId, role, label),
    });
  }

  const words = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let node, length = 0;
  while ((node = walker.nextNode()) && length < textLimit) {
    const value = normal(node.textContent), parent = node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight &&
        rect.right > 0 && rect.left < innerWidth) {
      words.push(value);
      length += value.length;
    }
  }
  return {
    url: location.href,
    title: document.title || '',
    visible_text: words.join('\n').slice(0, textLimit),
    elements,
    can_scroll_up: scrollY > 0,
    can_scroll_down: scrollY + innerHeight < document.documentElement.scrollHeight - 2,
    can_go_back: Boolean(document.referrer),
  };
}
"""


def decode_observation(raw: dict | None) -> BrowserSnapshot:
    if raw is None:
        raise RuntimeError("Document is navigating; no browser decision was made")
    elements = tuple(
        ObservedElement(
            **{
                **item,
                "options": tuple(ObservedOption(**option) for option in item.get("options", ())),
            }
        )
        for item in raw["elements"]
    )
    return BrowserSnapshot(
        url=raw["url"],
        title=raw["title"],
        visible_text=raw["visible_text"],
        elements=elements,
        can_scroll_up=bool(raw.get("can_scroll_up")),
        can_scroll_down=bool(raw.get("can_scroll_down")),
        can_go_back=bool(raw.get("can_go_back")),
    )


def observe(browser, text_limit: int = 3_500) -> BrowserSnapshot:
    expression = f"({OBSERVE_SCRIPT})({json.dumps({'textLimit': text_limit})})"
    return decode_observation(browser.evaluate(expression))


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


GOAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "choose",
    "click",
    "find",
    "for",
    "from",
    "get",
    "go",
    "google",
    "in",
    "navigate",
    "of",
    "on",
    "open",
    "option",
    "official",
    "page",
    "please",
    "properties",
    "property",
    "select",
    "show",
    "site",
    "search",
    "the",
    "then",
    "to",
    "with",
    "website",
}


def _goal_terms(value: str) -> set[str]:
    return _tokens(value) - GOAL_STOPWORDS


def _score(element: ObservedElement, goal: str) -> float:
    overlap = len(_goal_terms(goal) & _tokens(element.goal_description))
    role_bonus = 2 if element.role in {"button", "link", "searchbox", "textbox"} else 0
    return overlap * 5 + role_bonus + (1 if element.name else -2) + max(0, 1 - element.y / 2_000)


def _goal_match(element: ObservedElement, goal: str) -> str:
    matches = sorted(_goal_terms(goal) & _tokens(element.goal_description))
    if matches:
        return (
            " Direct goal match: YES. Prefer over controls with no match. "
            f"Matched terms: {', '.join(matches)}."
        )
    return " Direct goal match: no."


def build_actions(
    snapshot: BrowserSnapshot,
    goal: str,
    prepared_inputs: Mapping[str, str],
    max_candidates: int,
    include_done: bool,
    success_url_prefix: str | None = None,
    success_url_regex: str | None = None,
    preferred_domains: frozenset[str] = frozenset(),
) -> tuple[CandidateAction, ...]:
    if include_done:
        return (
            CandidateAction(
                "done",
                ActionKind.DONE,
                "Finish because every ordered task step has verified browser progress",
            ),
        )

    ranked: list[tuple[float, CandidateAction]] = []
    strong_action_ids: set[str] = set()
    for element in snapshot.elements:
        target_url = urljoin(snapshot.url, element.href) if element.href else None
        prefix_match = bool(
            success_url_prefix and target_url and target_url.startswith(success_url_prefix)
        )
        regex_match = bool(
            success_url_regex and target_url and re.search(success_url_regex, target_url)
        )
        preferred_match = bool(
            target_url
            and urlparse(snapshot.url).hostname not in preferred_domains
            and urlparse(target_url).hostname in preferred_domains
        )
        verifier_match = prefix_match or regex_match
        score = _score(element, goal) + (40 if verifier_match else 0) + (
            30 if preferred_match else 0
        )
        label_goal_match = bool(_goal_terms(goal) & _tokens(element.goal_description))
        goal_match = label_goal_match or verifier_match
        verifier_fact = (
            " Destination matches the required success URL: YES."
            if verifier_match
            else ""
        )
        preferred_fact = (
            " Destination is an explicitly approved non-start domain: YES."
            if preferred_match
            else ""
        )
        editable = element.role in {"textbox", "searchbox", "spinbutton"} or (
            element.role == "combobox" and element.tag != "select"
        )
        if editable:
            for input_key in prepared_inputs:
                if element.value == prepared_inputs[input_key]:
                    continue
                prepared_goal_match = bool(
                    _goal_terms(goal) & _tokens(prepared_inputs[input_key])
                ) or (len(prepared_inputs) == 1 and element.submit_on_enter)
                progress_fact = (
                    " Prepared value directly advances the goal: YES."
                    if prepared_goal_match
                    else " Prepared value directly advances the goal: unknown."
                )
                ranked.append(
                    (
                        score
                        + (20 if prepared_goal_match else 0)
                        + (4 if input_key.lower() in _tokens(element.description) else 0),
                        CandidateAction(
                            f"fill_{element.element_id}_{input_key}",
                            ActionKind.FILL,
                            f"Enter prepared '{input_key}' into {element.semantic_description}."
                            + progress_fact
                            + verifier_fact
                            + preferred_fact
                            + _goal_match(element, goal),
                            element.element_id,
                            input_key=input_key,
                            goal_match=goal_match or prepared_goal_match,
                        ),
                    )
                )
            if element.submit_on_enter and element.value:
                action = CandidateAction(
                    f"submit_{element.element_id}",
                    ActionKind.PRESS_ENTER,
                    f"Submit the current search from {element.semantic_description}. "
                    "Deterministic planner: the field has a value and still needs submission: YES."
                    + _goal_match(element, goal),
                    element.element_id,
                    goal_match=True,
                )
                ranked.append((score + 35, action))
                strong_action_ids.add(action.action_id)
            ranked.append(
                (
                    score - 1,
                    CandidateAction(
                        f"click_{element.element_id}",
                        ActionKind.CLICK,
                        f"Open or focus {element.semantic_description}."
                        + verifier_fact
                        + preferred_fact
                        + _goal_match(element, goal),
                        element.element_id,
                        goal_match=goal_match,
                    ),
                )
            )
        elif element.tag == "select":
            for index, option in enumerate(element.options[:8]):
                ranked.append(
                    (
                        score,
                        CandidateAction(
                            f"select_{element.element_id}_{index}",
                            ActionKind.SELECT,
                            f"Select '{option.label}' in {element.description}."
                            + verifier_fact
                            + preferred_fact
                            + _goal_match(element, goal),
                            element.element_id,
                            option=option.label,
                            option_value=option.value,
                            goal_match=goal_match,
                        ),
                    )
                )
        else:
            action = CandidateAction(
                f"click_{element.element_id}",
                ActionKind.CLICK,
                f"Activate {element.description}."
                + verifier_fact
                + preferred_fact
                + _goal_match(element, goal),
                element.element_id,
                target_url=target_url,
                goal_match=goal_match or preferred_match,
            )
            ranked.append(
                (
                    score,
                    action,
                )
            )
            if verifier_match or preferred_match:
                strong_action_ids.add(action.action_id)

    if "first" in _tokens(goal):
        for index, (score, action) in enumerate(ranked):
            if action.kind == ActionKind.CLICK and action.goal_match:
                ranked[index] = (
                    score + 25,
                    replace(
                        action,
                        description=(
                            action.description
                            + " Requested ordinal: FIRST visible matching target: YES."
                        ),
                    ),
                )
                break

    transition_pending = bool(
        preferred_domains and urlparse(snapshot.url).hostname not in preferred_domains
    )
    if strong_action_ids:
        ranked = [(score, action) for score, action in ranked if action.action_id in strong_action_ids]
    elif transition_pending:
        ranked = [
            (score, action)
            for score, action in ranked
            if not action.target_url
            or urlparse(action.target_url).hostname in preferred_domains
        ]
    has_direct_target = any(action.goal_match for _, action in ranked)
    if not strong_action_ids and not transition_pending and has_direct_target:
        ranked = [(score, action) for score, action in ranked if action.goal_match]

    controls: list[CandidateAction] = []
    if snapshot.can_scroll_down:
        controls.append(
            CandidateAction(
                "scroll_down",
                ActionKind.SCROLL_DOWN,
                (
                    "Scroll down. Deterministic planner: no direct target is visible and "
                    "unexplored content exists below: YES."
                    if not has_direct_target
                    else "Scroll down for more content"
                ),
                goal_match=not has_direct_target,
            )
        )
    if snapshot.can_scroll_up:
        controls.append(
            CandidateAction("scroll_up", ActionKind.SCROLL_UP, "Scroll up to earlier content")
        )
    if snapshot.can_go_back:
        controls.append(CandidateAction("back", ActionKind.BACK, "Return to the previous page"))
    if not has_direct_target and not snapshot.can_scroll_down:
        controls.append(
            CandidateAction("wait", ActionKind.WAIT, "Wait briefly for the page to update")
        )
    reserved = len(controls)
    ranked.sort(key=lambda item: (-item[0], item[1].action_id))
    actions = [action for _, action in ranked[: max_candidates - reserved]]
    actions.extend(controls)
    return tuple(actions)
