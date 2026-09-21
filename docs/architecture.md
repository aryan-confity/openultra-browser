# Architecture

## Decision boundary

Laya does not receive selectors, executable JavaScript, secrets, or arbitrary coordinates. It sees:

- the user's goal;
- current URL and title;
- bounded visible text;
- recent action outcomes;
- a finite dictionary of action IDs and semantic descriptions.

The operation and compatible-target questions are independent rows in one local MLX batch. Target
heads exist only for operations currently supported by visible controls. The runtime combines
operation and target probabilities for safe fallback, and treats every answer as a proposal, not
authorization. Configured completion conditions are independently verified; otherwise a `DONE`
proposal remains `needs_verification`. Stuck detection uses observed outcomes.

Laya preserves each question head and may truncate the tail of long state. The state therefore puts
the goal, current URL, untrusted-content boundary, and recent outcomes before bounded page text so
the least important text tail is discarded first.

The interactive runtime exposes the same state machine in two phases. `predict` binds a decision to
the current snapshot fingerprint without mutating the page. `act` consumes that decision before
input begins and refuses a different fingerprint. Automatic mode calls those same methods; it is not
a second execution path.

## Observation

One browser evaluation stamps visible interactive nodes with ephemeral IDs and returns their role,
accessible name, value metadata, destination, state, select options, and viewport geometry. Hidden and
offscreen controls are excluded. Every observation receives a content fingerprint.

The candidate builder scores goal/element token overlap and keeps a bounded set that fits Laya's
context. Global back, scroll, and wait actions remain available. Text actions refer to named prepared
values; the actual values remain in the executor. A fill whose exact prepared value is already
present is pruned. Form-associated search inputs and textareas expose a separate typed `SUBMIT`
operation, so entering a value is never confused with submitting it.
The planner may tell Laya that a hidden prepared value overlaps the goal, but it never includes the
value itself in model state, traces, or action descriptions.
Direct goal matching uses accessible labels, not query terms embedded in destination URLs. URLs are
retained separately for domain policy and navigation guards.
When a caller supplies a success URL prefix, an observed link to that prefix receives an explicit
planner fact. Laya still chooses the operation and target; completion is checked only after navigation.
URL-regex postconditions support dynamic result routes without preselecting a concrete result ID.
When a visible candidate directly matches the goal or verifier, unrelated element targets are pruned
from that decision cycle. Scroll/back controls remain available for recovery and exploration.
If no direct target is visible while unexplored content exists below, scrolling receives an explicit
progress fact and waiting is removed for that cycle.
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
6. selects the next-highest safe probability when intervention is required.

The executor resolves only the ephemeral observed ID. Model output never becomes a selector or script.
If the page becomes stale before input starts, the decision is discarded and the next iteration
observes again. Once any input may have executed, transport uncertainty stops the run instead of
retrying and risking a duplicate action.

## Verification

After execution, the runtime takes one fresh atomic observation. Expected link and history
navigations first receive a bounded document-transition wait so the old page cannot be mistaken for
the result. A changed fingerprint records progress. Three consecutive non-wait no-op steps stop the
run. Deterministic visible-text and URL-prefix checks can complete a run. If both are configured,
both must pass. Without a configured verifier, a `done` proposal is reported for external
verification and never treated as proof.

## Local inspector

The visual inspector is an observability and control surface around the core runtime, not an
authority boundary. It displays the current screenshot, indexed targets, operation and target
probabilities, selected safe action, policy intervention, monotonic timer, and executed history. It
supports manual prediction, manual execution, and bounded automatic execution.

The HTTP server binds to `127.0.0.1`, validates the exact Host and Origin, requires a random per-run
token for mutation requests, limits request sizes and field lengths, serves no third-party assets,
and applies a restrictive content-security policy. Prepared input values stay in `RunConfig` and the
executor; serialized inspector state exposes only their names through action descriptions.

## Known limits

- Generic Laya weights were not trained specifically for browser navigation.
- DOM-only observation cannot operate visual-only canvas controls.
- Arbitrary text generation is intentionally absent; callers supply prepared values.
- Confidence is useful for ranking but is not a correctness or safety guarantee.
- Same-origin redirects can still load a different origin before the next observation blocks further
  actions; use an isolated browser profile and trusted starting sites.
- Shadow roots, frames, canvas, downloads, uploads, pop-up tabs, and nested scrolling are outside the
  current runtime.
- Complex tasks should be decomposed into bounded goals with deterministic postconditions.
