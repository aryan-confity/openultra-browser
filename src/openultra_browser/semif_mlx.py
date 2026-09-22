"""Optional MLX conditional-option adapter inspired by SemIf's direct readout."""

from __future__ import annotations

import json
import math
from pathlib import Path

import mlx.core as mx
from huggingface_hub import snapshot_download

SEMIF_MODEL = "semif-mlx"
SEMIF_REPOSITORY = "mlx-community/Qwen3.5-4B-MLX-4bit"
SEMIF_REVISION = "32f3e8ecf65426fc3306969496342d504bfa13f3"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_TOKENS = 2_048
BATCH_SIZE = 4
VISIBLE_MARKER = "Visible page text:\n"
SYSTEM_PROMPT = (
    "Apply the criterion to the supplied evidence. Choose exactly one listed option. "
    "Respond with only its uppercase letter, with no explanation or reasoning."
)


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit == 0:
        return "[Visible text omitted to fit the local model context.]"
    head = limit // 2
    return f"{text[:head]}\n[Middle of visible text omitted.]\n{text[-(limit - head):]}"


def _state_candidates(state: str):
    yield state
    prefix, separator, visible = state.partition(VISIBLE_MARKER)
    if not separator:
        return
    for limit in (1_200, 800, 400, 200, 0):
        yield prefix + separator + _excerpt(visible, limit)
    lines = prefix.splitlines()
    essential = [
        line
        for line in lines
        if line.startswith(
            ("Overall task:", "Current required step:", "Current page:", "Trust boundary:")
        )
    ]
    recent = next((line for line in lines if line.startswith("Recent actions:")), "")
    if recent:
        essential.append("Recent actions (latest excerpt): " + recent[-500:])
    compact = "\n".join(essential) + "\n" + separator
    for limit in (600, 300, 0):
        yield compact + _excerpt(visible, limit)


def _question_choices(question: dict) -> tuple[list[str], list[str]]:
    kind = question.get("type")
    criteria = question.get("criteria") or {}
    if kind == "choice" and isinstance(criteria, dict) and criteria:
        choices = list(criteria)
        descriptions = [str(criteria[key] or key) for key in choices]
    elif kind == "noul" and isinstance(criteria, dict):
        choices = ["true", "false"]
        descriptions = [
            str(criteria.get("true") or "The condition is true."),
            str(criteria.get("false") or "The condition is false."),
        ]
    else:
        raise ValueError(f"SemIf cannot score question type {kind!r}")
    if not 2 <= len(choices) <= len(LETTERS):
        raise ValueError("SemIf requires 2-26 modeled options per question")
    return choices, descriptions


class SemIfMLXPredictor:
    """Score declared options from Qwen3.5 next-token logits, without generation."""

    def __init__(self) -> None:
        try:
            from mlx_lm import load
        except ImportError as error:
            raise RuntimeError("SemIf requires: pip install 'openultra-browser[semif]'") from error
        directory = Path(
            snapshot_download(
                SEMIF_REPOSITORY,
                revision=SEMIF_REVISION,
                allow_patterns=["*.json", "*.safetensors", "*.jinja", "*.txt", "*.model"],
            )
        )
        config = json.loads((directory / "config.json").read_text())
        quantization = config.get("quantization") or config.get("quantization_config") or {}
        if config.get("model_type") != "qwen3_5" or quantization.get("bits") != 4:
            raise ValueError("SemIf checkpoint must be the pinned Qwen3.5 4-bit MLX model")
        mx.set_cache_limit(256 * 1024 * 1024)
        self.model, self.tokenizer = load(
            directory,
            tokenizer_config={"trust_remote_code": False},
        )
        self.model.eval()
        mx.eval(self.model.parameters())
        self.slots = []
        for letter in LETTERS:
            ids = self.tokenizer.encode(letter, add_special_tokens=False)
            if len(ids) != 1 or self.tokenizer.decode(ids) != letter:
                raise ValueError(f"SemIf answer slot {letter} is not one round-trip token")
            self.slots.append(ids[0])
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("SemIf answer slots collide")
        self.pad_id = self.tokenizer.pad_token_id
        if self.pad_id is None:
            self.pad_id = self.tokenizer.eos_token_id
        if self.pad_id is None:
            raise ValueError("SemIf tokenizer has no padding or EOS token")

    def _encode(self, state: str, question: dict) -> tuple[list[int], list[str]]:
        choices, descriptions = _question_choices(question)
        payload = {
            "evidence": state,
            "criterion": str(question.get("instructions") or ""),
            "options": [
                {"letter": LETTERS[index], "description": description}
                for index, description in enumerate(descriptions)
            ],
        }
        for candidate_state in _state_candidates(state):
            payload["evidence"] = candidate_state
            prompt = self.tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            ids = self.tokenizer.encode(prompt, add_special_tokens=False)
            if ids and len(ids) <= MAX_TOKENS:
                break
        else:
            raise ValueError("SemIf task and action choices exceed its 2048-token browser limit")
        for index in range(len(choices)):
            if self.tokenizer.encode(prompt + LETTERS[index], add_special_tokens=False) != (
                ids + [self.slots[index]]
            ):
                raise ValueError("SemIf answer boundary changes tokenization")
        return ids, choices

    def predict(self, state: str, questions: dict) -> dict:
        answers: dict[str, dict] = {}
        modeled = {}
        for key, question in questions.items():
            criteria = question.get("criteria")
            if question.get("type") == "choice" and isinstance(criteria, dict) and len(criteria) == 1:
                choice = next(iter(criteria))
                answers[key] = {
                    "choice": choice,
                    "probabilities": {choice: 1.0},
                    "confidence": 1.0,
                }
            else:
                modeled[key] = question
        encoded = [(key, *self._encode(state, question)) for key, question in modeled.items()]
        for start in range(0, len(encoded), BATCH_SIZE):
            chunk = encoded[start : start + BATCH_SIZE]
            width = max(len(ids) for _, ids, _ in chunk)
            tokens = mx.array([ids + [self.pad_id] * (width - len(ids)) for _, ids, _ in chunk])
            logits = self.model(tokens)
            selected = [
                logits[index, len(ids) - 1, mx.array(self.slots[: len(choices)])].astype(mx.float32)
                for index, (_, ids, choices) in enumerate(chunk)
            ]
            mx.eval(selected)
            for (key, _, choices), values in zip(chunk, selected, strict=True):
                raw = values.tolist()
                if any(not math.isfinite(value) for value in raw):
                    raise ValueError("SemIf returned nonfinite option scores")
                probabilities = mx.softmax(values).tolist()
                if questions[key]["type"] == "noul":
                    answers[key] = {"noul": probabilities[0]}
                else:
                    best = probabilities.index(max(probabilities))
                    ordered = sorted(probabilities, reverse=True)
                    answers[key] = {
                        "choice": choices[best],
                        "probabilities": dict(zip(choices, probabilities, strict=True)),
                        "confidence": ordered[0] - ordered[1],
                    }
        return {
            "answers": answers,
            "usage": {"input_tokens": sum(len(ids) for _, ids, _ in encoded)},
        }
