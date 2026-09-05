"""CLI for refreshing Qwen extraction snapshots and reproducing evaluation reports."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from langchain_core.messages import HumanMessage, SystemMessage

from app.config.business import get_business_config
from app.evaluation.evaluator import evaluate
from app.evaluation.schemas import (
    EvaluationDataset,
    ExtractionPrediction,
    PredictedMemory,
    PredictionSnapshot,
)
from app.memory.extractor import EXTRACTION_PROMPT, ExtractionResult
from app.models.qwen import create_chat_model

EVALUATION_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = EVALUATION_DIR / "cases.json"
DEFAULT_PREDICTIONS = EVALUATION_DIR / "baseline_predictions.json"
DEFAULT_REPORT = EVALUATION_DIR / "reports" / "baseline.json"


def _read_model(path: Path, model_type):
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


async def refresh_predictions(
    dataset: EvaluationDataset,
) -> PredictionSnapshot:
    business = get_business_config()
    model = create_chat_model(config=business).with_structured_output(
        ExtractionResult, include_raw=True
    )
    predictions: dict[str, ExtractionPrediction] = {}
    for case in dataset.extraction_cases:
        started = perf_counter()
        result = await model.ainvoke(
            [
                SystemMessage(content=EXTRACTION_PROMPT),
                HumanMessage(content=case.message),
            ]
        )
        parsed = result["parsed"]
        raw = result["raw"]
        usage = raw.usage_metadata or {}
        predictions[case.id] = ExtractionPrediction(
            memories=[
                PredictedMemory(
                    content=item.content,
                    kind=item.kind,
                    confidence=item.confidence,
                    sensitivity=item.sensitivity,
                    explicit=item.explicit,
                )
                for item in parsed.memories
            ],
            latency_ms=round((perf_counter() - started) * 1000, 2),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
    return PredictionSnapshot(
        dataset_version=dataset.version,
        model=business.models.chat_model,
        generated_at=datetime.now(timezone.utc).isoformat(),
        predictions=predictions,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行长期记忆离线评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--refresh-predictions",
        action="store_true",
        help="调用真实千问并覆盖提取预测快照",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = _read_model(args.dataset, EvaluationDataset)
    if args.refresh_predictions:
        snapshot = asyncio.run(refresh_predictions(dataset))
        args.predictions.write_text(
            snapshot.model_dump_json(indent=2), encoding="utf-8"
        )
    else:
        snapshot = _read_model(args.predictions, PredictionSnapshot)
    report = evaluate(dataset, snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
