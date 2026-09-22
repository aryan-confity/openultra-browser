import mlx.core as mx
import pytest

from openultra_browser import semif_mlx
from openultra_browser.semif_mlx import SemIfMLXPredictor


def test_semif_single_observed_target_needs_no_model_call():
    predictor = object.__new__(SemIfMLXPredictor)
    result = predictor.predict(
        "A visible Rent link exists",
        {"target": {"type": "choice", "criteria": {"click_rent": "Activate Rent"}}},
    )

    assert result["answers"]["target"] == {
        "choice": "click_rent",
        "probabilities": {"click_rent": 1.0},
        "confidence": 1.0,
    }
    assert result["usage"]["input_tokens"] == 0


def test_semif_rejects_more_options_than_single_token_slots():
    predictor = object.__new__(SemIfMLXPredictor)
    with pytest.raises(ValueError, match="2-26"):
        predictor._encode(
            "state",
            {"type": "choice", "criteria": {str(index): str(index) for index in range(27)}},
        )


def test_semif_rejects_unsupported_question_types():
    predictor = object.__new__(SemIfMLXPredictor)
    with pytest.raises(ValueError, match="cannot score"):
        predictor._encode("state", {"type": "score", "criteria": ["low", "high"]})


class FakeTokenizer:
    pad_token_id = 0
    eos_token_id = 3

    def encode(self, text, add_special_tokens=False):
        assert not add_special_tokens
        if len(text) == 1 and text.isupper():
            return [ord(text)]
        if text.startswith("PROMPT") and len(text) == len("PROMPT") + 1:
            return [1, 2, ord(text[-1])]
        return [1, 2]

    def decode(self, ids):
        return chr(ids[0])

    def apply_chat_template(self, messages, **kwargs):
        assert "evidence" in messages[-1]["content"]
        assert kwargs["enable_thinking"] is False
        return "PROMPT"


def test_semif_checkpoint_load_and_batched_prediction(tmp_path, monkeypatch):
    import json

    import mlx_lm

    class FakeModel:
        def eval(self):
            return self

        def parameters(self):
            return []

        def __call__(self, tokens):
            batch, width = tokens.shape
            row = [0.0] * 100
            row[ord("A")] = 5.0
            row[ord("B")] = 1.0
            return mx.array([[row] * width for _ in range(batch)])

    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "quantization": {"bits": 4}})
    )
    monkeypatch.setattr(semif_mlx, "snapshot_download", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(mlx_lm, "load", lambda *_a, **_k: (FakeModel(), FakeTokenizer()))

    predictor = SemIfMLXPredictor()
    result = predictor.predict(
        "Rent is visible",
        {
            "operation": {
                "type": "choice",
                "instructions": "What advances the goal?",
                "criteria": {"click": "Click Rent", "wait": "Wait"},
            },
            "ready": {
                "type": "noul",
                "instructions": "Can the task proceed?",
                "criteria": {"true": "Yes", "false": "No"},
            },
        },
    )

    assert result["answers"]["operation"]["choice"] == "click"
    assert result["answers"]["ready"]["noul"] > 0.9
    assert result["usage"]["input_tokens"] == 4


def test_semif_rejects_wrong_quantization(tmp_path, monkeypatch):
    import json

    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "quantization": {"bits": 8}})
    )
    monkeypatch.setattr(semif_mlx, "snapshot_download", lambda *_a, **_k: str(tmp_path))
    with pytest.raises(ValueError, match="pinned"):
        SemIfMLXPredictor()
