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

## Observation

One browser evaluation stamps visible interactive nodes with ephemeral IDs and returns their role,
accessible name, value metadata, destination, state, select options, and viewport geometry. Hidden and
offscreen controls are excluded. Every observation receives a content fingerprint.

The candidate builder scores goal/element token overlap and keeps a bounded set that fits Laya's
context. Global back, scroll, and wait actions remain available. Text actions refer to named prepared
values; the actual values remain in the executor.

## Safety shield

Before execution, deterministic code:

1. confirms the action came from the current observation;
2. rejects stale, missing, ambiguous, hidden, password, and file-input targets;
3. restricts navigation to explicit domains;
4. blocks destructive and financial actions by default;
5. avoids repeating recent actions that produced no observable change;
6. selects the next-highest safe probability when intervention is required.

The executor resolves only the ephemeral observed ID. Model output never becomes a selector or script.

## Verification

After execution, the runtime takes one fresh atomic observation. Expected link and history
navigations first receive a bounded document-transition wait so the old page cannot be mistaken for
the result. A changed fingerprint records progress. Three consecutive non-wait no-op steps stop the
run. Deterministic visible-text and URL-prefix checks can complete a run. If both are configured,
both must pass. Without a configured verifier, a `done` proposal is reported for external
verification and never treated as proof.

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
