"""Single-call visible DOM observation and operation-aware action construction."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from urllib.parse import parse_qsl, urljoin, urlparse

from .models import (
    ActionKind,
    BrowserSnapshot,
    CandidateAction,
    ObservedElement,
    ObservedOption,
    ObservedTab,
)
from .task_progress import (
    SKIP_AD_CONTROL,
    requested_scroll_direction,
    requests_skip_ad,
    step_requests_submission,
)

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
    'option','gridcell','combobox','textbox','searchbox','spinbutton','slider'];
  const selector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map((role) => `[role="${role}"]`).join(',')+
    ',[onclick],[oncontextmenu],[ondblclick],[draggable="true"],[tabindex]:not([tabindex="-1"])';
  const semanticSelector = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
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
      if (node.type === 'range') return 'slider';
      if (node.type === 'color') return 'button';
      if (['text', 'email', 'url', 'tel', 'date', 'datetime-local', 'month', 'time',
           'week', ''].includes(node.type)) return 'textbox';
    }
    if (node.matches('[onclick],[oncontextmenu],[ondblclick],[draggable="true"],'+
        '[tabindex]:not([tabindex="-1"])')) return 'clickable';
    const cursor = getComputedStyle(node).cursor;
    if (cursor === 'pointer' && getComputedStyle(node.parentElement || document.body).cursor !== 'pointer' &&
        !node.closest(semanticSelector)) return 'clickable';
    return null;
  };
  const guard = (node, nodeId, role, label) => {
    const scope = node.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || node.parentElement;
    return JSON.stringify([
      nodeId, role, label, node.value ?? null,
      node.closest('[data-iso]')?.getAttribute('data-iso') ?? null,
      node.checked ?? null, node.selectedIndex ?? null,
      node.readOnly ?? null, node.matches(':disabled'), node.getAttribute('aria-disabled'),
      node.getAttribute('aria-expanded'), node.getAttribute('aria-checked'),
      node.getAttribute('aria-pressed'), node.getAttribute('aria-selected'),
      node.getAttribute('aria-busy'), node.getAttribute('href'),
      normal(scope?.innerText).slice(0, 800)
    ]);
  };

  const candidates = Array.from(document.querySelectorAll(selector));
  const seenCandidates = new Set(candidates);
  for (const node of document.querySelectorAll('body *')) {
    if (seenCandidates.has(node) || getComputedStyle(node).cursor !== 'pointer' ||
        getComputedStyle(node.parentElement || document.body).cursor === 'pointer' ||
        node.closest(semanticSelector)) continue;
    seenCandidates.add(node);
    candidates.push(node);
  }

  const elements = [];
  for (const node of candidates) {
    if (elements.length >= 500 || !safe(node) || !visible(node) || node.matches(':disabled') ||
        node.closest('[aria-disabled="true"]')) continue;
    const rect = node.getBoundingClientRect();
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    if (!rect.width || !rect.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (!top || !node.contains(top)) continue;
    const role = implicitRole(node);
    if (!role || (role === 'gridcell' && node.querySelector('button,[role="button"]'))) continue;
    if (role === 'clickable' && node.querySelector(semanticSelector)) continue;
    const nodeId = identity(node);
    const elementId = `e${nodeId}`;
    const label = name(node).slice(0, 140) || role;
    if (role === 'clickable' && (label === role || rect.width * rect.height > innerWidth * innerHeight * 0.5)) continue;
    node.setAttribute('data-openultra-browser-id', elementId);
    elements.push({
      element_id: elementId,
      role,
      name: label,
      tag: node.tagName.toLowerCase(),
      input_type: normal(node.getAttribute('type')).toLowerCase(),
      value: 'value' in node ? normal(String(node.value)).slice(0, 100) : '',
      date_value: normal(node.closest('[data-iso]')?.getAttribute('data-iso')).slice(0, 20),
      href: node.tagName === 'A' ? node.href : '',
      disabled: false,
      checked: typeof node.checked === 'boolean' ? node.checked : null,
      pressed: node.getAttribute('aria-pressed') === null ? null : node.getAttribute('aria-pressed') === 'true',
      selected: node.getAttribute('aria-selected') === null ? null : node.getAttribute('aria-selected') === 'true',
      expanded: node.getAttribute('aria-expanded') === null ? null : node.getAttribute('aria-expanded') === 'true',
	  busy: node.getAttribute('aria-busy') === null ? null : node.getAttribute('aria-busy') === 'true',
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
  const alerts = Array.from(document.querySelectorAll('[role="alert"],[aria-live="assertive"]'))
    .filter(visible).map((node) => normal(node.innerText || node.textContent)).filter(Boolean).slice(0, 10);
  return {
    url: location.href,
    title: document.title || '',
    visible_text: words.join('\n').slice(0, textLimit),
    elements,
    can_scroll_up: scrollY > 0,
    can_scroll_down: scrollY + innerHeight < document.documentElement.scrollHeight - 2,
    scroll_y: scrollY,
    can_go_back: Boolean(document.referrer),
    alerts,
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
        scroll_y=float(raw.get("scroll_y", 0)),
        can_go_back=bool(raw.get("can_go_back")),
        alerts=tuple(raw.get("alerts", ())),
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
    "change",
    "click",
    "date",
    "find",
    "for",
    "from",
    "get",
    "go",
    "google",
    "in",
    "it",
    "its",
    "him",
    "her",
    "his",
    "them",
    "that",
    "this",
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
    "set",
    "show",
    "site",
    "search",
    "the",
    "then",
    "to",
    "with",
    "update",
    "website",
}

STATEFUL_CONTROL_VERBS = {
    "dislike",
    "follow",
    "like",
    "save",
    "share",
    "subscribe",
    "unfollow",
    "unsubscribe",
}


def _goal_terms(value: str) -> set[str]:
    return _tokens(value) - GOAL_STOPWORDS


def _reverses_requested_route(element: ObservedElement, goal: str) -> bool:
    """A search result for the opposite direction is not a route match."""
    requested = re.search(
        r"\b(?:from|between)\s+([\w .-]+?)\s+(?:to|and)\s+([\w .-]+?)"
        r"(?=\s+(?:on|for|departing|returning|in)\b|[,.;!?]|$)",
        goal,
        re.IGNORECASE,
    )
    if not requested:
        return False
    origin, destination = (re.escape(value.strip()) for value in requested.groups())
    if not origin or not destination or origin.casefold() == destination.casefold():
        return False
    label = element.name
    return bool(
        re.search(rf"\b{destination}\s*(?:to|[-–→])\s*{origin}\b", label, re.IGNORECASE)
    )


def _search_vertical_match(element: ObservedElement, goal: str, current_url: str) -> bool:
    """Prefer a first-party vertical over a generic search ad for that category."""
    current = urlparse(current_url)
    target = urlparse(element.href)
    return bool(
        element.role == "link"
        and current.hostname in {"google.com", "www.google.com"}
        and target.hostname in {"google.com", "www.google.com"}
        and bool({"flight", "flights"} & _goal_terms(goal))
        and target.path.startswith("/travel/flights")
    )


def _score(element: ObservedElement, goal: str) -> float:
    overlap = len(_matched_goal_terms(element, goal))
    role_bonus = 2 if element.role in {"button", "link", "searchbox", "textbox"} else 0
    return overlap * 5 + role_bonus + (1 if element.name else -2) + max(0, 1 - element.y / 2_000)


def _matched_goal_terms(element: ObservedElement, goal: str) -> set[str]:
    if _reverses_requested_route(element, goal):
        return set()
    goal_terms = _goal_terms(goal)
    element_terms = _tokens(element.goal_description)
    requested_stateful = goal_terms & STATEFUL_CONTROL_VERBS
    element_stateful = element_terms & STATEFUL_CONTROL_VERBS
    if element_stateful and not requested_stateful & element_stateful:
        return set()
    matches = goal_terms & element_terms
    if "submit" in goal_terms and element_terms & {"send", "continue"}:
        matches.add("submit")
    return matches


def _goal_match(element: ObservedElement, goal: str) -> str:
    matches = sorted(_matched_goal_terms(element, goal))
    if matches:
        return (
            " Direct goal match: YES. Prefer over controls with no match. "
            f"Matched terms: {', '.join(matches)}."
        )
    return " Direct goal match: no."


def _content_detail_match(element: ObservedElement, goal: str, current_url: str) -> bool:
    """Recognize explicit content-detail destinations without site-specific routes."""
    if element.role != "link" or not element.href:
        return False
    goal_terms = _goal_terms(goal)
    if not goal_terms & {
        "article",
        "details",
        "item",
        "listing",
        "option",
        "product",
        "property",
        "result",
        "video",
    }:
        return False
    label = element.name.casefold()
    path_terms = _tokens(urlparse(element.href).path)
    current_path_terms = _tokens(urlparse(current_url).path)
    if (
        "video" in goal_terms
        and path_terms & {"video", "videos", "watch"}
        and not current_path_terms & {"video", "videos", "watch"}
    ):
        return True
    return bool(
        re.search(r"\b(?:view|open|read|watch)\b.{0,40}\bdetails?\b", label)
        or re.search(r"\bdetails?\s+(?:for|of)\b", label)
    )


def _prepared_keys_for_element(
    element: ObservedElement, prepared_inputs: Mapping[str, str]
) -> list[str]:
    element_terms = _tokens(element.goal_description)
    if "search" in element_terms:
        element_terms.add("query")
    scored = [
        (len(_tokens(key) & element_terms), key)
        for key in prepared_inputs
        if _tokens(key) & element_terms
    ]
    if scored:
        best = max(score for score, _ in scored)
        return [key for score, key in scored if score == best]
    if len(prepared_inputs) == 1 and element.submit_on_enter:
        return list(prepared_inputs)
    return []


def _prepared_value_is_satisfied(input_key: str, actual: str, expected: str) -> bool:
    if actual.strip().casefold() == expected.strip().casefold():
        return True
    if _tokens(input_key) & {"phone", "whatsapp", "mobile", "telephone"}:
        actual_digits = re.sub(r"\D", "", actual).lstrip("0")
        expected_digits = re.sub(r"\D", "", expected).lstrip("0")
        return bool(actual_digits and actual_digits == expected_digits)
    return False


def _url_has_submitted_value(url: str, value: str) -> bool:
    expected = " ".join(value.casefold().split())
    if not expected:
        return False
    parsed = urlparse(url)
    candidates = [candidate for _, candidate in parse_qsl(parsed.query)]
    if "?" in parsed.fragment:
        candidates.extend(candidate for _, candidate in parse_qsl(parsed.fragment.split("?", 1)[1]))
    return any(" ".join(candidate.casefold().split()) == expected for candidate in candidates)


def _is_meaningful_candidate(
    action: CandidateAction, elements_by_id: Mapping[str, ObservedElement]
) -> bool:
    """Distinguish real page controls from generic chrome such as a home logo."""
    if action.kind not in {
        ActionKind.CLICK,
        ActionKind.FILL,
        ActionKind.PRESS_ENTER,
        ActionKind.SELECT,
    }:
        return False
    element = elements_by_id.get(action.element_id or "")
    if element is None:
        return False
    label = element.name.strip().casefold()
    if label in {
        "",
        "button",
        "clickable",
        "home",
        "link",
        "logo",
        "menu",
        "unlabelled",
    }:
        target = urlparse(action.target_url or "")
        return bool(target.path and target.path != "/")
    return True


MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}


def _requested_calendar_date(goal: str, observed: list[date]) -> date | None:
    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", goal)
    if iso:
        try:
            return date(*(int(value) for value in iso.groups()))
        except ValueError:
            return None
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    match = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:[ ,]+(\d{{4}}))?\b",
        goal,
        re.IGNORECASE,
    )
    if not match or not observed:
        return None
    month = MONTHS[match.group(1).casefold()]
    day = int(match.group(2))
    years = (
        [int(match.group(3))]
        if match.group(3)
        else range(
            min(item.year for item in observed) - 1,
            max(item.year for item in observed) + 2,
        )
    )
    candidates: list[date] = []
    for year in years:
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    first, last = min(observed), max(observed)
    midpoint = first + timedelta(days=(last - first).days // 2)
    return min(candidates, key=lambda item: abs((item - midpoint).days))


def _has_calendar_request(goal: str) -> bool:
    if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", goal):
        return True
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    return bool(
        re.search(
            rf"\b(?:{month_names})\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
            goal,
            re.IGNORECASE,
        )
    )


def _explicit_actions(
    snapshot: BrowserSnapshot, goal: str, include_done: bool
) -> tuple[CandidateAction, ...] | None:
    if include_done:
        return (
            CandidateAction(
                "done",
                ActionKind.DONE,
                "Finish because every ordered task step has verified browser progress",
            ),
        )
    scroll_direction = requested_scroll_direction(goal)
    if scroll_direction:
        available = (
            snapshot.can_scroll_up
            if scroll_direction == ActionKind.SCROLL_UP
            else snapshot.can_scroll_down
        )
        if available:
            direction = "up" if scroll_direction == ActionKind.SCROLL_UP else "down"
            return (
                CandidateAction(
                    f"scroll_{direction}",
                    scroll_direction,
                    f"Scroll {direction}. Explicit user direction: YES.",
                    goal_match=True,
                ),
            )
        return (
            CandidateAction(
                "blocked",
                ActionKind.BLOCKED,
                "The page cannot scroll farther in the requested direction",
            ),
        )
    if requests_skip_ad(goal):
        skip_controls = tuple(
            CandidateAction(
                f"click_{item.element_id}",
                ActionKind.CLICK,
                f"Activate visible {item.semantic_description}. Skip control: YES.",
                element_id=item.element_id,
                goal_match=True,
            )
            for item in snapshot.elements
            if item.role in {"button", "clickable"} and SKIP_AD_CONTROL.search(item.name)
        )
        return skip_controls or (
            CandidateAction(
                "blocked",
                ActionKind.BLOCKED,
                "No visible Skip Ad control is available on this page",
            ),
        )
    return None


@dataclass(frozen=True)
class _ElementFacts:
    score: float
    target_url: str | None
    goal_match: bool
    verifier_match: bool
    preferred_match: bool
    vertical_match: bool
    suffix: str


@dataclass
class _ActionBuilder:
    snapshot: BrowserSnapshot
    goal: str
    prepared_inputs: Mapping[str, str]
    max_candidates: int
    success_url_prefix: str | None
    success_url_regex: str | None
    preferred_domains: frozenset[str]
    context_goal: str | None
    ranked: list[tuple[float, CandidateAction]] = field(default_factory=list)
    strong_action_ids: set[str] = field(default_factory=set)
    calendar_transition_pending: bool = False
    back_requested: bool = False
    scroll_is_only_progress: bool = False

    def __post_init__(self) -> None:
        self.matching_goal = self.goal if _goal_terms(self.goal) else self.context_goal or self.goal
        self.elements_by_id = {item.element_id: item for item in self.snapshot.elements}

    def _facts(self, element: ObservedElement) -> _ElementFacts:
        target_url = urljoin(self.snapshot.url, element.href) if element.href else None
        prefix_match = bool(
            self.success_url_prefix and target_url and target_url.startswith(self.success_url_prefix)
        )
        regex_match = bool(
            self.success_url_regex and target_url and re.search(self.success_url_regex, target_url)
        )
        preferred_match = bool(
            target_url
            and urlparse(self.snapshot.url).hostname not in self.preferred_domains
            and urlparse(target_url).hostname in self.preferred_domains
        )
        verifier_match = prefix_match or regex_match
        vertical_match = _search_vertical_match(
            element, self.matching_goal, self.snapshot.url
        )
        score = (
            _score(element, self.matching_goal)
            + (40 if verifier_match else 0)
            + (30 if preferred_match else 0)
            + (35 if vertical_match else 0)
        )
        content_detail_match = _content_detail_match(
            element, self.matching_goal, self.snapshot.url
        )
        suffix = (
            (" Destination matches the required success URL: YES." if verifier_match else "")
            + (
                " Destination is an explicitly approved non-start domain: YES."
                if preferred_match else ""
            )
            + (
                " Visible content-detail destination for the requested item: YES."
                if content_detail_match else ""
            )
            + _goal_match(element, self.matching_goal)
            + (" First-party search category for the requested flight task: YES."
               if vertical_match else "")
        )
        return _ElementFacts(
            score=score,
            target_url=target_url,
            goal_match=bool(_matched_goal_terms(element, self.matching_goal))
            or content_detail_match or verifier_match,
            verifier_match=verifier_match,
            preferred_match=preferred_match,
            vertical_match=vertical_match,
            suffix=suffix,
        )

    def rank_elements(self) -> None:
        for element in self.snapshot.elements:
            if _reverses_requested_route(element, self.matching_goal):
                continue
            element_stateful = _tokens(element.goal_description) & STATEFUL_CONTROL_VERBS
            if element_stateful and not element_stateful & _goal_terms(self.matching_goal):
                continue
            facts = self._facts(element)
            editable = element.role in {"textbox", "searchbox", "spinbutton"} or (
                element.role == "combobox" and element.tag != "select"
            )
            if editable:
                self._rank_editable(element, facts)
            elif element.tag == "select":
                self._rank_select(element, facts)
            else:
                self._rank_click(element, facts)

    def _rank_editable(self, element: ObservedElement, facts: _ElementFacts) -> None:
        for input_key in _prepared_keys_for_element(element, self.prepared_inputs):
            if _prepared_value_is_satisfied(
                input_key, element.value, self.prepared_inputs[input_key]
            ):
                continue
            self.ranked.append(
                (
                    facts.score + 20 + (4 if input_key.lower() in _tokens(element.description) else 0),
                    CandidateAction(
                        f"fill_{element.element_id}_{input_key}",
                        ActionKind.FILL,
                        f"Enter prepared '{input_key}' into {element.semantic_description}."
                        " Prepared value directly advances the goal: YES." + facts.suffix,
                        element.element_id,
                        input_key=input_key,
                        goal_match=True,
                    ),
                )
            )
        if (
            element.submit_on_enter
            and element.value
            and step_requests_submission(self.goal)
            and not _url_has_submitted_value(self.snapshot.url, element.value)
        ):
            action = CandidateAction(
                f"submit_{element.element_id}",
                ActionKind.PRESS_ENTER,
                f"Submit the current search from {element.semantic_description}. "
                "Deterministic planner: the field has a value and still needs submission: YES."
                + _goal_match(element, self.matching_goal),
                element.element_id,
                goal_match=True,
            )
            self.ranked.append((facts.score + 35, action))
            self.strong_action_ids.add(action.action_id)
        self.ranked.append(
            (
                facts.score - 1,
                CandidateAction(
                    f"click_{element.element_id}",
                    ActionKind.CLICK,
                    f"Open or focus {element.semantic_description}." + facts.suffix,
                    element.element_id,
                    goal_match=facts.goal_match,
                ),
            )
        )

    def _rank_select(self, element: ObservedElement, facts: _ElementFacts) -> None:
        prepared_keys = _prepared_keys_for_element(element, self.prepared_inputs)
        desired = self.prepared_inputs[prepared_keys[0]] if prepared_keys else None
        options = [
            (index, option)
            for index, option in enumerate(element.options[:8])
            if desired is None
            or desired.casefold() in {option.label.casefold(), option.value.casefold()}
        ]
        for index, option in options:
            option_goal_match = (
                bool(_goal_terms(self.matching_goal) & _tokens(option.label)) or desired is not None
            )
            self.ranked.append(
                (
                    facts.score + (20 if option_goal_match else 0),
                    CandidateAction(
                        f"select_{element.element_id}_{index}",
                        ActionKind.SELECT,
                        f"Select '{option.label}' in {element.description}." + facts.suffix,
                        element.element_id,
                        option=option.label,
                        option_value=option.value,
                        goal_match=facts.goal_match or option_goal_match,
                    ),
                )
            )

    def _rank_click(self, element: ObservedElement, facts: _ElementFacts) -> None:
        action = CandidateAction(
            f"click_{element.element_id}",
            ActionKind.CLICK,
            f"Activate {element.description}." + facts.suffix,
            element.element_id,
            target_url=facts.target_url,
            goal_match=facts.goal_match or facts.preferred_match or facts.vertical_match,
        )
        self.ranked.append((facts.score, action))
        if facts.verifier_match or facts.preferred_match or facts.vertical_match:
            self.strong_action_ids.add(action.action_id)

    def promote_complete_matches(self) -> None:
        goal_terms = _goal_terms(self.matching_goal)
        if len(goal_terms) < 2:
            return
        complete_match_ids = {
            element.element_id
            for element in self.snapshot.elements
            if _matched_goal_terms(element, self.matching_goal) == goal_terms
        }
        self.strong_action_ids.update(
            action.action_id
            for _, action in self.ranked
            if action.element_id in complete_match_ids
            and action.kind in {
                ActionKind.CLICK, ActionKind.FILL, ActionKind.PRESS_ENTER, ActionKind.SELECT
            }
        )

    def promote_calendar(self) -> None:
        observed_dates = []
        for element in self.snapshot.elements:
            if not element.date_value:
                continue
            try:
                observed_dates.append(date.fromisoformat(element.date_value))
            except ValueError:
                continue
        requested_date = _requested_calendar_date(self.goal, observed_dates)
        self.calendar_transition_pending = bool(
            _has_calendar_request(self.goal)
            and not observed_dates
            and any(element.name.startswith("Done.") for element in self.snapshot.elements)
        )
        if requested_date:
            self._promote_calendar_date(requested_date, observed_dates)
        if self.calendar_transition_pending:
            self.ranked = []
            self.strong_action_ids.clear()

    def _promote_calendar_date(self, requested_date: date, observed_dates: list[date]) -> None:
        exact_ids = {
            element.element_id
            for element in self.snapshot.elements
            if element.date_value == requested_date.isoformat()
        }
        if exact_ids:
            self._promote_calendar_targets(
                exact_ids,
                f" Exact requested calendar date {requested_date.isoformat()}: YES.",
            )
            return
        first, last = min(observed_dates), max(observed_dates)
        direction = None
        if requested_date < first:
            direction = "previous"
        elif requested_date > last:
            direction = "next"
        if direction:
            target_ids = {
                item.element_id for item in self.snapshot.elements
                if item.name.strip().casefold() == direction
            }
            self._promote_calendar_targets(
                target_ids,
                f" Requested date {requested_date.isoformat()} is {direction} of "
                f"the visible calendar range {first.isoformat()} to {last.isoformat()}: YES.",
            )

    def _promote_calendar_targets(self, element_ids: set[str], fact: str) -> None:
        promoted = []
        for score, action in self.ranked:
            if action.element_id in element_ids and action.kind == ActionKind.CLICK:
                action = replace(
                    action, description=action.description + fact, goal_match=True
                )
                score += 90
                self.strong_action_ids.add(action.action_id)
            promoted.append((score, action))
        self.ranked = promoted

    def narrow_form(self) -> None:
        pending = any(
            action.kind in {ActionKind.FILL, ActionKind.SELECT} and action.goal_match
            for _, action in self.ranked
        )
        if not pending:
            return
        form_control_ids = {
            f"click_{element.element_id}"
            for element in self.snapshot.elements
            if element.role in {"checkbox", "radio", "switch"}
        }
        self.ranked = [
            (score, action)
            for score, action in self.ranked
            if action.kind in {ActionKind.FILL, ActionKind.SELECT}
            or action.action_id in form_control_ids
        ]
        self.strong_action_ids.intersection_update(
            action.action_id for _, action in self.ranked
        )

    def narrow_search_submit(self) -> None:
        pending_ids = {
            action.action_id
            for _, action in self.ranked
            if action.kind == ActionKind.PRESS_ENTER and action.goal_match
        }
        if pending_ids:
            self.ranked = [
                (score, action)
                for score, action in self.ranked
                if action.action_id in pending_ids
            ]
            self.strong_action_ids.intersection_update(pending_ids)

    def promote_first(self) -> None:
        if "first" not in _tokens(self.goal):
            return
        first_matching_click = next(
            (
                action.action_id
                for _, action in self.ranked
                if action.kind == ActionKind.CLICK and action.goal_match
            ),
            None,
        )
        if first_matching_click:
            self.ranked = [
                (
                    score + 25,
                    replace(
                        action,
                        description=action.description
                        + " Requested ordinal: FIRST visible matching target: YES.",
                    ),
                )
                if action.action_id == first_matching_click else (score, action)
                for score, action in self.ranked
                if not (
                    action.kind == ActionKind.CLICK
                    and action.goal_match
                    and action.action_id != first_matching_click
                )
            ]

    def add_tabs(self) -> None:
        goal_tokens = _tokens(self.goal)
        tab_intent = bool(
            goal_tokens & {"tab", "tabs"}
            and goal_tokens & {"back", "change", "focus", "open", "return", "switch"}
        )
        self.back_requested = bool(
            re.search(r"\b(?:go|switch|return)\s+back\b", self.goal, re.IGNORECASE)
        )
        back_to_tab = not self.snapshot.can_go_back and self.back_requested
        inactive_tabs = [tab for tab in self.snapshot.tabs if not tab.active]
        previous_tab_id = inactive_tabs[-1].target_id if inactive_tabs else None
        if not (tab_intent or back_to_tab):
            return
        for tab in inactive_tabs:
            tab_terms = _tokens(f"{tab.title} {urlparse(tab.url).hostname or ''}")
            matches = sorted(_goal_terms(self.goal) & tab_terms)
            is_previous = back_to_tab and tab.target_id == previous_tab_id
            self.ranked.append(self._tab_candidate(tab, matches, is_previous))

    @staticmethod
    def _tab_candidate(
        tab: ObservedTab, matches: list[str], is_previous: bool
    ) -> tuple[float, CandidateAction]:
        goal_match = bool(matches) or is_previous
        facts = " Direct goal match: no."
        if goal_match:
            detail = "This is the most recently active prior tab."
            if not is_previous:
                detail = f"Matched terms: {', '.join(matches)}."
            facts = " Direct goal match: YES. " + detail
        return (
            80 + len(matches) * 5 + (20 if is_previous else 0),
            CandidateAction(
                action_id=f"switch_tab_{tab.target_id}",
                kind=ActionKind.SWITCH_TAB,
                description=f"Focus browser tab | {tab.title} | {tab.url}.{facts}",
                browser_target_id=tab.target_id,
                goal_match=goal_match,
            ),
        )

    def filter_targets(self) -> None:
        transition_pending = bool(
            self.preferred_domains
            and urlparse(self.snapshot.url).hostname not in self.preferred_domains
        )
        if self.strong_action_ids:
            self.ranked = [
                (score, action)
                for score, action in self.ranked
                if action.action_id in self.strong_action_ids
            ]
        elif transition_pending:
            self.ranked = [
                (score, action)
                for score, action in self.ranked
                if not action.target_url
                or urlparse(action.target_url).hostname in self.preferred_domains
            ]
        has_direct_target = any(action.goal_match for _, action in self.ranked)
        if not self.strong_action_ids and not transition_pending and has_direct_target:
            self.ranked = [
                (score, action) for score, action in self.ranked if action.goal_match
            ]
        meaningful = {
            action.action_id
            for _, action in self.ranked
            if _is_meaningful_candidate(action, self.elements_by_id)
        }
        if meaningful:
            self.ranked = [
                (score, action)
                for score, action in self.ranked
                if action.action_id in meaningful
            ]
        self.scroll_is_only_progress = not has_direct_target

    def controls(self) -> list[CandidateAction]:
        controls = []
        visibly_busy = any(element.busy is True for element in self.snapshot.elements)
        if self.calendar_transition_pending:
            controls.append(self._calendar_control())
        elif self.snapshot.can_scroll_down:
            controls.append(self._scroll_down_control())
        if self.snapshot.can_scroll_up and not self.calendar_transition_pending:
            controls.append(
                CandidateAction("scroll_up", ActionKind.SCROLL_UP, "Scroll up to earlier content")
            )
        if self.snapshot.can_go_back and not self.calendar_transition_pending:
            controls.append(self._back_control())
        if not self.calendar_transition_pending and (
            visibly_busy or (self.scroll_is_only_progress and not self.snapshot.can_scroll_down)
        ):
            controls.append(
                CandidateAction(
                    "wait", ActionKind.WAIT,
                    (
                        "Wait briefly. Deterministic observer: a visible control is busy: YES."
                        if visibly_busy else "Wait briefly for the page to update"
                    ),
                    goal_match=visibly_busy and self.scroll_is_only_progress,
                )
            )
        return controls

    def _scroll_down_control(self) -> CandidateAction:
        description = "Scroll down for more content"
        if self.scroll_is_only_progress:
            description = (
                "Scroll down. Deterministic planner: no direct target is visible and "
                "unexplored content exists below: YES."
            )
        return CandidateAction(
            "scroll_down", ActionKind.SCROLL_DOWN,
            description, goal_match=self.scroll_is_only_progress,
        )

    def _back_control(self) -> CandidateAction:
        description = "Return to the previous page"
        if self.back_requested:
            description = "Return to the previous page. Direct goal match: YES."
        return CandidateAction(
            "back", ActionKind.BACK, description, goal_match=self.back_requested
        )

    def _calendar_control(self) -> CandidateAction:
        if self.snapshot.can_scroll_up:
            return CandidateAction(
                "scroll_up", ActionKind.SCROLL_UP,
                "Scroll up to reveal the open calendar date grid", goal_match=True,
            )
        return CandidateAction(
            "wait", ActionKind.WAIT,
            "Wait briefly for the calendar month transition to finish", goal_match=True,
        )

    def finish(self) -> tuple[CandidateAction, ...]:
        controls = self.controls()
        self.ranked.sort(key=lambda item: (-item[0], item[1].action_id))
        actions = [action for _, action in self.ranked[: self.max_candidates - len(controls)]]
        actions.extend(controls)
        return tuple(actions)


def build_actions(
    snapshot: BrowserSnapshot,
    goal: str,
    prepared_inputs: Mapping[str, str],
    max_candidates: int,
    include_done: bool,
    success_url_prefix: str | None = None,
    success_url_regex: str | None = None,
    preferred_domains: frozenset[str] = frozenset(),
    context_goal: str | None = None,
) -> tuple[CandidateAction, ...]:
    explicit = _explicit_actions(snapshot, goal, include_done)
    if explicit is not None:
        return explicit
    builder = _ActionBuilder(
        snapshot, goal, prepared_inputs, max_candidates,
        success_url_prefix, success_url_regex, preferred_domains, context_goal,
    )
    builder.rank_elements()
    builder.promote_complete_matches()
    builder.promote_calendar()
    builder.narrow_form()
    builder.narrow_search_submit()
    builder.promote_first()
    builder.add_tabs()
    builder.filter_targets()
    return builder.finish()
