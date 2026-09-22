"""Optional one-shot local extraction of literal task values for browser fields."""

from __future__ import annotations

import importlib.util
import json
import os
import re

FROM_FIELD = "where from"
TO_FIELD = "where to"

ALLOWED_FIELDS = {
    "from": FROM_FIELD,
    FROM_FIELD: FROM_FIELD,
    "origin": FROM_FIELD,
    "to": TO_FIELD,
    TO_FIELD: TO_FIELD,
    "destination": TO_FIELD,
    "departure": "departure",
    "departure date": "departure",
    "date": "date",
    "return": "return",
    "return date": "return",
    "name": "name",
    "phone": "phone",
    "email": "email",
    "whatsapp number": "whatsapp number",
    "line id": "line id",
    "rent or sell": "rent or sell",
}


def planner_available() -> bool:
    return os.environ.get("OPENULTRA_LOCAL_PLANNER", "1") != "0" and (
        importlib.util.find_spec("mlx_lm") is not None
    )


def _literal_in_goal(goal: str, value: str) -> bool:
    def normalized(text: str) -> str:
        return re.sub(r"[^\w]+", " ", text.casefold()).strip()
    candidate = normalized(value)
    return bool(candidate and f" {candidate} " in f" {normalized(goal)} ")


def _accepted_requirement(goal: str, raw_key: object, raw_value: object) -> tuple[str, str] | None:
    if not isinstance(raw_key, str) or not isinstance(raw_value, str):
        return None
    key = ALLOWED_FIELDS.get(raw_key.strip().casefold())
    value = raw_value.strip()
    if key and 0 < len(value) <= 100 and _literal_in_goal(goal, value):
        return key, value
    return None


def _requirement_pairs(item: object):
    if not isinstance(item, dict):
        return ()
    if "what" in item or "value" in item:
        return ((item.get("what"), item.get("value")),)
    return item.items()


def validate_plan(goal: str, response: str) -> dict[str, str]:
    """Reject generated or navigation values; the model cannot authorize actions."""
    start = response.find("{")
    if start < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(response[start:])
    except (ValueError, TypeError):
        return {}
    requirements = payload.get("requirements") if isinstance(payload, dict) else None
    if not isinstance(requirements, list):
        return {}
    values: dict[str, str] = {}
    for item in requirements[:8]:
        for raw_key, raw_value in _requirement_pairs(item):
            accepted = _accepted_requirement(goal, raw_key, raw_value)
            if accepted:
                key, value = accepted
                values.setdefault(key, value)
    return values


def plan_literal_inputs(goal: str) -> dict[str, str]:
    """Run a bounded local MLX text pass once, never in the per-action decision loop."""
    from mlx_lm import generate

    from .semif_mlx import load_local_text_model

    model, tokenizer = load_local_text_model()
    prompt = tokenizer.apply_chat_template(
        [
            {
                "role": "system",
                "content": (
                    "Extract only literal field values explicitly provided by the user. "
                    "Return compact JSON: {\"requirements\":[{\"what\":field,\"value\":text}]}. "
                    "Allowed fields: where from, where to, departure, return, date, name, phone, "
                    "email, whatsapp number, line id, rent or sell. "
                    "Never output website, page, navigation, action, or finish requirements. "
                    "Never invent or infer a value. Preserve user spelling and date wording. "
                    "For 'find flights between Bangkok to Munich on Jan 28', output "
                    "where from=Bangkok, where to=Munich, departure=Jan 28. "
                    "For 'change the date to September 28', output date=September 28. "
                    "If no literal field value is provided, return {\"requirements\":[]}."
                ),
            },
            {"role": "user", "content": goal[:2_000]},
        ],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    answer = generate(model, tokenizer, prompt=prompt, max_tokens=220, verbose=False)
    return validate_plan(goal, answer)
