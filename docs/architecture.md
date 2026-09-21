# Architecture

## Decision boundary

The decision model does not receive selectors, executable JavaScript, secrets, or arbitrary
coordinates. It sees:

- the user's goal;
- current URL and title;
- bounded visible text;
- recent action outcomes;
- a finite dictionary of action IDs and semantic descriptions.

The operation, compatible-target, completion, and stuck questions are independent rows in one local
MLX batch. Target heads exist only for operations currently supported by visible controls. The
runtime combines operation and target probabilities for safe fallback, and treats every answer as a
proposal, not authorization. A low independent completion or stuck probability removes `DONE` or
`BLOCKED` from consideration. Generic completion requires both the in-step completion head and a
second focused Boolean check; configured deterministic postconditions remain stronger. Stuck
detection also uses observed outcomes.

The model preserves each question head and may truncate the tail of long state. The state therefore puts
the goal, current URL, untrusted-content boundary, and recent outcomes before bounded page text so
the least important text tail is discarded first.

The interactive runtime exposes the same state machine in two phases. `predict` binds a decision to
the current snapshot fingerprint without mutating the page. `act` consumes that decision before
input begins and refuses a different fingerprint. Automatic mode calls those same methods; it is not
a second execution path.

Task continuation keeps a separate session context containing at most three code-owned action
outcomes and the page before the latest navigation. That context lets the same batch resolve
references and corrections such as choosing another result. It is never merged into the new run's
history, so prior actions cannot satisfy completion checks, suppress repeat detection, or consume
the new run's limits. Prepared values are excluded from this context.

## Observation

One browser evaluation stamps visible interactive nodes with ephemeral IDs and returns their role,
accessible name, value metadata, destination, state, select options, and viewport geometry. It
observes semantic controls plus explicit event handlers, focusable surfaces, and labelled leaf
pointer targets. Covered, oversized, hidden, offscreen, and duplicate containers are excluded.
Every observation receives a content fingerprint.

The candidate builder scores goal/element token overlap and keeps a bounded set that fits the model's
context. Global back, scroll, and wait actions remain available. Text actions refer to named prepared
values; the actual values remain in the executor. A fill whose exact prepared value is already
present is pruned. Form-associated search inputs and textareas expose a separate typed `SUBMIT`
operation, so entering a value is never confused with submitting it.
The planner may tell the model that a hidden prepared value overlaps the goal, but it never includes the
value itself in model state, traces, or action descriptions.
Direct goal matching uses accessible labels, not query terms embedded in destination URLs. URLs are
retained separately for domain policy and navigation guards.
When a caller supplies a success URL prefix, an observed link to that prefix receives an explicit
planner fact. The model still chooses the operation and target; completion is checked only after navigation.
URL-regex postconditions support dynamic result routes without preselecting a concrete result ID.
When a visible candidate directly matches the goal or verifier, unrelated element targets are pruned
from that decision cycle. Scroll/back controls remain available for recovery and exploration.
If no direct target is visible while unexplored content exists below, scrolling receives an explicit
progress fact and waiting is removed for that cycle.
Date controls expose only date-shaped current values to goal matching. Calendar cells retain their
ISO date, allowing deterministic Previous/Next navigation and exact-date selection while preserving
fresh-target validation. Calendar animation settling waits for the visible ISO range to change, and
recovers the viewport if the site scrolls an open date grid offscreen.
Explicit non-start domains act as transition constraints: while the browser remains on the start
domain, a visible link to an approved destination outranks same-site distractions. If that
destination is not visible yet, the runtime explores the current page instead of following a
goal-word-matching map or suggestion on the start site.

## Safety shield

Before execution, deterministic code:

1. confirms the action came from the current observation;
2. rejects stale, missing, ambiguous, hidden, password, and file-input targets;
3. restricts navigation to explicit domains;
4. blocks destructive and financial actions by default;
5. avoids repeating recent actions that produced no observable change;
6. excludes the previous target when the model independently identifies the updated task as a
   correction;
7. selects the next-highest safe probability when intervention is required.

The executor resolves only the ephemeral observed ID. Model output never becomes a selector or script.
If the page becomes stale before input starts, the decision is discarded and the next iteration
observes again. Once any input may have executed, transport uncertainty stops the run instead of
retrying and risking a duplicate action.

## Verification

After execution, the runtime takes one fresh atomic observation. Expected link and history
navigations first receive a bounded document-transition wait so the old page cannot be mistaken for
the result. A changed fingerprint records progress. Three consecutive non-wait no-op steps stop the
run. Deterministic visible-text and URL checks can complete a run. If multiple checks are configured,
all must pass. Without configured checks, `done` must pass both the in-step completion head and a
second focused local Boolean confirmation.

## Local inspector

The visual inspector is an observability and control surface around the core runtime, not an
authority boundary. Its primary input is only the user's task. Deterministic setup extracts an
explicit HTTP(S) destination or literal search phrase from that task and never generates missing
text. Its fixed-height app shell keeps a compact task rail beside the live browser. Starting a task
begins bounded automatic execution; the rail exposes pause/resume, elapsed time, latest decision
latency, decisions per second, current action, and recent history without exposing internal
constraints or probability panels.

The task input can use text or the browser's Web Speech API. Voice recognition is an input adapter,
not a second agent: only a final utterance is submitted, and it enters the same reset or continue
contract, task decomposition, policy, execution, and verification path as typed text. Audio is not
sent to the OpenUltra server. Browser speech-recognition availability and processing behavior are
controlled by the browser implementation.

A read-only frame endpoint captures browser pixels independently from DOM observation. It uses the
same nonblocking browser lock as commands, so preview refreshes cannot race prediction or input.
Automatic mode yields browser paint frames between prediction and execution and never obscures an
existing preview with the initial loading state.

The HTTP server binds to `127.0.0.1`, validates the exact Host and Origin, requires a random per-run
token for mutation requests, limits request sizes and field lengths, serves no third-party assets,
and applies a restrictive content-security policy. Prepared input values stay in `RunConfig` and the
executor; serialized inspector state exposes only their names through action descriptions.

## Known limits

- The underlying generic decision weights were not trained specifically for browser navigation.
- DOM-only observation cannot operate visual-only canvas controls.
- Arbitrary text generation is intentionally absent; the inspector extracts literal values already
  present in the task and pauses when a page requires text the task did not supply.
- Confidence is useful for ranking but is not a correctness or safety guarantee.
- Same-origin redirects can still load a different origin before the next observation blocks further
  actions; use an isolated browser profile and trusted starting sites.
- Shadow roots, frames, canvas, downloads, uploads, pop-up tabs, and nested scrolling are outside the
  current runtime.
- Complex tasks should be decomposed into bounded goals with deterministic postconditions.
