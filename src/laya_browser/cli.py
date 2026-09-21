"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import BrowserAgent
from .config import RunConfig, default_model_path
from .models import StepRecord


def _prepared_inputs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or not key.strip():
            raise argparse.ArgumentTypeError("--input must use NAME=VALUE")
        result[key.strip()] = content
    return result


def _step_line(record: StepRecord) -> None:
    shield = " shield" if record.policy_intervened else ""
    changed = "changed" if record.changed else "unchanged"
    print(
        f"[{record.step:02d}] {record.executed_action or 'BLOCKED'} "
        f"p={record.confidence:.3f} {record.inference_ms:.1f}ms {changed}{shield}"
    )
    print(f"     {record.description}")
    if record.policy_reason:
        print(f"     policy: {record.policy_reason}")
    if record.action_error:
        print(f"     error: {record.action_error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laya-browser",
        description="Control a browser with local Laya typed decisions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run a bounded browser goal")
    run.add_argument("url")
    run.add_argument("--goal", required=True)
    run.add_argument("--model", default=default_model_path())
    run.add_argument("--allow-domain", action="append", default=[])
    run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    run.add_argument("--success-text")
    run.add_argument("--success-url-prefix")
    run.add_argument("--max-steps", type=int, default=20)
    run.add_argument("--max-seconds", type=float, default=60)
    run.add_argument("--max-candidates", type=int, default=18)
    run.add_argument("--keep-open", type=float, default=0, metavar="SECONDS")
    run.add_argument("--allow-risky", action="store_true")
    run.add_argument("--optimize", action="store_true")
    run.add_argument("--trace", type=Path)
    return parser


def _run(args: argparse.Namespace) -> int:
    config = RunConfig(
        goal=args.goal,
        start_url=args.url,
        model=args.model,
        allowed_domains=frozenset(args.allow_domain),
        prepared_inputs=_prepared_inputs(args.input),
        success_text=args.success_text,
        success_url_prefix=args.success_url_prefix,
        max_steps=args.max_steps,
        max_seconds=args.max_seconds,
        max_candidates=args.max_candidates,
        keep_open_seconds=args.keep_open,
        allow_risky=args.allow_risky,
        optimize=args.optimize,
        trace_path=args.trace,
    )
    print(f"Loading local Laya model: {config.model}")
    result = BrowserAgent(config, on_step=_step_line).run()
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == "completed" else 1


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
