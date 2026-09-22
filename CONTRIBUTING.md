# Contributing

Thanks for helping improve OpenUltra. The most useful contributions are reproducible browser failures, tests for previously missed controls, accessibility fixes, and narrowly scoped improvements to the observation-decision-execution loop.

## Before opening a PR

1. Search existing issues and PRs. For a behavior change, open an issue describing the task, expected outcome, and observed outcome before a large implementation.
2. Keep page content untrusted. The model may rank only observed, bounded actions; its answer must never become executable JavaScript, an unvalidated selector, or a new permission.
3. Add a focused regression test. If you change the Python decision or CDP path, test the before/after state and uncertainty behavior. If you change the inspector, add a JavaScript test and check the browser view.
4. Keep changes scoped. Do not add network model calls, hidden browser profiles, new permissions, or generated content without explaining the trust boundary and testing it.

## Local setup

The current runtime requires an Apple Silicon Mac. CI also runs Python and inspector-JavaScript tests on Linux without loading the MLX checkpoint.

```bash
./scripts/setup_mac.sh
.venv/bin/python -m pip install -e '.[dev]'
npm ci
.venv/bin/ruff check .
.venv/bin/pytest -q --cov=openultra_browser --cov-report=xml:coverage.xml --cov-fail-under=80
npm test
.venv/bin/python -m build
```

For a local SonarQube scan, run the tests first to generate `coverage.xml` and `coverage/lcov.info`, then run SonarScanner using `sonar-project.properties` and your own server URL/token. The target is zero open issues, at least 80% overall coverage, and no material duplication; a default new-code-only pass is not sufficient.

## Reporting a browser failure

Include the smallest task, starting URL, browser/OS version, whether text or voice was used, what action was proposed, what happened, and a redacted trace or screenshot if safe. State whether a login, iframe, shadow root, canvas, or upload is involved. Do not post credentials, cookies, personal data, private URLs, or raw recordings of other people.

## Review expectations

PRs should explain the cause, user-visible behavior, safety impact, and verification performed. New behavior needs tests and documentation where appropriate. Maintainers may ask for a smaller change or a deterministic postcondition before merging. See [Security](SECURITY.md) for private vulnerability reporting and [Code of Conduct](CODE_OF_CONDUCT.md) for community standards.
