import json
from pathlib import Path

from app.evaluation.evaluator import evaluate
from app.evaluation.schemas import EvaluationDataset, PredictionSnapshot

EVALUATION_DIR = Path(__file__).resolve().parents[1] / "app" / "evaluation"


def test_baseline_report_is_reproducible_without_model_api() -> None:
    dataset = EvaluationDataset.model_validate_json(
        (EVALUATION_DIR / "cases.json").read_text(encoding="utf-8")
    )
    snapshot = PredictionSnapshot.model_validate_json(
        (EVALUATION_DIR / "baseline_predictions.json").read_text(encoding="utf-8")
    )
    expected_report = json.loads(
        (EVALUATION_DIR / "reports" / "baseline.json").read_text(encoding="utf-8")
    )

    report = evaluate(dataset, snapshot)

    assert 30 <= report["sample_count"] <= 50
    assert report == expected_report
    assert report["retrieval"]["deletion_residual_rate"] == 0
    assert report["retrieval"]["cross_user_leak_rate"] == 0


def test_prediction_snapshot_does_not_persist_fixture_secret() -> None:
    snapshot_text = (EVALUATION_DIR / "baseline_predictions.json").read_text(
        encoding="utf-8"
    )

    assert "sk-demo-value" not in snapshot_text
