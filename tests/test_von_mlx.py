import mlx.core as mx
import pytest
from laya_mlx.model import EncoderConfig

from openultra_browser import von_mlx
from openultra_browser.von_mlx import VonMLXPredictor, _probabilities, _VonClassifier, _VonHead


def test_von_probabilities_are_normalized_and_finite():
    values = _probabilities([1000.0, 998.0, -1000.0])
    assert sum(values) == pytest.approx(1.0)
    assert values[0] > values[1] > values[2]


def test_von_adapter_preserves_choice_ids_and_boolean_direction():
    predictor = object.__new__(VonMLXPredictor)
    predictor.temperature = 1.0
    captured = []

    def scores(pairs):
        captured.extend(pairs)
        return [5.0, 1.0, 4.0, 0.0], 42

    predictor._entailment_scores = scores
    result = predictor.predict(
        "A visible Rent link is present.",
        {
            "action": {
                "type": "choice",
                "instructions": "Select the useful action.",
                "criteria": {"rent": "Open Rent", "login": "Open Login"},
            },
            "ready": {
                "type": "noul",
                "instructions": "Can the task proceed?",
                "criteria": {"true": "The link is visible", "false": "The link is absent"},
            },
        },
    )

    assert captured[0] == ("A visible Rent link is present.", "Select the useful action. Open Rent")
    assert result["answers"]["action"]["choice"] == "rent"
    assert result["answers"]["ready"]["noul"] > 0.9
    assert result["usage"]["input_tokens"] == 42


def test_von_adapter_rejects_unsupported_question_types():
    predictor = object.__new__(VonMLXPredictor)
    with pytest.raises(ValueError, match="cannot score question"):
        predictor.predict("state", {"rating": {"type": "score", "criteria": ["low", "high"]}})


def test_von_head_and_classifier_accept_masked_inputs():
    config = EncoderConfig.from_dict(
        {
            "vocab_size": 32,
            "hidden_size": 8,
            "intermediate_size": 16,
            "num_hidden_layers": 1,
            "num_attention_heads": 2,
        }
    )
    head = _VonHead(config)
    assert head(mx.ones((1, 8))).shape == (1, 8)
    classifier = _VonClassifier(config)
    result = classifier(mx.array([[1, 2, 0]]), mx.array([[1, 1, 0]]))
    mx.eval(result)
    assert result.shape == (1, 3)


def test_von_checkpoint_load_validates_architecture_and_temperature(tmp_path, monkeypatch):
    import json

    class FakeModel:
        def __init__(self, _config):
            self.loaded = False

        def load_weights(self, weights, *, strict):
            self.loaded = strict and len(weights) == 1

        def eval(self):
            return self

        def parameters(self):
            return []

    class FakeTokenizer:
        def enable_truncation(self, **kwargs):
            assert kwargs["max_length"] == 512

        def token_to_id(self, _token):
            return 0

    config = {
        "architectures": ["ModernBertForSequenceClassification"],
        "classifier_pooling": "mean",
        "id2label": {"0": "entailment", "1": "neutral", "2": "contradiction"},
        "vocab_size": 32,
        "hidden_size": 8,
        "intermediate_size": 16,
        "num_hidden_layers": 1,
        "num_attention_heads": 2,
    }
    for name, content in {
        "config.json": json.dumps(config),
        "calibration.json": '{"temperature": 1.25}',
        "model.safetensors": "",
        "tokenizer.json": "",
    }.items():
        (tmp_path / name).write_text(content)
    monkeypatch.setattr(von_mlx, "_download", lambda name: tmp_path / name)
    monkeypatch.setattr(von_mlx, "_VonClassifier", FakeModel)
    monkeypatch.setattr(von_mlx.mx, "load", lambda _path: {"weight": mx.ones((1,))})
    monkeypatch.setattr(von_mlx.Tokenizer, "from_file", lambda _path: FakeTokenizer())

    predictor = VonMLXPredictor()
    assert predictor.model.loaded
    assert predictor.temperature == 1.25
    assert predictor.pad_id == 0

    config["classifier_pooling"] = "cls"
    (tmp_path / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="architecture"):
        VonMLXPredictor()


def test_von_entailment_scores_pad_and_batch(monkeypatch):
    class Encoding:
        def __init__(self, ids):
            self.ids = ids

    class FakeTokenizer:
        def encode_batch(self, _pairs, add_special_tokens):
            assert add_special_tokens
            return [Encoding([1, 2, 3]), Encoding([1, 2])]

    class FakeModel:
        def __call__(self, ids, mask):
            assert ids.shape == (2, 3)
            assert mask.tolist() == [[1, 1, 1], [1, 1, 0]]
            return mx.array([[4.0, 0.0, 0.0], [1.0, 0.0, 0.0]])

    predictor = object.__new__(VonMLXPredictor)
    predictor.tokenizer = FakeTokenizer()
    predictor.model = FakeModel()
    predictor.pad_id = 0
    scores, token_count = predictor._entailment_scores([("a", "b"), ("a", "c")])
    assert scores == [4.0, 1.0]
    assert token_count == 5
