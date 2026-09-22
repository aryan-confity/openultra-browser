"""MLX adapter for Von 1.0's default ModernBERT NLI checkpoint."""

from __future__ import annotations

import json
import math
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
from huggingface_hub import hf_hub_download
from laya_mlx.model import EncoderConfig, ModernBert
from tokenizers import Tokenizer

VON_MODEL = "von-1.0-mlx"
VON_REPOSITORY = "wfzyx/von-1.0"
VON_REVISION = "aa2fdc9630ecdadef32c56073553b3a69bed38bf"
MAX_TOKENS = 512  # Match Von's published default NLI backend.
BATCH_SIZE = 8


class _VonHead(nn.Module):
    def __init__(self, config: EncoderConfig):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.norm = nn.LayerNorm(config.hidden_size, eps=config.norm_eps, bias=False)

    def __call__(self, hidden):
        return self.norm(nn.gelu(self.dense(hidden)))


class _VonClassifier(nn.Module):
    def __init__(self, config: EncoderConfig):
        super().__init__()
        self.model = ModernBert(config)
        self.head = _VonHead(config)
        self.classifier = nn.Linear(config.hidden_size, 3)

    def __call__(self, ids, mask):
        hidden = self.model(ids, mask)
        valid = mask[..., None].astype(hidden.dtype)
        pooled = (hidden * valid).sum(axis=1) / mx.maximum(valid.sum(axis=1), 1)
        return self.classifier(self.head(pooled))


def _download(name: str) -> Path:
    return Path(hf_hub_download(VON_REPOSITORY, name, revision=VON_REVISION))


def _probabilities(values: list[float]) -> list[float]:
    largest = max(values)
    exponents = [math.exp(value - largest) for value in values]
    total = sum(exponents)
    return [value / total for value in exponents]


def _question_options(question_id: str, question: dict) -> tuple[list[str], list[str], str]:
    kind = question.get("type")
    criteria = question.get("criteria")
    if kind == "choice" and isinstance(criteria, dict) and criteria:
        choices = list(criteria)
        descriptions = [str(criteria[key] or key) for key in choices]
    elif kind == "noul" and (criteria is None or isinstance(criteria, dict)):
        criteria = criteria or {}
        choices = ["true", "false"]
        descriptions = [
            str(criteria.get("true") or "Yes, the condition holds."),
            str(criteria.get("false") or "No, the condition is false."),
        ]
    else:
        raise ValueError(f"Von cannot score question {question_id!r} of type {kind!r}")
    return choices, descriptions, kind


class VonMLXPredictor:
    """Expose Von's entailment classifier through OpenUltra's typed decision API."""

    def __init__(self) -> None:
        config = json.loads(_download("config.json").read_text())
        if (
            config.get("architectures") != ["ModernBertForSequenceClassification"]
            or config.get("classifier_pooling") != "mean"
            or config.get("id2label", {}).get("0") != "entailment"
            or len(config.get("id2label", {})) != 3
        ):
            raise ValueError("Von checkpoint architecture or class order changed")
        encoder_config = EncoderConfig.from_dict(config)
        self.model = _VonClassifier(encoder_config)
        weights = mx.load(str(_download("model.safetensors")))
        self.model.load_weights(
            [(name, value.astype(mx.float16)) for name, value in weights.items()],
            strict=True,
        )
        self.model.eval()
        mx.eval(self.model.parameters())
        self.tokenizer = Tokenizer.from_file(str(_download("tokenizer.json")))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS, strategy="longest_first")
        self.pad_id = self.tokenizer.token_to_id("[PAD]")
        if self.pad_id is None:
            raise ValueError("Von tokenizer has no [PAD] token")
        calibration = json.loads(_download("calibration.json").read_text())
        self.temperature = float(calibration["temperature"])
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("Von calibration temperature must be positive")

    def _entailment_scores(self, pairs: list[tuple[str, str]]) -> tuple[list[float], int]:
        encodings = self.tokenizer.encode_batch(pairs, add_special_tokens=True)
        scores: list[float] = []
        input_tokens = sum(len(item.ids) for item in encodings)
        for start in range(0, len(encodings), BATCH_SIZE):
            chunk = encodings[start : start + BATCH_SIZE]
            width = max(len(item.ids) for item in chunk)
            ids = mx.array([item.ids + [self.pad_id] * (width - len(item.ids)) for item in chunk])
            mask = mx.array([[1] * len(item.ids) + [0] * (width - len(item.ids)) for item in chunk])
            logits = self.model(ids, mask).astype(mx.float32)
            mx.eval(logits)
            scores.extend(float(value) for value in logits[:, 0].tolist())
        return scores, input_tokens

    def predict(self, state: str, questions: dict) -> dict:
        pairs: list[tuple[str, str]] = []
        grouped: list[tuple[str, list[str], str]] = []
        for question_id, question in questions.items():
            choices, descriptions, kind = _question_options(question_id, question)
            instructions = str(question.get("instructions") or "")
            grouped.append((question_id, choices, kind))
            pairs.extend((state, f"{instructions} {description}".strip()) for description in descriptions)

        scores, input_tokens = self._entailment_scores(pairs)
        answers: dict[str, dict] = {}
        offset = 0
        for question_id, choices, kind in grouped:
            values = scores[offset : offset + len(choices)]
            offset += len(choices)
            probabilities = _probabilities([value / self.temperature for value in values])
            if kind == "noul":
                answers[question_id] = {"noul": probabilities[0]}
            else:
                best = probabilities.index(max(probabilities))
                ordered = sorted(probabilities, reverse=True)
                answers[question_id] = {
                    "choice": choices[best],
                    "probabilities": dict(zip(choices, probabilities, strict=True)),
                    "confidence": ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0],
                }
        return {"answers": answers, "usage": {"input_tokens": input_tokens}}
