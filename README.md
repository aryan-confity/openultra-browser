# OpenUltra

Fast, local browser control powered by typed decisions and a persistent Chrome CDP session.

OpenUltra observes visible page controls, constructs a bounded action space, and asks a local
decision model to rank the next operation and every compatible target in one batch. A deterministic
policy validates the selected element, blocks unapproved destinations and sensitive controls,
executes through Chrome DevTools Protocol, and verifies observable progress before the next
decision.

```text
visible DOM + goal
       |
       v
bounded typed actions ---> local MLX model ---> probabilities
       |                                         |
       +--------- deterministic policy <---------+
                              |
                              v
                       CDP action + verification
```

## Properties

- Local MLX inference on Apple Silicon; no remote model calls.
- One local batched call per step for the operation and every compatible target head.
- Dynamic operation and target heads built only from currently visible, indexed DOM nodes.
- Stale-target, cross-domain, password, upload, financial, and destructive-action guards.
- Named prepared values for text fields; values are not placed in the model prompt.
- Probability-aware fallback when the highest-ranked action is blocked or recently ineffective.
- Independent typed completion and stuck heads, optional deterministic checks, bounded execution,
  and atomic JSON traces.
- A dedicated visible Chrome profile that isolates automation from personal browser data.
- A loopback-only one-page app with a live browser preview, elapsed clock, decision latency,
  decisions-per-second rate, automatic execution, pause/resume, and a compact activity trail.

## Install

Requires Apple Silicon, Python 3.11-3.13, Google Chrome, and a local MLX checkpoint. The default
is the stronger English checkpoint; pass the multilingual checkpoint explicitly for non-English
tasks.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
browser-harness --doctor
```

Use an already downloaded model when needed:

```bash
export OPENULTRA_MODEL_PATH=/path/to/laya-mlx
```

## Run

```bash
./scripts/launch_chrome.sh
export BU_CDP_URL=http://127.0.0.1:9333

openultra-browser run https://example.com \
  --goal "Open the documentation" \
  --success-text "Documentation" \
  --success-url-prefix "https://example.com/docs" \
  --trace artifacts/run.json
```

Prepared text is identified to the model by name while the value remains outside the prompt:

```bash
openultra-browser run https://example.com/search \
  --goal "Search for local inference" \
  --input query="local inference" \
  --success-text "Search results"
```

The launcher uses an isolated browser profile under `.runtime/`; it does not attach to the normal
Chrome profile. The start domain is the only permitted destination by default. Add domains with
`--allow-domain`. Sensitive or destructive actions remain blocked unless `--allow-risky` is given;
password and file inputs are always outside this runtime's control.

Supply `--success-text`, `--success-url-prefix`, `--success-url-regex`, or a combination whenever a
task has an observable postcondition. Every configured check must pass. Without one, completion
requires an in-step Boolean completion head and a second focused local confirmation.

## Visual Inspector

Start the isolated Chrome profile, then launch the local inspector:

```bash
./scripts/launch_chrome.sh
export BU_CDP_URL=http://127.0.0.1:9333
openultra-browser inspect
```

The inspector opens at `http://127.0.0.1:8766`. Enter a browser task in one prompt. A deterministic
local planner selects an explicit URL from the task when present, extracts literal search text from
requests such as `search for ...`, or starts a web search for an otherwise generic task. It never
invents text. Submitting the task starts the guarded run immediately. The fixed-height workspace
keeps the prompt, pause/resume control, elapsed clock, latest decision time, decision rate, compact
activity trail, and continuously refreshed browser preview visible together without dashboard
panels or advanced run-constraint fields.

Task-first inspector runs may follow observed HTTP(S) links across sites because their eventual
destination is not known before the task starts. Password and file inputs, non-HTTP destinations,
and destructive or financial actions remain blocked. The command-line runtime retains its explicit
domain allowlist for automation contracts that require a narrower boundary.

The server binds only to loopback, requires a random per-run request token and same-origin POSTs,
sets a restrictive content-security policy, and sends no model request over the network. Prepared
text remains executor-only and is excluded from model state and trace exports.

## Local Demo

```bash
./scripts/run_demo.sh
```

The demo starts a local website, launches the isolated Chrome profile, loads the local MLX
checkpoint, and navigates a two-step documentation task. The resulting trace is written to
`artifacts/demo.json`.

## Development

```bash
ruff check .
pytest -q
python -m build
```

See [Architecture](docs/architecture.md) for the decision and safety boundaries.
