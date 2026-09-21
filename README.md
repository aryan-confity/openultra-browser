# Laya Browser

Fast, local browser control powered by Laya typed decisions and a persistent Chrome CDP session.

Laya Browser observes visible page controls, constructs a bounded action space, and asks a local
Laya model to rank the next operation and every compatible target in one batch. A deterministic
policy validates the selected element, blocks unapproved destinations and sensitive controls,
executes through Chrome DevTools Protocol, and verifies observable progress before the next
decision.

```text
visible DOM + goal
       |
       v
bounded typed actions ---> local Laya MLX ---> probabilities
       |                                         |
       +--------- deterministic policy <---------+
                              |
                              v
                       CDP action + verification
```

## Properties

- Local MLX inference on Apple Silicon; no remote model calls.
- One batched Laya call per step for the operation and every compatible target head.
- Dynamic operation and target heads built only from currently visible, indexed DOM nodes.
- Stale-target, cross-domain, password, upload, financial, and destructive-action guards.
- Named prepared values for text fields; values are not placed in the model prompt.
- Probability-aware fallback when the highest-ranked action is blocked or recently ineffective.
- Deterministic success checks, bounded steps and time, and atomic JSON traces.
- A dedicated visible Chrome profile that isolates automation from personal browser data.
- A loopback-only visual inspector with a live timer, target overlays, probabilities, guarded
  predict/execute controls, automatic mode, and trace export.

## Install

Requires Apple Silicon, Python 3.11-3.13, Google Chrome, and a local Laya MLX checkpoint. The default
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
export LAYA_MODEL_PATH=/path/to/laya-mlx
```

## Run

```bash
./scripts/launch_chrome.sh
export BU_CDP_URL=http://127.0.0.1:9333

laya-browser run https://example.com \
  --goal "Open the documentation" \
  --success-text "Documentation" \
  --success-url-prefix "https://example.com/docs" \
  --trace artifacts/run.json
```

Prepared text is identified to Laya by name while the value remains outside the prompt:

```bash
laya-browser run https://example.com/search \
  --goal "Search for local inference" \
  --input query="local inference" \
  --success-text "Search results"
```

The launcher uses an isolated browser profile under `.runtime/`; it does not attach to the normal
Chrome profile. The start domain is the only permitted destination by default. Add domains with
`--allow-domain`. Sensitive or destructive actions remain blocked unless `--allow-risky` is given;
password and file inputs are always outside this runtime's control.

Supply `--success-text`, `--success-url-prefix`, `--success-url-regex`, or a combination whenever a
task has an observable postcondition. Every configured check must pass. Without a deterministic
postcondition, a Laya `DONE` proposal returns `needs_verification` instead of claiming success.

## Visual Inspector

Start the isolated Chrome profile, then launch the local inspector:

```bash
./scripts/launch_chrome.sh
export BU_CDP_URL=http://127.0.0.1:9333
laya-browser inspect
```

The inspector opens at `http://127.0.0.1:8766`. Enter a task, start URL, optional prepared text, and
deterministic success checks. `Choose next` runs local Laya inference without mutating the page;
`Execute choice` applies exactly that fingerprint-bound decision; `Run automatically` repeats the
same guarded cycle. The live timer, screenshots, indexed targets, operation probabilities, ranked
actions, policy interventions, and decision trail remain visible throughout the run.

The server binds only to loopback, requires a random per-run request token and same-origin POSTs,
sets a restrictive content-security policy, and sends no model request over the network. Prepared
text remains executor-only and is excluded from model state and trace exports.

## Local Demo

```bash
./scripts/run_demo.sh
```

The demo starts a local website, launches the isolated Chrome profile, loads the local Laya MLX
checkpoint, and navigates a two-step documentation task. The resulting trace is written to
`artifacts/demo.json`.

## Development

```bash
ruff check .
pytest -q
python -m build
```

See [Architecture](docs/architecture.md) for the decision and safety boundaries.
