# Contributing

Use Python 3.11-3.13. Keep model decisions typed and bounded, keep execution deterministic,
and add regression coverage for every behavior change.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest -q
python -m build
```

Do not commit model weights, browser profiles, traces, credentials, or captured user data.
